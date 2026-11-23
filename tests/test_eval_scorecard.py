import unittest
from types import SimpleNamespace
from unittest.mock import patch


class EvalScorecardTest(unittest.TestCase):
    def test_cached_tokens_are_part_of_input(self):
        from claim_sense.core import config
        from claim_sense.integrations.llm import response_usage

        counts = SimpleNamespace(input_tokens=10000, output_tokens=2000,
                                 input_tokens_details=SimpleNamespace(cached_tokens=3000))
        with patch.object(config, "OPENAI_MODEL", "gpt-5.4-nano"), patch.object(config, "USD_TO_INR", 88):
            usage = response_usage("test", counts)
        self.assertEqual(usage["cached_input_tokens"], 3000)
        self.assertAlmostEqual(usage["cost_inr"], (7000 * .20 + 3000 * .02 + 2000 * 1.25) * 88 / 1_000_000)

    def test_resume_preserves_the_original_prediction_and_counts_both_phases(self):
        from evals import run_evals as ev

        label = dict(claim_id="demo", expected_route="AUTO_APPROVE", expected_recommendation="APPROVE",
                     expected_payable="500", is_fraud="False")
        pending = dict(claim_id="demo", route="ADJUSTER_REVIEW", status="PENDING_ADJUSTER",
                       recommendation="REJECT", payable_amount=501, fraud_score=0,
                       details={"cost_inr": 1.4}, error=None)
        final = {**pending, "status": "REJECTED", "details": {"cost_inr": 2.0}}
        with (patch.object(ev, "sample_claim_dir", return_value="unused"),
              patch.object(ev, "process_claim", return_value=pending),
              patch.object(ev, "review_claim", return_value=final) as review,
              patch.object(ev, "perf_counter", side_effect=[0, 25, 100, 105])):
            row = ev.evaluate_claim(label, resume=True)
        self.assertEqual(review.call_args.args[1].decision, "REJECT")
        self.assertEqual(row["got_recommendation"], "REJECT")
        self.assertTrue(row["payable_ok"])
        self.assertEqual((row["seconds"], row["resume_seconds"], row["total_seconds"]), (25, 5, 30))
        self.assertEqual((row["cost_inr"], row["resume_cost_inr"], row["total_cost_inr"]), (1.4, .6, 2))
        self.assertFalse(row["under_2_inr"])
        self.assertFalse(row["under_30_seconds"])

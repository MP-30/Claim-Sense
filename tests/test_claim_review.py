"""Exercise the three reference routes through FastAPI, PostgreSQL, and LangGraph."""

import csv
import json
import subprocess
import sys
import time
import unittest
from contextlib import contextmanager
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch

import psycopg
from fastapi.testclient import TestClient


@contextmanager
def disposable_postgres():
    container = subprocess.check_output(
        [
            "docker", "run", "--rm", "--detach", "--publish", "127.0.0.1::5432",
            "--env", "POSTGRES_USER=claimsense_test",
            "--env", "POSTGRES_PASSWORD=claimsense_test",
            "--env", "POSTGRES_DB=claimsense_test",
            "postgres:18",
        ],
        text=True,
    ).strip()
    try:
        address = subprocess.check_output(
            ["docker", "port", container, "5432/tcp"], text=True,
        ).strip()
        url = f"postgresql://claimsense_test:claimsense_test@{address}/claimsense_test"
        deadline = time.monotonic() + 30
        while True:
            try:
                with psycopg.connect(url, connect_timeout=2):
                    break
            except psycopg.OperationalError as error:
                if time.monotonic() >= deadline:
                    raise RuntimeError("The test PostgreSQL container did not become ready.") from error
                time.sleep(0.25)
        yield url
    finally:
        subprocess.run(["docker", "stop", container], check=True, stdout=subprocess.DEVNULL)


def extraction_responses(scenario):
    from claim_sense.schemas.schemas import ClaimForm, FIRReport, SurveyReport

    policy, form = scenario["policy"], scenario["form"]
    survey, fir = scenario["survey"], scenario["fir"]
    responses = {
        ClaimForm: ClaimForm(
            policy_number=policy["policy_number"], claimant_name=policy["holder_name"],
            vehicle_reg=policy["vehicle_reg"], loss_date=form["loss_date"], loss_time=form["loss_time"],
            loss_location=form["location"], intimation_date=form["intimation_date"],
            driver_name=form["driver"], driver_licence_no=form["licence_no"],
            licence_valid_till=form["licence_valid_till"], vehicle_use_at_time=form["use"],
            accident_description=form["description"], damaged_areas=form["damaged_areas"],
            fir_filed=form["fir"], witness_available=form["witness"].startswith("Yes"),
            garage_id=form["garage"], garage_estimate=form["estimate"],
        ),
        SurveyReport: SurveyReport(
            surveyor_name=survey["surveyor"], inspection_date=survey["inspection_date"],
            damaged_areas=survey["damaged_areas"], observations=survey["observations"],
            cause_consistent_with_claim=survey["consistent"],
            items=[
                {"description": description, "category": category, "amount": amount}
                for description, category, amount in survey["items"]
            ],
        ),
    }
    if fir:
        reading = fir["breath"].split()[0]
        responses[FIRReport] = FIRReport(
            fir_number=fir["fir_number"], incident_date=fir["date"], incident_time=fir["time"],
            location=fir["location"], summary=fir["summary"], driver_intoxicated=fir["intoxicated"],
            breath_alcohol_mg_per_100ml=float(reading) if reading != "Not" else None,
        )
    return responses


class ClaimReviewTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from sqlalchemy import make_url

        directory = cls.enterClassContext(TemporaryDirectory())
        url = cls.enterClassContext(disposable_postgres())
        cls.enterClassContext(patch.dict("os.environ", {
            "DATABASE_URL": url, "CLAIMS_DATA_DIR": directory, "OPENAI_API_KEY": "",
        }))

        from claim_sense.core import config
        from claim_sense.db.session import engine
        from claim_sense.main import app
        from scripts.generate_data import SCENARIOS

        cls.data_dir = Path(directory)
        if engine.url != make_url(url).set(drivername="postgresql+psycopg") or config.DATA_DIR != cls.data_dir:
            raise RuntimeError("Application was initialized outside the disposable test environment")
        cls.seed()
        config.POLICY_WORDING_PATH.write_text(
            "## [OD-1] Own damage cover\nAccidental damage is covered subject to exclusions.\n\n"
            "## [EXC-2] Wear and breakdown\nWear and mechanical breakdown are excluded.\n\n"
            "## [EXC-4] Driving under the influence\nDamage is excluded when the driver is intoxicated.\n",
            encoding="utf-8",
        )
        cls.scenarios = {case["claim_id"]: case for case in SCENARIOS}
        cls.api = cls.enterClassContext(TestClient(app))

    @staticmethod
    def seed():
        subprocess.run([sys.executable, "-m", "scripts.generate_data"], check=True,
                       cwd=Path(__file__).resolve().parents[1], stdout=subprocess.DEVNULL)

    def verify_seed(self):
        from sqlalchemy import func, select

        from claim_sense.db.repository import count_prior_claims
        from claim_sense.db.session import SessionLocal
        from claim_sense.models.models import Garage, Policy, PriorClaim

        with SessionLocal() as session:
            for model, expected in ((Policy, 25), (Garage, 13), (PriorClaim, 5)):
                self.assertEqual(session.scalar(select(func.count()).select_from(model)), expected)
        self.assertEqual(count_prior_claims("NS-MOT-250118", "2026-10-03"), 2)
        sample_dir = self.data_dir / "sample_claims"
        expected_ids = {f"CLM-{i}" for i in range(1001, 1021)}
        self.assertEqual({folder.name for folder in sample_dir.iterdir()}, expected_ids)
        self.assertEqual(len(list(sample_dir.glob("*/*.pdf"))), 44)
        with (self.data_dir / "eval_labels.csv").open(newline="") as labels:
            self.assertCountEqual([row["claim_id"] for row in csv.DictReader(labels)], expected_ids)

    def run_sample(self, claim_id):
        from claim_sense.integrations import llm

        with patch.object(llm, "client", return_value=model_stub(self.scenarios[claim_id])):
            response = self.api.post(f"/claims/sample/{claim_id}")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertIsNone(response.json()["error"])
        saved = self.api.get(f"/claims/{claim_id}")
        self.assertEqual(saved.status_code, 200, saved.text)
        return saved.json()

    def test_clm_1001_automatic_payout(self):
        claim = self.run_sample("CLM-1001")
        self.assertEqual(claim["route"], "AUTO_APPROVE")
        self.assertEqual(claim["status"], "APPROVED")
        self.assertEqual(claim["recommendation"], "APPROVE")
        assessment = claim["details"]["assessment"]
        self.assertEqual(assessment["gross_assessed"], 20700)
        self.assertEqual(assessment["total_depreciation"], 7650)
        self.assertEqual(assessment["deductible"], 1000)
        self.assertEqual(assessment["payable"], 12050)
        self.assertEqual(claim["final_decision"], "APPROVE")
        self.assertEqual(claim["final_amount"], 12050)
        self.assertTrue(claim["letter"])
        steps = [event["step"] for event in claim["audit_trail"]]
        self.assertNotIn("human_review", steps)
        self.assertEqual(steps.count("finalize"), 1)

    def test_clm_1016_supported_breakdown_exclusion(self):
        claim = self.run_sample("CLM-1016")
        self.assertEqual(claim["details"]["coverage"]["exclusion_clause_ids"], ["EXC-2"])
        self.assertEqual(claim["recommendation"], "REJECT")
        self.assertEqual(claim["status"], "PENDING_ADJUSTER")
        self.assertIsNone(claim["final_decision"])

    def test_clm_1019_siu_investigation(self):
        claim = self.run_sample("CLM-1019")
        self.assertEqual(claim["route"], "SIU_INVESTIGATION")
        self.assertEqual(claim["status"], "PENDING_SIU")
        self.assertEqual(claim["recommendation"], "APPROVE")
        self.assertEqual(claim["details"]["coverage"]["exclusion_clause_ids"], [])
        self.assertEqual(claim["details"]["coverage"]["confidence"], "low")
        signals = {s["code"] for s in claim["details"]["fraud"]["signals"]}
        self.assertTrue({"WATCHLIST_GARAGE", "SURVEYOR_DOUBTS_CAUSE", "DOCUMENT_INCONSISTENCY"} <= signals)
        self.assertGreaterEqual(claim["fraud_score"], 50)
        self.assertIsNone(claim["final_decision"])
        self.assertIsNone(claim["final_amount"])
        self.assertIsNone(claim["letter"])
        queue = self.api.get("/claims", params={"status": "PENDING_SIU"})
        self.assertEqual(queue.status_code, 200)
        self.assertIn(claim["claim_id"], [row["claim_id"] for row in queue.json()])

    def test_clm_1014_upload_and_rejection(self):
        from claim_sense.core import config
        from claim_sense.integrations import llm

        self.verify_seed()
        scenario = self.scenarios["CLM-1014"]
        fixture_dir = config.SAMPLE_CLAIMS_DIR / "CLM-1014"
        api = self.api
        pdfs = {
            field: (f"{field}.pdf", (fixture_dir / f"{field}.pdf").read_bytes(), "application/pdf")
            for field in ("claim_form", "survey_report", "fir")
        }

        with patch.object(llm, "client", return_value=model_stub(scenario)):
            upload = api.post("/claims", files=pdfs)
            self.assertEqual(upload.status_code, 200, upload.text)
            pending = upload.json()
            claim_id = pending["claim_id"]
            self.assertTrue(claim_id.startswith("CLM-U"))
            self.assertEqual(pending["route"], "ADJUSTER_REVIEW")
            self.assertEqual(pending["status"], "PENDING_ADJUSTER", pending.get("error"))
            self.assertEqual(pending["recommendation"], "REJECT")
            self.assertEqual(pending["details"]["coverage"]["exclusion_clause_ids"], ["EXC-4"])
            self.assertEqual(pending["details"]["retrieved_clauses"][0]["id"], "EXC-4")
            self.assertIsNone(pending["final_decision"])

            reviewer = "Demo Adjuster"
            review = api.post(f"/claims/{claim_id}/review", json={
                "decision": "REJECT", "approved_amount": 0, "reviewer": reviewer,
            })
            self.assertEqual(review.status_code, 200, review.text)
            saved = api.get(f"/claims/{claim_id}")
            self.assertEqual(saved.status_code, 200, saved.text)
            claim = saved.json()
            self.assertEqual(claim["status"], "REJECTED")
            self.assertEqual(claim["final_decision"], "REJECT")
            self.assertEqual(claim["final_amount"], 0)
            self.assertEqual(claim["reviewer"], reviewer)
            self.assertEqual(claim["letter"], f"Claim {claim_id} was rejected under EXC-4. Amount payable: Rs. 0.")
            actions = [
                (event["step"], event["actor"]) for event in claim["audit_trail"]
                if event["step"] in {"human_review", "finalize"}
            ]
            self.assertEqual(actions, [("human_review", reviewer), ("finalize", reviewer)])

            self.seed()
            self.verify_seed()
            self.assertEqual(api.get(f"/claims/{claim_id}").json(), claim)
            for field, (_, content, _) in pdfs.items():
                self.assertEqual((config.UPLOADS_DIR / claim_id / f"{field}.pdf").read_bytes(), content)

    def test_queued_upload_recovers_saved_interrupt_on_repeat_delivery(self):
        from sqlalchemy.exc import OperationalError

        from claim_sense.core import config
        from claim_sense.db import repository
        from claim_sense.integrations import documents, llm
        from claim_sense.services import service

        claim_id = service.new_upload_id()
        path = f"/claims/uploads/{claim_id}/complete"
        sample_dir = config.SAMPLE_CLAIMS_DIR / "CLM-1014"
        with (
            patch.object(config, "DOCUMENTS_BUCKET", "test-documents"),
            patch.object(config, "PROCESSING_QUEUE_URL", "https://example.com/processing"),
            patch.object(documents, "verify_upload", return_value=True),
            patch.object(documents, "upload_location", return_value=str(sample_dir)),
            patch.object(service.boto3, "client") as aws_client,
            patch.object(llm, "client", return_value=model_stub(self.scenarios["CLM-1014"])),
        ):
            first = self.api.post(path, json={"has_fir": True})
            self.assertEqual(first.status_code, 202, first.text)
            self.assertEqual(first.headers["Location"], f"/claims/{claim_id}")
            queued = first.json()
            self.assertEqual(queued["status"], "QUEUED")
            self.assertEqual(self.api.post(path, json={"has_fir": True}).json()["thread_id"], queued["thread_id"])
            self.assertEqual(aws_client.return_value.send_message.call_count, 2)

            with repository.lock_claim_processing(claim_id):
                with self.assertRaisesRegex(RuntimeError, "already has an active worker"):
                    service.process_queued_claim(claim_id)

            original_save = repository.save_claim

            def lose_database_before_publishing(claim_id, **fields):
                if fields.get("status") == "PENDING_ADJUSTER":
                    raise OperationalError(None, None, ConnectionError("database disconnected"))
                return original_save(claim_id, **fields)

            with patch.object(repository, "save_claim", side_effect=lose_database_before_publishing):
                with self.assertRaises(OperationalError):
                    service.process_queued_claim(claim_id)
            self.assertEqual(repository.get_claim(claim_id)["status"], "PROCESSING")

            service.process_queued_claim(claim_id)
            service.process_queued_claim(claim_id)
            service.process_queued_claim(claim_id, dead_letter=True)

            saved = self.api.get(f"/claims/{claim_id}").json()
            self.assertEqual(saved["status"], "PENDING_ADJUSTER", saved.get("error"))
            self.assertEqual(saved["thread_id"], queued["thread_id"])
            steps = [event["step"] for event in saved["audit_trail"]]
            self.assertEqual(steps.count("received"), 1)
            self.assertEqual(steps.count("route_claim"), 1)
            self.assertEqual(self.api.post(path, json={"has_fir": True}).status_code, 200)
            self.assertEqual(aws_client.return_value.send_message.call_count, 2)

            review = self.api.post(f"/claims/{claim_id}/review", json={
                "decision": "REJECT", "approved_amount": 0, "reviewer": "Demo Adjuster",
            })
            self.assertEqual(review.status_code, 200, review.text)
            self.assertEqual(review.json()["status"], "REJECTED")


def model_stub(scenario):
    from claim_sense.schemas.schemas import ConsistencyReport, CoverageDecision, SurveyCauseFinding

    extracted = extraction_responses(scenario)
    usage = SimpleNamespace(input_tokens=10, output_tokens=5)

    def parse(*, input, text_format, **kwargs):
        if text_format == CoverageDecision:
            prompt = input[0]["content"][0]["text"]
            facts = json.loads(prompt.split("\n\nFACTS:\n", 1)[1])
            assert facts["assessed_repair_items"] == [row[0] for row in scenario["survey"]["items"]]
            if scenario["claim_id"] == "CLM-1014":
                assert "[EXC-4]" in prompt
                assert '"driver_intoxicated": true' in prompt
                result = CoverageDecision(
                    covered=False, exclusion_clause_ids=["EXC-4"],
                    reasoning="The FIR records intoxication, which triggers EXC-4.", confidence="high",
                )
            elif scenario["claim_id"] == "CLM-1016":
                result = CoverageDecision(covered=False, exclusion_clause_ids=["EXC-2"],
                                          reasoning="Oil starvation caused the engine seizure.", confidence="high")
            elif scenario["claim_id"] == "CLM-1019":
                result = CoverageDecision(covered=False, exclusion_clause_ids=["EXC-2"],
                                          reasoning="The collision account is disputed.", confidence="low")
            else:
                result = CoverageDecision(covered=True, exclusion_clause_ids=[],
                                          reasoning="Accidental damage under OD-1.", confidence="high")
        elif text_format == SurveyCauseFinding:
            evidence = json.loads(input[0]["content"][0]["text"])
            assert evidence["surveyor_observations"] == scenario["survey"]["observations"]
            assert evidence["assessed_repair_items"] == [row[0] for row in scenario["survey"]["items"]]
            result = SurveyCauseFinding(finding=scenario["survey"]["observations"],
                                        wear_or_breakdown=scenario["claim_id"] == "CLM-1016")
        elif text_format == ConsistencyReport:
            contradictions = []
            if scenario["claim_id"] == "CLM-1019":
                contradictions.append(dict(topic="Impact location", claim_form_says="Rear impact",
                                           other_document_says="Front damage", severity="high"))
            result = ConsistencyReport(inconsistencies=contradictions)
        else:
            result = extracted[text_format]
            pdf = input[0]["content"][0]
            assert pdf["type"] == "input_file"
            assert pdf["file_data"].startswith("data:application/pdf;base64,")
        return SimpleNamespace(output_parsed=result, usage=usage)

    def embed(*, input, **kwargs):
        vectors = [
            [1.0, 0.0] if text.startswith("[EXC-4]") or "drunken driving" in text else [0.0, 1.0]
            for text in input
        ]
        return SimpleNamespace(
            data=[SimpleNamespace(embedding=vector) for vector in vectors],
            usage=SimpleNamespace(prompt_tokens=10),
        )

    def write(*, input, **kwargs):
        facts = json.loads(input)
        if scenario["claim_id"] in ("CLM-1001", "CLM-1008"):
            assert facts["decision"] == "APPROVE"
            amount = {"CLM-1001": 12050, "CLM-1008": 90000}[scenario["claim_id"]]
            assert facts["amount_payable_rupees"] == amount
            letter = f"Claim {facts['claim_id']} was approved for Rs. {amount:,}."
        else:
            assert facts["decision"] == "REJECT"
            assert facts["amount_payable_rupees"] == 0
            assert "EXC-4" in " ".join(facts["rejection_grounds"])
            letter = f"Claim {facts['claim_id']} was rejected under EXC-4. Amount payable: Rs. 0."
        return SimpleNamespace(output_text=letter, usage=usage)

    return SimpleNamespace(
        responses=SimpleNamespace(parse=parse, create=write),
        embeddings=SimpleNamespace(create=embed),
    )

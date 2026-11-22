"""Exercise CLM-1014 through FastAPI, PostgreSQL, and LangGraph."""

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
    return {
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
        FIRReport: FIRReport(
            fir_number=fir["fir_number"], incident_date=fir["date"], incident_time=fir["time"],
            location=fir["location"], summary=fir["summary"], driver_intoxicated=fir["intoxicated"],
            breath_alcohol_mg_per_100ml=float(fir["breath"].split()[0]),
        ),
    }


class ClaimReviewTest(unittest.TestCase):
    def test_clm_1014_upload_and_rejection(self):
        with TemporaryDirectory() as directory, disposable_postgres() as url:
            data_dir = Path(directory)
            with patch.dict("os.environ", {
                "DATABASE_URL": url, "CLAIMS_DATA_DIR": directory, "OPENAI_API_KEY": "",
            }):
                self.run_claim_review(url, data_dir)

    def seed_and_verify(self, data_dir):
        from sqlalchemy import func, select

        from claim_sense.db.repository import count_prior_claims
        from claim_sense.db.session import SessionLocal
        from claim_sense.models.models import Garage, Policy, PriorClaim

        subprocess.run([sys.executable, "-m", "scripts.generate_data"], check=True,
                       cwd=Path(__file__).resolve().parents[1], stdout=subprocess.DEVNULL)
        with SessionLocal() as session:
            for model, expected in ((Policy, 25), (Garage, 13), (PriorClaim, 5)):
                self.assertEqual(session.scalar(select(func.count()).select_from(model)), expected)
        self.assertEqual(count_prior_claims("NS-MOT-250118", "2026-10-03"), 2)
        sample_dir = data_dir / "sample_claims"
        expected_ids = {f"CLM-{i}" for i in range(1001, 1021)}
        self.assertEqual({folder.name for folder in sample_dir.iterdir()}, expected_ids)
        self.assertEqual(len(list(sample_dir.glob("*/*.pdf"))), 44)
        with (data_dir / "eval_labels.csv").open(newline="") as labels:
            self.assertCountEqual([row["claim_id"] for row in csv.DictReader(labels)], expected_ids)

    def run_claim_review(self, database_url, data_dir):
        from sqlalchemy import make_url

        from claim_sense.core import config
        from claim_sense.db.session import engine
        from claim_sense.integrations import llm
        from claim_sense.main import app
        from scripts import generate_data

        # Validate these before app startup, even if another test imported the app first.
        self.assertEqual(engine.url, make_url(database_url).set(drivername="postgresql+psycopg"))
        self.assertEqual(config.DATA_DIR, data_dir)
        scenario = next(case for case in generate_data.SCENARIOS if case["claim_id"] == "CLM-1014")
        self.seed_and_verify(data_dir)
        fixture_dir = config.SAMPLE_CLAIMS_DIR / "CLM-1014"
        config.POLICY_WORDING_PATH.write_text(
            "## [OD-1] Own damage cover\nAccidental damage is covered subject to exclusions.\n\n"
            "## [EXC-4] Driving under the influence\nDamage is excluded when the driver is intoxicated.\n",
            encoding="utf-8",
        )

        pdfs = {
            field: (f"{field}.pdf", (fixture_dir / f"{field}.pdf").read_bytes(), "application/pdf")
            for field in ("claim_form", "survey_report", "fir")
        }

        with patch.object(llm, "client", return_value=self.model_stub(scenario)), TestClient(app) as api:
            upload = api.post("/claims", files=pdfs)
            self.assertEqual(upload.status_code, 200, upload.text)
            pending = upload.json()
            claim_id = pending["claim_id"]
            self.assertTrue(claim_id.startswith("CLM-U"))
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

            self.seed_and_verify(data_dir)
            self.assertEqual(api.get(f"/claims/{claim_id}").json(), claim)
            for field, (_, content, _) in pdfs.items():
                self.assertEqual((config.UPLOADS_DIR / claim_id / f"{field}.pdf").read_bytes(), content)

    def model_stub(self, scenario):
        from claim_sense.schemas.schemas import ConsistencyReport, CoverageDecision

        extracted = extraction_responses(scenario)
        usage = SimpleNamespace(input_tokens=10, output_tokens=5)

        def parse(*, input, text_format, **kwargs):
            if text_format == CoverageDecision:
                prompt = input[0]["content"][0]["text"]
                self.assertIn("[EXC-4]", prompt)
                self.assertIn('"driver_intoxicated": true', prompt)
                result = CoverageDecision(
                    covered=False, exclusion_clause_ids=["EXC-4"],
                    reasoning="The FIR records intoxication, which triggers EXC-4.", confidence="high",
                )
            elif text_format == ConsistencyReport:
                result = ConsistencyReport(inconsistencies=[])
            else:
                result = extracted[text_format]
                pdf = input[0]["content"][0]
                self.assertEqual(pdf["type"], "input_file")
                self.assertTrue(pdf["file_data"].startswith("data:application/pdf;base64,"))
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
            self.assertEqual(facts["decision"], "REJECT")
            self.assertEqual(facts["amount_payable_rupees"], 0)
            self.assertIn("EXC-4", " ".join(facts["rejection_grounds"]))
            letter = f"Claim {facts['claim_id']} was rejected under EXC-4. Amount payable: Rs. 0."
            return SimpleNamespace(output_text=letter, usage=usage)

        return SimpleNamespace(
            responses=SimpleNamespace(parse=parse, create=write),
            embeddings=SimpleNamespace(create=embed),
        )

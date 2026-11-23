"""Exercise the Streamlit review form across two independent API processes."""

import multiprocessing
import os
import socket
import subprocess
import sys
import time
import unittest
from contextlib import contextmanager
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock, patch

import requests
from streamlit.testing.v1 import AppTest

from tests.test_claim_review import disposable_postgres, model_stub


ROOT = Path(__file__).resolve().parents[1]


def serve_api(listener):
    import uvicorn

    from claim_sense.integrations import llm
    from claim_sense.main import app
    from scripts.generate_data import SCENARIOS

    case = next(s for s in SCENARIOS if s["claim_id"] == "CLM-1008")
    with patch.object(llm, "client", return_value=model_stub(case)):
        server = uvicorn.Server(uvicorn.Config(app, log_level="warning"))
        server.run(sockets=[listener])


@contextmanager
def running_api(listener, url):
    process = multiprocessing.get_context("spawn").Process(target=serve_api, args=(listener,))
    process.start()
    try:
        deadline = time.monotonic() + 30
        while process.is_alive() and time.monotonic() < deadline:
            try:
                if requests.get(f"{url}/health", timeout=0.5).ok:
                    break
            except (requests.ConnectionError, requests.Timeout):
                pass
            time.sleep(0.1)
        else:
            raise RuntimeError("Fixture API failed to start")
        yield process.pid
    finally:
        process.terminate()
        process.join(timeout=10)
        if process.is_alive():
            process.kill()
            process.join()
        process.close()


class PortalRestartTest(unittest.TestCase):
    def test_dashboard_and_legacy_siu_detail(self):
        rows = []
        for claim_id, status, route in (
            ("auto", "APPROVED", "AUTO_APPROVE"), ("manual", "APPROVED", "ADJUSTER_REVIEW"),
            ("waiting", "PENDING_ADJUSTER", "ADJUSTER_REVIEW"), ("investigation", "PENDING_SIU", "SIU_INVESTIGATION"),
        ):
            rows.append(dict(claim_id=claim_id, status=status, route=route, recommendation="APPROVE",
                             payable_amount=2000, final_amount=None, updated_at="2026-11-23"))
        legacy_detail = dict(rows[-1], fraud_score=50, audit_trail=[], details={
            "retrieved_clauses": [{"id": "OD-1", "title": "Own damage", "score": 0.9}],
            "fraud": {"signals": [{"code": "WATCHLIST_GARAGE", "points": 25, "detail": "Garage on watchlist"}]},
        })

        def read_claims(method, url, **kwargs):
            self.assertEqual(method, "GET")
            payload = legacy_detail if url.endswith("/investigation") else rows
            return Mock(json=lambda: payload)

        with patch.object(requests, "request", side_effect=read_claims):
            portal = AppTest.from_file(str(ROOT / "ui/portal.py")).run()
            portal.radio(key="workspace").set_value("Manager dashboard").run()
            metrics = {m.label: m.value for m in portal.metric}
            self.assertEqual(metrics, {"Total claims": "4", "Pending adjuster": "1",
                                       "Pending SIU": "1", "Auto-approved share": "25.0%"})
            portal.selectbox(key="selected_claim").select("investigation").run()
            self.assertFalse(portal.exception)
            self.assertIn("OD-1 · Own damage", [t.value for t in portal.text])
            signals = next(d.value for d in portal.dataframe if "Signal" in d.value.columns)
            self.assertEqual(signals.iloc[0]["Evidence"], "Garage on watchlist")
            self.assertFalse(any(b.label == "Submit review" for b in portal.button))

    def test_review_survives_api_restart(self):
        with TemporaryDirectory() as directory, disposable_postgres() as db_url, socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            url = f"http://127.0.0.1:{listener.getsockname()[1]}"
            with patch.dict(os.environ, {"DATABASE_URL": db_url, "CLAIMS_DATA_DIR": directory,
                                         "OPENAI_API_KEY": "", "API_URL": url}):
                subprocess.run([sys.executable, "-m", "scripts.generate_data"], cwd=ROOT,
                               stdout=subprocess.DEVNULL, check=True)
                (Path(directory) / "policy_wording.md").write_text(
                    "## [OD-1] Own damage\nAccidental damage is covered.\n", encoding="utf-8"
                )
                self.check_review(listener, url)

    def check_review(self, listener, url):
        case_url = f"{url}/claims/CLM-1008"
        portal = AppTest.from_file(str(ROOT / "ui/portal.py"), default_timeout=20)
        with running_api(listener, url) as first_pid:
            portal.run()
            portal.radio(key="workspace").set_value("Submit claim").run()
            portal.radio[1].set_value("Run a sample").run()
            portal.selectbox[0].select("CLM-1008").run()
            next(b for b in portal.button if b.label == "Run sample").click().run()
            self.assertFalse(portal.exception)
            pending = requests.get(case_url, timeout=5).json()
            self.assertEqual(pending["status"], "PENDING_ADJUSTER")
            self.assertEqual(pending["payable_amount"], 90000)
            self.assertTrue(pending["thread_id"])

        with running_api(listener, url) as second_pid:
            self.assertNotEqual(first_pid, second_pid)
            self.assertEqual(requests.get(case_url, timeout=5).json(), pending)
            portal.radio(key="workspace").set_value("Manager dashboard").run()
            metrics = {m.label: m.value for m in portal.metric}
            self.assertEqual(metrics["Pending adjuster"], "1")
            self.assertEqual(metrics["Auto-approved share"], "0.0%")
            portal.radio(key="workspace").set_value("Claims").run()
            portal.selectbox(key="queue").select("Adjuster review").run()
            portal.selectbox(key="selected_claim").select("CLM-1008").run()
            self.assertFalse(portal.exception)
            self.assertIn("Accidental damage is covered.", [t.value for t in portal.text])
            lines = next(d.value for d in portal.dataframe if "Survey item" in d.value.columns)
            self.assertEqual(set(lines["Clause"]), {"ADD-ZD", "OD-2"})
            self.assertEqual(lines["Net (₹)"].sum(), 92000)
            self.assertIn("No fraud signals recorded.", [t.value for t in portal.text])
            portal.radio(key="decision_CLM-1008").set_value("REJECT").run()
            portal.text_input[0].set_value("Recovery adjuster")
            next(b for b in portal.button if b.label == "Submit review").click().run()
            self.assertTrue(any("notes" in e.value.lower() for e in portal.error))
            response = requests.post(f"{case_url}/review", json={
                "decision": "REJECT", "approved_amount": 0, "reviewer": "Recovery adjuster", "notes": " ",
            }, timeout=5)
            self.assertEqual(response.status_code, 400)
            self.assertEqual(requests.get(case_url, timeout=5).json(), pending)

            portal.radio(key="decision_CLM-1008").set_value("APPROVE").run()
            portal.text_input[0].set_value("Recovery adjuster")
            next(b for b in portal.button if b.label == "Submit review").click().run()
            self.assertFalse(portal.exception)
            saved = requests.get(case_url, timeout=5).json()
            self.assertEqual(saved["claim_id"], pending["claim_id"])
            self.assertEqual(saved["thread_id"], pending["thread_id"])
            self.assertEqual(saved["status"], "APPROVED")
            self.assertEqual(saved["final_amount"], 90000)
            self.assertEqual(saved["reviewer"], "Recovery adjuster")
            self.assertIn("90,000", saved["letter"])
            self.assertIn(saved["letter"], [t.value for t in portal.text])
            actions = [event["step"] for event in saved["audit_trail"]]
            self.assertEqual(actions.count("human_review"), 1)
            self.assertEqual(actions.count("finalize"), 1)
            portal.radio(key="workspace").set_value("Manager dashboard").run()
            self.assertEqual(next(m.value for m in portal.metric if m.label == "Pending adjuster"), "0")

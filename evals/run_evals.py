"""Score the labelled sample claims in a dedicated demo database."""

import argparse
import csv
from datetime import datetime
from pathlib import Path
from statistics import mean
from time import perf_counter

from claim_sense.core import config
from claim_sense.db.session import init_db
from claim_sense.schemas.schemas import ReviewDecision
from claim_sense.services.policy_rag import get_index
from claim_sense.services.service import process_claim, review_claim, sample_claim_dir
from claim_sense.workflows.graph import init_checkpoints


def pct(numerator: int, denominator: int) -> str:
    return f"{numerator / denominator:.1%} ({numerator}/{denominator})" if denominator else "n/a"


def evaluate_claim(label: dict, resume: bool) -> dict:
    claim_id = label["claim_id"]
    start = perf_counter()
    claim = process_claim(claim_id, sample_claim_dir(claim_id))
    seconds = perf_counter() - start
    details = claim["details"]
    expected = float(label["expected_payable"]) if label["expected_payable"] else None
    payable = claim["payable_amount"]
    cost = details.get("cost_inr")
    row = {
        "claim_id": claim_id, "expected_route": label["expected_route"], "got_route": claim["route"],
        "expected_recommendation": label["expected_recommendation"], "got_recommendation": claim["recommendation"],
        "expected_payable": expected, "got_payable": payable,
        "payable_ok": None if expected is None else payable is not None and abs(payable - expected) <= 1,
        "is_fraud": label["is_fraud"] == "True", "flagged_siu": claim["route"] == "SIU_INVESTIGATION",
        "fraud_score": claim["fraud_score"], "initial_status": claim["status"],
        "routing_reasons": " | ".join(details.get("reasons", [])),
        "coverage_reasoning": (details.get("coverage") or {}).get("reasoning"),
        "fraud_signals": " | ".join(s["code"] for s in (details.get("fraud") or {}).get("signals", [])),
        "cost_inr": cost, "seconds": round(seconds, 4), "resume_cost_inr": None, "resume_seconds": None,
        "final_status": claim["status"], "total_cost_inr": None, "total_seconds": None,
        "under_2_inr": None, "under_30_seconds": None, "error": claim["error"] or "",
    }
    total_seconds = seconds
    if resume and claim["status"] in ("PENDING_ADJUSTER", "PENDING_SIU"):
        # Follow the saved recommendation so the labels never influence the measured workflow.
        decision = ReviewDecision(reviewer="Simulated eval reviewer", decision=claim["recommendation"],
                                  approved_amount=(payable or 0) if claim["recommendation"] == "APPROVE" else 0)
        start = perf_counter()
        try:
            claim = review_claim(claim_id, decision)
        except Exception as exc:
            row["resume_seconds"] = round(perf_counter() - start, 4)
            row["error"] = f"Resume failed: {type(exc).__name__}"
            return row
        elapsed = perf_counter() - start
        total_seconds += elapsed
        row["resume_seconds"] = round(elapsed, 4)
        row["resume_cost_inr"] = round(claim["details"]["cost_inr"] - cost, 4)
        row["final_status"] = claim["status"]
    if claim["status"] in ("APPROVED", "REJECTED"):
        total_cost = claim["details"]["cost_inr"]
        row.update(total_cost_inr=total_cost, total_seconds=round(total_seconds, 4),
                   under_2_inr=total_cost < 2, under_30_seconds=total_seconds < 30)
    return row


def report(rows: list[dict]) -> None:
    n = len(rows)
    true_pos = sum(r["flagged_siu"] and r["is_fraud"] for r in rows)
    payable = [r for r in rows if r["payable_ok"] is not None]
    resumed = [r for r in rows if r["resume_seconds"] is not None and not r["error"]]
    finished = [r for r in rows if r["total_seconds"] is not None]
    print(f"Route accuracy: {pct(sum(r['got_route'] == r['expected_route'] for r in rows), n)}")
    print(f"Recommendation accuracy: {pct(sum(r['got_recommendation'] == r['expected_recommendation'] for r in rows), n)}")
    print(f"SIU precision: {pct(true_pos, sum(r['flagged_siu'] for r in rows))}")
    print(f"SIU recall: {pct(true_pos, sum(r['is_fraud'] for r in rows))}")
    print(f"Payable within Rs.1: {pct(sum(r['payable_ok'] for r in payable), len(payable))}")
    print(f"Straight-through: {pct(sum(r['got_route'] == 'AUTO_APPROVE' and r['initial_status'] == 'APPROVED' for r in rows), n)}")
    print(f"Errors: {sum(bool(r['error']) for r in rows)}")
    known_costs = [r["cost_inr"] for r in rows if r["cost_inr"] is not None]
    if known_costs:
        print(f"Average submission cost: Rs.{mean(known_costs):.4f} ({len(known_costs)}/{n} recorded)")
    print(f"Average submission time: {mean(r['seconds'] for r in rows):.2f}s")
    if resumed:
        print(f"Average resume time: {mean(r['resume_seconds'] for r in resumed):.2f}s ({len(resumed)} simulated reviews)")
    if finished:
        print(f"Reached final decision: {len(finished)}/{n}")
        print(f"Average processing through final decision: Rs.{mean(r['total_cost_inr'] for r in finished):.4f}, "
              f"{mean(r['total_seconds'] for r in finished):.2f}s")
        print(f"Finalized under Rs.2: {pct(sum(r['under_2_inr'] for r in finished), n)}")
        print(f"Finalized under 30s: {pct(sum(r['under_30_seconds'] for r in finished), n)}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--resume", action="store_true", help="Simulate an immediate decision on each pending review")
    parser.add_argument("--output", type=Path, help="New per-claim CSV (default: timestamped file under evals/)")
    args = parser.parse_args()
    for model in (config.OPENAI_MODEL, config.EMBEDDING_MODEL):
        if model not in config.PRICES_USD_PER_1M:
            parser.error(f"Add verified token prices for {model} before measuring cost")
    with config.EVAL_LABELS_PATH.open(encoding="utf-8", newline="") as fh:
        labels = list(csv.DictReader(fh))
    if not labels or len({r["claim_id"] for r in labels}) != len(labels):
        parser.error("Evaluation labels must contain at least one claim and no duplicate claim IDs")
    output = args.output or Path(__file__).with_name(f"results_{datetime.now():%Y%m%d_%H%M%S}.csv")
    output.parent.mkdir(parents=True, exist_ok=True)
    init_db()
    init_checkpoints()
    with output.open("x", encoding="utf-8", newline="") as fh:
        get_index()  # Shared corpus setup, outside per-claim latency and cost.
        print(f"Model {config.OPENAI_MODEL}; embeddings {config.EMBEDDING_MODEL}; INR per USD {config.USD_TO_INR}")
        rows = []
        for label in labels:
            row = evaluate_claim(label, args.resume)
            if not rows:
                writer = csv.DictWriter(fh, fieldnames=row)
                writer.writeheader()
            writer.writerow(row)
            fh.flush()
            rows.append(row)
            print(f"{row['claim_id']}  {row['got_route']}  {row['got_recommendation']}  "
                  f"{row['seconds']:.2f}s to route  {row['error']}", flush=True)
    report(rows)
    print(f"Per-claim results: {output}")


if __name__ == "__main__":
    main()

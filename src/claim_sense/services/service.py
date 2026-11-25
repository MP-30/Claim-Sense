import uuid
from pathlib import Path

from langgraph.types import Command

from claim_sense.core import config
from claim_sense.db import repository as db
from claim_sense.integrations import documents
from claim_sense.schemas.schemas import ReviewDecision
from claim_sense.workflows.graph import get_app


def describe(node: str, update: dict) -> str:
    """One readable line per graph step (used by the CLI to show the agent 'thinking')."""
    if node == "__interrupt__":
        payload = update[0].value
        return f"PAUSED - waiting for {payload['queue']} decision (AI recommends {payload['ai_recommendation']})"
    if node == "intake":
        docs = [k for k, v in update["extracted"].items() if v]
        form = update["extracted"]["claim_form"]
        return f"read {len(docs)} documents {docs} | policy {form['policy_number']} | accident {form['loss_date']}"
    if node == "policy_check":
        issues = update["policy_issues"]
        return "policy OK" if not issues else "ISSUES: " + "; ".join(i["code"] for i in issues)
    if node == "coverage_check":
        c = update["coverage"]
        clauses = [x["id"] for x in update["retrieved_clauses"]]
        return (f"covered={c['covered']} exclusions={c['exclusion_clause_ids']} confidence={c['confidence']} "
                f"| retrieved {clauses}")
    if node == "assess_amount":
        a = update["assessment"]
        return (f"gross Rs.{a['gross_assessed']:,.0f} - depreciation Rs.{a['total_depreciation']:,.0f} "
                f"- deductible Rs.{a['deductible']:,} = payable Rs.{a['payable']:,}"
                + (" | TOTAL LOSS" if a["total_loss"] else ""))
    if node == "fraud_check":
        f = update["fraud"]
        return f"fraud score {f['score']} | signals {[s['code'] for s in f['signals']] or 'none'}"
    if node == "route_claim":
        return f"{update['route']} (AI recommends {update['recommendation']})"
    if node == "human_review":
        h = update["human_decision"]
        return f"{h['reviewer']} decided {h['decision']} Rs.{h['approved_amount']:,.0f}"
    if node == "finalize":
        return f"FINAL {update['final_decision']} Rs.{update['final_amount']:,.0f} | letter written"
    return ""


def process_claim(claim_id: str, docs_dir: str | Path, verbose: bool = False, *, new_upload: bool = False) -> dict:
    """Run a claim through the graph until it finishes (auto-approve) or pauses (human review)."""
    thread_id = f"{claim_id}-{uuid.uuid4().hex[:8]}"     # one LangGraph thread per run
    fields = {"docs_dir": str(docs_dir), "thread_id": thread_id, "status": "PROCESSING"}
    if new_upload:
        if not db.create_claim(claim_id, **fields):
            return db.get_claim(claim_id)
    else:
        db.reset_claim(claim_id)  # Reprocessing an existing sample starts a clean run.
        db.save_claim(claim_id, **fields)
    db.add_audit(claim_id, "received", "agent", {"docs_dir": str(docs_dir)})

    graph_config = {"configurable": {"thread_id": thread_id}}
    try:
        with get_app() as graph:
            state = {"claim_id": claim_id, "docs_dir": str(docs_dir), "llm_usage": []}
            for step in graph.stream(state, graph_config, stream_mode="updates", durability="sync"):
                if verbose:
                    for node, update in step.items():
                        print(f"  [{node:<14}] {describe(node, update)}")
    except Exception as error:   # any failure -> claim goes to FAILED with the reason, never silently lost
        db.save_claim(claim_id, status="FAILED", error=f"{type(error).__name__}: {error}")
        db.add_audit(claim_id, "error", "agent", {"error": str(error)})
        if verbose:
            print(f"  [error         ] {type(error).__name__}: {error}")
    return db.get_claim(claim_id)


def review_claim(claim_id: str, review: ReviewDecision, verbose: bool = False) -> dict:
    """Resume a paused claim with the adjuster's / SIU officer's decision."""
    claim = db.get_claim(claim_id)
    if claim is None:
        raise ValueError(f"Claim {claim_id} not found")
    if claim["status"] not in ("PENDING_ADJUSTER", "PENDING_SIU"):
        raise ValueError(f"Claim {claim_id} is {claim['status']}, not waiting for review")

    idv = (claim["details"].get("policy") or {}).get("idv")
    if review.decision == "APPROVE" and idv and review.approved_amount > idv:
        raise ValueError(f"Approved amount cannot exceed the IDV of Rs.{idv:,.0f}")
    if review.decision != claim["recommendation"] and not review.notes.strip():
        raise ValueError("Notes are required when overriding the AI recommendation")

    graph_config = {"configurable": {"thread_id": claim["thread_id"]}}
    with get_app() as graph:
        command = Command(resume=review.model_dump())
        for step in graph.stream(command, graph_config, stream_mode="updates", durability="sync"):
            if verbose:
                for node, update in step.items():
                    print(f"  [{node:<14}] {describe(node, update)}")
    return db.get_claim(claim_id)


def new_upload_id() -> str:
    return f"CLM-U{uuid.uuid4().hex.upper()}"


def save_upload(files: dict[str, bytes]) -> tuple[str, str | Path]:
    claim_id = new_upload_id()
    if config.DOCUMENTS_BUCKET:
        for name, content in files.items():
            documents.store_pdf(claim_id, name, content)
        return claim_id, documents.upload_location(claim_id)

    folder = config.UPLOADS_DIR / claim_id
    folder.mkdir(parents=True, exist_ok=True)
    for name, content in files.items():
        (folder / f"{name}.pdf").write_bytes(content)
    return claim_id, folder


def sample_claim_dir(claim_id: str) -> Path:
    folder = config.SAMPLE_CLAIMS_DIR / claim_id
    if not folder.exists():
        raise ValueError(f"No sample claim {claim_id}")
    return folder

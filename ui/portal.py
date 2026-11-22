"""Local Streamlit portal for the ClaimSense API."""

import os
from urllib.parse import quote

import requests
import streamlit as st


API_URL = os.getenv("API_URL", "http://127.0.0.1:8000").rstrip("/")
REVIEW_QUEUES = {
    "PENDING_ADJUSTER": "Adjuster review",
    "PENDING_SIU": "SIU investigation",
}
QUEUES = {"All claims": None, **{label: status for status, label in REVIEW_QUEUES.items()}}


def api_request(method: str, path: str, **kwargs):
    # Claim processing and letter drafting run within the POST request.
    timeout = (3, 5 if method == "GET" else 300)
    try:
        response = requests.request(method, f"{API_URL}{path}", timeout=timeout, **kwargs)
        response.raise_for_status()
        return response.json()
    except requests.HTTPError as error:
        detail = None
        try:
            detail = error.response.json().get("detail")
        except ValueError:
            pass
        if isinstance(detail, list):
            detail = " ".join(item["msg"] for item in detail)
        st.error(detail or f"The ClaimSense API returned HTTP {error.response.status_code}.")
    except requests.Timeout:
        if method == "POST":
            st.warning("The request timed out. Check the claims list before submitting again.")
        else:
            st.error("The ClaimSense API took too long to respond.")
    except requests.RequestException:
        st.error("Could not reach the ClaimSense API. Check that it is running.")
    return None


def submit(path: str, progress: str, **kwargs) -> None:
    with st.spinner(progress):
        claim = api_request("POST", path, **kwargs)
    if claim is not None:
        st.session_state["claim_result"] = (claim["claim_id"], claim["status"])
        st.rerun()


def upload_claim() -> None:
    with st.form("upload_claim"):
        claim_form = st.file_uploader("Claim form", type="pdf")
        survey_report = st.file_uploader("Surveyor report", type="pdf")
        fir = st.file_uploader("Police FIR (optional)", type="pdf")
        submitted = st.form_submit_button("Process claim", type="primary")

    if not submitted:
        return
    if claim_form is None or survey_report is None:
        st.error("Upload both a claim form and a surveyor report.")
        return
    uploads = {"claim_form": claim_form, "survey_report": survey_report, "fir": fir}
    files = {
        field: (upload.name, upload.getvalue(), "application/pdf")
        for field, upload in uploads.items()
        if upload is not None
    }
    submit("/claims", "Processing claim documents…", files=files)


def run_sample() -> None:
    sample_ids = api_request("GET", "/claims/samples")
    if sample_ids is None:
        return
    if not sample_ids:
        st.info("No sample claims found. Add sample PDFs under data/sample_claims.")
        return

    sample_id = st.selectbox("Sample claim", sample_ids, index=None)
    st.caption("Running the same sample again replaces its previous run and audit history.")
    if st.button("Run sample", type="primary", disabled=sample_id is None):
        submit(f"/claims/sample/{quote(sample_id, safe='')}", "Processing sample claim…")


def review_claim(claim: dict) -> None:
    claim_id = claim["claim_id"]
    recommendation = claim["recommendation"]
    st.subheader(REVIEW_QUEUES[claim["status"]])
    decision = st.radio(
        "Decision",
        ("APPROVE", "REJECT"),
        index=int(recommendation == "REJECT"),
        format_func=str.title,
        horizontal=True,
        key=f"decision_{claim_id}",
    )
    with st.form(f"review_{claim_id}"):
        reviewer = st.text_input("Reviewer name")
        amount = 0.0
        if decision == "APPROVE":
            amount = st.number_input(
                "Approved payout (₹)",
                min_value=0.0,
                value=float(claim["payable_amount"] or 0),
                step=0.01,
            )
        notes = st.text_area("Notes for the customer", help="Required when overriding the AI recommendation.")
        submitted = st.form_submit_button("Submit review", type="primary")
    if not submitted:
        return

    reviewer = reviewer.strip()
    notes = notes.strip()
    if len(reviewer) < 2:
        st.error("Enter a reviewer name of at least two characters.")
        return
    if decision != recommendation and not notes:
        st.error("Add notes explaining why you are overriding the AI recommendation.")
        return
    submit(
        f"/claims/{quote(claim_id, safe='')}/review",
        "Saving the decision and drafting the letter…",
        json={"decision": decision, "approved_amount": amount, "reviewer": reviewer, "notes": notes},
    )


def format_payout(amount: float | None) -> str:
    return f"₹{amount:,.2f}" if amount is not None else "—"


def show_claim(claim_id: str) -> None:
    claim = api_request("GET", f"/claims/{quote(claim_id, safe='')}")
    if claim is None:
        return

    st.subheader(claim_id)
    st.write(f"Status: **{REVIEW_QUEUES.get(claim['status'], claim['status'].title())}**")
    if claim.get("policy_number"):
        st.text(f"Policy {claim['policy_number']}")
    recommendation, assessed, final, fraud = st.columns(4)
    recommendation.metric("AI recommendation", claim["recommendation"] or "—")
    assessed.metric("Assessed payout", format_payout(claim["payable_amount"]))
    final.metric("Final payout", format_payout(claim["final_amount"]))
    fraud.metric("Fraud score", claim["fraud_score"] if claim["fraud_score"] is not None else "—")
    if claim.get("final_decision"):
        st.text(f"Final decision: {claim['final_decision'].title()} · {claim['reviewer'] or '—'}")
    if claim.get("error"):
        st.error(claim["error"])

    reasons = claim["details"].get("reasons")
    if reasons:
        st.subheader("Routing reasons")
        for reason in reasons:
            st.write(reason)

    if claim["status"] in REVIEW_QUEUES:
        review_claim(claim)
    if claim.get("letter"):
        st.subheader("Decision letter")
        st.text(claim["letter"])
    with st.expander("Audit trail"):
        st.json(claim["audit_trail"], expanded=False)


def show_queue() -> None:
    queue = st.selectbox("Queue", tuple(QUEUES), key="queue")
    st.button("Refresh claims")
    status = QUEUES[queue]
    claims = api_request("GET", "/claims", params={"status": status} if status else None)
    if claims is None:
        return

    if not claims:
        st.info("No claims in this queue." if status else "No claims yet. Open Submit claim to get started.")
        return

    st.dataframe(
        [
            {
                "Claim ID": claim["claim_id"],
                "Status": claim["status"],
                "Recommendation": claim["recommendation"],
                "Assessed payout (₹)": claim["payable_amount"],
                "Final payout (₹)": claim["final_amount"],
                "Updated": claim["updated_at"],
            }
            for claim in claims
        ],
        hide_index=True,
    )
    claim_ids = [claim["claim_id"] for claim in claims]
    if st.session_state.get("selected_claim") not in claim_ids:
        st.session_state.pop("selected_claim", None)
    claim_id = st.selectbox("Open a claim", claim_ids, index=None, key="selected_claim")
    if claim_id is not None:
        show_claim(claim_id)


def main() -> None:
    st.set_page_config(page_title="ClaimSense", layout="wide")
    st.title("ClaimSense")
    result = st.session_state.pop("claim_result", None)
    if result is not None:
        claim_id, status = result
        st.session_state["workspace"] = "Claims"
        st.session_state["queue"] = "All claims"
        st.session_state["selected_claim"] = claim_id
        message = f"{claim_id} · {REVIEW_QUEUES.get(status, status.title())}"
        if status == "FAILED":
            st.error(message)
        else:
            st.success(message)

    workspace = st.radio("Workspace", ("Claims", "Submit claim"), horizontal=True, key="workspace")
    if workspace == "Claims":
        show_queue()
        return

    source = st.radio("Claim source", ("Upload PDFs", "Run a sample"), horizontal=True)
    if source == "Upload PDFs":
        upload_claim()
    else:
        run_sample()


if __name__ == "__main__":
    main()

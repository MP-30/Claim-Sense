"""Local Streamlit view of claims stored by the API."""

import os

import requests
import streamlit as st


API_URL = os.getenv("API_URL", "http://127.0.0.1:8000").rstrip("/")


def get_json(path: str):
    response = requests.get(f"{API_URL}{path}", timeout=5)
    response.raise_for_status()
    return response.json()


def main() -> None:
    st.set_page_config(page_title="ClaimSense")
    st.title("ClaimSense")
    st.header("Motor insurance claims")
    st.button("Refresh claims")

    try:
        claims = get_json("/claims")
    except requests.RequestException:
        st.error("Could not load claims. Check that the ClaimSense API is running.")
        return

    if not claims:
        st.info("No claims yet. Claims will appear here after they are submitted to the API.")
        return

    st.dataframe(
        [
            {
                "Claim ID": claim["claim_id"],
                "Status": claim["status"],
                "Recommendation": claim["recommendation"],
                "Payable (₹)": claim["payable_amount"],
                "Updated": claim["updated_at"],
            }
            for claim in claims
        ],
        hide_index=True,
    )
    claim_id = st.selectbox("Open a claim", [claim["claim_id"] for claim in claims], index=None)
    if claim_id is None:
        return

    try:
        claim = get_json(f"/claims/{claim_id}")
    except requests.RequestException:
        st.error("Could not load this claim. Refresh the list and try again.")
        return

    if claim.get("error"):
        st.error(claim["error"])
    if claim.get("letter"):
        st.subheader("Decision letter")
        st.text(claim["letter"])
    st.subheader("Audit trail")
    st.json(claim["audit_trail"], expanded=False)


if __name__ == "__main__":
    main()

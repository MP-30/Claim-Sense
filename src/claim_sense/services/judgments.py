import json

from claim_sense.integrations import llm
from claim_sense.services import policy_rag

from claim_sense.schemas.schemas import ConsistencyReport, CoverageDecision, SurveyCauseFinding



# 1. Coverage check

COVERAGE_INSTRUCTIONS = """
You are a senior motor claims officer at an Indian insurance company.
Decide whether the loss described in the FACTS is covered by the POLICY CLAUSES provided.
Rules:
- Use ONLY the clauses provided. Cite clause IDs exactly as written, e.g. EXC-4.
- covered=false only if a specific exclusion or limitation clearly applies to the facts.
  Identify the fact that triggers each cited clause for this loss or for the vehicle's use.
  Evaluate the damage in the assessed repair items. Old damage mentioned elsewhere in the survey
  is a basis for exclusion only when the exclusion applies to the loss being assessed.
  Apply a clause in order: first establish its excluded cause using affirmative evidence, then
  consider any exception to that exclusion. For EXC-2, identify wear, depreciation, or a
  mechanical/electrical failure causing the assessed loss before considering the accident proviso.
  Uncertainty about which accident caused impact damage supplies no evidence of breakdown or wear.
  Conflicting accounts of an impact belong in the consistency check. If the documents indicate
  accidental impact and no specific exclusion applies, return covered=true with an empty clause list.
- Do NOT decide on late reporting, fraud suspicion, policy dates or licence expiry.
  Other checks handle those. Judge only the nature of the loss and how the car was being used.
- confidence=high when the facts clearly point one way; medium or low when facts are ambiguous or missing.
"""


def check_coverage(extracted: dict) -> tuple[dict, list[dict], list[dict]]:
    """Returns (coverage_decision, retrieved_clauses, usages)."""
    form, survey, fir = extracted["claim_form"], extracted["survey"], extracted["fir"]

    # The facts that decide coverage: what happened, how the car was used, what the surveyor and police saw.
    facts = {
        "how_accident_happened": form["accident_description"],
        "use_of_vehicle_at_time": form["vehicle_use_at_time"],
        "claimed_damage": form["damaged_areas"],
        "assessed_repair_items": [item["description"] for item in survey["items"]],
        "surveyor_observations": survey["observations"],
        "police_report": None if fir is None else {
            "summary": fir["summary"], "driver_intoxicated": fir["driver_intoxicated"],
            "breath_alcohol_mg_per_100ml": fir["breath_alcohol_mg_per_100ml"]},
    }
    query = (f"{form['accident_description']} Vehicle use: {form['vehicle_use_at_time']}. "
             f"Surveyor: {survey['observations']} " + (f"Police: {fir['summary']}" if fir else ""))
    clauses, embed_usage = policy_rag.retrieve(query)

    clause_text = "\n\n".join(f"[{c['id']}] {c['title']}\n{c['text']}" for c in clauses)
    decision, usage = llm.parse(
        step="coverage:decide",
        instructions=COVERAGE_INSTRUCTIONS,
        content=[llm.text_part(f"POLICY CLAUSES:\n{clause_text}\n\nFACTS:\n{json.dumps(facts, indent=2)}")],
        schema=CoverageDecision,
    )
    coverage = decision.model_dump()

    # Guardrail: the model may only cite clauses we actually gave it (no invented clause IDs).
    retrieved_ids = {c["id"] for c in clauses}
    invented = [cid for cid in coverage["exclusion_clause_ids"] if cid not in retrieved_ids]
    coverage["exclusion_clause_ids"] = [cid for cid in coverage["exclusion_clause_ids"] if cid in retrieved_ids]
    if invented or (not coverage["covered"] and not coverage["exclusion_clause_ids"]):
        coverage["confidence"] = "low"   # a rejection without a valid clause must go to a human
        coverage["reasoning"] += f" [Guardrail: unsupported clause citation {invented or 'missing'}]"

    usages = [embed_usage, usage]
    if "EXC-2" in coverage["exclusion_clause_ids"]:
        # Verify the cause independently of the claimant's account and the first model judgment.
        finding, cause_usage = llm.parse(
            step="coverage:verify_cause",
            instructions=(
                "Read the surveyor's findings and identify what caused the assessed repairs. "
                "Record whether the survey positively attributes any of these repairs to wear, "
                "depreciation, or mechanical/electrical failure. Impact damage and unrelated old "
                "scratches do not establish such a cause. Give the physical finding in one sentence."
            ),
            content=[llm.text_part(json.dumps({
                "surveyor_observations": survey["observations"],
                "assessed_repair_items": facts["assessed_repair_items"],
            }))],
            schema=SurveyCauseFinding,
        )
        usages.append(cause_usage)
        if not finding.wear_or_breakdown:
            remaining = [cid for cid in coverage["exclusion_clause_ids"] if cid != "EXC-2"]
            coverage.update(
                covered=not remaining,
                exclusion_clause_ids=remaining,
                confidence="low",
                reasoning=(f"Survey finding: {finding.finding} EXC-2 is unsupported for the assessed repairs. "
                           + (f"Other cited exclusions: {', '.join(remaining)}." if remaining
                              else "No supported exclusion remains. Human review is required.")),
            )
    return coverage, clauses, usages



# 2. Consistency check (feeds the fraud score)

CONSISTENCY_INSTRUCTIONS = """
You are a motor insurance fraud analyst.
Compare what the CLAIMANT says with the INDEPENDENT documents (surveyor report and police FIR, if any).
List contradictions about WHAT happened and WHERE the car was damaged: cause of damage, damaged areas,
sequence of events, vehicles involved.
Ignore:
- dates, times and amounts (they are checked by code),
- different words for the same part (e.g. "tail lamps" vs "rear lights"),
- small extra parts found next to the claimed damage (e.g. a bracket behind a damaged bumper).
Severity: high = contradicts the core story (e.g. front damage found but a rear impact was claimed);
medium = significant unexplained difference; low = minor.
Return an empty list if the documents agree.
"""


def check_consistency(extracted: dict) -> tuple[list[dict], dict]:
    form, survey, fir = extracted["claim_form"], extracted["survey"], extracted["fir"]
    payload = {
        "CLAIMANT (claim form)": {"story": form["accident_description"], "damaged_areas": form["damaged_areas"]},
        "SURVEYOR (independent inspection)": {"damaged_areas": survey["damaged_areas"],
                                              "observations": survey["observations"]},
        "POLICE (FIR)": None if fir is None else {"summary": fir["summary"], "location": fir["location"]},
    }
    report, usage = llm.parse(
        step="fraud:consistency",
        instructions=CONSISTENCY_INSTRUCTIONS,
        content=[llm.text_part(json.dumps(payload, indent=2))],
        schema=ConsistencyReport,
    )
    return [item.model_dump() for item in report.inconsistencies], usage



# 3. Customer letter

LETTER_INSTRUCTIONS = """
Write a short, polite claim decision letter from "Claims Team, NorthStar General Insurance" to the policyholder.
Use ONLY the facts given. Use the exact rupee amounts given; do not calculate anything yourself.
If APPROVED: state the amount and a one-line breakdown (assessed repair cost, depreciation, deductible),
and say the claims team will coordinate payment with the garage.
If REJECTED: explain the rejection_grounds in plain language and mention the policy clause IDs given.
If a note_from_claims_officer is given, include its substance.
Tell the customer they can reply to this letter with additional information for a review.
Under 170 words. Plain text, no markdown.
"""


def write_letter(state: dict, decision: str, amount: float) -> tuple[str, dict]:
    form = state["extracted"]["claim_form"]
    assessment = state.get("assessment") or {}

    # Only customer-facing grounds go into the letter. Internal signals (fraud score, routing
    # thresholds) are never shared with the customer.
    grounds = []
    if decision == "REJECT":
        grounds = [i["detail"] + (f" (clause {i['clause']})" if i["clause"] else "")
                   for i in state.get("policy_issues", [])]
        coverage = state.get("coverage")
        if coverage and not coverage["covered"]:
            grounds.append(f"{coverage['reasoning']} (clauses {', '.join(coverage['exclusion_clause_ids'])})")

    facts = {
        "claim_id": state["claim_id"],
        "policyholder": form["claimant_name"],
        "vehicle": form["vehicle_reg"],
        "accident_date": form["loss_date"],
        "decision": decision,
        "amount_payable_rupees": amount,
        "assessed_repair_cost": assessment.get("gross_assessed"),
        "depreciation": assessment.get("total_depreciation"),
        "deductible": assessment.get("deductible"),
        "rejection_grounds": grounds,
        "note_from_claims_officer": (state.get("human_decision") or {}).get("notes", ""),
    }
    return llm.write("finalize:letter", LETTER_INSTRUCTIONS, json.dumps(facts, indent=2))

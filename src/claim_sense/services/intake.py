from claim_sense.integrations import documents, llm
from claim_sense.schemas.schemas import ClaimForm, FIRReport, SurveyReport


COMMON_RULES = """
You are a careful data-entry assistant at an Indian motor insurance company.
Read the attached document and fill every field of the schema.
Rules:
- Dates in the document are Indian format DD/MM/YYYY. Return them as YYYY-MM-DD.
- Times may be 12-hour (e.g. 7:45 PM). Return 24-hour HH:MM (e.g. 19:45).
- Amounts like "Rs. 22,500" become the number 22500.
- Copy facts exactly as written. Do NOT correct, guess or fill gaps using common sense.
- If a text field is genuinely absent, return an empty string.
"""

DOCUMENTS = {
    # file name        -> (schema,        extra instructions)
    "claim_form.pdf": (ClaimForm, "This is a claim form filled by the policyholder. "
                                  "garage_id is the code before the dash (e.g. G07). "
                                  "witness_available is true if the witness line starts with Yes."),
    "survey_report.pdf": (SurveyReport, "This is an independent surveyor's inspection report. "
                                        "Map the Category column: Metal->metal, Plastic/Rubber->plastic_rubber, "
                                        "Fibre glass->fibre_glass, Glass->glass, Labour->labour. "
                                        "Do not include the total row as an item. "
                                        "cause_consistent_with_claim reflects the surveyor's written opinion."),
    "fir.pdf": (FIRReport, "This is a police First Information Report (FIR). "
                           "breath_alcohol_mg_per_100ml is the numeric breath analyser reading, or null if not tested. "
                           "driver_intoxicated is true only if the FIR records drunken driving."),
}


def extract_documents(docs_dir: str) -> tuple[dict, list[dict]]:
    """
    Returns ({"claim_form": {...}, "survey": {...}, "fir": {...} or None}, [usage, ...]).
    The claim form and survey report are mandatory; the FIR is optional.
    """
    names = documents.document_names(docs_dir)
    for required in ("claim_form.pdf", "survey_report.pdf"):
        if required not in names:
            raise FileNotFoundError(f"{required} is missing in {docs_dir}")

    extracted, usages = {}, []
    for file_name, (schema, extra) in DOCUMENTS.items():
        key = {"claim_form.pdf": "claim_form", "survey_report.pdf": "survey", "fir.pdf": "fir"}[file_name]
        if file_name not in names:     # only the FIR can be missing at this point
            extracted[key] = None
            continue
        parsed, usage = llm.parse(
            step=f"intake:{key}",
            instructions=COMMON_RULES + "\n" + extra,
            content=[
                llm.pdf_bytes_part(file_name, documents.read_pdf(docs_dir, file_name)),
                llm.text_part("Extract the fields from this document."),
            ],
            schema=schema,
        )
        extracted[key] = parsed.model_dump()
        usages.append(usage)
    return extracted, usages

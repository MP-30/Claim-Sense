"""Check the live document extractor against the 20 synthetic course claims."""

import argparse
import re
import subprocess
from collections import Counter
from decimal import Decimal
from pathlib import Path

from openai import APIStatusError, OpenAIError

from claim_sense.core import config
from claim_sense.services.intake import extract_documents
from scripts.generate_data import SCENARIOS


def printed_total(survey_pdf: Path) -> Decimal:
    result = subprocess.run(
        ["pdftotext", "-layout", str(survey_pdf), "-"], check=True, capture_output=True, text=True,
    )
    totals = re.findall(
        r"^\s*Total assessed \(before depreciation\)[ \t]+Rs\.[ \t]+([\d,]+(?:\.\d+)?)\s*$",
        result.stdout, re.MULTILINE,
    )
    if len(totals) != 1:
        raise ValueError(f"Expected one printed survey total in {survey_pdf.name}, found {len(totals)}")
    return Decimal(totals[0].replace(",", ""))


def check_extraction(case: dict, extracted: dict, total: Decimal) -> list[str]:
    errors = []
    form, survey, fir = case["form"], case["survey"], case["fir"]
    expected_fields = {
        "claim_form": {
            "policy_number": case["policy"]["policy_number"], "garage_id": form["garage"],
            "loss_date": form["loss_date"], "loss_time": form["loss_time"],
            "intimation_date": form["intimation_date"], "licence_valid_till": form["licence_valid_till"],
            "garage_estimate": form["estimate"],
        },
        "survey": {"inspection_date": survey["inspection_date"]},
    }
    if (extracted["fir"] is None) != (fir is None):
        errors.append("FIR presence differs from fixture")
    elif fir is not None:
        reading = re.match(r"\d+(?:\.\d+)?", fir["breath"])
        expected_fields["fir"] = {
            "incident_date": fir["date"], "incident_time": fir["time"],
            "driver_intoxicated": fir["intoxicated"],
            "breath_alcohol_mg_per_100ml": float(reading[0]) if reading else None,
        }
    for document, fields in expected_fields.items():
        for field, expected in fields.items():
            actual = extracted[document][field]
            if actual != expected:
                errors.append(f"{document}.{field}: expected {expected!r}, got {actual!r}")

    items = extracted["survey"]["items"]
    expected_items = Counter((category, Decimal(amount)) for _, category, amount in survey["items"])
    actual_items = Counter((item["category"], Decimal(str(item["amount"]))) for item in items)
    if actual_items != expected_items:
        errors.append("survey item categories or amounts differ from fixture")
    item_sum = sum(Decimal(str(item["amount"])) for item in items)
    if item_sum != total:
        errors.append(f"survey items sum to {item_sum}, printed total is {total}")
    return errors


def main() -> int:
    cases = {s["claim_id"]: s for s in SCENARIOS}
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("claim_ids", nargs="*", help="Optional sample IDs (default: all 20)")
    ids = parser.parse_args().claim_ids or list(cases)
    unknown = set(ids) - cases.keys()
    if unknown:
        parser.error(f"Unknown sample IDs: {', '.join(sorted(unknown))}")
    print(f"Live intake using {config.OPENAI_MODEL}", flush=True)
    passed = pdf_count = 0
    for claim_id in ids:
        folder = config.SAMPLE_CLAIMS_DIR / claim_id
        try:
            total = printed_total(folder / "survey_report.pdf")
            extracted, usage = extract_documents(str(folder))
            errors = check_extraction(cases[claim_id], extracted, total)
        except OpenAIError as exc:
            status = f"HTTP {exc.status_code}, {exc.code}" if isinstance(exc, APIStatusError) else type(exc).__name__
            print(f"{claim_id} stopped at the model service ({status})", flush=True)
            break
        except (OSError, ValueError, subprocess.CalledProcessError) as exc:
            print(f"{claim_id} failed: {exc}", flush=True)
            continue
        pdf_count += len(usage)
        if errors:
            print(f"{claim_id} failed: {'; '.join(errors)}", flush=True)
        else:
            passed += 1
            print(f"{claim_id} passed ({len(usage)} PDFs, survey total Rs. {total:,})", flush=True)
    print(f"Passed {passed}/{len(ids)} claims; finished extraction on {pdf_count} PDFs.")
    return int(passed != len(ids))


if __name__ == "__main__":
    raise SystemExit(main())

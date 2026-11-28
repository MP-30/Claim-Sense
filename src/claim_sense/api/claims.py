from typing import Annotated

from botocore.exceptions import BotoCoreError, ClientError
from fastapi import APIRouter, Depends, File, HTTPException, Path, Response, UploadFile
from pydantic import BaseModel, Field

from claim_sense.api.auth import require_user
from claim_sense.core import config
from claim_sense.db import repository as db
from claim_sense.integrations import documents
from claim_sense.schemas.schemas import ReviewDecision
from claim_sense.services.service import (
    enqueue_upload, new_upload_id, process_claim, review_claim, sample_claim_dir, save_upload,
)


router = APIRouter(dependencies=[Depends(require_user)])

PDFSize = Annotated[int, Field(gt=0, le=documents.MAX_PDF_BYTES)]


class UploadSizes(BaseModel):
    claim_form: PDFSize
    survey_report: PDFSize
    fir: PDFSize | None = None


class UploadCompletion(BaseModel):
    has_fir: bool = False


@router.post("/claims/uploads")
def prepare_upload(sizes: UploadSizes, username: Annotated[str | None, Depends(require_user)]):
    """Issue one presigned PUT per PDF for a new claim."""
    if not config.DOCUMENTS_BUCKET:
        raise HTTPException(503, "S3 uploads are not configured.")
    claim_id = new_upload_id()
    metadata = documents.upload_metadata(username, sizes.fir is not None)
    try:
        targets = {
            field: documents.presign_upload(claim_id, field, size, metadata)
            for field, size in sizes.model_dump(exclude_none=True).items()
        }
    except (BotoCoreError, ClientError) as error:
        raise HTTPException(503, "Claim document storage is temporarily unavailable.") from error
    return {"claim_id": claim_id, "uploads": targets, "expires_in": documents.UPLOAD_EXPIRY_SECONDS}


def accept_upload(claim_id: str, location: str, response: Response) -> dict:
    if not config.PROCESSING_QUEUE_URL:
        return process_claim(claim_id, location, new_upload=True)
    try:
        claim = enqueue_upload(claim_id, location)
    except (BotoCoreError, ClientError) as error:
        raise HTTPException(503, f"Could not queue claim {claim_id}. Retry upload confirmation.") from error
    if claim["status"] in {"QUEUED", "PROCESSING"}:
        response.status_code = 202
        response.headers["Location"] = f"/claims/{claim_id}"
    return claim


@router.post("/claims/uploads/{claim_id}/complete", responses={202: {"description": "Claim queued for processing"}})
def complete_upload(claim_id: Annotated[str, Path(pattern=r"^CLM-U[A-F0-9]{32}$")],
                    completion: UploadCompletion, username: Annotated[str | None, Depends(require_user)],
                    response: Response):
    """Confirm that every expected PDF was uploaded before processing the claim."""
    if not config.DOCUMENTS_BUCKET:
        raise HTTPException(503, "S3 uploads are not configured.")
    try:
        verified = documents.verify_upload(claim_id, username, completion.has_fir)
    except (BotoCoreError, ClientError) as error:
        raise HTTPException(503, "Claim document storage is temporarily unavailable.") from error
    if not verified:
        raise HTTPException(409, "The expected claim PDFs are missing or do not match this upload.")
    return accept_upload(claim_id, documents.upload_location(claim_id), response)


@router.post("/claims", responses={202: {"description": "Claim queued for processing"}})
def submit_claim(response: Response, claim_form: UploadFile = File(..., description="Claim form PDF"),
                 survey_report: UploadFile = File(..., description="Surveyor report PDF"),
                 fir: UploadFile | None = File(None, description="Police FIR PDF (optional)")):
    """Upload a new claim's PDFs and process it. Returns the claim with its status and AI decision."""
    files = {"claim_form": claim_form, "survey_report": survey_report, "fir": fir}
    contents = {}
    for name, upload in files.items():
        if upload is None:
            continue
        if not (upload.filename or "").lower().endswith(".pdf"):
            raise HTTPException(400, f"{name} must be a PDF")
        contents[name] = upload.file.read()
    try:
        claim_id, folder = save_upload(contents)
    except (BotoCoreError, ClientError) as error:
        raise HTTPException(503, "Claim document storage is temporarily unavailable.") from error
    return accept_upload(claim_id, str(folder), response)


@router.post("/claims/sample/{claim_id}")
def run_sample_claim(claim_id: str):
    """Process one of the 20 bundled sample claims (handy for demos)."""
    try:
        folder = sample_claim_dir(claim_id)
    except ValueError as error:
        raise HTTPException(404, str(error))
    return process_claim(claim_id, folder)


@router.get("/claims/samples")
def list_sample_claims():
    return sorted(p.name for p in config.SAMPLE_CLAIMS_DIR.glob("*") if p.is_dir())


@router.get("/claims")
def list_all_claims(status: str | None = None):
    """Work queues, e.g. /claims?status=PENDING_ADJUSTER or /claims?status=PENDING_SIU"""
    return db.list_claims(status)


@router.get("/claims/{claim_id}")
def get_claim(claim_id: str):
    claim = db.get_claim(claim_id)
    if claim is None:
        raise HTTPException(404, f"Claim {claim_id} not found")
    return {**claim, "audit_trail": db.get_audit(claim_id)}


@router.post("/claims/{claim_id}/review")
def submit_review(claim_id: str, review: ReviewDecision, username: Annotated[str | None, Depends(require_user)]):
    """Adjuster / SIU decision. Resumes the paused LangGraph run and writes the customer letter."""
    try:
        if username is not None:
            review = review.model_copy(update={"reviewer": username})
        return review_claim(claim_id, review)
    except ValueError as error:
        raise HTTPException(400, str(error))

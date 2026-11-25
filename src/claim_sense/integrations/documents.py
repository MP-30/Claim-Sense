"""Private storage for uploaded claim PDFs."""

from functools import lru_cache
from hashlib import sha256
from pathlib import Path
from urllib.parse import urlsplit

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError

from claim_sense.core import config


UPLOAD_EXPIRY_SECONDS = 300
MAX_PDF_BYTES = 49_999_999
REQUIRED_FIELDS = ("claim_form", "survey_report")


@lru_cache
def s3_client():
    return boto3.session.Session().client("s3", config=Config(signature_version="s3v4"))


def upload_prefix(claim_id: str) -> str:
    return f"uploads/{claim_id}"


def upload_location(claim_id: str) -> str:
    return f"s3://{config.DOCUMENTS_BUCKET}/{upload_prefix(claim_id)}"


def upload_metadata(username: str | None, has_fir: bool) -> dict[str, str]:
    owner = sha256((username or "").encode()).hexdigest()
    return {"owner": owner, "has-fir": str(has_fir).lower()}


def presign_upload(claim_id: str, field: str, size: int, metadata: dict[str, str]) -> dict:
    headers = {
        "Content-Type": "application/pdf",
        "Content-Length": str(size),
        "If-None-Match": "*",
        **{f"x-amz-meta-{key}": value for key, value in metadata.items()},
    }
    url = s3_client().generate_presigned_url(
        "put_object",
        Params={
            "Bucket": config.DOCUMENTS_BUCKET,
            "Key": f"{upload_prefix(claim_id)}/{field}.pdf",
            "ContentType": headers["Content-Type"],
            "ContentLength": size,
            "IfNoneMatch": "*",
            "Metadata": metadata,
        },
        ExpiresIn=UPLOAD_EXPIRY_SECONDS,
    )
    return {"url": url, "headers": headers}


def verify_upload(claim_id: str, username: str | None, has_fir: bool) -> bool:
    expected = upload_metadata(username, has_fir)
    fields = (*REQUIRED_FIELDS, "fir") if has_fir else REQUIRED_FIELDS
    for field in fields:
        try:
            obj = s3_client().head_object(
                Bucket=config.DOCUMENTS_BUCKET, Key=f"{upload_prefix(claim_id)}/{field}.pdf",
            )
        except ClientError as error:
            if error.response["ResponseMetadata"]["HTTPStatusCode"] in (403, 404):
                return False
            raise
        if obj["Metadata"] != expected or obj["ContentType"] != "application/pdf":
            return False
        if not 0 < obj["ContentLength"] <= MAX_PDF_BYTES:
            return False
    return True


def store_pdf(claim_id: str, field: str, content: bytes) -> None:
    s3_client().put_object(
        Bucket=config.DOCUMENTS_BUCKET, Key=f"{upload_prefix(claim_id)}/{field}.pdf",
        Body=content, ContentType="application/pdf", IfNoneMatch="*",
    )


def document_names(location: str) -> set[str]:
    if not location.startswith("s3://"):
        return {path.name for path in Path(location).iterdir() if path.is_file()}
    uri = urlsplit(location)
    prefix = f"{uri.path.lstrip('/')}/"
    result = s3_client().list_objects_v2(Bucket=uri.netloc, Prefix=prefix)
    return {obj["Key"].removeprefix(prefix) for obj in result.get("Contents", [])}


def read_pdf(location: str, filename: str) -> bytes:
    if not location.startswith("s3://"):
        return (Path(location) / filename).read_bytes()
    uri = urlsplit(location)
    body = s3_client().get_object(Bucket=uri.netloc, Key=f"{uri.path.lstrip('/')}/{filename}")["Body"]
    with body:
        return body.read()

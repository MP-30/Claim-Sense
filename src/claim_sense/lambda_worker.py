"""Run one queued upload per invocation using the shared claim workflow."""

from functools import cache
import json
import os
import re

import boto3


@cache
def configure_worker() -> None:
    """Resolve runtime secrets before the database and workflow modules load."""
    db_secret = os.environ["CLAIMS_DATABASE_SECRET_ARN"]
    secret = boto3.client("secretsmanager").get_secret_value(SecretId=db_secret)["SecretString"]
    os.environ["POSTGRES_PASSWORD"] = json.loads(secret)["password"]
    parameter_name = os.getenv("CLAIMS_OPENAI_PARAMETER")
    if parameter_name:
        parameter = boto3.client("ssm").get_parameter(Name=parameter_name, WithDecryption=True)
        os.environ["OPENAI_API_KEY"] = parameter["Parameter"]["Value"]


def handler(event: dict, context) -> None:
    configure_worker()
    from claim_sense.services.service import process_queued_claim

    for record in event["Records"]:
        claim_id = record["body"]
        dead_letter = record["eventSourceARN"] == os.environ["CLAIMS_PROCESSING_DLQ_ARN"]
        if not re.fullmatch(r"CLM-U[A-F0-9]{32}", claim_id):
            if dead_letter:
                print("Discarding invalid claim ID from the processing dead-letter queue")
                continue
            raise ValueError("Invalid queued claim ID")
        process_queued_claim(claim_id, dead_letter=dead_letter)

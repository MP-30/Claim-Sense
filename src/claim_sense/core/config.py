import os
from pathlib import Path

from dotenv import load_dotenv

ROOT_DIR = Path(__file__).resolve().parents[3]
load_dotenv(ROOT_DIR / ".env")

DATABASE_URL = os.getenv("DATABASE_URL")
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4.1-mini")
EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", "text-embedding-3-small")


# Standard USD per million tokens: input, cached input, output.
PRICES_USD_PER_1M = {
    "gpt-4.1-mini": (0.40, 0.10, 1.60),
    "gpt-5-mini": (0.25, 0.025, 2.00),
    "gpt-5.4-nano": (0.20, 0.02, 1.25),
    "text-embedding-3-small": (0.02, 0.02, 0.0),
}
USD_TO_INR = float(os.getenv("USD_TO_INR", "88"))


# Paths

DATA_DIR = Path(os.getenv("CLAIMS_DATA_DIR", str(ROOT_DIR / "data")))
SAMPLE_CLAIMS_DIR = DATA_DIR / "sample_claims"
UPLOADS_DIR = DATA_DIR / "uploads"
DOCUMENTS_BUCKET = os.getenv("CLAIMS_DOCUMENTS_BUCKET")
POLICY_WORDING_PATH = DATA_DIR / "policy_wording.md"
POLICY_INDEX_PATH = DATA_DIR / "policy_index.json"
EVAL_LABELS_PATH = DATA_DIR / "eval_labels.csv"


# Business rules (the "risk appetite" of the insurer)

AUTO_APPROVE_LIMIT_INR = 50_000
FRAUD_SIU_THRESHOLD = 50
FRAUD_REVIEW_THRESHOLD = 25
TOTAL_LOSS_RATIO = 0.75
INTIMATION_DAYS_LIMIT = 7
EARLY_CLAIM_DAYS = 30
ESTIMATE_INFLATION_RATIO = 1.40
RETRIEVAL_TOP_K = 6

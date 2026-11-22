# ClaimSense

ClaimSense processes synthetic motor insurance claims. It reads PDF claim forms, surveyor reports, and optional police reports, then checks policy coverage, calculates a payout, and routes claims for review. FastAPI serves the workflow, PostgreSQL 18 stores claims and LangGraph checkpoints, and the Streamlit portal handles claim submission and review.

## Run locally with Docker Compose

From the project root, start PostgreSQL, the API, and the Streamlit portal.

```bash
docker compose up --build --wait
docker compose ps
```

| Browser page | What you can do |
| --- | --- |
| [ClaimSense portal](http://127.0.0.1:8501) | Submit claims, work the review queues, and inspect decisions and audit history. |
| [Swagger UI](http://127.0.0.1:8000/docs) | Explore and call the FastAPI endpoints directly. |

The portal runs in Streamlit viewer mode, which hides the default Deploy button. If you previously started only the database and API, bring up the portal with `docker compose up --build --wait ui`.

Check the API and database version from the terminal.

```bash
curl --fail http://127.0.0.1:8000/health
docker compose exec db sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "SHOW server_version;"'
```

FastAPI initializes the claim and checkpoint tables on startup. PostgreSQL data persists in the `postgres-data` volume. The API mounts the repository's `data/` directory for policy wording, uploads, and sample documents.

## Seed the demo dataset

From the project root, start PostgreSQL and run the generator on the host. It uses the database settings in `.env`. The default settings connect to the published Compose database port.

```bash
docker compose up -d --wait db
uv run --locked python -m scripts.generate_data
```

The generator seeds 25 policies, 13 garages and five prior claims. It writes the PDFs for 20 scenarios under `data/sample_claims/` and their expected outcomes to `data/eval_labels.csv`. Reruns refresh the supplied fixtures and preserve submitted claims, reviews, uploads and audit history. Data setup works without an OpenAI key.

For model-backed processing, also copy `claimsense/data/policy_wording.md` from Shivank Agrawal’s course package into the project’s `data/` directory and set `OPENAI_API_KEY` for the API. In the local course installation the source is `~/Documents/scaler_notes/ai-bootcamp/day6/insurance-project3/claimsense/data/policy_wording.md`.

## Submit and review a claim

1. Open **Submit claim**. Upload the claim form, surveyor report, and optional police FIR as PDFs, or choose **Run a sample**. The API processes the claim during the request.
2. Inspect the recommendation, assessed payout, routing reasons, and audit trail. Use the **Adjuster review** or **SIU investigation** queue to find claims awaiting a decision.
3. Open a pending claim, enter the reviewer name, and submit an approval or rejection. Add notes when overriding the AI recommendation. The portal displays the final payout and decision letter after the API saves the decision.

## Run the claim review integration test

With Docker running, run this from the project root.

```bash
uv run --locked python -m unittest discover -s tests -v
```

The test seeds a disposable PostgreSQL 18 database, uploads the generated `CLM-1014` PDFs and runs the FastAPI and LangGraph workflow with fake OpenAI responses. It repeats the seed after human review and checks dataset counts, claim details, uploaded PDFs and audit history. It verifies the adjuster queue status, the rejection under `EXC-4`, the ₹0 final payout, the decision letter, and both audit events. The test uses temporary files and removes its database container on exit.

## Project layout

```text
Claim-Sense/
├── src/claim_sense/
│   ├── main.py             FastAPI app and router registration
│   ├── api/                HTTP endpoints
│   ├── core/               Configuration
│   ├── db/                 Persistence
│   ├── domain/             Payout and routing rules
│   ├── integrations/       Model client
│   ├── models/             SQLAlchemy table mappings
│   ├── schemas/            Pydantic models
│   ├── services/           Claim intake, coverage, and review
│   └── workflows/          LangGraph orchestration
├── data/                   Local policy and demo data
├── evals/                  Evaluation commands
├── scripts/                Dataset and claim commands
├── tests/
├── ui/                     Streamlit portal
├── Dockerfile
├── docker-compose.yml
└── pyproject.toml
```

## Run the API with hot reload

To run the API in your local Python environment with reload, stop the Compose API if it is using port 8000, then start just the database.

```bash
docker compose up -d --wait db
uv sync --locked
uv run fastapi dev
```

See `.env.example` for local defaults. Set `POSTGRES_PORT` if port 5432 is in use. The host-run API uses the published port, and the API container uses `db:5432`. A host-run API can connect to another PostgreSQL instance through `DATABASE_URL`. The database image applies the configured username, password, and database name when it initializes a fresh volume.

## Map the course files

Destinations below are inside `src/claim_sense/`.

| Course file | Destination |
| --- | --- |
| `app/config.py` | `core/config.py` |
| `app/schemas.py` | `schemas/schemas.py` |
| `app/db.py` | `models/models.py`, `db/session.py`, and `db/repository.py` |
| `app/rules.py` | `domain/rules.py` |
| `app/llm.py` | `integrations/llm.py` |
| `app/intake.py` | `services/intake.py` |
| `app/policy_rag.py` | `services/policy_rag.py` |
| `app/judgments.py` | `services/judgments.py` |
| `app/service.py` | `services/service.py` |
| `app/graph.py` | `workflows/graph.py` |
| `app/api.py` | `api/claims.py` for route functions |

Use `claim_sense.*` imports when moving the remaining course code.

Terraform lives in the sibling `Claim-Sense-Terraform` repository.

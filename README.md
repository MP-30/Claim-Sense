# ClaimSense

ClaimSense processes synthetic motor insurance claims. It reads PDF claim forms, surveyor reports, and optional police reports, then checks policy coverage, calculates a payout, and routes claims for review. FastAPI serves the workflow, PostgreSQL 18 stores claims and LangGraph checkpoints, and Streamlit displays saved results. The course reference is credited to Shivank Agrawal.

## Run locally with Docker Compose

From the project root, start PostgreSQL, the API, and the Streamlit portal.

```bash
docker compose up --build --wait
docker compose ps
```

| Browser page | What you can do |
| --- | --- |
| [ClaimSense portal](http://127.0.0.1:8501) | Browse saved claims, decision letters, and audit history. |
| [Swagger UI](http://127.0.0.1:8000/docs) | Upload PDFs, run sample claims, and submit review decisions. |

The portal is read-only and runs in Streamlit viewer mode, which hides the default Deploy button. **No claims yet** means the API returned an empty claim list. If you previously started only the database and API, bring up the portal with `docker compose up --build -d ui`.

Check the API and database version from the terminal.

```bash
curl --fail http://127.0.0.1:8000/health
docker compose exec db sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "SHOW server_version;"'
```

FastAPI initializes the claim and checkpoint tables on startup. PostgreSQL data persists in the `postgres-data` volume. The API mounts the repository's `data/` directory for policy wording, uploads, and sample documents.

## Submit and review a claim

For a model-backed run, set `OPENAI_API_KEY`, add the policy clauses to `data/policy_wording.md`, and seed PostgreSQL with the matching reference data. To run a sample claim through `POST /claims/sample/{claim_id}`, place its PDFs in `data/sample_claims/{claim_id}/`. The sample generator is still a placeholder.

1. Use `POST /claims` in Swagger UI to upload a claim form, a surveyor report, and an optional police report as PDFs. The current endpoint processes the claim during the request.
2. Use `GET /claims/{claim_id}` to inspect the returned claim, including its recommendation and audit history.
3. For a pending claim, use `POST /claims/{claim_id}/review` to submit the decision. The current API accepts the reviewer's name in the request body. Refresh the portal to see the updated status and decision letter.

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

Use `claim_sense.*` imports when moving the remaining course code. When adapting the course data generator, use `SessionLocal` and the SQLAlchemy models to seed PostgreSQL. The course generator writes reference data to its SQLite database.

Terraform lives in the sibling `Claim-Sense-Terraform` repository.

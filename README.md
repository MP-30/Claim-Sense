# ClaimSense

FastAPI application scaffold for motor insurance claims processing. The course reference is credited to Shivank Agrawal.

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

## Run PostgreSQL and the API

From the project root, start PostgreSQL 18 and FastAPI together.

```bash
docker compose up --build --wait db api
curl --fail http://127.0.0.1:8000/health
docker compose exec db psql -U claimsense -d claimsense -c 'SHOW server_version;'
```

Open [Swagger UI](http://127.0.0.1:8000/docs) to try the endpoints. Both the claims tables and LangGraph checkpoint tables are initialized during API startup. PostgreSQL data persists in the `postgres-data` volume. Policy wording, uploads, and sample documents live in the repository's `data/` directory, mounted into the API container.

To view stored claims in the browser, start the Streamlit service.

```bash
docker compose up --build -d ui
```

Open the [ClaimSense portal](http://127.0.0.1:8501) to view claims, decision letters, and audit history. Use Swagger UI to submit claims.

To run the API in your local Python environment with reload, start just the database.

```bash
docker compose up -d --wait db
uv sync --locked
uv run fastapi dev
```

See `.env.example` for the local defaults. Set `POSTGRES_PORT` if port 5432 is in use. The local API connects through the published port, while the API container uses `db:5432`. A host-run API can also connect to another PostgreSQL instance through `DATABASE_URL`. The database image applies username, password, and database initialization settings on a fresh volume.

To run a sample claim, add the policy clauses to `data/policy_wording.md`, set `OPENAI_API_KEY`, and populate the sample PDFs and their PostgreSQL reference data. The sample generator is still a placeholder. With an empty database, `GET /claims` returns an empty list.

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

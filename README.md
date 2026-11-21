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
│   ├── schemas.py          Pydantic models
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

## Run the API

```bash
uv sync --locked
uv run fastapi dev
```

Open `http://127.0.0.1:8000/` for the existing smoke endpoint. The application entry point is `claim_sense.main:app`.

## Map the course files

Destinations below are inside `src/claim_sense/`.

| Course file | Destination |
| --- | --- |
| `app/config.py` | `core/config.py` |
| `app/schemas.py` | `schemas.py` |
| `app/db.py` | `db/repository.py` |
| `app/rules.py` | `domain/rules.py` |
| `app/llm.py` | `integrations/llm.py` |
| `app/intake.py` | `services/intake.py` |
| `app/policy_rag.py` | `services/policy_rag.py` |
| `app/judgments.py` | `services/judgments.py` |
| `app/service.py` | `services/claims.py` |
| `app/graph.py` | `workflows/graph.py` |
| `app/api.py` | `api/claims.py` for route functions |

Use `claim_sense.*` imports when moving the course code, including in `scripts/` and `evals/`. For the HTTP endpoints, use the existing `router` and change the course decorators from `@app.get` and `@app.post` to `@router.get` and `@router.post`. Put any database startup setup in the application's lifespan in `main.py`.

Point the configuration at the repo's top-level `data/` directory. The course's relative path calculation assumes its original `app/` layout. Add the local policy text to `data/policy_wording.md`. The course data generator replaces its demo database, PDFs, and evaluation labels, so run it against local sample data. The course SQLite workflow also requires `langgraph-checkpoint-sqlite`.

The Docker files are reserved for the local container setup. Terraform lives in the sibling `Claim-Sense-Terraform` repository.

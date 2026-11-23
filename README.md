# ClaimSense

ClaimSense reviews synthetic motor insurance claims. A model reads the claim form, surveyor’s report and optional police report, then checks the loss against retrieved policy clauses. Python calculates the payout and routes the claim to automatic approval, an adjuster or the Special Investigation Unit (SIU). Every recommended rejection requires a human decision.

The Streamlit portal shows reviewers the survey line items, deductions, policy clauses and fraud signals behind each recommendation. FastAPI runs the LangGraph workflow, and PostgreSQL 18 stores claim records, audit events and checkpoints. In the restart test, an adjuster resumed the same claim after the API process was replaced and approved ₹90,000 with one finalization event. [System design](Design.md)

## Evaluation results

One live run on **20 labelled synthetic claims** produced the following results. Twelve claims received an immediate simulated review.

| Measure | Result |
| --- | --- |
| Route and recommendation accuracy | 19/20 each (95%) |
| SIU referrals | All 4 labelled cases referred, with 0 false referrals |
| Assessed payout accuracy | 14/14 labelled amounts within ₹1 |
| Mean active processing time through final decision | 16.03 seconds, excluding human waiting time |
| Mean estimated API cost through final decision | ₹0.1371 per claim |

**One paid-use claim was routed incorrectly.** `CLM-1015` was automatically approved for ₹10,200. Intake captured the paid ride, but retrieval omitted the vehicle-use exclusion. The expected outcome was adjuster review with a rejection recommendation. [Scorecard and methodology](evals/scorecards/2026-11-23.md) · [Per-claim results](evals/scorecards/2026-11-23.csv)

## Run locally with Docker Compose

From the project root, create the local data directory and start PostgreSQL, FastAPI and Streamlit. To process claims with the model, first create a local `.env` from `.env.example`, set `OPENAI_API_KEY`, and follow the [demo dataset setup](#seed-the-demo-dataset).

```bash
mkdir -p data
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

From the project root, start PostgreSQL and run the generator on the host with `uv` and Python 3.14. It uses the database settings in `.env`. The default settings connect to the published Compose database port.

```bash
docker compose up -d --wait db
uv run --locked python -m scripts.generate_data
```

The generator seeds 25 policies, 13 garages and five prior claims. It writes the PDFs for 20 scenarios under `data/sample_claims/` and their expected outcomes to `data/eval_labels.csv`. Reruns refresh the supplied fixtures and preserve submitted claims, reviews, uploads and audit history. Data setup works without an OpenAI key.

For model-backed processing, also copy `claimsense/data/policy_wording.md` from Shivank Agrawal’s course package into the project’s `data/` directory and set `OPENAI_API_KEY` for the API.

## Submit and review a claim

1. Open **Submit claim**. Upload the claim form, surveyor report, and optional police FIR as PDFs, or choose **Run a sample**. The API processes the claim during the request.
2. Open **Claims** and choose **Adjuster review** or **SIU investigation**. Each claim shows the coverage decision, retrieved policy clauses, survey line items and their depreciation rules, deductible, fraud signals and audit trail.
3. Open a pending claim, enter the reviewer name, and submit an approval or rejection. Add notes when overriding the AI recommendation. The portal displays the final payout and decision letter after the API saves the decision.
4. Open **Manager dashboard** for both queue sizes and the share of all claims approved automatically. Select a claim to inspect its decision and audit history.

## Verify document extraction (M2)

After generating the samples, run the intake check from the project root. It uses the configured OpenAI model and needs Poppler’s `pdftotext` on the host. A complete run sends 20 claim forms, 20 surveys and four FIRs for extraction and incurs API usage charges.

```bash
uv run --locked python -m evals.check_intake
```

For a single case, append its ID, for example `CLM-1014`. The command checks normalized dates, times and amounts against the fixture, compares survey item categories and amounts, and sums the extracted items against the total read directly from each PDF. It prints a result for each claim and exits with a nonzero status if any requested claim fails or cannot be checked. It does not submit claims or modify the database.

## Run the claim review integration test

With Docker running, run this from the project root.

```bash
uv run --locked python -m unittest discover -s tests -v
```

The suite seeds a disposable PostgreSQL 18 database and runs FastAPI and LangGraph with mocked OpenAI responses. It verifies automatic approval of `CLM-1001` for ₹12,050, adjuster review and human rejection of `CLM-1014` under `EXC-4`, the supported breakdown exclusion for `CLM-1016`, and the pending SIU referral for `CLM-1019`. It also repeats the seed after human review and checks that the uploaded PDFs, decision letter and audit history survive. The portal tests exercise Streamlit over HTTP, replace the API process while `CLM-1008` is pending, and verify the saved approval on the same thread. Docker removes the disposable databases on exit.

## Run the 20-claim scorecard (M5)

Use a dedicated seeded demo database with the policy wording, sample PDFs, labels and a configured model key. The evaluator reprocesses the sample IDs. The following command also simulates an immediate review of every pending claim and writes a timestamped CSV under `evals/`.

```bash
uv run --locked python -m evals.run_evals --resume
```

Omit `--resume` to stop at the first pause or automatic settlement. Use `--output PATH` to select a new results file. The CSV preserves the original prediction, assessed amount, routing reasons, initial processing time and cost. With simulated reviews it also records resume time, incremental cost, and the two phases combined. Setup, shared policy indexing and human waiting time are outside the per-claim measurements. [Measured scorecard](evals/scorecards/2026-11-23.md)

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

The local workflow and evaluation are complete. The AWS deployment and teardown demonstration is planned in the companion [ClaimSense Terraform repository](https://github.com/MP-30/Claim-Sense-Terraform).

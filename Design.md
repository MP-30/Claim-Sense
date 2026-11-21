# ClaimSense System Design

ClaimSense processes synthetic motor insurance claims from a claim form, a surveyor report, and an optional police report. It checks policy coverage, calculates a payable amount, and routes claims to automatic approval, an adjuster, or the Special Investigation Unit (SIU). The design builds on Shivank Agrawal's ClaimSense course.

The local baseline runs on FastAPI, PostgreSQL 18, LangGraph, and Streamlit. A smoke test with mocked model responses verified that a pending claim could survive container replacement and resume from PostgreSQL. The roadmap's next milestone is a repeatable `CLM-1014` upload and review test.

## Current runtime

```mermaid
flowchart LR
    Browser["Local browser"]
    Model["OpenAI models"]

    subgraph Local["Docker Compose"]
        Portal["Streamlit portal · 8501"]
        API["FastAPI and Swagger UI · 8000"]
        Graph["LangGraph workflow inside the API process"]
        Database[("PostgreSQL 18 · claim records, audit events, checkpoints")]
        Files[("Mounted data directory · PDFs, policy clauses, embedding cache")]

        Portal -->|"Read claims and audit history"| API
        API -->|"Run or resume during the request"| Graph
        API -->|"Read claim records"| Database
        API -->|"Save uploaded PDFs"| Files
        Graph -->|"Read references and persist progress"| Database
        Graph -->|"Read PDFs and policy clauses"| Files
    end

    Browser -->|"Browse saved results"| Portal
    Browser -->|"Upload or submit a review"| API
    Graph -->|"Extraction, embeddings, judgments, letter"| Model
```

The portal is read-only. The API persists claim snapshots for browser reads. LangGraph persists execution state under the stored `thread_id` so a later review request can resume the same run. Sample reprocessing currently replaces the prior claim and its audit history. [Local setup](README.md) · [API routes](src/claim_sense/api/claims.py) · [Claim service](src/claim_sense/services/service.py)

## Claim workflow

```mermaid
flowchart TD
    Start["Start claim run"] --> Intake["Extract typed fields from PDFs"]
    Intake --> Policy{"Hard policy check found an issue?"}
    Policy -->|"No"| Coverage["Retrieve policy clauses and judge coverage"]
    Coverage --> Amount["Calculate depreciation, deductible, and payable amount"]
    Amount --> Fraud["Check document consistency and score fraud signals"]
    Policy -->|"Yes"| Fraud
    Fraud --> Route{"Choose route"}
    Route -->|"AUTO_APPROVE"| Finalize["Write letter and persist final decision"]
    Route -->|"ADJUSTER_REVIEW"| Adjuster["PENDING_ADJUSTER"]
    Route -->|"SIU_INVESTIGATION"| SIU["PENDING_SIU"]
    Adjuster --> Pause["Pause at the human-review checkpoint"]
    SIU --> Pause
    Pause -->|"Review submitted for the same thread"| Finalize
    Finalize --> Result["APPROVED or REJECTED"]
```

Python controls eligibility checks, payout arithmetic, fraud scoring, and routing thresholds. Policy retrieval ranks embedded clauses by similarity. The model extracts fields, judges coverage using those clauses, flags document inconsistencies, and drafts a letter from the selected decision and amount. With the current defaults, a fraud score of 50 or more routes to SIU. Otherwise, a rejection recommendation, score of 25 or more, total loss, payable amount above ₹50,000, or lower-confidence coverage routes to an adjuster. A human makes the final decision on any recommended rejection. [Workflow](src/claim_sense/workflows/graph.py) · [Policy retrieval](src/claim_sense/services/policy_rag.py) · [Business rules](src/claim_sense/domain/rules.py)

## Planned worker and review flow

The API will acknowledge uploads after committing a claim and a processing job. A separate worker will run the graph and use the existing PostgreSQL checkpoints. PostgreSQL also holds the accepted review and the job that resumes it.

```mermaid
sequenceDiagram
    actor Client as Claim submitter
    actor Reviewer
    participant API as FastAPI
    participant Files as PDF storage
    participant DB as PostgreSQL
    participant Worker as Claim worker

    Client->>API: POST /claims with PDFs and idempotency key
    API->>Files: Store immutable PDFs
    API->>DB: Commit claim, run, and processing job
    API-->>Client: 202 Accepted with claim ID
    Worker->>DB: Claim job and commit lease
    Worker->>Files: Read PDFs for the run
    Worker->>DB: Run graph and persist checkpoints
    alt Human review required
        Worker->>DB: Publish pending status after checkpoint is saved
        Reviewer->>API: Submit review decision
        API->>API: Authenticate reviewer and check assigned queue
        API->>DB: Commit accepted review and resume job together
        API-->>Reviewer: 202 Accepted
        Worker->>DB: Claim resume job and read saved decision and checkpoint
        Worker->>Worker: Resume the same LangGraph thread
    else Eligible for automatic approval
        Worker->>Worker: Use rule-based approval and calculated payout
    end
    Worker->>DB: Commit final result, letter, audit event, and job completion
    Client->>API: GET /claims/{claim_id}
    API->>DB: Read committed result
    API-->>Client: Claim status, decision, and audit history
```

### Uploads, leases, and recovery

- **Upload idempotency.** Scope an idempotency key to the submitter and associate it with a hash of the PDF contents by document type. Reusing the key with the same contents returns the original claim ID. Different contents return `409 Conflict`. Keep staged PDFs at distinct storage keys and clean up unreferenced uploads after failed or duplicate submissions.
- **Job leasing.** Select an eligible job using `FOR UPDATE SKIP LOCKED`. Commit a lease token, expiry, and attempt record before starting work. Renew the lease during longer operations, let replacement workers reclaim expired leases, and use bounded backoff for transient failures.
- **Safe recovery.** Extend checkpoint persistence to verify the lease token and expiry in the same transaction as each checkpoint write. Apply the same check to business writes. A replacement worker resumes from the last committed checkpoint with the same `thread_id`. If a crash occurs between saving the review interrupt and publishing the pending status, the replacement can publish the already-saved pending state.
- **Finalization.** Use one final decision row per run. Commit the outcome, saved letter, final audit event, and job completion together. Retries can repeat external model calls. The database constraint and lease check keep the committed result unique.
- **Review authorization.** Derive reviewer identity from authentication and enforce access to the assigned queue. Store one accepted decision per run with actor, timestamp, amount, and notes. Require notes for overrides. Return the saved result for an identical replay and reject conflicting decisions.

### Planned status transitions

```mermaid
stateDiagram-v2
    [*] --> QUEUED
    QUEUED --> PROCESSING: Worker leases initial job
    PROCESSING --> PENDING_ADJUSTER: Adjuster checkpoint saved
    PROCESSING --> PENDING_SIU: SIU checkpoint saved
    PROCESSING --> FINALIZING: Auto-approval route
    PENDING_ADJUSTER --> REVIEW_QUEUED: Authorized decision committed
    PENDING_SIU --> REVIEW_QUEUED: Authorized decision committed
    REVIEW_QUEUED --> FINALIZING: Worker resumes saved thread
    FINALIZING --> APPROVED: Approval and letter committed
    FINALIZING --> REJECTED: Rejection and letter committed
    PROCESSING --> FAILED: Permanent error or retries exhausted
    REVIEW_QUEUED --> FAILED: Resume retries exhausted
    FINALIZING --> FAILED: Permanent error or retries exhausted
    APPROVED --> [*]
    REJECTED --> [*]
    FAILED --> [*]
```

Keep retry scheduling in the job record. The claim stays in its current phase while an attempt can still recover.

## Planned persistence model

The baseline already has `claims`, `audit_log`, `policies`, `prior_claims`, and `garages`, plus the tables managed by LangGraph's PostgreSQL checkpointer. The target claim-processing records below are the basis for versioned migrations. The reference tables continue to supply policy, history, and garage data. [Current SQLAlchemy models](src/claim_sense/models/models.py)

```mermaid
erDiagram
    CLAIMS ||--o{ CLAIM_DOCUMENTS : contains
    CLAIMS ||--o{ CLAIM_RUNS : has
    CLAIM_RUNS ||--o{ PROCESSING_JOBS : schedules
    PROCESSING_JOBS ||--o{ JOB_ATTEMPTS : records
    CLAIM_RUNS ||--o| REVIEW_DECISIONS : accepts
    CLAIM_RUNS ||--o| FINAL_DECISIONS : produces
    CLAIM_RUNS ||--o{ AUDIT_LOG : records

    CLAIMS {
        string claim_id PK
        string policy_number
        string status
        string submission_scope
        string idempotency_key
        string input_hash
    }
    CLAIM_DOCUMENTS {
        string document_id PK
        string claim_id FK
        string document_type
        string storage_key
    }
    CLAIM_RUNS {
        string run_id PK
        string claim_id FK
        string thread_id UK
    }
    PROCESSING_JOBS {
        string job_id PK
        string run_id FK
        string kind
        string status
        string lease_token
        datetime lease_expires_at
        datetime available_at
    }
    JOB_ATTEMPTS {
        string attempt_id PK
        string job_id FK
        datetime started_at
        datetime finished_at
        string outcome
    }
    REVIEW_DECISIONS {
        string run_id PK, FK
        string reviewer_id
        string decision
        decimal approved_amount
        string notes
        datetime decided_at
    }
    FINAL_DECISIONS {
        string run_id PK, FK
        string decision
        decimal final_amount
        string letter
    }
    AUDIT_LOG {
        bigint id PK
        string run_id FK
        string actor
        string step
        datetime ts
    }
```

Enforce unique `(submission_scope, idempotency_key)` and `(run_id, kind)` constraints for claim submissions and jobs. Job kinds are `PROCESS` and `RESUME`. Retrying a job appends an attempt to the same job. A deliberate rerun gets a new run and thread ID while preserving previous attempts, decisions, and audit events. Application tables remain the authority for API status and decisions.

## Planned evidence and deployment

- **PDF evidence.** Start with payout-critical survey line items. Store each extracted value with its document ID, page number, supporting quote, and verification result. Match the quote against text on the cited page and check that it supports the value. Missing or conflicting evidence goes to manual review. The reviewer sees the PDF evidence, retrieved policy clauses, and deterministic payout breakdown.
- **Local development.** Run the API, UI, worker, and PostgreSQL in Docker Compose. Make the PDF directory available to both the API and worker. Run integration tests with fake model responses and a disposable PostgreSQL database.
- **AWS demo.** The sibling `Claim-Sense-Terraform` repository will define the deployment. The planned topology uses ECS tasks for API, UI, and worker, RDS for PostgreSQL, and private S3 storage for PDFs. Use runtime secrets and task roles for resource access. Log claim IDs, workflow steps, attempts, latency, and model cost without including PDF contents.

## Verification scenarios

| Scenario | Expected result |
| --- | --- |
| `CLM-1014` baseline | With fake model responses, the claim reaches `PENDING_ADJUSTER` with a rejection recommendation. A human rejection yields `REJECTED`, zero payout, a letter, and review and finalization audit events. |
| `CLM-1001` and `CLM-1019` | Verify the ₹12,050 payout for the clean claim and SIU routing for the investigation case. |
| Duplicate and conflicting uploads | An identical replay returns the same claim ID. Changing the PDFs with the same key yields `409 Conflict`. |
| Worker lost during processing | A replacement reclaims the job, resumes the run, and leaves prior attempts visible. |
| Worker stops after a review is accepted | The review and resume job survive together. The replacement produces one final decision, letter, and finalization audit event. |
| Reviewer or citation failure | A reviewer outside the assigned queue is denied. A payout-critical value without verified evidence goes to manual review. |

Use the same cases to compare payout accuracy, routing accuracy, evidence verification, model cost, and end-to-end latency. Record per-case results and document how to reproduce them before the AWS recovery demo.

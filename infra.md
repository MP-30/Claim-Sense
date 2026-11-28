# Claim-Sense infrastructure

The LocalStack Pro configuration runs Claim-Sense in the `claimsense-local` ECS service and provisions a separate RDS for PostgreSQL 18 instance. Docker Compose provides a development environment with its own PostgreSQL 18 container and volume.

## 1. Where a LocalStack ECS claim goes

```mermaid
flowchart LR
    User["Adjuster browser"]
    subgraph Task["LocalStack ECS · claimsense-local task"]
        UI["Streamlit portal"]
        API["FastAPI and LangGraph"]
        UI -->|"Request upload URLs, then confirm the upload"| API
    end
    API -->|"Connect to the RDS endpoint"| DB[("LocalStack RDS for PostgreSQL 18")]
    User -->|"127.0.0.1:18501"| UI
    User -->|"127.0.0.1:18000/docs"| API
    UI -->|"Exchange username and password for an access token"| Cognito["LocalStack Cognito user pool"]
    API -->|"Fetch public keys to verify access tokens"| Cognito
    UI -->|"PUT PDFs with five-minute presigned URLs"| S3[("Private, versioned S3 bucket")]
    API -->|"Verify uploaded objects"| S3
    API -->|"Send claim ID, then return HTTP 202"| Queue["SQS processing queue"]
    Queue -->|"Invoke for one claim"| Worker["Lambda worker"]
    Queue -->|"Redrive after repeated delivery failures"| DLQ["Dead-letter queue"]
    DLQ -->|"Mark an unfinished claim FAILED"| Worker
    Worker -->|"Read PDFs"| S3
    Worker -->|"Save claim status, audit and checkpoints"| DB
    Worker -->|"Extract, assess and draft letters"| Model
    API -->|"Read policy files, sample PDFs and earlier uploads"| Files[("Mounted ECS data directory")]
    API -->|"PDF extraction, embeddings and model calls"| Model["OpenAI API"]
```

RDS stores claim records, audit events and LangGraph checkpoints. Streamlit uploads the PDFs using presigned PUT URLs and calls FastAPI to confirm them. With the worker deployed, the API verifies the objects at `uploads/<claim_id>/<document>.pdf`, records a `QUEUED` claim and sends only its ID through SQS. Lambda reads the PDFs, runs the original workflow and records the result in RDS. Human reviews and sample runs use FastAPI. Sample and older PDFs remain on the host mount under `~/.cache/claimsense-ecs/data`. [Claim processing and review flow](Design.md)

**Verified on LocalStack Pro on 26 November 2026.** All three PDFs for synthetic claim `CLM-UB8B99BB472F64ED0ACDC60FE9F9CCE96` matched the source files in private S3. A repeated PUT was rejected. FastAPI read the PDFs from S3, saved the claim as `PENDING_ADJUSTER`, and returned the same workflow thread on a repeated completion request.

**Lambda verified on LocalStack Pro on 28 November 2026.** Synthetic claim `CLM-U315744012C0D41689760365DB107AFD0` received HTTP 202 in 0.14 seconds after upload confirmation, then advanced through `QUEUED`, `PROCESSING` and `PENDING_ADJUSTER`. The ECS review API resumed the same thread and approved ₹90,000.

- Streamlit portal [http://127.0.0.1:18501/](http://127.0.0.1:18501/)
- FastAPI docs [http://127.0.0.1:18000/docs](http://127.0.0.1:18000/docs)

The LocalStack portal requires a Cognito account. An administrator can create one with `make local-user USERNAME=adjuster` from the Terraform repository. FastAPI verifies the access token before serving claim routes, and review records use the authenticated username. The health endpoint stays available for ECS probes. [LocalStack Terraform setup](https://github.com/MP-30/Claim-Sense-Terraform)

## 2. What Terraform provisions in LocalStack

```mermaid
flowchart LR
    TF["Terraform in environments/localstack"] -->|"AWS API calls to 127.0.0.1:4566"| Gateway
    subgraph Local["LocalStack Pro · ap-south-1"]
        Gateway["LocalStack gateway"]
        Gateway --> ECR["ECR repository · claimsense"]
        Gateway --> ECS["ECS cluster · claimsense-cluster"]
        ECS --> Service["ECS service · claimsense-local"]
        ECR -->|"Task image"| Service
        Gateway --> RDS[("RDS for PostgreSQL 18")]
        RDS -->|"Manages the database secret"| DBSecret["Secrets Manager"]
        SSM["SSM SecureString<br/>/claimsense/openai_api_key"] -->|"ECS execution role reads at task startup"| Service
        Service -->|"Inject OPENAI_API_KEY into the API container"| TaskAPI["API container"]
        DBSecret -->|"Inject POSTGRES_PASSWORD at task startup"| TaskAPI
        TaskAPI -->|"PostgreSQL connection"| RDS
        Gateway --> Logs["CloudWatch Logs · /ecs/claimsense<br/>7-day retention"]
        Gateway --> S3[("S3 bucket · claimsense-pdfs-*<br/>Public access blocked, versioning enabled,<br/>AES-256 default encryption")]
        TaskAPI -->|"ECS task role authorizes access to uploads/"| S3
        Gateway --> Queue["SQS processing queue and dead-letter queue"]
        Queue --> Worker["Container-image Lambda worker"]
        ECR -->|"Lambda image"| Worker
        Worker -->|"Read PDFs"| S3
        Worker -->|"Read password"| DBSecret
        Worker -->|"Read OpenAI key"| SSM
        Worker -->|"Persist workflow state"| RDS
    end
```

Terraform provisions the foundation first. Publish the API and worker images before applying the local configuration to update ECS and deploy Lambda with its SQS mappings. Run `make local-check` in the Terraform repository to verify Lambda, the HTTP endpoints and the PostgreSQL version. [Terraform repository](https://github.com/MP-30/Claim-Sense-Terraform)

**Verified on 25 November 2026.** The ECS API connected to LocalStack RDS running PostgreSQL 18.7. The migration preserved the three existing claims and their audit and checkpoint records. A new synthetic claim, `CLM-U14C6F4`, kept its LangGraph thread and original PDFs across task replacement and was then approved for ₹90,000.

### OpenAI configuration and claim retries

Store the OpenAI key in LocalStack Parameter Store as a `SecureString`. The local Terraform variables file, `environments/localstack/local-model.auto.tfvars.json`, contains the parameter name and model setting. ECS reads the key during task startup and sets `OPENAI_API_KEY` for FastAPI. The verified local deployment uses `gpt-5.4-nano`. [LocalStack setup](https://github.com/MP-30/Claim-Sense-Terraform)

The portal and API health endpoint can respond before OpenAI credentials are configured. If a submission records `OpenAIError: Missing credentials`, check the parameter, apply the ECS configuration so the replacement task receives the key, and resubmit the claim. On 24 November 2026, rerunning synthetic claim `CLM-1007` through the portal's API endpoint changed its status from `FAILED` to `APPROVED` with a payout of ₹12,100.

## 3. How the AWS demo is designed to run

Solid arrows show runtime connections described by the root Terraform configuration or backend code. Dashed arrows show work still needed for deployment. This is the target topology for M6.

```mermaid
flowchart TB
    Build["Build the backend Dockerfile"] -.->|"Publish a tagged image"| ECR["ECR repository"]
    Param["SSM Parameter Store<br/>OpenAI key supplied before launch"] -->|"Inject key into API at startup"| API
    ECR -->|"ECS pulls one image for both containers"| Task
    User["Adjuster browser"] -->|"Public task IP, port 8501"| UI
    subgraph VPC["Default VPC"]
        subgraph Task["ECS Fargate task · public subnet"]
            UI["Streamlit"]
            API["FastAPI and LangGraph"]
            UI -->|"HTTP to localhost:8000"| API
        end
        DB[("Planned private Amazon RDS for PostgreSQL 18<br/>Claims, audit log and checkpoints")]
        API -.->|"Connect using a database secret"| DB
    end
    API -.->|"Presign uploads and read PDFs with an ECS task role"| S3[("Private, versioned S3 bucket")]
    UI -.->|"PUT PDFs using presigned URLs"| S3
    API -->|"Model calls over HTTPS"| Model["OpenAI API"]
    UI -->|"Container logs"| Logs["CloudWatch Logs"]
    API -->|"Container logs"| Logs
```

The root Terraform configuration defines one Fargate task with two containers sharing a network interface. Its security group allows ports 8501 and 8000 from `allowed_cidr`. ECS uses the **execution role** to pull the image, deliver logs and read the OpenAI key from `/claimsense/openai_api_key`. The proposed **task role** gives the running API access to claim objects in S3.

**Remaining work for the demo**

1. Provision a private Amazon RDS for PostgreSQL 18 instance. Allow database traffic from the ECS security group and supply the API’s database credentials through Secrets Manager.
2. Configure the AWS ECS task role and set `CLAIMS_DOCUMENTS_BUCKET` for the API and portal. Deploy SQS and Lambda with private RDS connectivity and outbound access to OpenAI. Provision the synthetic demo inputs separately. [Docker build exclusions](.dockerignore)
3. Publish the tagged image, supply the OpenAI parameter, then start the service. Submit a synthetic claim that needs review, replace the task, and approve the same claim. Verify its decision, audit history and PDFs survive replacement before demonstrating teardown. [M6 acceptance criteria](roadmap.md#m6--demonstrate-deployment-and-teardown)

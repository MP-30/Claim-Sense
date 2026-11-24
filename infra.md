# Claim-Sense infrastructure

Docker Compose runs the claims application. LocalStack Pro hosts the first stage of its AWS infrastructure on the same machine. As of 24 November 2026, a claim submitted through the local portal uses Compose PostgreSQL and the mounted data directory. The ECS cluster in LocalStack is empty.

## 1. Where a local claim goes

```mermaid
flowchart LR
    User["Adjuster browser"]
    subgraph Compose["Docker Compose"]
        UI["Streamlit portal"]
        API["FastAPI and LangGraph"]
        DB[("PostgreSQL 18")]
        UI -->|"HTTP to api:8000"| API
        API -->|"SQL to db:5432"| DB
    end
    User -->|"127.0.0.1:8501"| UI
    User -->|"127.0.0.1:8000/docs"| API
    DB -->|"Claims, audit log and checkpoints"| Volume[("postgres-data volume")]
    API -->|"Save uploaded PDFs and read evidence"| Files[("Host data/ mounted at /app/data")]
    API -->|"PDF extraction, embeddings and model calls"| Model["OpenAI API"]
```

When a review pauses, PostgreSQL stores both the claim's thread ID and its LangGraph checkpoint. Uploaded PDFs live under `data/uploads/<claim_id>/`. Replacing the API container preserves both the pending review and its files as long as the database volume and host directory remain available. [Compose configuration](docker-compose.yml) · [Claim processing and review flow](Design.md)

## 2. What Terraform provisions in LocalStack

```mermaid
flowchart LR
    TF["Terraform in environments/localstack"] -->|"AWS API calls to 127.0.0.1:4566"| Gateway
    subgraph Local["LocalStack Pro · ap-south-1"]
        Gateway["LocalStack gateway"]
        Gateway --> ECR["ECR repository · claimsense"]
        Gateway --> ECS["ECS cluster · claimsense-cluster"]
        Gateway --> Logs["CloudWatch Logs · /ecs/claimsense<br/>7-day retention"]
        Gateway --> S3[("S3 bucket · claimsense-pdfs-*<br/>Public access blocked, versioning enabled,<br/>AES-256 default encryption")]
    end
```

Terraform manages seven resources here. S3 accounts for four of them because Terraform manages the bucket, public access block, versioning and encryption individually. To inspect the emulator, run `make local-check` from the Terraform repository. To find the generated bucket name and ECR URL, run `terraform -chdir=environments/localstack output` there. [Terraform repository](https://github.com/MP-30/Claim-Sense-Terraform)

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
        DB[("Planned private RDS PostgreSQL<br/>Claim records, audit log and checkpoints")]
        API -.->|"Connect using a database secret"| DB
    end
    API -.->|"Read and write PDFs with an ECS task role"| S3[("Private, versioned S3 bucket")]
    API -->|"Model calls over HTTPS"| Model["OpenAI API"]
    UI -->|"Container logs"| Logs["CloudWatch Logs"]
    API -->|"Container logs"| Logs
```

The root Terraform configuration defines one Fargate task with two containers sharing a network interface. Its security group allows ports 8501 and 8000 from `allowed_cidr`. ECS uses the **execution role** to pull the image, deliver logs and read the OpenAI key from `/claimsense/openai_api_key`. The proposed **task role** gives the running API access to claim objects in S3.

**Remaining work for the demo**

1. Provision PostgreSQL, allow database traffic from the task security group, and supply the API's database connection secret. Keep secret values outside Terraform state.
2. Update intake to store uploaded PDFs in S3 and retain their object keys with the claim. Provision the synthetic demo inputs separately. The image build excludes uploads and sample claim directories. [Docker build exclusions](.dockerignore)
3. Publish the tagged image, supply the OpenAI parameter, then start the service. Submit a synthetic claim that needs review, replace the task, and approve the same claim. Verify its decision, audit history and PDFs survive replacement before demonstrating teardown. [M6 acceptance criteria](roadmap.md#m6--demonstrate-deployment-and-teardown)

# Claim-Sense infrastructure

The LocalStack Pro configuration runs Claim-Sense in the `claimsense-local` ECS service and provisions a separate RDS for PostgreSQL 18 instance. Docker Compose provides a development environment with its own PostgreSQL 18 container and volume.

## 1. Where a LocalStack ECS claim goes

```mermaid
flowchart LR
    User["Adjuster browser"]
    subgraph Task["LocalStack ECS · claimsense-local task"]
        UI["Streamlit portal"]
        API["FastAPI and LangGraph"]
        UI -->|"HTTP on the task's Docker network"| API
    end
    API -->|"Connect to the RDS endpoint"| DB[("LocalStack RDS for PostgreSQL 18")]
    User -->|"127.0.0.1:18501"| UI
    User -->|"127.0.0.1:18000/docs"| API
    UI -->|"Exchange username and password for an access token"| Cognito["LocalStack Cognito user pool"]
    API -->|"Fetch public keys to verify access tokens"| Cognito
    API -->|"Save uploaded PDFs and read evidence"| Files[("Persisted LocalStack ECS data directory")]
    API -->|"PDF extraction, embeddings and model calls"| Model["OpenAI API"]
```

RDS stores the claim records, audit log, and LangGraph checkpoints. The API stores uploaded PDFs under `~/.cache/claimsense-ecs/data` on the host so replacement tasks can read them. The S3 bucket is ready for the planned storage integration. [Claim processing and review flow](Design.md)

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
    end
```

Terraform provisions the foundation first. Publishing the image and applying the local configuration again adds RDS, the ECS task definition and service, and execution permissions. Run `make local-check` in the Terraform repository to check the HTTP endpoints and confirm the database engine version from inside ECS. [Terraform repository](https://github.com/MP-30/Claim-Sense-Terraform)

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
    API -.->|"Read and write PDFs with an ECS task role"| S3[("Private, versioned S3 bucket")]
    API -->|"Model calls over HTTPS"| Model["OpenAI API"]
    UI -->|"Container logs"| Logs["CloudWatch Logs"]
    API -->|"Container logs"| Logs
```

The root Terraform configuration defines one Fargate task with two containers sharing a network interface. Its security group allows ports 8501 and 8000 from `allowed_cidr`. ECS uses the **execution role** to pull the image, deliver logs and read the OpenAI key from `/claimsense/openai_api_key`. The proposed **task role** gives the running API access to claim objects in S3.

**Remaining work for the demo**

1. Provision a private Amazon RDS for PostgreSQL 18 instance. Allow database traffic from the ECS security group and supply the API’s database credentials through Secrets Manager.
2. Update intake to store uploaded PDFs in S3 and retain their object keys with the claim. Provision the synthetic demo inputs separately. The image build excludes uploads and sample claim directories. [Docker build exclusions](.dockerignore)
3. Publish the tagged image, supply the OpenAI parameter, then start the service. Submit a synthetic claim that needs review, replace the task, and approve the same claim. Verify its decision, audit history and PDFs survive replacement before demonstrating teardown. [M6 acceptance criteria](roadmap.md#m6--demonstrate-deployment-and-teardown)

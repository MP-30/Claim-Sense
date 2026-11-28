FROM ghcr.io/astral-sh/uv:0.7.22 AS uv

FROM public.ecr.aws/lambda/python:3.14 AS worker
WORKDIR /var/task
COPY --from=uv /uv /usr/local/bin/uv
ENV UV_PROJECT_ENVIRONMENT=/opt/claimsense \
    PATH="/opt/claimsense/bin:${PATH}" \
    PYTHONPATH="/opt/claimsense/lib/python3.14/site-packages" \
    CLAIMS_DATA_DIR=/var/task/data \
    CLAIMS_POLICY_INDEX_PATH=/tmp/claimsense-policy-index.json
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --locked --no-dev --no-install-project
COPY src ./src
RUN uv sync --locked --no-dev --no-editable
COPY data/policy_wording.md ./data/
CMD ["claim_sense.lambda_worker.handler"]

FROM python:3.14-slim AS app

WORKDIR /app

COPY --from=uv /uv /usr/local/bin/uv
ENV PATH="/app/.venv/bin:${PATH}"

COPY pyproject.toml uv.lock README.md ./
RUN uv sync --locked --no-dev --no-install-project

COPY src ./src
RUN uv sync --locked --no-dev
COPY data ./data
COPY ui ./ui

EXPOSE 8000 8501
CMD ["uvicorn", "claim_sense.main:app", "--host", "0.0.0.0", "--port", "8000"]

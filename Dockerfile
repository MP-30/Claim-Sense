FROM python:3.14-slim

WORKDIR /app

COPY --from=ghcr.io/astral-sh/uv:0.7.22 /uv /usr/local/bin/uv
ENV PATH="/app/.venv/bin:${PATH}"

COPY pyproject.toml uv.lock README.md ./
RUN uv sync --locked --no-dev --no-install-project

COPY src ./src
RUN uv sync --locked --no-dev
COPY data ./data
COPY ui ./ui

EXPOSE 8000 8501
CMD ["uvicorn", "claim_sense.main:app", "--host", "0.0.0.0", "--port", "8000"]

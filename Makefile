.DEFAULT_GOAL := help

COMPOSE := docker compose
SERVICE ?=

.PHONY: help up down ps logs health db db-shell sync dev

help:
	@printf '%s\n' \
		'make up                Build and start PostgreSQL, FastAPI, and Streamlit' \
		'make down              Stop the stack and keep the PostgreSQL volume' \
		'make ps                Show container status' \
		'make logs              Follow logs for all services' \
		'make logs SERVICE=api  Follow logs for one service (db, api, or ui)' \
		'make health            Check the FastAPI health endpoint' \
		'make db                Start PostgreSQL only' \
		'make db-shell          Open a psql session in the running database' \
		'make sync              Install Python dependencies from uv.lock' \
		'make dev               Start PostgreSQL and run FastAPI with hot reload'

up:
	mkdir -p data
	$(COMPOSE) up --build --wait

down:
	$(COMPOSE) down

ps:
	$(COMPOSE) ps

logs:
	$(COMPOSE) logs --follow $(SERVICE)

health:
	$(COMPOSE) exec -T api python -c 'from urllib.request import urlopen; print(urlopen("http://127.0.0.1:8000/health", timeout=5).read().decode())'

db:
	$(COMPOSE) up --wait db

db-shell:
	$(COMPOSE) exec db sh -c 'exec psql -U "$$POSTGRES_USER" -d "$$POSTGRES_DB"'

sync:
	uv sync --locked

dev: db
	uv run --locked fastapi dev

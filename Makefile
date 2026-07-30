# Cortex Code Headless Agent — Makefile
# Set REPO_URL for build/push/deploy targets:
#   export REPO_URL=<org>-<acct>.registry.snowflakecomputing.com/cortex_headless_db/app/images

IMAGE ?= cortex-headless-agent
TAG   ?= latest
PLATFORM ?= linux/amd64

.PHONY: help sync dev run lint test fmt build push deploy logs

help:
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | awk 'BEGIN{FS=":.*?## "}{printf "  \033[36m%-12s\033[0m %s\n", $$1, $$2}'

sync: ## Install deps into a local .venv (incl. dev group)
	uv sync --group dev

dev: ## Run the API locally with reload (uses .env)
	uv run uvicorn headless_agent.server:app --reload --host 127.0.0.1 --port 8000 --loop asyncio

run: ## One-shot local prompt: make run PROMPT="..."
	uv run python scripts/run_local.py "$(PROMPT)"

lint: ## Lint with ruff
	uv run ruff check src scripts tests

fmt: ## Auto-format with ruff
	uv run ruff format src scripts tests

test: ## Run the test suite
	uv run --group dev pytest -q

build: ## Build the container image (set REPO_URL)
	docker build --platform $(PLATFORM) -t "$(REPO_URL)/$(IMAGE):$(TAG)" .

push: ## Push the image to the Snowflake image repo (set REPO_URL)
	docker push "$(REPO_URL)/$(IMAGE):$(TAG)"

deploy: ## Create/upgrade the SPCS service (edit <REPO_URL> in the SQL first)
	snow sql -f deploy/20_create_service.sql

logs: ## Tail service logs
	snow sql -q "SELECT SYSTEM\$$GET_SERVICE_LOGS('CORTEX_HEADLESS_AGENT', 0, 'agent', 200)"

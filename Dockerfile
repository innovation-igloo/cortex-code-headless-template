# syntax=docker/dockerfile:1

# Cortex Code Headless Agent — container image.
#
# Ships the cortex CLI (a hard runtime dependency the SDK does NOT bundle) plus
# the FastAPI service. Build for the SPCS node architecture (linux/amd64 or
# linux/arm64): `docker build --platform linux/amd64 ...`.
FROM python:3.11-slim-bookworm

# uv, copied from the official image (pin to match local tooling).
COPY --from=ghcr.io/astral-sh/uv:0.11.2 /uv /uvx /bin/

RUN apt-get update \
    && apt-get install -y --no-install-recommends curl ca-certificates bash \
    && rm -rf /var/lib/apt/lists/*

# Install the Cortex Code CLI. Keep this in sync with the SDK version pinned in
# pyproject.toml (the SDK enforces a MINIMUM_CLI_VERSION at connect time).
RUN curl -LsS https://ai.snowflake.com/static/cc-scripts/install.sh | sh
ENV CORTEX_CODE_CLI_PATH="/root/.local/bin/cortex"

WORKDIR /app

# Install dependencies first (cached layer), then the project.
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

COPY src ./src
COPY scripts ./scripts
RUN uv sync --frozen --no-dev \
    && chmod +x scripts/entrypoint.sh \
    && mkdir -p /workspace

# venv + cortex CLI on PATH so `uvicorn`/`python`/`cortex` resolve in entrypoint.
ENV PATH="/app/.venv/bin:/root/.local/bin:${PATH}" \
    CORTEX_CODE_AGENT_SDK_SKIP_VERSION_CHECK=1 \
    HEADLESS_AGENT_AUTH_MODE=pat \
    HEADLESS_AGENT_WORKDIR=/workspace \
    HEADLESS_AGENT_HOST=0.0.0.0 \
    HEADLESS_AGENT_PORT=8000

EXPOSE 8000
ENTRYPOINT ["scripts/entrypoint.sh"]

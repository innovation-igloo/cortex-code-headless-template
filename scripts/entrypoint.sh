#!/usr/bin/env bash
# Container entrypoint.
#   1. Bridge SPCS auth into a cortex CLI connection (no-op in dev mode).
#   2. Sanity-check the cortex CLI is present.
#   3. Launch the FastAPI service under uvicorn.
set -euo pipefail

echo "[entrypoint] cortex CLI: $(command -v cortex || echo 'NOT FOUND')"
cortex --version || true

# Write ~/.snowflake/config.toml from the SPCS-injected token when AUTH_MODE=spcs.
python -m headless_agent.auth

# NOTE: --loop asyncio is required. The SDK spawns the cortex CLI with a `user`
# kwarg that uvloop (bundled with uvicorn[standard]) rejects; plain asyncio
# accepts it.

exec uvicorn headless_agent.server:app \
  --host "${HEADLESS_AGENT_HOST:-0.0.0.0}" \
  --port "${HEADLESS_AGENT_PORT:-8000}" \
  --loop asyncio \
  --no-access-log

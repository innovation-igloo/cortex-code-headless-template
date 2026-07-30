"""Minimal headless driver for quick local testing (no HTTP).

Usage:
    uv run python scripts/run_local.py "What tables are in the SALES.PUBLIC schema?"

Reads the same settings as the service (env / .env). Requires the ``cortex`` CLI
installed and a working Snowflake connection named in HEADLESS_AGENT_CONNECTION.
"""

from __future__ import annotations

import asyncio
import sys

from headless_agent.agent import AgentSession, build_options
from headless_agent.auth import ensure_connection
from headless_agent.config import get_settings
from headless_agent.logging import configure_logging, get_logger

log = get_logger("run_local")


async def main(prompt: str) -> int:
    settings = get_settings()
    configure_logging(settings.log_level)
    ensure_connection(settings)
    log.info("auth_mode=%s connection=%s model=%s", settings.auth_mode, settings.connection, settings.model)

    session = AgentSession("local", build_options(settings))
    try:
        async for event in session.stream(prompt):
            kind = event["type"]
            if kind == "text":
                print(event["text"], end="", flush=True)
            elif kind == "tool_use":
                print(f"\n[tool] {event['name']}", flush=True)
            elif kind == "result":
                print(
                    f"\n\n[done] subtype={event['subtype']} "
                    f"turns={event['num_turns']} cost_usd={event['total_cost_usd']}",
                    flush=True,
                )
                if event.get("is_error") or event.get("subtype") != "success":
                    import json as _json

                    print("[result-detail] " + _json.dumps(event), flush=True)
                return 1 if event.get("is_error") else 0
            elif kind == "error":
                print(f"\n[error] {event.get('message')}", flush=True)
                return 1
    finally:
        await session.aclose()
    return 0


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print('usage: python scripts/run_local.py "<prompt>"', file=sys.stderr)
        raise SystemExit(2)
    raise SystemExit(asyncio.run(main(" ".join(sys.argv[1:]))))

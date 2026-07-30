"""FastAPI application exposing the headless agent over HTTP + SSE.

Endpoints:
    GET  /health        liveness (process is up)
    GET  /ready         readiness (cortex CLI resolvable + connection configured)
    POST /chat          send a prompt, stream events back as SSE
    GET  /sessions      list active session ids
    DELETE /sessions/{id}  close a session

The ``/chat`` response is a Server-Sent Events stream. Each event's ``data`` is a
JSON object: ``{"type": "text"|"thinking"|"tool_use"|"result"|"error", ...}``.
"""

from __future__ import annotations

import json
import shutil
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from .agent import SessionManager, build_options
from .auth import ensure_connection
from .config import Settings, get_settings
from .logging import configure_logging, get_logger

log = get_logger(__name__)


class ChatRequest(BaseModel):
    """A single chat turn."""

    prompt: str = Field(..., min_length=1)
    session_id: str = Field("default", min_length=1, max_length=128)


def _cli_available() -> bool:
    """True if the cortex CLI can be located (PATH or CORTEX_CODE_CLI_PATH)."""
    import os

    explicit = os.environ.get("CORTEX_CODE_CLI_PATH")
    if explicit and os.path.isfile(explicit):
        return True
    return shutil.which("cortex") is not None


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings: Settings = get_settings()
    configure_logging(settings.log_level)
    # Prepare the CLI connection in-process so env changes (pat host fix) and
    # the config.toml (spcs_oauth) are set before any CLI subprocess spawns.
    ensure_connection(settings)
    log.info(
        "starting headless agent: auth_mode=%s connection=%s model=%s",
        settings.auth_mode,
        settings.connection,
        settings.model,
    )
    app.state.settings = settings
    app.state.sessions = SessionManager(build_options(settings))
    try:
        yield
    finally:
        await app.state.sessions.close_all()
        log.info("shutdown complete")


app = FastAPI(title="Cortex Code Headless Agent", version="0.1.0", lifespan=lifespan)


@app.get("/health")
async def health() -> dict[str, str]:
    """Liveness probe: the process is running."""
    return {"status": "ok"}


@app.get("/ready")
async def ready() -> dict[str, object]:
    """Readiness probe: CLI resolvable and a connection name configured."""
    settings: Settings = app.state.settings
    cli_ok = _cli_available()
    ready_ok = cli_ok and bool(settings.connection)
    return {
        "ready": ready_ok,
        "cli_available": cli_ok,
        "connection": settings.connection,
        "auth_mode": settings.auth_mode,
    }


def _sse(event: dict[str, object]) -> str:
    """Format one event as an SSE frame."""
    return f"data: {json.dumps(event)}\n\n"


@app.post("/chat")
async def chat(request: ChatRequest) -> StreamingResponse:
    """Stream an agent turn as Server-Sent Events."""
    sessions: SessionManager = app.state.sessions

    async def event_stream() -> AsyncIterator[str]:
        try:
            session = await sessions.get_or_create(request.session_id)
            async for event in session.stream(request.prompt):
                yield _sse(event)
        except Exception as exc:  # noqa: BLE001 - surface errors to the client
            log.exception("chat stream failed for session %s", request.session_id)
            yield _sse({"type": "error", "message": str(exc)})

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.get("/sessions")
async def list_sessions() -> dict[str, list[str]]:
    sessions: SessionManager = app.state.sessions
    return {"sessions": sessions.list_sessions()}


@app.delete("/sessions/{session_id}")
async def close_session(session_id: str) -> dict[str, object]:
    sessions: SessionManager = app.state.sessions
    closed = await sessions.close(session_id)
    return {"session_id": session_id, "closed": closed}

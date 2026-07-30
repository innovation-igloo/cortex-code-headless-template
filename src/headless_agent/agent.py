"""Cortex Code Agent SDK wrapper.

Owns the mapping from application settings to ``CortexCodeAgentOptions`` and the
lifecycle of long-lived agent sessions. Each :class:`AgentSession` holds one
connected ``CortexCodeSDKClient`` (which in turn owns one ``cortex`` CLI
subprocess). Reusing a session across turns preserves the agent's conversation
context and avoids paying subprocess-spawn + version-check + initialize cost on
every request.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from dataclasses import replace
from typing import Any

from cortex_code_agent_sdk import (
    AssistantMessage,
    CortexCodeAgentOptions,
    CortexCodeSDKClient,
    ResultMessage,
)

from .config import Settings
from .hooks import build_hooks
from .logging import get_logger
from .permissions import make_permission_callback
from .tools import build_tool_server

log = get_logger(__name__)


def build_options(settings: Settings) -> CortexCodeAgentOptions:
    """Translate application settings into SDK options."""
    permission_mode = settings.effective_permission_mode
    bypass = permission_mode == "bypassPermissions"

    # Env passed to the cortex CLI subprocess.
    #  - Skip the CLI version handshake (saves ~2s per connect; the CLI is pinned
    #    in the image anyway).
    #  - Force the CLI to use *our* connection for everything, including Cortex
    #    inference, overriding any global cortexAgentConnectionName in
    #    ~/.snowflake/cortex/settings.json (which could point elsewhere / trigger
    #    a browser-auth popup).
    env = {
        "CORTEX_CODE_AGENT_SDK_SKIP_VERSION_CHECK": "1",
        "SNOWFLAKE_DEFAULT_CONNECTION_NAME": settings.connection,
    }

    # Footgun guard: with bypassPermissions the SDK passes
    # --dangerously-allow-all-tool-calls; adding an --allowed-tools allowlist on
    # top of that makes CLI 1.1.x *permanently block* the built-in SQL tool.
    # So in bypass mode we send no allowlist.
    allowed_tools = [] if bypass else list(settings.allowed_tools)

    options = CortexCodeAgentOptions(
        connection=settings.connection,
        model=settings.model,
        cwd=settings.workdir,
        env=env,
        allowed_tools=allowed_tools,
        disallowed_tools=list(settings.disallowed_tools),
        permission_mode=permission_mode,
        max_turns=settings.max_turns,
        system_prompt=settings.system_prompt,
        # The SDK keeps CLI stderr silent unless we opt in. Route it to the log.
        stderr=lambda line: log.warning("cortex-cli: %s", line.rstrip()),
        # In-process MCP tools for the POC (extend in tools.py).
        mcp_servers={"poc_tools": build_tool_server()},
        # Lifecycle audit logging.
        hooks=build_hooks(),
        # bypass mode must be explicitly acknowledged by the SDK too.
        allow_dangerously_skip_permissions=bypass,
    )

    # can_use_tool and bypass are mutually exclusive: bypass approves everything,
    # so a callback would never be consulted.
    if not bypass:
        options = replace(
            options,
            can_use_tool=make_permission_callback(list(settings.allowed_tools)),
        )
    return options


def _blocks_to_events(message: AssistantMessage) -> list[dict[str, Any]]:
    """Normalize an assistant message's content blocks into serializable events."""
    events: list[dict[str, Any]] = []
    for block in message.content:
        if hasattr(block, "text") and getattr(block, "text", None) is not None:
            events.append({"type": "text", "text": block.text})
        elif hasattr(block, "thinking"):
            events.append({"type": "thinking", "text": block.thinking})
        elif hasattr(block, "name") and hasattr(block, "id"):
            # ToolUseBlock
            events.append(
                {
                    "type": "tool_use",
                    "name": block.name,
                    "id": block.id,
                    "input": getattr(block, "input", None),
                }
            )
    return events


def _result_to_event(message: ResultMessage) -> dict[str, Any]:
    """Normalize the terminal result message."""
    return {
        "type": "result",
        "subtype": getattr(message, "subtype", None),
        "is_error": getattr(message, "is_error", False),
        "num_turns": getattr(message, "num_turns", None),
        "duration_ms": getattr(message, "duration_ms", None),
        "total_cost_usd": getattr(message, "total_cost_usd", None),
        "stop_reason": getattr(message, "stop_reason", None),
        "result": getattr(message, "result", None),
    }


class AgentSession:
    """A single long-lived agent conversation backed by one CLI subprocess."""

    def __init__(self, session_id: str, options: CortexCodeAgentOptions) -> None:
        self.session_id = session_id
        self._client = CortexCodeSDKClient(options)
        self._turn_lock = asyncio.Lock()
        self._connected = False

    async def connect(self) -> None:
        if not self._connected:
            await self._client.connect()
            self._connected = True
            log.info("session %s connected (pid=%s)", self.session_id, self._client.pid)

    async def stream(self, prompt: str) -> AsyncIterator[dict[str, Any]]:
        """Send a prompt and yield normalized events until the turn completes.

        Only one turn runs at a time per session; concurrent callers wait.
        """
        await self.connect()
        async with self._turn_lock:
            await self._client.query(prompt, session_id="default")
            async for message in self._client.receive_response():
                if isinstance(message, AssistantMessage):
                    for event in _blocks_to_events(message):
                        yield event
                elif isinstance(message, ResultMessage):
                    yield _result_to_event(message)
                    return

    async def aclose(self) -> None:
        if not self._connected:
            return
        disconnect = getattr(self._client, "disconnect", None)
        try:
            if disconnect is not None:
                await disconnect()
        except Exception:  # noqa: BLE001 - best-effort cleanup
            log.exception("error disconnecting session %s", self.session_id)
        finally:
            self._connected = False
            log.info("session %s closed", self.session_id)


class SessionManager:
    """Registry of active agent sessions keyed by session id."""

    def __init__(self, options: CortexCodeAgentOptions) -> None:
        self._options = options
        self._sessions: dict[str, AgentSession] = {}
        self._lock = asyncio.Lock()

    async def get_or_create(self, session_id: str) -> AgentSession:
        async with self._lock:
            session = self._sessions.get(session_id)
            if session is None:
                session = AgentSession(session_id, self._options)
                self._sessions[session_id] = session
        await session.connect()
        return session

    async def close(self, session_id: str) -> bool:
        async with self._lock:
            session = self._sessions.pop(session_id, None)
        if session is None:
            return False
        await session.aclose()
        return True

    def list_sessions(self) -> list[str]:
        return list(self._sessions.keys())

    async def close_all(self) -> None:
        async with self._lock:
            sessions = list(self._sessions.values())
            self._sessions.clear()
        for session in sessions:
            await session.aclose()

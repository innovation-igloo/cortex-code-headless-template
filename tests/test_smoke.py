"""Smoke tests.

The agent tests are skipped unless a live Snowflake connection and the cortex
CLI are available; the rest validate wiring without spawning the CLI.
"""

from __future__ import annotations

import os

import pytest
from fastapi.testclient import TestClient

from headless_agent.agent import build_options
from headless_agent.config import Settings
from headless_agent.server import app


def test_health() -> None:
    with TestClient(app) as client:
        resp = client.get("/health")
        assert resp.status_code == 200
        assert resp.json() == {"status": "ok"}


def test_ready_reports_fields() -> None:
    with TestClient(app) as client:
        resp = client.get("/ready")
        assert resp.status_code == 200
        body = resp.json()
        assert set(body) >= {"ready", "cli_available", "connection", "auth_mode"}


def test_build_options_uses_permission_callback_by_default() -> None:
    opts = build_options(Settings(allow_bypass=False, permission_mode="default"))
    assert opts.can_use_tool is not None
    assert opts.allow_dangerously_skip_permissions is False


def test_bypass_requires_allow_flag() -> None:
    # bypass requested but not allowed -> downgraded to default, callback set.
    guarded = Settings(permission_mode="bypassPermissions", allow_bypass=False)
    assert guarded.effective_permission_mode == "default"
    opts = build_options(guarded)
    assert opts.can_use_tool is not None
    assert opts.allow_dangerously_skip_permissions is False

    # bypass allowed -> no callback, dangerous flag set.
    allowed = Settings(permission_mode="bypassPermissions", allow_bypass=True)
    assert allowed.effective_permission_mode == "bypassPermissions"
    opts2 = build_options(allowed)
    assert opts2.can_use_tool is None
    assert opts2.allow_dangerously_skip_permissions is True


def test_allowed_tools_csv_parsing() -> None:
    s = Settings(allowed_tools="SQL, Read ,Bash")  # type: ignore[arg-type]
    assert s.allowed_tools == ["SQL", "Read", "Bash"]


@pytest.mark.skipif(
    not os.environ.get("HEADLESS_AGENT_LIVE"),
    reason="set HEADLESS_AGENT_LIVE=1 with a working connection + cortex CLI to run",
)
def test_chat_stream_live() -> None:
    with TestClient(app) as client, client.stream(
        "POST", "/chat", json={"prompt": "Say hello in one word.", "session_id": "test"}
    ) as resp:
        assert resp.status_code == 200
        saw_result = False
        for line in resp.iter_lines():
            if line and '"type": "result"' in line:
                saw_result = True
        assert saw_result

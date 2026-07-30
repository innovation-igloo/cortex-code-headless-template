"""Lifecycle hooks.

Hooks run custom code at points in the agent loop. Here we use them for audit
logging of tool use, which is useful when the service is shared with a customer
during a POC. Hooks return an (optional) directive dict; returning ``{}`` is a
no-op that lets execution proceed.

Register more events (Stop, UserPromptSubmit, PreCompact, ...) as needed.
"""

from __future__ import annotations

from typing import Any

from cortex_code_agent_sdk import HookMatcher

from .logging import get_logger

log = get_logger(__name__)


async def _audit_pre_tool_use(
    input_data: dict[str, Any],
    tool_use_id: str | None,
    context: Any,
) -> dict[str, Any]:
    """Log every tool invocation before it runs."""
    tool_name = input_data.get("tool_name", "<unknown>")
    log.info("hook.PreToolUse tool=%s tool_use_id=%s", tool_name, tool_use_id)
    return {}


async def _audit_post_tool_use(
    input_data: dict[str, Any],
    tool_use_id: str | None,
    context: Any,
) -> dict[str, Any]:
    """Log every tool invocation after it completes."""
    tool_name = input_data.get("tool_name", "<unknown>")
    log.info("hook.PostToolUse tool=%s tool_use_id=%s", tool_name, tool_use_id)
    return {}


def build_hooks() -> dict[str, list[HookMatcher]]:
    """Build the hook registration map passed to CortexCodeAgentOptions.hooks.

    ``matcher=None`` matches all tools; use a regex like ``"Bash|Write"`` to
    scope a hook to specific tools.
    """
    return {
        "PreToolUse": [HookMatcher(matcher=None, hooks=[_audit_pre_tool_use])],
        "PostToolUse": [HookMatcher(matcher=None, hooks=[_audit_post_tool_use])],
    }

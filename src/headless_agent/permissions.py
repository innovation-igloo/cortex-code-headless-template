"""Tool-permission policy.

The agent asks this callback before running any tool that is not in the
``allowed_tools`` allowlist. The default policy denies write/exec tools unless
they were explicitly allowlisted, which is a safe starting point for a POC that
serves untrusted prompts. Tighten or loosen it for the specific engagement.
"""

from __future__ import annotations

from typing import Any

from cortex_code_agent_sdk import (
    PermissionResult,
    PermissionResultAllow,
    PermissionResultDeny,
    ToolPermissionContext,
)

from .logging import get_logger

log = get_logger(__name__)

# Tools considered read-only / low-risk. These are auto-allowed by the policy
# even if the caller did not put them in allowed_tools.
_READ_ONLY_TOOLS = frozenset({"SQL", "Read", "Grep", "Glob", "web_search", "web_fetch"})

# Tools that mutate state or execute commands. Denied by the default policy
# unless explicitly allowlisted via settings.allowed_tools.
_MUTATING_TOOLS = frozenset({"Bash", "Write", "Edit", "notebook_edit", "notebook_execute"})


def make_permission_callback(allowed_tools: list[str]):
    """Build a ``can_use_tool`` callback closed over the configured allowlist.

    The SDK already auto-approves anything in ``allowed_tools`` before it ever
    reaches this callback, so this handles the *remaining* tools.
    """
    allowlist = frozenset(allowed_tools)

    async def can_use_tool(
        tool_name: str,
        tool_input: dict[str, Any],
        context: ToolPermissionContext,
    ) -> PermissionResult:
        if tool_name in allowlist or tool_name in _READ_ONLY_TOOLS:
            log.info("permission: allow %s", tool_name)
            return PermissionResultAllow()

        if tool_name in _MUTATING_TOOLS:
            log.warning("permission: deny %s (mutating tool not allowlisted)", tool_name)
            return PermissionResultDeny(
                message=(
                    f"Tool '{tool_name}' is a state-changing tool and is not in the "
                    "configured allowlist. Add it to HEADLESS_AGENT_ALLOWED_TOOLS to enable it."
                )
            )

        # Unknown tool (e.g. an in-process MCP tool): default-allow so the POC's
        # own tools work, but log it for visibility. Deny here instead if you
        # want a strict default.
        log.info("permission: allow %s (unlisted, default-allow)", tool_name)
        return PermissionResultAllow()

    return can_use_tool

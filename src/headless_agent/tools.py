"""Example in-process MCP tool server.

This is the primary extension point for a POC. In-process ``@tool`` servers run
inside *this* Python process (not a separate subprocess), so tools have direct
access to application state. This capability is Python-only in the SDK.

Replace ``echo`` with real tools for the customer's use case (for example a tool
that validates credentials, previews a schema, or calls an internal API).
"""

from __future__ import annotations

from typing import Any

from cortex_code_agent_sdk import McpSdkServerConfig, create_sdk_mcp_server, tool

from .logging import get_logger

log = get_logger(__name__)

# The server name becomes the MCP namespace. Tools are addressed by the agent
# as ``mcp__<server_name>__<tool_name>`` (e.g. mcp__poc_tools__echo).
SERVER_NAME = "poc_tools"


@tool(
    "echo",
    "Echo a message back to the caller. Placeholder to demonstrate in-process tools.",
    {"message": str},
)
async def echo(args: dict[str, Any]) -> dict[str, Any]:
    """Return the input message. Replace with real POC logic."""
    message = args.get("message", "")
    log.info("poc_tools.echo called")
    return {"content": [{"type": "text", "text": f"echo: {message}"}]}


def build_tool_server() -> McpSdkServerConfig:
    """Build the in-process MCP server exposing the POC tools."""
    return create_sdk_mcp_server(SERVER_NAME, tools=[echo])

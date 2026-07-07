"""GitHub access via the remote GitHub MCP server (least-privilege by design).

Wiring is per the official ADK GitHub integration
(https://adk.dev/integrations/github): ``StreamableHTTPConnectionParams`` to
``https://api.githubcopilot.com/mcp/`` with ``Authorization`` /
``X-MCP-Toolsets`` / ``X-MCP-Readonly`` headers.

Two factories enforce the read/write split from TDD section 6 — every persona
gets the **read-only** toolset except the single status/Delivery write path.
A bad toolset string makes the server fail closed (HTTP 400), so the split is
fail-safe. The token is passed straight into headers and never logged or placed
in a prompt.
"""

from __future__ import annotations

from google.adk.tools.mcp_tool import McpToolset
from google.adk.tools.mcp_tool.mcp_session_manager import (
    StreamableHTTPConnectionParams,
)

from app import config

# Read scope for analysts/reviewers: browse code, read issues/labels, and run
# the security/dependabot read tools.
READ_TOOLSETS = "repos,issues,labels,discussions,code_security,dependabot"

# Write scope reserved for the status (Orchestrator) and Delivery paths only.
WRITE_TOOLSETS = "repos,pull_requests,issues,labels"


def _toolset(
    toolsets: str,
    readonly: bool,
    token: str | None,
    tool_filter: list[str] | None = None,
) -> McpToolset:
    token = token or config.github_token()
    kwargs = {} if tool_filter is None else {"tool_filter": tool_filter}
    return McpToolset(
        connection_params=StreamableHTTPConnectionParams(
            url=config.GH_MCP_URL,
            headers={
                "Authorization": f"Bearer {token}",
                "X-MCP-Toolsets": toolsets,
                "X-MCP-Readonly": "true" if readonly else "false",
            },
        ),
        **kwargs,
    )


def github_read(token: str | None = None) -> McpToolset:
    """Read-only GitHub MCP toolset (issues, repos, labels, security)."""
    return _toolset(READ_TOOLSETS, readonly=True, token=token)


def github_write(
    token: str | None = None, tool_filter: list[str] | None = None
) -> McpToolset:
    """Write-scoped GitHub MCP toolset — ONLY for status updates and Delivery.

    Pass ``tool_filter`` to restrict the exposed write tools to a minimal
    allowlist (least privilege). This also avoids name collisions with local
    FunctionTools (e.g. the MCP ``create_branch`` vs. the host git tool) and
    keeps dangerous tools like ``merge_pull_request`` out of the agent's hands.
    """
    return _toolset(WRITE_TOOLSETS, readonly=False, token=token, tool_filter=tool_filter)

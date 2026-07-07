"""Delivery / PR Author (TDD persona #7) — P2b.

The ONLY persona granted the write-scoped GitHub toolset. It branches, commits,
and pushes the host working tree (git tools), then opens a DRAFT pull request
linked to the issue via the write MCP, and records a `DeliveryResult`. A human
always merges (no auto-merge — TDD non-goal).
"""

from __future__ import annotations

from google.adk.agents import Agent
from google.adk.agents.readonly_context import ReadonlyContext

from app import config, schemas
from app.sub_agents._common import model
from app.tools.github_mcp import github_write
from app.tools.sandbox import GIT_TOOLS
from app.tools.state_tools import record_delivery_result


def _instruction(ctx: ReadonlyContext) -> str:
    req = ctx.state.get(schemas.STATE_ISSUE_REQUEST, {})
    spec = ctx.state.get(schemas.STATE_ISSUE_SPEC, {})
    repo = ctx.state.get(schemas.STATE_REPO_CONTEXT, {})
    plan = ctx.state.get(schemas.STATE_CHANGE_PLAN, {})
    return f"""\
You are the PR Author. Deliver the implemented, reviewed change as a DRAFT pull
request. Steps, in order:

1. `create_branch` with a descriptive name, e.g. 'agent/issue-{req.get('issue_id', 'N')}-<slug>'.
2. `commit_all` with a clear conventional message referencing the issue.
3. `push_branch` to push it to origin.
4. Open a **draft** pull request with the write GitHub tools: head = your branch,
   base = the repo's default branch ('{repo.get('default_branch', 'main')}'),
   draft = true, body links the issue (e.g. 'Closes #{req.get('issue_id', 'N')}')
   and summarises the change + test results.
5. Call `record_delivery_result(branch, pr_url, status_label='agent:needs-review')`
   with the PR URL returned by the create-PR tool.

Use ONLY these tools: `create_branch`, `commit_all`, `push_branch`, the
create-pull-request tool, and `record_delivery_result`. `commit_all` already
stages exactly the right files — do NOT try to run shell/git commands yourself.

NEVER merge — a human merges. If any step fails, stop and report the error; do
not fabricate a PR URL.

ISSUE: {req}
SPEC: {spec}
PLAN: {plan}
"""


def create_delivery() -> Agent:
    return Agent(
        name="pr_author",
        model=model(config.MODEL_DELIVERY),
        mode="single_turn",
        description="Pushes a branch and opens a draft PR linked to the issue.",
        instruction=_instruction,
        # Minimal write surface: the host git tools handle branch/commit/push,
        # and only `create_pull_request` is exposed from MCP — this avoids the
        # MCP-vs-local `create_branch` name collision and keeps dangerous write
        # tools (merge_pull_request, delete_file, …) out of the agent's reach.
        tools=[
            *GIT_TOOLS,
            github_write(tool_filter=["create_pull_request"]),
            record_delivery_result,
        ],
    )

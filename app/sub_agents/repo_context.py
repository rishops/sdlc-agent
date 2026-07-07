"""Repo Context / Codebase Navigator (TDD persona #2).

Clones the target repo into the sandbox and records a structured `RepoContext`.
The clone/scan happen via the sandbox FunctionTools (not via MCP) to keep large
file content out of the model context.
"""

from __future__ import annotations

from google.adk.agents import Agent
from google.adk.agents.readonly_context import ReadonlyContext
from google.adk.tools.skill_toolset import SkillToolset

from app import config, schemas
from app.sub_agents._common import load_skill, model
from app.tools.sandbox import SANDBOX_TOOLS
from app.tools.state_tools import record_repo_context


def _instruction(ctx: ReadonlyContext) -> str:
    spec = ctx.state.get(schemas.STATE_ISSUE_SPEC, {})
    return f"""\
You are the Codebase Navigator for an autonomous issue-to-PR workflow.

Use the `repo-recon` skill for the method. The issue spec is:

{spec}

Clone the repository named in the spec, map its structure, detect its build and
test tooling, and identify the files most relevant to this issue.

TOOL ORDER: You MUST call `clone_repo` FIRST. Do not call `list_repo_tree`,
`read_repo_file`, or `run_in_repo` until `clone_repo` has returned success. If
any tool reports "call clone_repo first", call `clone_repo` and then retry.

SAFETY: Never read or echo secrets/.env files. Summarise structure, not
credentials. Do not run heavy build/test commands during recon.

OUTPUT CONTRACT: Your FINAL action MUST be a single call to `record_repo_context`.
Do not end your turn with a prose summary like "I am ready for the next steps" —
the structured tool call IS the deliverable. Do not narrate the context instead
of recording it.
"""


def create_repo_context() -> Agent:
    return Agent(
        name="codebase_navigator",
        model=model(config.MODEL_REPO_CONTEXT),
        mode="single_turn",
        description="Clones the repo and builds a structured map for planning.",
        instruction=_instruction,
        tools=[
            SkillToolset(skills=[load_skill("repo-recon")]),
            *SANDBOX_TOOLS,
            record_repo_context,
        ],
    )

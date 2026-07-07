"""Coder / Implementer (TDD persona #4) — leaf of the P2a build loop.

Applies the approved change plan by editing files in the cloned host workdir via
the deterministic EDIT_TOOLS. On the ``agent_engine`` backend the same work runs
through the managed code executor instead.
"""

from __future__ import annotations

from google.adk.agents import Agent
from google.adk.agents.readonly_context import ReadonlyContext
from google.adk.tools.skill_toolset import SkillToolset

from app import config, schemas
from app.sub_agents._common import load_skill, model
from app.tools.sandbox import EDIT_TOOLS, maybe_code_executor


def _instruction(ctx: ReadonlyContext) -> str:
    plan = ctx.state.get(schemas.STATE_CHANGE_PLAN, {})
    report = ctx.state.get(schemas.STATE_TEST_REPORT, {})
    return f"""\
You are the Implementer. Apply the change plan by editing files in the cloned
repository with `write_repo_file`, following the repo's conventions. Read files
first with `read_repo_file` so you preserve surrounding code. If a previous test
run failed, fix exactly the reported failures.

Use the `coding-standards` and `safe-edits` skills for method.

CHANGE PLAN:
{plan}

LAST TEST REPORT (if any):
{report}

Make minimal, surgical edits. Do not touch unrelated code. Implement only the
non-test files in the plan — do NOT write test files; the Verifier owns tests.
Do not run the test suite — the Verifier does that next.
"""


def create_coder() -> Agent:
    return Agent(
        name="implementer",
        model=model(config.MODEL_CODER),
        description="Implements the change plan by editing files in the sandbox.",
        instruction=_instruction,
        code_executor=maybe_code_executor(),
        tools=[
            SkillToolset(skills=[load_skill("coding-standards"), load_skill("safe-edits")]),
            *EDIT_TOOLS,
        ],
    )

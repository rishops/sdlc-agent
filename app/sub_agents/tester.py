"""Tester / Verifier (TDD persona #5) — leaf of the P2a build loop.

Writes/extends tests with the edit tools, runs the suite via `run_in_repo`, and
records a `TestReport`. The build loop exits when `test_report.passed` is true.
"""

from __future__ import annotations

from google.adk.agents import Agent
from google.adk.agents.readonly_context import ReadonlyContext
from google.adk.tools.skill_toolset import SkillToolset

from app import config, schemas
from app.sub_agents._common import load_skill, model
from app.tools.sandbox import EDIT_TOOLS, maybe_code_executor
from app.tools.state_tools import record_test_report


def _instruction(ctx: ReadonlyContext) -> str:
    repo = ctx.state.get(schemas.STATE_REPO_CONTEXT, {})
    return f"""\
You are the Verifier. Write or extend tests for the implemented change (with
`write_repo_file`), then run the repository's test suite with `run_in_repo` and
report a structured result.

Use the `test-authoring` skill for method. Write exactly the test file(s) named
in the plan's test strategy — do not create redundant or duplicate test files.
If the repo has no test command, add a minimal one appropriate to the stack (per
the repo context) and run it.

REPO CONTEXT (test command + layout):
{repo}

Run the suite, capture pass/fail counts and failing test ids, then call
`record_test_report` exactly once. Set passed=true ONLY if the suite actually
passed. Your FINAL action MUST be the `record_test_report` tool call.
"""


def create_tester() -> Agent:
    return Agent(
        name="verifier",
        model=model(config.MODEL_TESTER),
        description="Writes and runs tests in the sandbox, records the result.",
        instruction=_instruction,
        code_executor=maybe_code_executor(),
        tools=[
            SkillToolset(skills=[load_skill("test-authoring")]),
            *EDIT_TOOLS,
            record_test_report,
        ],
    )

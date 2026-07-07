"""Reviewer / Critic (TDD persona #6) — P2b.

Checks the implemented change (the working-tree diff) against the acceptance
criteria and surfaces security/lint findings before delivery. Records a
`ReviewVerdict`; the orchestrator gates Delivery on `approved`.
"""

from __future__ import annotations

from google.adk.agents import Agent
from google.adk.agents.readonly_context import ReadonlyContext

from app import config, schemas
from app.sub_agents._common import model
from app.tools.github_mcp import github_read
from app.tools.sandbox import read_repo_file, repo_diff, run_in_repo
from app.tools.state_tools import record_review_verdict


def _instruction(ctx: ReadonlyContext) -> str:
    spec = ctx.state.get(schemas.STATE_ISSUE_SPEC, {})
    plan = ctx.state.get(schemas.STATE_CHANGE_PLAN, {})
    report = ctx.state.get(schemas.STATE_TEST_REPORT, {})
    return f"""\
You are the Critic. Review the implemented change before it is delivered.

Inspect the actual change ONLY with the tools you have: `repo_diff` (the staged
diff), `read_repo_file` (file context), and `run_in_repo` for read-only commands
like `git status` or `git log`. Use the read-only GitHub security tools
(code_security, dependabot) to surface findings. Do not call any other tool.

Verify the change against each acceptance criterion and check that the tests
genuinely cover it.

CALIBRATION — block ONLY on blocking issues: an acceptance criterion is not met,
tests fail, the build is broken, or there is a real security problem. Treat
style nits (e.g. an extra/redundant test file, naming, formatting) as
non-blocking notes — record them in `criteria_results` but still set
approved=true. Note: only files written by the agent are committed, so build
artifacts (e.g. `__pycache__`) are NOT part of the change — do not reject for
those.

ISSUE SPEC:
{spec}

CHANGE PLAN:
{plan}

TEST REPORT:
{report}

Approve ONLY if every acceptance criterion is met and there are no blocking
issues. If not, list specific `required_changes`. Your FINAL action MUST be a
single `record_review_verdict` call.
"""


def create_reviewer() -> Agent:
    return Agent(
        name="critic",
        model=model(config.MODEL_REVIEWER),
        mode="single_turn",
        description="Validates the change against criteria and safety checks.",
        instruction=_instruction,
        tools=[repo_diff, read_repo_file, run_in_repo, github_read(), record_review_verdict],
    )

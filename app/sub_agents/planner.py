"""Planner / Architect (TDD persona #3).

Pure reasoning over the issue spec + repo context — no tools — so it can use
`output_schema=ChangePlan` and write the validated plan to state via
`output_key`. The orchestrator posts the plan to the issue thread (see callbacks).
"""

from __future__ import annotations

from google.adk.agents import Agent
from google.adk.agents.readonly_context import ReadonlyContext

from app import config, schemas
from app.sub_agents._common import model


def _instruction(ctx: ReadonlyContext) -> str:
    spec = ctx.state.get(schemas.STATE_ISSUE_SPEC, {})
    repo = ctx.state.get(schemas.STATE_REPO_CONTEXT, {})
    approval = ctx.state.get(schemas.STATE_PLAN_APPROVAL) or {}

    revision = ""
    if approval and not approval.get("approved", True):
        changes = "\n".join(f"- {c}" for c in approval.get("required_changes", []))
        revision = (
            "\n\nThe Plan Reviewer REJECTED your previous plan. Revise it to "
            f"address every required change:\n{changes}\n"
            f"(Reviewer rationale: {approval.get('rationale', '')})\n"
        )

    return f"""\
You are the Architect for an autonomous issue-to-PR workflow.

Convert the issue spec and repository context into a concrete change plan and a
test strategy. Be specific about which files change and how the change will be
verified. Flag anything risky (dependency changes, deletions, schema/migration
changes) in `risk_flags`.

ISSUE SPEC:
{spec}

REPO CONTEXT:
{repo}

Produce a ChangePlan. Keep it grounded in the actual files from the repo context;
do not invent paths.{revision}
"""


def create_planner() -> Agent:
    return Agent(
        name="architect",
        model=model(config.MODEL_PLANNER),
        mode="single_turn",
        description="Turns the spec + repo context into a concrete change plan.",
        instruction=_instruction,
        output_schema=schemas.ChangePlan,
        output_key=schemas.STATE_CHANGE_PLAN,
    )

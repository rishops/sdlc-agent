"""Plan Reviewer / Approver (P2a).

Replaces the human pre-implementation HITL gate (TDD §9 gate 1) with an agent
that judges the ChangePlan. Tool-less, so it can use ``output_schema`` to emit a
validated ``PlanApproval``. It is paired with the Planner in a bounded re-plan
loop (see ``app/agent.py``): on rejection, the planner revises using
``required_changes``; the loop exits when ``plan_approval.approved`` is true.
"""

from __future__ import annotations

from google.adk.agents import Agent
from google.adk.agents.readonly_context import ReadonlyContext

from app import config, schemas
from app.sub_agents._common import model


def _instruction(ctx: ReadonlyContext) -> str:
    spec = ctx.state.get(schemas.STATE_ISSUE_SPEC, {})
    repo = ctx.state.get(schemas.STATE_REPO_CONTEXT, {})
    plan = ctx.state.get(schemas.STATE_CHANGE_PLAN, {})
    return f"""\
You are the Plan Reviewer. Decide whether the proposed change plan is sound
enough to implement. Approve only if ALL of these hold:
- It is **grounded**: every file it names exists in the repo context; no invented
  paths.
- It is **complete**: ordered steps that plausibly satisfy every acceptance
  criterion, plus an explicit test strategy.
- It is **safe and in scope**: no unrequested scope creep; risky items
  (dependency changes, deletions, migrations) are flagged.

If it falls short, set approved=false and list specific, actionable
`required_changes` the planner must make. Be a strict but fair reviewer — do not
approve vague or ungrounded plans, and do not reject over trivial nits.

ISSUE SPEC:
{spec}

REPO CONTEXT:
{repo}

CHANGE PLAN:
{plan}

Produce a PlanApproval verdict.
"""


def create_plan_approver() -> Agent:
    return Agent(
        name="plan_reviewer",
        model=model(config.MODEL_PLAN_APPROVER),
        mode="single_turn",
        description="Auto-approves or rejects the change plan (no human gate).",
        instruction=_instruction,
        output_schema=schemas.PlanApproval,
        output_key=schemas.STATE_PLAN_APPROVAL,
    )

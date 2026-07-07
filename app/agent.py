"""Issue-to-PR orchestrator (root agent).

Milestone **P2b**: a custom ``BaseAgent`` orchestrator that runs the stages and
**routes** between them (the linear P1 ``SequentialAgent`` couldn't express the
branches):

    intake → (stop if not actionable)
           → repo_context
           → plan_and_approve loop  → (stop if plan not approved)
           → build loop             → (stop if tests not green)
           → reviewer               → (stop if not approved)
           → delivery (draft PR)

The orchestrator owns GitHub status side-effects via the ``app.callbacks``
before/after callbacks; its terminal label is derived from state by ``on_run_end``.

NOTE (ADK 2.0): ``LoopAgent`` is deprecated in favour of graph ``Workflow``; P2a
keeps it for the two bounded loops. Migrating to a graph ``Workflow`` is a later
task.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator

from google.adk.agents import BaseAgent, LoopAgent
from google.adk.agents.invocation_context import InvocationContext
from google.adk.apps import App
from google.adk.events import Event
from google.adk.plugins.logging_plugin import LoggingPlugin

from app import config, schemas
from app.callbacks import on_run_end, on_run_start
from app.plugins import BoundedGenerationPlugin, ToolErrorRecoveryPlugin
from app.sub_agents.coder import create_coder
from app.sub_agents.delivery import create_delivery
from app.sub_agents.intake import create_intake
from app.sub_agents.loop_checks import create_escalation_checker
from app.sub_agents.plan_approver import create_plan_approver
from app.sub_agents.planner import create_planner
from app.sub_agents.repo_context import create_repo_context
from app.sub_agents.reviewer import create_reviewer
from app.sub_agents.tester import create_tester

# Ensure Vertex AI env is configured before any model handle is built.
config.setup_vertex_env()


def _build_plan_and_approve() -> LoopAgent:
    """Planner ⇄ Approver loop; exits when ``plan_approval.approved`` is true."""
    return LoopAgent(
        name="plan_and_approve",
        description="Plans the change and auto-approves it (re-plans on rejection).",
        sub_agents=[
            create_planner(),
            create_plan_approver(),
            create_escalation_checker(
                "plan_gate", schemas.STATE_PLAN_APPROVAL, "approved"
            ),
        ],
        max_iterations=config.MAX_PLAN_ITERATIONS,
    )


def _build_build_loop() -> LoopAgent:
    """Coder ⇄ Tester loop; exits when ``test_report.passed`` is true."""
    return LoopAgent(
        name="build_and_verify",
        description="Implements and verifies the change until tests pass.",
        sub_agents=[
            create_coder(),
            create_tester(),
            create_escalation_checker(
                "build_gate", schemas.STATE_TEST_REPORT, "passed"
            ),
        ],
        max_iterations=config.MAX_LOOP_ITERATIONS,
    )


class IssueToPrOrchestrator(BaseAgent):
    """Custom orchestrator that sequences the stages and routes on state."""

    intake: BaseAgent
    repo_context: BaseAgent
    plan_and_approve: BaseAgent
    build_loop: BaseAgent
    reviewer: BaseAgent
    delivery: BaseAgent

    async def _run_async_impl(
        self, ctx: InvocationContext
    ) -> AsyncGenerator[Event, None]:
        state = ctx.session.state

        # 1. Intake → stop early if the issue isn't actionable.
        async for event in self.intake.run_async(ctx):
            yield event
        spec = state.get(schemas.STATE_ISSUE_SPEC) or {}
        if spec and not spec.get("is_actionable", True):
            return  # on_run_end posts the clarification request.

        # 2. Repo context.
        async for event in self.repo_context.run_async(ctx):
            yield event

        # 3. Plan and auto-approve (bounded re-plan loop).
        async for event in self.plan_and_approve.run_async(ctx):
            yield event
        approval = state.get(schemas.STATE_PLAN_APPROVAL) or {}
        if not approval.get("approved", False):
            return  # on_run_end posts needs-revision.

        # 4. Build loop (Coder ⇄ Tester) → stop if tests never went green.
        async for event in self.build_loop.run_async(ctx):
            yield event
        report = state.get(schemas.STATE_TEST_REPORT) or {}
        if not report.get("passed", False):
            return  # on_run_end posts build-failed.

        # 5. Review → stop if the Critic did not approve.
        async for event in self.reviewer.run_async(ctx):
            yield event
        verdict = state.get(schemas.STATE_REVIEW_VERDICT) or {}
        if not verdict.get("approved", False):
            return  # on_run_end posts changes-requested.

        # 6. Delivery: push a branch and open a DRAFT PR linked to the issue.
        async for event in self.delivery.run_async(ctx):
            yield event


def build_orchestrator() -> IssueToPrOrchestrator:
    # Construct each child once; pass as named fields AND in sub_agents (the
    # documented custom-agent pattern) so they are parented for tracing.
    intake = create_intake()
    repo_context = create_repo_context()
    plan_and_approve = _build_plan_and_approve()
    build_loop = _build_build_loop()
    reviewer = create_reviewer()
    delivery = create_delivery()
    return IssueToPrOrchestrator(
        name="issue_to_pr_orchestrator",
        description="Reads a labeled issue, plans + auto-approves, implements, reviews, and opens a draft PR.",
        intake=intake,
        repo_context=repo_context,
        plan_and_approve=plan_and_approve,
        build_loop=build_loop,
        reviewer=reviewer,
        delivery=delivery,
        sub_agents=[intake, repo_context, plan_and_approve, build_loop, reviewer, delivery],
        before_agent_callback=on_run_start,
        after_agent_callback=on_run_end,
    )


root_agent = build_orchestrator()

# LoggingPlugin traces every agent transition, tool call, and final response.
# BoundedGenerationPlugin caps output tokens so a degenerate model loop can't
# stream unbounded. ToolErrorRecoveryPlugin makes a hallucinated/unavailable
# tool call recoverable instead of crashing the run.
app = App(
    root_agent=root_agent,
    name="app",
    plugins=[LoggingPlugin(), BoundedGenerationPlugin(), ToolErrorRecoveryPlugin()],
)

"""Offline structural smoke tests for the P2a orchestration graph.

These do not call the model or the network — the end-to-end path is exercised by
`scripts/run_local_issue.py` / `adk web` against a real test repo.
"""

from __future__ import annotations


def test_app_registers_all_plugins() -> None:
    """The App must carry the plugins; the run path goes through it (not bare
    root_agent), else guardrails like ToolErrorRecoveryPlugin silently drop."""
    from app.agent import app

    names = {type(p).__name__ for p in app.plugins}
    assert {
        "LoggingPlugin",
        "BoundedGenerationPlugin",
        "ToolErrorRecoveryPlugin",
    } <= names


def test_run_pipeline_runs_through_app_with_plugins() -> None:
    """Regression: run_core must build its Runner from the App so plugins are
    active on the webhook/CLI path (this was the bug that made a hallucinated
    tool call fatal on the webhook run)."""
    import inspect

    from app import run_core

    src = inspect.getsource(run_core.run_pipeline)
    assert "Runner(app=app" in src
    assert "from app.agent import app" in src


def test_orchestrator_structure() -> None:
    from app.agent import IssueToPrOrchestrator, root_agent

    assert isinstance(root_agent, IssueToPrOrchestrator)
    assert [a.name for a in root_agent.sub_agents] == [
        "intake_analyst",
        "codebase_navigator",
        "plan_and_approve",
        "build_and_verify",
        "critic",
        "pr_author",
    ]


def test_delivery_has_git_and_write_tools() -> None:
    from google.adk.tools.mcp_tool import McpToolset

    from app.sub_agents.delivery import create_delivery

    tools = create_delivery().tools
    names = {getattr(t, "__name__", type(t).__name__) for t in tools}
    assert {"create_branch", "commit_all", "push_branch", "record_delivery_result"} <= names

    # The write MCP toolset must be scoped to a minimal allowlist — no
    # merge_pull_request etc., and no MCP create_branch (name collision).
    mcp = next(t for t in tools if isinstance(t, McpToolset))
    assert mcp.tool_filter == ["create_pull_request"]


def test_plan_and_build_loops_have_escalation_checkers() -> None:
    from app.agent import root_agent

    plan = root_agent.plan_and_approve
    build = root_agent.build_loop
    assert [a.name for a in plan.sub_agents] == ["architect", "plan_reviewer", "plan_gate"]
    assert [a.name for a in build.sub_agents] == ["implementer", "verifier", "build_gate"]


def test_plan_approver_is_tool_less_with_output_schema() -> None:
    from app.sub_agents.plan_approver import create_plan_approver

    approver = create_plan_approver()
    assert approver.output_schema is not None
    assert not approver.tools


def test_coder_has_no_host_code_executor_but_tester_records() -> None:
    from app.sub_agents.coder import create_coder
    from app.sub_agents.tester import create_tester

    # Host backend (conftest sets SANDBOX_BACKEND=host): edits go via tools.
    assert create_coder().code_executor is None
    tool_names = {getattr(t, "__name__", "") for t in create_tester().tools}
    assert "record_test_report" in tool_names


def test_escalation_checker_escalates_on_true_slot() -> None:
    import asyncio
    from types import SimpleNamespace

    from app.sub_agents.loop_checks import create_escalation_checker

    checker = create_escalation_checker("g", "test_report", "passed")

    async def _events(passed: bool):
        ctx = SimpleNamespace(session=SimpleNamespace(state={"test_report": {"passed": passed}}))
        return [e async for e in checker._run_async_impl(ctx)]

    escalated = asyncio.run(_events(True))
    not_escalated = asyncio.run(_events(False))
    assert escalated[0].actions.escalate is True
    assert not not_escalated[0].actions.escalate

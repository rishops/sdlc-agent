"""Unit tests for the data contracts and the structured-output recording tools."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app import schemas


def test_issue_spec_roundtrip() -> None:
    spec = schemas.IssueSpec(
        issue_id=12,
        repo="octo/demo",
        title="Add health endpoint",
        problem="No /health endpoint exists.",
        acceptance_criteria=["GET /health returns 200"],
        constraints=[],
        affected_area="api",
        is_actionable=True,
        clarification_needed="",
    )
    dumped = spec.model_dump()
    assert dumped["issue_id"] == 12
    assert schemas.IssueSpec(**dumped) == spec


def test_change_plan_defaults() -> None:
    plan = schemas.ChangePlan()
    assert plan.steps == []
    assert plan.files_to_change == []
    assert plan.risk_flags == []


def test_issue_spec_requires_actionable_flag() -> None:
    with pytest.raises(ValidationError):
        schemas.IssueSpec(issue_id=1, repo="a/b", title="t", problem="p")  # type: ignore[call-arg]


class _FakeToolContext:
    def __init__(self) -> None:
        self.state: dict = {}


def test_record_issue_spec_writes_state() -> None:
    from app.tools.state_tools import record_issue_spec

    ctx = _FakeToolContext()
    result = record_issue_spec(
        issue_id=1,
        repo="a/b",
        title="t",
        problem="p",
        acceptance_criteria=["x"],
        constraints=[],
        affected_area="core",
        is_actionable=True,
        clarification_needed="",
        tool_context=ctx,  # type: ignore[arg-type]
    )
    assert result["status"] == "success"
    assert ctx.state[schemas.STATE_ISSUE_SPEC]["repo"] == "a/b"

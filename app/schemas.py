"""Data contracts exchanged between agents (TDD section 5.4).

These Pydantic models are the *contract* between pipeline stages. Each is
written to a known session-state slot (``state["issue_spec"]`` etc.) so that
hand-offs are validated and traces/evals stay legible. Field descriptions are
sent to the model when a schema is used as ``output_schema``, so keep them
informative.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

# Canonical session-state slot names. Use these constants everywhere rather
# than bare strings so the contract is greppable and typo-proof.
# The run's trigger: {"repo": "owner/name", "issue_id": int}. Seeded at start.
STATE_ISSUE_REQUEST = "issue_request"
STATE_ISSUE_SPEC = "issue_spec"
STATE_REPO_CONTEXT = "repo_context"
STATE_CHANGE_PLAN = "change_plan"
STATE_PLAN_APPROVAL = "plan_approval"
STATE_TEST_REPORT = "test_report"
STATE_REVIEW_VERDICT = "review_verdict"
STATE_DELIVERY_RESULT = "delivery_result"


class IssueSpec(BaseModel):
    """Structured understanding of the triggering GitHub issue."""

    issue_id: int = Field(description="The GitHub issue number.")
    repo: str = Field(description="Target repository as 'owner/name'.")
    title: str = Field(description="Issue title.")
    problem: str = Field(description="Concise statement of the problem to solve.")
    acceptance_criteria: list[str] = Field(
        default_factory=list,
        description="Concrete, checkable conditions that define 'done'.",
    )
    constraints: list[str] = Field(
        default_factory=list,
        description="Constraints to respect (APIs to keep, files to avoid, etc.).",
    )
    affected_area: str = Field(
        default="",
        description="Best guess at the component/area of the codebase affected.",
    )
    is_actionable: bool = Field(
        description="True if the issue has enough detail for the agent to proceed."
    )
    clarification_needed: str = Field(
        default="",
        description="If not actionable, the single most important question to ask.",
    )


class RepoContext(BaseModel):
    """Map of the target repository, produced after cloning into the sandbox."""

    default_branch: str = Field(description="The repo's default branch (e.g. 'main').")
    languages: list[str] = Field(
        default_factory=list, description="Primary languages detected."
    )
    build_system: str = Field(
        default="", description="Build/package system (e.g. 'uv', 'npm', 'maven')."
    )
    test_command: str = Field(
        default="", description="Command that runs the test suite, if discovered."
    )
    relevant_paths: list[str] = Field(
        default_factory=list,
        description="Files/dirs most relevant to the issue.",
    )
    conventions_notes: str = Field(
        default="",
        description="Notable conventions (style, structure, test layout).",
    )


class ChangePlan(BaseModel):
    """The concrete plan of attack produced by the Planner."""

    steps: list[str] = Field(
        default_factory=list, description="Ordered implementation steps."
    )
    files_to_change: list[str] = Field(
        default_factory=list, description="Files expected to be created or edited."
    )
    test_strategy: str = Field(
        default="", description="How the change will be verified by tests."
    )
    risk_flags: list[str] = Field(
        default_factory=list,
        description="Risky aspects needing human attention (deps, deletions, etc.).",
    )


class PlanApproval(BaseModel):
    """The Plan Reviewer agent's verdict on a ChangePlan (replaces the human gate)."""

    approved: bool = Field(
        description="True if the plan is sound enough to implement as-is."
    )
    rationale: str = Field(
        description="Brief justification for the approve/reject decision."
    )
    required_changes: list[str] = Field(
        default_factory=list,
        description="Concrete revisions the planner must make (empty if approved).",
    )


class TestReport(BaseModel):
    """Structured result of running the test suite in the sandbox."""

    passed: bool = Field(description="True if the whole suite passed.")
    total: int = Field(default=0, description="Total number of tests executed.")
    failed: list[str] = Field(
        default_factory=list, description="Identifiers of failing tests."
    )
    logs_ref: str = Field(
        default="", description="Reference (artifact name/path) to full logs."
    )
    coverage: float | None = Field(
        default=None, description="Coverage percentage, if measured."
    )


class ReviewVerdict(BaseModel):
    """The Reviewer's judgement against acceptance criteria and safety checks."""

    approved: bool = Field(description="True if the change is ready to deliver.")
    criteria_results: list[str] = Field(
        default_factory=list,
        description="Per-acceptance-criterion pass/fail with brief rationale.",
    )
    security_findings: list[str] = Field(
        default_factory=list, description="Security/lint findings, if any."
    )
    required_changes: list[str] = Field(
        default_factory=list,
        description="Changes required before approval (empty if approved).",
    )


class DeliveryResult(BaseModel):
    """The outcome of the Delivery stage."""

    branch: str = Field(default="", description="Branch the change was pushed to.")
    pr_url: str = Field(default="", description="URL of the opened pull request.")
    status_label: str = Field(
        description="Terminal status label set on the issue (e.g. 'agent:needs-review')."
    )

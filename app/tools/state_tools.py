"""Tools that record validated structured output into session state.

ADK disables tool calling when an agent sets ``output_schema``. Intake and
RepoContext both need to *use tools* (GitHub MCP / sandbox) **and** produce a
structured contract object. The pattern: the agent calls one of these
``record_*`` tools as its final action; the tool validates the payload against
the Pydantic schema and writes it to the canonical state slot. Downstream agents
read that slot (directly or via ``{slot}`` instruction injection).
"""

from __future__ import annotations

from google.adk.tools import ToolContext
from pydantic import ValidationError

from app import schemas


def record_issue_spec(
    issue_id: int,
    repo: str,
    title: str,
    problem: str,
    acceptance_criteria: list[str],
    constraints: list[str],
    affected_area: str,
    is_actionable: bool,
    clarification_needed: str,
    tool_context: ToolContext,
) -> dict:
    """Record the structured issue spec. Call this once, as the final step.

    Args:
        issue_id: GitHub issue number.
        repo: Repository as 'owner/name'.
        title: Issue title.
        problem: Concise problem statement.
        acceptance_criteria: Checkable conditions defining done.
        constraints: Constraints to respect.
        affected_area: Best guess at the affected component/area.
        is_actionable: Whether there is enough detail to proceed.
        clarification_needed: The key question to ask if not actionable, else "".

    Returns:
        dict with 'status'.
    """
    try:
        spec = schemas.IssueSpec(
            issue_id=issue_id,
            repo=repo,
            title=title,
            problem=problem,
            acceptance_criteria=acceptance_criteria,
            constraints=constraints,
            affected_area=affected_area,
            is_actionable=is_actionable,
            clarification_needed=clarification_needed,
        )
    except ValidationError as e:
        return {"status": "error", "error": str(e)}
    tool_context.state[schemas.STATE_ISSUE_SPEC] = spec.model_dump()
    # Seed the run's issue target if it wasn't pre-seeded (e.g. a Dev UI run
    # where the issue came from the user's message rather than the dispatcher).
    if not tool_context.state.get(schemas.STATE_ISSUE_REQUEST):
        tool_context.state[schemas.STATE_ISSUE_REQUEST] = {
            "repo": repo,
            "issue_id": issue_id,
        }
    return {"status": "success", "recorded": schemas.STATE_ISSUE_SPEC}


def record_repo_context(
    default_branch: str,
    languages: list[str],
    build_system: str,
    test_command: str,
    relevant_paths: list[str],
    conventions_notes: str,
    tool_context: ToolContext,
) -> dict:
    """Record the structured repo context. Call this once, as the final step.

    Args:
        default_branch: The repo's default branch.
        languages: Primary languages detected.
        build_system: Build/package system in use.
        test_command: Command that runs the test suite, if found.
        relevant_paths: Files/dirs most relevant to the issue.
        conventions_notes: Notable conventions observed.

    Returns:
        dict with 'status'.
    """
    try:
        ctx = schemas.RepoContext(
            default_branch=default_branch,
            languages=languages,
            build_system=build_system,
            test_command=test_command,
            relevant_paths=relevant_paths,
            conventions_notes=conventions_notes,
        )
    except ValidationError as e:
        return {"status": "error", "error": str(e)}
    tool_context.state[schemas.STATE_REPO_CONTEXT] = ctx.model_dump()
    return {"status": "success", "recorded": schemas.STATE_REPO_CONTEXT}


def record_test_report(
    passed: bool,
    total: int,
    failed: list[str],
    logs_ref: str,
    coverage: float,
    tool_context: ToolContext,
) -> dict:
    """Record the result of running the test suite. Call after each test run.

    Args:
        passed: True only if the entire suite passed.
        total: Total number of tests executed.
        failed: Identifiers of failing tests (empty if all passed).
        logs_ref: Short reference to where full logs live (e.g. a command).
        coverage: Coverage percent if measured, else -1.

    Returns:
        dict with 'status'.
    """
    try:
        report = schemas.TestReport(
            passed=passed,
            total=total,
            failed=failed,
            logs_ref=logs_ref,
            coverage=None if coverage is None or coverage < 0 else coverage,
        )
    except ValidationError as e:
        return {"status": "error", "error": str(e)}
    tool_context.state[schemas.STATE_TEST_REPORT] = report.model_dump()
    return {"status": "success", "recorded": schemas.STATE_TEST_REPORT, "passed": passed}


def record_review_verdict(
    approved: bool,
    criteria_results: list[str],
    security_findings: list[str],
    required_changes: list[str],
    tool_context: ToolContext,
) -> dict:
    """Record the review verdict. Call this once, as the final step.

    Args:
        approved: True only if the change is ready to deliver.
        criteria_results: Per-acceptance-criterion pass/fail with brief rationale.
        security_findings: Security/lint findings, if any.
        required_changes: Changes required before approval (empty if approved).

    Returns:
        dict with 'status'.
    """
    try:
        verdict = schemas.ReviewVerdict(
            approved=approved,
            criteria_results=criteria_results,
            security_findings=security_findings,
            required_changes=required_changes,
        )
    except ValidationError as e:
        return {"status": "error", "error": str(e)}
    tool_context.state[schemas.STATE_REVIEW_VERDICT] = verdict.model_dump()
    return {"status": "success", "recorded": schemas.STATE_REVIEW_VERDICT, "approved": approved}


def record_delivery_result(
    branch: str,
    pr_url: str,
    status_label: str,
    tool_context: ToolContext,
) -> dict:
    """Record the delivery outcome (branch, PR url). Call after opening the PR.

    Args:
        branch: Branch the change was pushed to.
        pr_url: URL of the opened draft pull request.
        status_label: Terminal status label (e.g. 'agent:needs-review').

    Returns:
        dict with 'status'.
    """
    try:
        result = schemas.DeliveryResult(
            branch=branch, pr_url=pr_url, status_label=status_label
        )
    except ValidationError as e:
        return {"status": "error", "error": str(e)}
    tool_context.state[schemas.STATE_DELIVERY_RESULT] = result.model_dump()
    return {"status": "success", "recorded": schemas.STATE_DELIVERY_RESULT}

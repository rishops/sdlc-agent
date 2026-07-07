"""Orchestrator side-effects: GitHub status updates and budget tracking.

Attached to the root pipeline as before/after agent callbacks. Status writes
are best-effort and gated on a real issue request being present in state, so the
pipeline still loads cleanly in `adk web` without a configured issue. Set
``POST_STATUS=false`` in the env to disable all status writes (e.g. dry runs).
"""

from __future__ import annotations

import asyncio
import logging
import os
import time

from google.adk.agents.callback_context import CallbackContext

from app import schemas
from app.tools import github_status

logger = logging.getLogger("sdlc_agent.orchestrator")


def _post_status_enabled() -> bool:
    return os.environ.get("POST_STATUS", "true").strip().lower() != "false"


def _issue_target(ctx: CallbackContext) -> tuple[str, int] | None:
    req = ctx.state.get(schemas.STATE_ISSUE_REQUEST) or {}
    repo, issue_id = req.get("repo"), req.get("issue_id")
    if repo and isinstance(issue_id, int):
        return repo, issue_id
    return None


async def on_run_start(callback_context: CallbackContext) -> None:
    """Record run start and post the 'picked up' ack + in-progress label.

    The parameter MUST be named ``callback_context`` — ADK passes it by keyword
    and enforces the name (see base_agent.py).
    """
    callback_context.state["temp:run_started_at"] = time.time()
    if not _post_status_enabled():
        return
    target = _issue_target(callback_context)
    if not target:
        return
    repo, issue_id = target
    result = await asyncio.to_thread(
        github_status.post_comment,
        repo,
        issue_id,
        "🤖 Picked up this issue — analysing the requirement and the codebase now.",
    )
    await asyncio.to_thread(github_status.add_labels, repo, issue_id, ["agent:in-progress"])
    logger.info("Posted ack to %s#%s: %s", repo, issue_id, result)


async def on_run_end(callback_context: CallbackContext) -> None:
    """Post the change plan to the issue and set a terminal P1 status label.

    The parameter MUST be named ``callback_context`` (enforced by ADK).
    """
    state = callback_context.state
    started = state.get("temp:run_started_at")
    elapsed = round(time.time() - started, 1) if started else None
    if elapsed is not None:
        state["temp:run_elapsed_s"] = elapsed

    plan = state.get(schemas.STATE_CHANGE_PLAN)
    spec = state.get(schemas.STATE_ISSUE_SPEC) or {}
    approval = state.get(schemas.STATE_PLAN_APPROVAL) or {}
    report = state.get(schemas.STATE_TEST_REPORT) or {}
    verdict = state.get(schemas.STATE_REVIEW_VERDICT) or {}
    delivery = state.get(schemas.STATE_DELIVERY_RESULT) or {}

    # Observability: always log how the run ended and which state slots the
    # agents actually populated (warn on a broken contract). This makes "did it
    # finish / did each agent record its output" unambiguous in the logs.
    present = {
        slot: (slot in state and state.get(slot) is not None)
        for slot in (
            schemas.STATE_ISSUE_SPEC,
            schemas.STATE_REPO_CONTEXT,
            schemas.STATE_CHANGE_PLAN,
            schemas.STATE_PLAN_APPROVAL,
            schemas.STATE_TEST_REPORT,
        )
    }
    logger.info(
        "Run complete in %ss — slots: %s | plan_approved=%s tests_passed=%s "
        "review_approved=%s pr=%s",
        elapsed,
        present,
        approval.get("approved"),
        report.get("passed"),
        verdict.get("approved"),
        delivery.get("pr_url"),
    )

    if not _post_status_enabled():
        logger.info("POST_STATUS disabled — skipping GitHub status writes.")
        return
    target = _issue_target(callback_context)
    if not target:
        logger.info("No issue target in state — skipping GitHub status writes.")
        return
    repo, issue_id = target

    # Not actionable → clarification gate.
    if spec and not spec.get("is_actionable", True):
        question = spec.get("clarification_needed", "")
        comment = f"🤖 I need a clarification before I can plan this:\n\n> {question}"
        label = "agent:needs-clarification"
    elif not plan:
        logger.warning("No change_plan in state — nothing to post to %s#%s.", repo, issue_id)
        return
    elif not approval.get("approved", False):
        # Plan reviewer rejected (or the re-plan loop exhausted its budget).
        changes = "\n".join(f"- {c}" for c in approval.get("required_changes", []))
        comment = (
            _format_plan(plan)
            + "\n\n### ❌ Plan not approved\n"
            + f"{approval.get('rationale', '')}\n\n"
            + (f"**Required changes**\n{changes}\n" if changes else "")
        )
        label = "agent:plan-needs-revision"
    elif not report.get("passed"):
        failed = ", ".join(report.get("failed", [])) or "see logs"
        comment = (
            _format_plan(plan)
            + "\n\n### ⚠️ Plan auto-approved but the build did not go green\n"
            + f"Tests still failing after the budget: {failed}."
        )
        label = "agent:build-failed"
    elif delivery.get("pr_url"):
        # Success: reviewed change delivered as a draft PR. A human merges.
        comment = (
            "### ✅ Change delivered as a draft PR\n"
            + f"Branch `{delivery.get('branch', '')}` → {delivery['pr_url']}\n\n"
            + f"Ran {report.get('total', 0)} test(s); all passed. "
            + "Opened as a **draft** — a human reviews and merges."
        )
        label = "agent:needs-review"
    elif verdict and not verdict.get("approved", False):
        changes = "\n".join(f"- {c}" for c in verdict.get("required_changes", []))
        comment = (
            "### 🔍 Reviewer requested changes\n"
            + (f"**Required changes**\n{changes}\n" if changes else "")
            + "\n".join(f"- {f}" for f in verdict.get("security_findings", []))
        )
        label = "agent:changes-requested"
    else:
        # Tests green and (review approved or not yet run) but no PR recorded.
        comment = (
            _format_plan(plan)
            + "\n\n### ✅ Implemented — tests are green\n"
            + f"Ran {report.get('total', 0)} test(s); all passed, but no pull "
            + "request was opened (delivery did not complete)."
        )
        label = "agent:delivery-failed" if verdict.get("approved") else "agent:tests-green"

    result = await asyncio.to_thread(github_status.post_comment, repo, issue_id, comment)
    await asyncio.to_thread(github_status.add_labels, repo, issue_id, [label])
    logger.info("Posted terminal status '%s' to %s#%s: %s", label, repo, issue_id, result)


def _format_plan(plan: dict) -> str:
    steps = "\n".join(f"{i}. {s}" for i, s in enumerate(plan.get("steps", []), 1))
    files = "\n".join(f"- `{f}`" for f in plan.get("files_to_change", []))
    risks = "\n".join(f"- ⚠️ {r}" for r in plan.get("risk_flags", []))
    return (
        "## 🤖 Change plan\n\n"
        f"**Steps**\n{steps or '_none_'}\n\n"
        f"**Files to change**\n{files or '_none_'}\n\n"
        f"**Test strategy**\n{plan.get('test_strategy', '_none_')}\n\n"
        f"**Risk flags**\n{risks or '_none_'}"
    )

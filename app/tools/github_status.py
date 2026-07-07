"""Narrow, deterministic GitHub status writes owned by the orchestrator.

The orchestrator (not the LLM) posts the "picked up" acknowledgment, status
labels, and the plan comment. Keeping these as deterministic REST calls makes
them auditable and idempotent, and reserves the write-scoped MCP toolset for the
Delivery persona only (TDD section 6). All functions are best-effort and return
a status dict — they never raise into the pipeline.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request

from app import config

_API = "https://api.github.com"


def _request(method: str, path: str, payload: dict | None) -> dict:
    try:
        token = config.github_token()
    except RuntimeError as e:
        return {"status": "skipped", "reason": str(e)}

    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(f"{_API}{path}", data=data, method=method)
    req.add_header("Authorization", f"Bearer {token}")
    req.add_header("Accept", "application/vnd.github+json")
    req.add_header("X-GitHub-Api-Version", "2022-11-28")
    req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return {"status": "success", "code": resp.status}
    except urllib.error.HTTPError as e:
        return {"status": "error", "code": e.code, "error": e.reason}
    except Exception as e:  # best effort: never let a status write break the run
        return {"status": "error", "error": str(e)}


def post_comment(repo: str, issue_id: int, body: str) -> dict:
    """Post a comment on an issue. repo is 'owner/name'."""
    return _request("POST", f"/repos/{repo}/issues/{issue_id}/comments", {"body": body})


def add_labels(repo: str, issue_id: int, labels: list[str]) -> dict:
    """Add one or more labels to an issue."""
    return _request(
        "POST", f"/repos/{repo}/issues/{issue_id}/labels", {"labels": labels}
    )

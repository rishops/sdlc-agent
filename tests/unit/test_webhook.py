"""Unit tests for the GitHub webhook ingress (HMAC, filtering, dedupe, routes)."""

from __future__ import annotations

import hashlib
import hmac
import json

import pytest
from fastapi.testclient import TestClient

from app import config, webhook

SECRET = "test-secret"


def _sign(body: bytes) -> str:
    return "sha256=" + hmac.new(SECRET.encode(), body, hashlib.sha256).hexdigest()


@pytest.fixture(autouse=True)
def _set_secret(monkeypatch):
    monkeypatch.setattr(config, "GITHUB_WEBHOOK_SECRET", SECRET)
    monkeypatch.setattr(config, "TRIGGER_LABEL", "agent:build")
    webhook._seen_deliveries.clear()
    # Make the background run a no-op so endpoint tests never start a real run.
    async def _noop(repo, issue_id):
        return {}
    monkeypatch.setattr(webhook, "run_pipeline", _noop)


def test_verify_signature_valid_and_invalid() -> None:
    body = b'{"a":1}'
    assert webhook.verify_signature(body, _sign(body), SECRET) is True
    assert webhook.verify_signature(body, "sha256=deadbeef", SECRET) is False
    assert webhook.verify_signature(body, None, SECRET) is False
    assert webhook.verify_signature(body, _sign(body), "") is False  # no secret → fail closed


def test_parse_trigger() -> None:
    payload = {
        "action": "labeled",
        "label": {"name": "agent:build"},
        "repository": {"full_name": "octo/demo"},
        "issue": {"number": 7},
    }
    assert webhook.parse_trigger("issues", payload) == ("octo/demo", 7)
    # wrong label
    assert webhook.parse_trigger("issues", {**payload, "label": {"name": "bug"}}) is None
    # wrong action
    assert webhook.parse_trigger("issues", {**payload, "action": "opened"}) is None
    # wrong event type
    assert webhook.parse_trigger("pull_request", payload) is None


def _client() -> TestClient:
    return TestClient(webhook.app)


def test_endpoint_rejects_bad_signature() -> None:
    body = json.dumps({"action": "labeled"}).encode()
    r = _client().post(
        "/webhook/github",
        content=body,
        headers={"X-GitHub-Event": "issues", "X-Hub-Signature-256": "sha256=bad"},
    )
    assert r.status_code == 401


def test_endpoint_ignores_non_trigger() -> None:
    body = json.dumps({"action": "opened", "issue": {"number": 1}}).encode()
    r = _client().post(
        "/webhook/github",
        content=body,
        headers={"X-GitHub-Event": "issues", "X-Hub-Signature-256": _sign(body)},
    )
    assert r.status_code == 200 and r.json()["status"] == "ignored"


def test_endpoint_accepts_and_dedupes_trigger() -> None:
    payload = {
        "action": "labeled",
        "label": {"name": "agent:build"},
        "repository": {"full_name": "octo/demo"},
        "issue": {"number": 7},
    }
    body = json.dumps(payload).encode()
    headers = {
        "X-GitHub-Event": "issues",
        "X-Hub-Signature-256": _sign(body),
        "X-GitHub-Delivery": "delivery-1",
    }
    client = _client()
    r1 = client.post("/webhook/github", content=body, headers=headers)
    assert r1.status_code == 202 and r1.json()["status"] == "accepted"
    # same delivery id → deduped
    r2 = client.post("/webhook/github", content=body, headers=headers)
    assert r2.status_code == 200 and r2.json()["status"] == "duplicate"

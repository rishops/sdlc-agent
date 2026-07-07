"""Event-driven GitHub webhook ingress (local-first stand-in for the Dispatcher).

Realises the original intent: labeling an issue ``agent:build`` triggers the
workflow — no chat. The handler verifies the HMAC signature, filters to the
trigger event, dedupes on the delivery id, returns 202 immediately (no model
call on the hot path), and runs the pipeline in a background task.

Run locally:
    uv run uvicorn app.webhook:app --port 8080
then expose with a tunnel (e.g. `npx smee-client -u <url> -t http://localhost:8080/webhook/github`)
and add a repo webhook (Issues events, the shared secret).
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import logging

from fastapi import FastAPI, Request, Response

from app import config
from app.run_core import run_pipeline

# Configure logging so background-run progress (our loggers + ADK's LoggingPlugin)
# is visible in the uvicorn terminal. `adk web` does this for you; a bare uvicorn
# process does not, so an async run would otherwise execute silently.
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)

logger = logging.getLogger("sdlc_agent.webhook")

app = FastAPI(title="sdlc-agent webhook ingress")

# In-memory delivery-id dedupe (local stand-in for Pub/Sub idempotency).
_seen_deliveries: set[str] = set()
_MAX_SEEN = 1000

# Keep references to background run tasks so they aren't garbage-collected.
_running: set[asyncio.Task] = set()


def verify_signature(body: bytes, signature_header: str | None, secret: str) -> bool:
    """Verify GitHub's ``X-Hub-Signature-256`` HMAC. Fails closed."""
    if not secret or not signature_header or not signature_header.startswith("sha256="):
        return False
    expected = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature_header.removeprefix("sha256="))


def parse_trigger(event_type: str | None, payload: dict) -> tuple[str, int] | None:
    """Return (repo, issue_id) if this is a qualifying trigger event, else None.

    Trigger = an ``issues`` event with action ``labeled`` whose label is the
    configured ``TRIGGER_LABEL``.
    """
    if event_type != "issues" or payload.get("action") != "labeled":
        return None
    if (payload.get("label") or {}).get("name") != config.TRIGGER_LABEL:
        return None
    repo = (payload.get("repository") or {}).get("full_name")
    issue_id = (payload.get("issue") or {}).get("number")
    if repo and isinstance(issue_id, int):
        return repo, issue_id
    return None


def _already_seen(delivery_id: str | None) -> bool:
    if not delivery_id:
        return False
    if delivery_id in _seen_deliveries:
        return True
    if len(_seen_deliveries) >= _MAX_SEEN:
        _seen_deliveries.clear()
    _seen_deliveries.add(delivery_id)
    return False


async def _run_in_background(repo: str, issue_id: int) -> None:
    try:
        await run_pipeline(repo, issue_id)
    except Exception:  # never let a background failure crash the server
        logger.exception("Pipeline run failed for %s#%s", repo, issue_id)


@app.get("/healthz")
async def healthz() -> dict:
    return {"status": "ok"}


@app.post("/webhook/github")
async def github_webhook(request: Request) -> Response:
    body = await request.body()
    if not verify_signature(
        body, request.headers.get("X-Hub-Signature-256"), config.GITHUB_WEBHOOK_SECRET
    ):
        return Response(content='{"status":"bad signature"}', status_code=401,
                        media_type="application/json")

    payload = await request.json()
    target = parse_trigger(request.headers.get("X-GitHub-Event"), payload)
    if target is None:
        return Response(content='{"status":"ignored"}', status_code=200,
                        media_type="application/json")

    delivery_id = request.headers.get("X-GitHub-Delivery")
    if _already_seen(delivery_id):
        return Response(content='{"status":"duplicate"}', status_code=200,
                        media_type="application/json")

    repo, issue_id = target
    logger.info("Accepted trigger for %s#%s (delivery %s)", repo, issue_id, delivery_id)
    task = asyncio.create_task(_run_in_background(repo, issue_id))
    _running.add(task)
    task.add_done_callback(_running.discard)

    # 202: accepted, work happens asynchronously (no model call on the hot path).
    return Response(content='{"status":"accepted"}', status_code=202,
                    media_type="application/json")

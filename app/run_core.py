"""Reusable entry point for running the issue-to-PR pipeline on one issue.

Shared by the CLI (`scripts/run_local_issue.py`) and the event-driven webhook
(`app/webhook.py`): seed the run's issue target into a fresh session and drive
the orchestrator to completion. Each issue runs in its own ephemeral session.
"""

from __future__ import annotations

import uuid


async def run_pipeline(repo: str, issue_id: int, *, verbose: bool = False) -> dict:
    """Run the full pipeline for one issue; return the final session state.

    Args:
        repo: Target repository as 'owner/name'.
        issue_id: The issue number to act on.
        verbose: If True, print the event stream (used by the CLI).

    Returns:
        The final session ``state`` dict (includes the recorded slots such as
        ``delivery_result``).
    """
    from google.adk.runners import Runner
    from google.adk.sessions import InMemorySessionService
    from google.genai import types

    from app import schemas

    # Run through the App (not bare root_agent) so the plugins registered on it
    # — LoggingPlugin, BoundedGenerationPlugin, ToolErrorRecoveryPlugin — are
    # active on the webhook/CLI path exactly as they are under `adk web`.
    from app.agent import app

    app_name = app.name
    target = {"repo": repo, "issue_id": int(issue_id)}
    user_id, session_id = "trigger", str(uuid.uuid4())
    session_service = InMemorySessionService()
    await session_service.create_session(
        app_name=app_name,
        user_id=user_id,
        session_id=session_id,
        state={schemas.STATE_ISSUE_REQUEST: target},
    )
    runner = Runner(app=app, session_service=session_service)
    trigger = types.Content(
        role="user",
        parts=[types.Part.from_text(text=f"Process issue {target}.")],
    )

    async for event in runner.run_async(
        user_id=user_id, session_id=session_id, new_message=trigger
    ):
        if verbose and event.content and event.content.parts:
            author = getattr(event, "author", "?")
            for part in event.content.parts:
                if getattr(part, "text", None):
                    print(f"[{author}] {part.text.strip()[:500]}")
                if getattr(part, "function_call", None):
                    print(f"[{author}] -> tool: {part.function_call.name}")

    session = await session_service.get_session(
        app_name=app_name, user_id=user_id, session_id=session_id
    )
    return dict(session.state)

"""Local trigger simulation — the CLI stand-in for the production Dispatcher.

Seeds an issue target and drives the pipeline via the shared ``run_pipeline``
(see ``app/run_core.py``), printing the event stream and the final state slots.

Usage:
    uv run python scripts/run_local_issue.py --repo owner/name --issue 12
    uv run python scripts/run_local_issue.py --fixture fixtures/issues/example.json
    # add --no-status to skip posting comments/labels to GitHub
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()  # load .env before importing the agent (model/creds/flags)


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Run the issue-to-PR pipeline locally.")
    p.add_argument("--repo", help="Target repo as owner/name.")
    p.add_argument("--issue", type=int, help="Issue number to act on.")
    p.add_argument("--fixture", help="Path to a JSON fixture with repo/issue_id.")
    p.add_argument(
        "--no-status",
        action="store_true",
        help="Disable posting status comments/labels to GitHub.",
    )
    return p.parse_args()


def _resolve_target(args: argparse.Namespace) -> dict:
    if args.fixture:
        data = json.loads(Path(args.fixture).read_text())
        return {"repo": data["repo"], "issue_id": int(data["issue_id"])}
    if args.repo and args.issue is not None:
        return {"repo": args.repo, "issue_id": args.issue}
    raise SystemExit("Provide --fixture, or both --repo and --issue.")


async def _run(target: dict) -> None:
    from app import schemas
    from app.run_core import run_pipeline

    print(f"\n=== Running pipeline for {target} ===\n")
    state = await run_pipeline(target["repo"], target["issue_id"], verbose=True)

    print("\n=== Final state slots ===")
    for key in (
        schemas.STATE_ISSUE_SPEC,
        schemas.STATE_REPO_CONTEXT,
        schemas.STATE_CHANGE_PLAN,
        schemas.STATE_REVIEW_VERDICT,
        schemas.STATE_DELIVERY_RESULT,
    ):
        print(f"\n--- {key} ---")
        print(json.dumps(state.get(key), indent=2, default=str))
    elapsed = state.get("temp:run_elapsed_s")
    if elapsed is not None:
        print(f"\nElapsed: {elapsed}s")


def main() -> None:
    args = _parse_args()
    if args.no_status:
        os.environ["POST_STATUS"] = "false"
    target = _resolve_target(args)
    asyncio.run(_run(target))


if __name__ == "__main__":
    main()

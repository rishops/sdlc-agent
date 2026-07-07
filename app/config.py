"""Environment-driven configuration for the issue-to-PR workflow.

Everything that varies between local verification and a future cloud deployment
is funneled through here: model selection, the sandbox backend flag, GitHub
credentials, and per-run budget/repo policy. Values are read from the
environment (a local ``.env`` is loaded automatically by the ADK CLI and by
``scripts/run_local_issue.py``).
"""

from __future__ import annotations

import os
from functools import cache

from dotenv import load_dotenv

# Load .env (project root) on import so `adk web`, scripts, and tests all see
# the same configuration. Existing process env always takes precedence.
load_dotenv()


def _env(key: str, default: str = "") -> str:
    return os.environ.get(key, default)


# --- Vertex AI credentials -------------------------------------------------
# The user selected Vertex AI for local development. We mirror the
# Agent-Starter-Pack pattern but keep it overridable via .env so the same code
# runs locally and (later) on Agent Runtime without edits.
def setup_vertex_env() -> None:
    """Populate the Vertex AI env vars expected by google-genai, if unset.

    Safe to call multiple times. Falls back to Application Default Credentials
    for the project id only when ``GOOGLE_CLOUD_PROJECT`` is not already set,
    so a missing ADC setup does not crash module import.
    """
    os.environ.setdefault("GOOGLE_GENAI_USE_VERTEXAI", _env("GOOGLE_GENAI_USE_VERTEXAI", "True"))
    os.environ.setdefault("GOOGLE_CLOUD_LOCATION", _env("GOOGLE_CLOUD_LOCATION", "global"))
    if not os.environ.get("GOOGLE_CLOUD_PROJECT"):
        try:
            import google.auth

            _, project_id = google.auth.default()
            if project_id:
                os.environ["GOOGLE_CLOUD_PROJECT"] = project_id
        except Exception:
            # No ADC available (e.g. pure offline schema/unit tests). Agents
            # that actually call the model will surface a clear auth error.
            pass


# --- Model selection (per-role, per TDD Q5) --------------------------------
# Defaults to the flash model the scaffold shipped (known-good on Vertex).
# Reasoning-heavy roles can be upgraded to gemini-3-pro-preview via .env.
MODEL_INTAKE = _env("MODEL_INTAKE", "gemini-3-flash-preview")
MODEL_REPO_CONTEXT = _env("MODEL_REPO_CONTEXT", "gemini-3-flash-preview")
MODEL_PLANNER = _env("MODEL_PLANNER", "gemini-3-flash-preview")
MODEL_PLAN_APPROVER = _env("MODEL_PLAN_APPROVER", "gemini-3-flash-preview")
MODEL_CODER = _env("MODEL_CODER", "gemini-3-flash-preview")
MODEL_TESTER = _env("MODEL_TESTER", "gemini-3-flash-preview")
MODEL_REVIEWER = _env("MODEL_REVIEWER", "gemini-3-flash-preview")
MODEL_DELIVERY = _env("MODEL_DELIVERY", "gemini-3-flash-preview")


# --- Sandbox backend (host vs Agent Runtime) -------------------------------
# "host"         -> run git/build/test on the local machine (offline, no GCP).
# "agent_engine" -> AgentEngineSandboxCodeExecutor against a GCP sandbox.
SANDBOX_BACKEND = _env("SANDBOX_BACKEND", "host").strip().lower()

# Only used when SANDBOX_BACKEND == "agent_engine".
SANDBOX_AGENT_ENGINE_RESOURCE = _env("SANDBOX_AGENT_ENGINE_RESOURCE", "")
SANDBOX_RESOURCE_NAME = _env("SANDBOX_RESOURCE_NAME", "")

# Root directory for host-backend working trees.
HOST_WORKDIR_ROOT = _env("HOST_WORKDIR_ROOT", "")  # "" -> system temp dir


# --- GitHub access ---------------------------------------------------------
GITHUB_TOKEN = _env("GITHUB_TOKEN", "")
GH_MCP_URL = _env("GH_MCP_URL", "https://api.githubcopilot.com/mcp/")

# Shared secret used to verify the GitHub webhook HMAC (X-Hub-Signature-256).
GITHUB_WEBHOOK_SECRET = _env("GITHUB_WEBHOOK_SECRET", "")

# The single repo this workflow is authorized to act on (owner/name) and the
# label that triggers a run. Per-repo policy lives here for v1 (single repo).
TARGET_REPO = _env("TARGET_REPO", "")  # e.g. "octocat/hello-world"
TRIGGER_LABEL = _env("TRIGGER_LABEL", "agent:build")

# Commit identity used by Delivery when committing the agent's change.
GIT_AUTHOR_NAME = _env("GIT_AUTHOR_NAME", "sdlc-agent[bot]")
GIT_AUTHOR_EMAIL = _env("GIT_AUTHOR_EMAIL", "sdlc-agent@users.noreply.github.com")


# --- Budget / loop bounds --------------------------------------------------
MAX_PLAN_ITERATIONS = int(_env("MAX_PLAN_ITERATIONS", "2"))
MAX_LOOP_ITERATIONS = int(_env("MAX_LOOP_ITERATIONS", "4"))

# Per-turn generation bounds. The token cap stops a degenerate model loop (the
# model repeating a token forever) from streaming unbounded. Applied globally by
# BoundedGenerationPlugin only when an agent hasn't set its own value.
MAX_OUTPUT_TOKENS = int(_env("MAX_OUTPUT_TOKENS", "8192"))
DEFAULT_TEMPERATURE = float(_env("DEFAULT_TEMPERATURE", "0.5"))
MAX_RUN_WALL_SECONDS = int(_env("MAX_RUN_WALL_SECONDS", "1800"))


@cache
def github_token() -> str:
    """Return the GitHub token, raising a clear error if it is missing.

    Centralised so the token is never interpolated into prompts/logs — callers
    pass it straight into the MCP connection headers.
    """
    if not GITHUB_TOKEN:
        raise RuntimeError(
            "GITHUB_TOKEN is not set. Add a fine-grained PAT (scoped to the test "
            "repo) to your .env before running the workflow."
        )
    return GITHUB_TOKEN

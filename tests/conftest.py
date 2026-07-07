"""Pytest fixtures: provide harmless dummy env so the agent graph imports
offline (no real credentials or network needed for structural tests)."""

import os

os.environ.setdefault("GITHUB_TOKEN", "test-token")
os.environ.setdefault("GOOGLE_CLOUD_PROJECT", "test-project")
os.environ.setdefault("GOOGLE_GENAI_USE_VERTEXAI", "True")
os.environ.setdefault("POST_STATUS", "false")
os.environ.setdefault("SANDBOX_BACKEND", "host")

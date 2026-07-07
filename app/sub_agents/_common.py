"""Shared helpers for sub-agent construction."""

from __future__ import annotations

from pathlib import Path

from google.adk.models import Gemini
from google.adk.skills import Skill, load_skill_from_dir
from google.genai import types

# app/skills/<skill-name>/SKILL.md
SKILLS_DIR = Path(__file__).resolve().parent.parent / "skills"


def model(model_name: str) -> Gemini:
    """Build a Gemini model handle with the project's standard retry policy."""
    return Gemini(
        model=model_name,
        retry_options=types.HttpRetryOptions(attempts=3),
    )


def load_skill(name: str) -> Skill:
    """Load an ADK Skill from ``app/skills/<name>``."""
    return load_skill_from_dir(SKILLS_DIR / name)

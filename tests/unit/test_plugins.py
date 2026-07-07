"""Unit tests for the bounded-generation plugin."""

from __future__ import annotations

import asyncio

from google.adk.models.llm_request import LlmRequest
from google.genai import types as genai_types

from app import config
from app.plugins import BoundedGenerationPlugin


def _run(req: LlmRequest) -> LlmRequest:
    plugin = BoundedGenerationPlugin()
    asyncio.run(plugin.before_model_callback(callback_context=None, llm_request=req))
    return req


def test_caps_unset_output_tokens_and_temperature() -> None:
    req = LlmRequest(config=genai_types.GenerateContentConfig())
    _run(req)
    assert req.config.max_output_tokens == config.MAX_OUTPUT_TOKENS
    assert req.config.temperature == config.DEFAULT_TEMPERATURE


def test_preserves_explicit_agent_config() -> None:
    req = LlmRequest(
        config=genai_types.GenerateContentConfig(max_output_tokens=256, temperature=0.1)
    )
    _run(req)
    assert req.config.max_output_tokens == 256  # not overridden
    assert req.config.temperature == 0.1


def test_tool_error_recovery_for_unknown_tool() -> None:
    import asyncio

    from google.adk.tools import BaseTool

    from app.plugins import ToolErrorRecoveryPlugin

    plugin = ToolErrorRecoveryPlugin()

    async def _call(tool):
        return await plugin.on_tool_error_callback(
            tool=tool, tool_args={}, tool_context=None, error=ValueError("x")
        )

    # Unknown tool → recoverable error dict (run continues).
    not_found = BaseTool(name="run_in_repo", description="Tool not found")
    res = asyncio.run(_call(not_found))
    assert res["status"] == "error" and "run_in_repo" in res["error"]

    # A real tool's execution error → propagate (return None).
    real = BaseTool(name="commit_all", description="Commit the change")
    assert asyncio.run(_call(real)) is None

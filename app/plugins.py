"""Cross-cutting plugins registered on the App."""

from __future__ import annotations

from typing import Any

from google.adk.models.llm_request import LlmRequest
from google.adk.plugins.base_plugin import BasePlugin
from google.adk.tools import BaseTool, ToolContext
from google.genai import types as genai_types

from app import config

# ADK sets this description on the placeholder tool when a function call names a
# tool that isn't registered (see flows/llm_flows/functions.py).
_TOOL_NOT_FOUND = "Tool not found"


class BoundedGenerationPlugin(BasePlugin):
    """Cap every LLM turn so a degenerate generation can't stream unbounded.

    Models occasionally fall into a repetition loop (emitting the same token
    forever). Without a ``max_output_tokens`` ceiling that stream is unbounded
    and floods/hangs the run. This plugin sets a cap (and a modest temperature)
    on every request — but only when the agent hasn't already set its own, so
    intentional per-agent config is preserved.
    """

    def __init__(self) -> None:
        super().__init__(name="bounded_generation")

    async def before_model_callback(
        self, *, callback_context, llm_request: LlmRequest
    ):
        cfg = llm_request.config
        if cfg is None:
            cfg = genai_types.GenerateContentConfig()
            llm_request.config = cfg
        if cfg.max_output_tokens is None:
            cfg.max_output_tokens = config.MAX_OUTPUT_TOKENS
        if cfg.temperature is None:
            cfg.temperature = config.DEFAULT_TEMPERATURE
        return None


class ToolErrorRecoveryPlugin(BasePlugin):
    """Turn a hallucinated/unavailable tool call into a recoverable error.

    When a model names a tool that isn't registered, ADK would otherwise raise a
    fatal ``ValueError`` and abort the whole run. Returning a dict from
    ``on_tool_error_callback`` feeds the error back to the model as the tool
    response so it can self-correct (call a tool it actually has). Genuine tool
    *execution* errors are left to propagate unchanged (return None).
    """

    def __init__(self) -> None:
        super().__init__(name="tool_error_recovery")

    async def on_tool_error_callback(
        self,
        *,
        tool: BaseTool,
        tool_args: dict[str, Any],
        tool_context: ToolContext,
        error: Exception,
    ) -> dict | None:
        if getattr(tool, "description", "") == _TOOL_NOT_FOUND:
            return {
                "status": "error",
                "error": (
                    f"Tool '{tool.name}' is not available. Call ONLY a tool from "
                    "your provided tool list, then continue."
                ),
            }
        return None

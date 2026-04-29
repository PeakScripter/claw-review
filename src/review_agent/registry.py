"""Tool registry with the read-only guard.

`ToolRegistry.register()` raises `ReadOnlyViolation` if a tool's
`is_read_only` ClassVar is anything other than the literal `True`. This is
the only enforcement point that prevents a write-capable tool from ever
becoming visible to the LLM.
"""

from __future__ import annotations

import json
from typing import Any

from review_agent.tool import ReviewTool, ToolContext, ToolResult


class ReadOnlyViolation(ValueError):
    """Raised when attempting to register a non-read-only tool."""


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, ReviewTool] = {}

    def register(self, tool: ReviewTool) -> None:
        # `is True` rejects truthy-but-not-True values (e.g. 1, "yes", object()).
        if tool.is_read_only is not True:
            raise ReadOnlyViolation(
                f"Tool {type(tool).__name__!r} has is_read_only={tool.is_read_only!r}; "
                "only literal True is permitted in the review agent."
            )
        if tool.name in self._tools:
            raise ValueError(f"Tool {tool.name!r} already registered.")
        self._tools[tool.name] = tool

    def get(self, name: str) -> ReviewTool | None:
        return self._tools.get(name)

    def names(self) -> list[str]:
        return list(self._tools.keys())

    def has(self, name: str) -> bool:
        return name in self._tools

    def openai_schema(self) -> list[dict[str, Any]]:
        return [t.openai_schema() for t in self._tools.values()]

    def openai_schema_for(self, names: list[str]) -> list[dict[str, Any]]:
        return [self._tools[n].openai_schema() for n in names if n in self._tools]

    async def dispatch(
        self, name: str, raw_arguments: str | dict[str, Any], ctx: ToolContext
    ) -> ToolResult:
        tool = self._tools.get(name)
        if tool is None:
            return ToolResult(ok=False, summary="unknown tool", error=f"No such tool: {name}")

        if isinstance(raw_arguments, str):
            try:
                args_dict = json.loads(raw_arguments) if raw_arguments else {}
            except json.JSONDecodeError as e:
                return ToolResult(
                    ok=False,
                    summary="invalid tool arguments JSON",
                    error=f"JSON parse error: {e}",
                )
        else:
            args_dict = raw_arguments

        try:
            parsed = tool.input_model.model_validate(args_dict)
        except Exception as e:
            return ToolResult(
                ok=False,
                summary="tool input validation failed",
                error=f"Validation error: {e}",
            )

        try:
            return await tool.call(parsed, ctx)
        except Exception as e:
            return ToolResult(
                ok=False,
                summary=f"{name} raised {type(e).__name__}",
                error=str(e),
            )

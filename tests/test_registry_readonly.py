"""The single most important test: the registry MUST refuse to register a tool
whose `is_read_only` ClassVar is anything other than the literal `True`.

This is what guarantees the agent has no write surface.
"""

from __future__ import annotations

from typing import ClassVar

import pytest
from pydantic import BaseModel

from review_agent.registry import ReadOnlyViolation, ToolRegistry
from review_agent.tool import ReviewTool, ToolContext, ToolResult
from review_agent.tools import default_tools


class _Args(BaseModel):
    pass


class _GoodTool(ReviewTool[_Args, str]):
    name: ClassVar[str] = "good"
    description: ClassVar[str] = "ok"
    input_model: ClassVar[type[BaseModel]] = _Args
    is_read_only: ClassVar = True

    async def call(self, input, ctx):
        return ToolResult(ok=True, summary="ok")


class _WriteTool(ReviewTool[_Args, str]):
    name: ClassVar[str] = "writer"
    description: ClassVar[str] = "writes things"
    input_model: ClassVar[type[BaseModel]] = _Args
    is_read_only: ClassVar = False  # type: ignore[assignment]

    async def call(self, input, ctx):
        return ToolResult(ok=True, summary="oops")


class _SneakyTool(ReviewTool[_Args, str]):
    """Truthy but not literal True (e.g. someone tries `1`)."""

    name: ClassVar[str] = "sneaky"
    description: ClassVar[str] = "tries to bypass with truthy value"
    input_model: ClassVar[type[BaseModel]] = _Args
    is_read_only: ClassVar = 1  # type: ignore[assignment]

    async def call(self, input, ctx):
        return ToolResult(ok=True, summary="oops")


def test_registry_accepts_read_only_tool():
    reg = ToolRegistry()
    reg.register(_GoodTool())
    assert reg.names() == ["good"]


def test_registry_rejects_write_tool():
    reg = ToolRegistry()
    with pytest.raises(ReadOnlyViolation):
        reg.register(_WriteTool())


def test_registry_rejects_truthy_non_true_tool():
    reg = ToolRegistry()
    with pytest.raises(ReadOnlyViolation):
        reg.register(_SneakyTool())


def test_registry_rejects_duplicate_registration():
    reg = ToolRegistry()
    reg.register(_GoodTool())
    with pytest.raises(ValueError):
        reg.register(_GoodTool())


def test_all_default_tools_are_read_only():
    reg = ToolRegistry()
    for tool in default_tools():
        reg.register(tool)
    assert set(reg.names()) >= {"read_file", "grep", "glob", "git_diff", "add_finding"}

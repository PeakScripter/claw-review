"""Tests for the Phase 1 tools that don't require LLM or git."""

from __future__ import annotations

from pathlib import Path

import pytest

from review_agent.findings.store import FindingStore
from review_agent.tool import ToolContext
from review_agent.tools.add_finding import AddFindingInput, AddFindingTool
from review_agent.tools.glob_tool import GlobInput, GlobTool
from review_agent.tools.grep import GrepInput, GrepTool
from review_agent.tools.read_file import ReadFileInput, ReadFileTool


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "a.py").write_text("def foo():\n    return 1\n")
    (tmp_path / "src" / "b.py").write_text("def bar():\n    raise ValueError('boom')\n")
    (tmp_path / "README.md").write_text("hello\n")
    return tmp_path


@pytest.fixture
def ctx(workspace: Path) -> ToolContext:
    return ToolContext(cwd=workspace, findings=FindingStore(), reviewer="test")


async def test_read_file_basic(ctx: ToolContext):
    tool = ReadFileTool()
    result = await tool.call(ReadFileInput(path="src/a.py"), ctx)
    assert result.ok
    assert "1: def foo():" in result.summary


async def test_read_file_outside_root(ctx: ToolContext):
    tool = ReadFileTool()
    result = await tool.call(ReadFileInput(path="../etc/passwd"), ctx)
    assert not result.ok
    assert "outside" in result.summary.lower()


async def test_read_file_missing(ctx: ToolContext):
    tool = ReadFileTool()
    result = await tool.call(ReadFileInput(path="src/missing.py"), ctx)
    assert not result.ok


async def test_grep_finds_match(ctx: ToolContext):
    tool = GrepTool()
    result = await tool.call(GrepInput(pattern=r"raise ValueError"), ctx)
    assert result.ok
    assert any(m.file.endswith("b.py") for m in result.data or [])


async def test_grep_glob_filter(ctx: ToolContext):
    tool = GrepTool()
    result = await tool.call(GrepInput(pattern="foo", glob=".py"), ctx)
    assert result.ok
    files = {m.file for m in result.data or []}
    assert all(f.endswith(".py") for f in files)


async def test_glob_lists_python_files(ctx: ToolContext):
    tool = GlobTool()
    result = await tool.call(GlobInput(pattern="**/*.py"), ctx)
    assert result.ok
    assert "src/a.py" in (result.data or [])
    assert "src/b.py" in (result.data or [])


async def test_add_finding_records_and_returns(ctx: ToolContext):
    tool = AddFindingTool()
    inp = AddFindingInput(
        severity="medium",
        category="correctness",
        file="src/a.py",
        line=2,
        title="weird return",
        rationale="returns 1 unconditionally; probably wrong",
        evidence="Line 2:     return 1",
    )
    result = await tool.call(inp, ctx)
    assert result.ok
    assert len(ctx.findings) == 1
    assert ctx.findings.snapshot()[0].title == "weird return"

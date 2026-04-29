"""Tests for the public SDK surface (Phase 6)."""

from __future__ import annotations

import review_agent


def test_public_imports_available():
    assert hasattr(review_agent, "ReviewEngine")
    assert hasattr(review_agent, "ParallelCoordinator")
    assert hasattr(review_agent, "Finding")
    assert hasattr(review_agent, "FindingStore")
    assert hasattr(review_agent, "DiffTask")
    assert hasattr(review_agent, "FilesTask")
    assert hasattr(review_agent, "PRTask")
    assert hasattr(review_agent, "RepoTask")
    assert hasattr(review_agent, "FinalEvent")
    assert hasattr(review_agent, "format_markdown")
    assert hasattr(review_agent, "format_json")
    assert hasattr(review_agent, "format_sarif")
    assert hasattr(review_agent, "format_github")


def test_version_string():
    assert isinstance(review_agent.__version__, str)
    assert review_agent.__version__ == "0.1.0"


def test_diff_task_construction():
    task = review_agent.DiffTask(base="main", head="HEAD")
    assert task.base == "main"
    assert task.head == "HEAD"
    assert task.kind == "diff"


def test_finding_construction():
    f = review_agent.Finding(
        id="F001",
        severity="high",
        category="security",
        file="src/app.py",
        line=42,
        title="injection",
        rationale="user input",
        reviewer="security",
    )
    assert f.severity == "high"
    assert f.severity_rank() == 3


def test_finding_store_roundtrip():
    store = review_agent.FindingStore()
    f = review_agent.Finding(
        id=store.next_id(),
        severity="medium",
        category="correctness",
        file="a.py",
        line=1,
        title="t",
        rationale="r",
        reviewer="test",
    )
    store.add(f)
    snap = store.snapshot()
    assert len(snap) == 1
    assert snap[0].id == f.id


def test_mcp_module_importable():
    from review_agent.mcp import server
    assert hasattr(server, "MCPServer")
    assert hasattr(server, "main")


def test_hooks_module_importable():
    from review_agent.hooks import HookRunner, HookEvent
    runner = HookRunner()
    result = runner.fire(HookEvent.PRE_REVIEW, {})
    assert not result.blocked


def test_commands_module_importable():
    from review_agent.commands import CommandRegistry, register_builtin_commands
    reg = CommandRegistry()
    register_builtin_commands(reg)
    commands = [c.name for c in reg.all()]
    assert "help" in commands
    assert "findings" in commands
    assert "export" in commands

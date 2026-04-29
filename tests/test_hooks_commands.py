"""Tests for hooks (Phase 5) and slash commands."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from review_agent.commands.builtin import register_builtin_commands
from review_agent.commands.registry import CommandRegistry
from review_agent.findings.model import Finding
from review_agent.findings.store import FindingStore
from review_agent.hooks.runner import HookEvent, HookRunner


# ---------------------------------------------------------------------------
# Hook runner
# ---------------------------------------------------------------------------

def test_hook_allows_when_no_hooks():
    runner = HookRunner()
    result = runner.fire(HookEvent.PRE_REVIEW, {"task": "diff"})
    assert not result.blocked


def test_hook_pre_review_blocks_on_nonzero():
    runner = HookRunner(
        hooks={"PreReview": ["exit 1"]},
        cwd=Path("/tmp"),
    )
    result = runner.fire(HookEvent.PRE_REVIEW, {"task": "diff"})
    assert result.blocked


def test_hook_pre_review_passes_on_zero():
    runner = HookRunner(
        hooks={"PreReview": ["exit 0"]},
        cwd=Path("/tmp"),
    )
    result = runner.fire(HookEvent.PRE_REVIEW, {})
    assert not result.blocked


def test_hook_post_review_does_not_block():
    runner = HookRunner(
        hooks={"PostReview": ["exit 1"]},
        cwd=Path("/tmp"),
    )
    result = runner.fire(HookEvent.POST_REVIEW, {})
    # PostReview is not a pre-hook so it never blocks.
    assert not result.blocked


def test_hook_receives_payload_on_stdin(tmp_path):
    script = tmp_path / "check.sh"
    output = tmp_path / "received.json"
    script.write_text(f"cat > {output.as_posix()}\n")
    script.chmod(0o755)
    runner = HookRunner(hooks={"PostReview": [str(script)]}, cwd=tmp_path)
    runner.fire(HookEvent.POST_REVIEW, {"key": "value"})
    import json
    data = json.loads(output.read_text())
    assert data["key"] == "value"


def test_load_hooks_from_settings(tmp_path):
    from review_agent.hooks.runner import load_hooks
    settings = tmp_path / ".review" / "settings.toml"
    settings.parent.mkdir()
    settings.write_text('[hooks]\nPostReview = ["echo done"]\n')
    runner = load_hooks(tmp_path)
    assert "PostReview" in runner.hooks


# ---------------------------------------------------------------------------
# Slash commands
# ---------------------------------------------------------------------------

class _FakeSession:
    def __init__(self, tmp_path):
        self.findings = FindingStore()
        self.cwd = tmp_path
        self.token_usage = {"input_tokens": 100, "output_tokens": 50, "total": 150}


def _make_finding(session, **kwargs):
    base = dict(
        id=session.findings.next_id(), severity="medium", category="correctness",
        file="src/a.py", line=10, title="bug", rationale="broken", reviewer="r",
    )
    base.update(kwargs)
    f = Finding(**base)
    session.findings.add(f)
    return f


def test_command_help(tmp_path):
    reg = CommandRegistry()
    register_builtin_commands(reg)
    session = _FakeSession(tmp_path)
    out = reg.dispatch("/help", session)
    assert "/findings" in out
    assert "/export" in out


def test_command_findings_empty(tmp_path):
    reg = CommandRegistry()
    register_builtin_commands(reg)
    session = _FakeSession(tmp_path)
    out = reg.dispatch("/findings", session)
    assert "No findings" in out


def test_command_findings_lists(tmp_path):
    reg = CommandRegistry()
    register_builtin_commands(reg)
    session = _FakeSession(tmp_path)
    _make_finding(session, title="off-by-one")
    out = reg.dispatch("/findings", session)
    assert "off-by-one" in out


def test_command_explain(tmp_path):
    reg = CommandRegistry()
    register_builtin_commands(reg)
    session = _FakeSession(tmp_path)
    f = _make_finding(session, title="my bug")
    out = reg.dispatch(f"/explain {f.id}", session)
    assert "my bug" in out
    assert f.id in out


def test_command_explain_unknown_id(tmp_path):
    reg = CommandRegistry()
    register_builtin_commands(reg)
    session = _FakeSession(tmp_path)
    out = reg.dispatch("/explain FXXXX-000000", session)
    assert "not found" in out.lower()


def test_command_export_markdown(tmp_path):
    reg = CommandRegistry()
    register_builtin_commands(reg)
    session = _FakeSession(tmp_path)
    _make_finding(session)
    out = reg.dispatch("/export markdown", session)
    assert "# Code Review" in out


def test_command_export_json(tmp_path):
    import json
    reg = CommandRegistry()
    register_builtin_commands(reg)
    session = _FakeSession(tmp_path)
    _make_finding(session)
    out = reg.dispatch("/export json", session)
    parsed = json.loads(out)
    assert isinstance(parsed, list)


def test_command_ignore_writes_file(tmp_path):
    reg = CommandRegistry()
    register_builtin_commands(reg)
    session = _FakeSession(tmp_path)
    f = _make_finding(session, title="false positive")
    out = reg.dispatch(f'/ignore {f.id} "already handled upstream"', session)
    assert "ignored" in out.lower()
    ignore_path = tmp_path / ".review" / "ignore.yaml"
    assert ignore_path.is_file()
    import yaml
    entries = yaml.safe_load(ignore_path.read_text())
    assert any(e["id"] == f.id for e in entries)


def test_command_cost(tmp_path):
    reg = CommandRegistry()
    register_builtin_commands(reg)
    session = _FakeSession(tmp_path)
    out = reg.dispatch("/cost", session)
    assert "100" in out  # input_tokens


def test_unknown_command(tmp_path):
    reg = CommandRegistry()
    register_builtin_commands(reg)
    session = _FakeSession(tmp_path)
    out = reg.dispatch("/nonexistent", session)
    assert "Unknown command" in out

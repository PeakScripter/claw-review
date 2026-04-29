"""Tests for Phase 3 analyzer tools (no external binaries required).

Each tool is tested against a mocked subprocess response so the suite runs
without ruff/mypy/semgrep/etc. being installed.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from review_agent.findings.store import FindingStore
from review_agent.tool import ToolContext
from review_agent.tools._subprocess import AnalyzerResult, AnalyzerNotFound
from review_agent.tools.dep_audit import DepAuditInput, DepAuditTool
from review_agent.tools.lint import LintInput, LintTool
from review_agent.tools.sast import SASTInput, SASTTool
from review_agent.tools.test_run import TestRunInput, TestRunTool
from review_agent.tools.type_check import TypeCheckInput, TypeCheckTool


@pytest.fixture
def ctx(tmp_path: Path) -> ToolContext:
    return ToolContext(cwd=tmp_path, findings=FindingStore(), reviewer="test")


# ---------------------------------------------------------------------------
# LintTool
# ---------------------------------------------------------------------------

RUFF_JSON = json.dumps([
    {
        "code": "E501",
        "message": "Line too long",
        "filename": "/workspace/src/foo.py",
        "location": {"row": 5, "column": 80},
        "fix": None,
    }
])


async def test_lint_ruff_parses_json(ctx, tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "foo.py").write_text("x = 1\n")

    ok_result = AnalyzerResult(stdout=RUFF_JSON, stderr="", returncode=1)
    with patch("review_agent.tools.lint.run_analyzer", AsyncMock(return_value=ok_result)):
        tool = LintTool()
        result = await tool.call(LintInput(path="src/foo.py", linter="ruff"), ctx)
    assert result.ok
    assert len(result.data or []) == 1
    assert result.data[0].severity in ("low", "medium")


async def test_lint_not_found_returns_error(ctx):
    with patch("review_agent.tools.lint.run_analyzer", AsyncMock(side_effect=AnalyzerNotFound("ruff not found"))):
        tool = LintTool()
        result = await tool.call(LintInput(path=".", linter="ruff"), ctx)
    assert not result.ok
    assert "not found" in result.summary.lower()


async def test_lint_auto_detects_ruff_for_python(ctx, tmp_path):
    (tmp_path / "src.py").write_text("x = 1\n")
    ok_result = AnalyzerResult(stdout="[]", stderr="", returncode=0)
    with patch("review_agent.tools.lint.run_analyzer", AsyncMock(return_value=ok_result)) as m:
        tool = LintTool()
        await tool.call(LintInput(path="src.py", linter="auto"), ctx)
    call_args = m.call_args
    assert call_args[0][0] == "ruff"


# ---------------------------------------------------------------------------
# TypeCheckTool
# ---------------------------------------------------------------------------

MYPY_OUTPUT = (
    "src/foo.py:10: error: Argument 1 to \"foo\" has incompatible type  [arg-type]\n"
    "Found 1 error in 1 file (checked 2 source files)\n"
)


async def test_type_check_mypy_parses_output(ctx):
    result = AnalyzerResult(stdout=MYPY_OUTPUT, stderr="", returncode=1)
    with patch("review_agent.tools.type_check.run_analyzer", AsyncMock(return_value=result)):
        tool = TypeCheckTool()
        r = await tool.call(TypeCheckInput(checker="mypy", path="."), ctx)
    assert r.ok
    assert len(r.data or []) == 1
    assert r.data[0].line == 10
    assert "arg-type" in r.data[0].title


async def test_type_check_clean_returns_no_findings(ctx):
    result = AnalyzerResult(stdout="Success: no issues found\n", stderr="", returncode=0)
    with patch("review_agent.tools.type_check.run_analyzer", AsyncMock(return_value=result)):
        tool = TypeCheckTool()
        r = await tool.call(TypeCheckInput(checker="mypy", path="."), ctx)
    assert r.ok
    assert len(r.data or []) == 0


# ---------------------------------------------------------------------------
# SASTTool
# ---------------------------------------------------------------------------

SEMGREP_JSON = json.dumps({
    "results": [{
        "check_id": "python.lang.security.use-after-exec",
        "path": "src/main.py",
        "start": {"line": 42},
        "end": {"line": 42},
        "extra": {
            "severity": "ERROR",
            "message": "os.system called with user input",
            "metadata": {"cwe": ["CWE-78"]},
        },
    }],
    "errors": [],
})


async def test_sast_semgrep_parses_json(ctx, tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "main.py").write_text("import os\nos.system(input())\n")
    result = AnalyzerResult(stdout=SEMGREP_JSON, stderr="", returncode=1)
    with patch("review_agent.tools.sast.run_analyzer", AsyncMock(return_value=result)):
        tool = SASTTool()
        r = await tool.call(SASTInput(path="src", tool="semgrep"), ctx)
    assert r.ok
    assert len(r.data or []) == 1
    assert r.data[0].category == "security"
    assert r.data[0].severity == "high"


BANDIT_JSON = json.dumps({
    "results": [{
        "filename": "src/app.py",
        "line_number": 7,
        "issue_text": "Use of assert detected",
        "test_id": "B101",
        "issue_severity": "LOW",
        "issue_confidence": "HIGH",
        "more_info": "https://bandit.readthedocs.io/en/latest/plugins/b101_assert_used.html",
    }],
})


async def test_sast_bandit_parses_json(ctx, tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text("assert x\n")
    result = AnalyzerResult(stdout=BANDIT_JSON, stderr="", returncode=1)
    with patch("review_agent.tools.sast.run_analyzer", AsyncMock(return_value=result)):
        tool = SASTTool()
        r = await tool.call(SASTInput(path="src", tool="bandit"), ctx)
    assert r.ok
    assert len(r.data or []) == 1
    assert "B101" in r.data[0].title


# ---------------------------------------------------------------------------
# DepAuditTool
# ---------------------------------------------------------------------------

PIP_AUDIT_JSON = json.dumps({
    "dependencies": [{
        "name": "requests",
        "version": "2.20.0",
        "vulns": [{
            "id": "PYSEC-2023-74",
            "aliases": ["CVE-2023-32681"],
            "description": "Unintended leak of Proxy-Authorization header",
            "fix_versions": ["2.31.0"],
        }],
    }],
})


async def test_dep_audit_pip_audit_parses(ctx, tmp_path):
    (tmp_path / "requirements.txt").write_text("requests==2.20.0\n")
    result = AnalyzerResult(stdout=PIP_AUDIT_JSON, stderr="", returncode=1)
    with patch("review_agent.tools.dep_audit.run_analyzer", AsyncMock(return_value=result)):
        tool = DepAuditTool()
        r = await tool.call(DepAuditInput(ecosystem="python"), ctx)
    assert r.ok
    assert len(r.data or []) == 1
    # CVE id appears in the references, title contains the PYSEC id
    assert any("CVE-2023-32681" in ref for ref in r.data[0].references)


async def test_dep_audit_auto_detects_python(ctx, tmp_path):
    (tmp_path / "requirements.txt").write_text("requests\n")
    result = AnalyzerResult(stdout='{"dependencies":[]}', stderr="", returncode=0)
    with patch("review_agent.tools.dep_audit.run_analyzer", AsyncMock(return_value=result)) as m:
        tool = DepAuditTool()
        await tool.call(DepAuditInput(ecosystem="auto"), ctx)
    assert m.call_args[0][0] == "pip-audit"


# ---------------------------------------------------------------------------
# TestRunTool
# ---------------------------------------------------------------------------

PYTEST_OUTPUT = (
    "FAILED tests/test_foo.py::test_bar - AssertionError: expected 1 got 2\n"
    "1 failed, 5 passed in 0.42s\n"
)


async def test_test_run_pytest_parses_failures(ctx):
    result = AnalyzerResult(stdout=PYTEST_OUTPUT, stderr="", returncode=1)
    with patch("review_agent.tools.test_run.run_analyzer", AsyncMock(return_value=result)):
        tool = TestRunTool()
        r = await tool.call(TestRunInput(runner="pytest", path="."), ctx)
    assert r.ok
    assert len(r.data or []) == 1
    assert "test_bar" in r.data[0].title


async def test_test_run_all_pass_no_findings(ctx):
    result = AnalyzerResult(stdout="5 passed in 0.21s\n", stderr="", returncode=0)
    with patch("review_agent.tools.test_run.run_analyzer", AsyncMock(return_value=result)):
        tool = TestRunTool()
        r = await tool.call(TestRunInput(runner="pytest", path="."), ctx)
    assert r.ok
    assert len(r.data or []) == 0

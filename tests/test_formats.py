"""Tests for SARIF, GitHub, and JSON formatters (Phase 4)."""

from __future__ import annotations

import json

from review_agent.findings.format import format_github, format_json, format_sarif
from review_agent.findings.model import Finding
from review_agent.findings.store import FindingStore


def _finding(store: FindingStore, **kwargs) -> Finding:
    base = dict(
        id=store.next_id(), severity="high", category="security",
        file="src/auth.py", line=42, title="SQL injection",
        rationale="user input concatenated into query",
        suggestion="Use parameterised queries",
        references=["https://cwe.mitre.org/data/definitions/89.html"],
        reviewer="security", confidence=0.9,
    )
    base.update(kwargs)
    f = Finding(**base)
    store.add(f)
    return f


# ---------------------------------------------------------------------------
# SARIF
# ---------------------------------------------------------------------------

def test_sarif_valid_json():
    store = FindingStore()
    _finding(store)
    sarif = format_sarif(store.snapshot())
    doc = json.loads(sarif)
    assert doc["version"] == "2.1.0"
    assert "$schema" in doc
    assert len(doc["runs"]) >= 1


def test_sarif_empty_findings_still_valid():
    sarif = format_sarif([])
    doc = json.loads(sarif)
    assert doc["version"] == "2.1.0"
    assert len(doc["runs"]) == 1


def test_sarif_result_has_location():
    store = FindingStore()
    _finding(store)
    doc = json.loads(format_sarif(store.snapshot()))
    result = doc["runs"][0]["results"][0]
    loc = result["locations"][0]["physicalLocation"]
    assert loc["artifactLocation"]["uri"] == "src/auth.py"
    assert loc["region"]["startLine"] == 42


def test_sarif_severity_to_level():
    store = FindingStore()
    _finding(store, severity="critical")
    doc = json.loads(format_sarif(store.snapshot()))
    result = doc["runs"][0]["results"][0]
    assert result["level"] == "error"


def test_sarif_multiple_reviewers_multiple_runs():
    store = FindingStore()
    _finding(store, reviewer="security")
    _finding(store, reviewer="correctness", category="correctness")
    doc = json.loads(format_sarif(store.snapshot()))
    run_tool_names = [r["tool"]["driver"]["name"] for r in doc["runs"]]
    assert any("security" in n for n in run_tool_names)
    assert any("correctness" in n for n in run_tool_names)


# ---------------------------------------------------------------------------
# GitHub comments
# ---------------------------------------------------------------------------

def test_github_format_structure():
    store = FindingStore()
    _finding(store)
    payload = json.loads(format_github(store.snapshot()))
    assert "comments" in payload
    assert len(payload["comments"]) == 1
    comment = payload["comments"][0]
    assert comment["path"] == "src/auth.py"
    assert comment["line"] == 42
    assert "SQL injection" in comment["body"]


def test_github_empty_findings():
    payload = json.loads(format_github([]))
    assert payload["comments"] == []


def test_github_severity_emoji_in_body():
    store = FindingStore()
    _finding(store, severity="critical")
    payload = json.loads(format_github(store.snapshot()))
    assert "🔴" in payload["comments"][0]["body"]


def test_github_multi_line_range():
    store = FindingStore()
    _finding(store, line=10, end_line=20)
    payload = json.loads(format_github(store.snapshot()))
    c = payload["comments"][0]
    assert c.get("start_line") == 10
    assert c["line"] == 20


# ---------------------------------------------------------------------------
# JSON format
# ---------------------------------------------------------------------------

def test_json_format_is_list():
    store = FindingStore()
    _finding(store)
    parsed = json.loads(format_json(store.snapshot()))
    assert isinstance(parsed, list)
    assert parsed[0]["severity"] == "high"


def test_json_format_empty():
    parsed = json.loads(format_json([]))
    assert parsed == []

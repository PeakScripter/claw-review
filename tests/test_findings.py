"""Smoke tests for the Finding model, store, and formatters."""

from __future__ import annotations

import json

from review_agent.findings.format import format_json, format_markdown
from review_agent.findings.model import Finding
from review_agent.findings.store import FindingStore


def _make(store: FindingStore, **kw) -> Finding:
    base = dict(
        id=store.next_id(),
        severity="medium",
        category="correctness",
        file="src/foo.py",
        line=10,
        title="bug",
        rationale="because",
        reviewer="correctness",
        confidence=0.8,
    )
    base.update(kw)
    finding = Finding(**base)
    store.add(finding)
    return finding


def test_store_assigns_unique_ids():
    store = FindingStore()
    f1 = _make(store)
    f2 = _make(store)
    assert f1.id != f2.id
    assert len(store) == 2
    assert store.snapshot() == [f1, f2]


def test_finding_rejects_end_line_before_start():
    try:
        Finding(
            id="X",
            severity="low",
            category="style",
            file="a.py",
            line=10,
            end_line=5,
            title="x",
            rationale="y",
            reviewer="r",
        )
    except Exception as e:  # pydantic ValidationError
        assert "end_line" in str(e)
    else:
        raise AssertionError("expected validation error")


def test_markdown_empty():
    out = format_markdown([])
    assert "No findings" in out


def test_markdown_groups_by_severity():
    store = FindingStore()
    _make(store, severity="critical", title="oh no")
    _make(store, severity="low", title="meh")
    md = format_markdown(store.snapshot())
    assert "CRITICAL (1)" in md
    assert "LOW (1)" in md
    assert md.index("CRITICAL") < md.index("LOW")


def test_json_roundtrip():
    store = FindingStore()
    _make(store, title="a")
    out = format_json(store.snapshot())
    parsed = json.loads(out)
    assert isinstance(parsed, list)
    assert parsed[0]["title"] == "a"

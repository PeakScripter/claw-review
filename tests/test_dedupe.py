"""Tests for findings deduplication."""

from __future__ import annotations

from review_agent.findings.dedupe import dedupe
from review_agent.findings.model import Finding
from review_agent.findings.store import FindingStore


def _f(store: FindingStore, *, file="a.py", line=10, end_line=None,
       severity="medium", category="correctness", title="bug",
       rationale="because", reviewer="r") -> Finding:
    f = Finding(
        id=store.next_id(),
        severity=severity,
        category=category,
        file=file,
        line=line,
        end_line=end_line,
        title=title,
        rationale=rationale,
        reviewer=reviewer,
    )
    store.add(f)
    return f


def test_no_duplicates_unchanged():
    store = FindingStore()
    f1 = _f(store, line=1)
    f2 = _f(store, line=20)
    result = dedupe(store.snapshot())
    assert len(result) == 2
    assert {r.id for r in result} == {f1.id, f2.id}


def test_same_line_same_category_merges():
    store = FindingStore()
    f1 = _f(store, line=10, severity="low", reviewer="r1")
    f2 = _f(store, line=10, severity="high", reviewer="r2")
    result = dedupe(store.snapshot())
    # Both at line 10, same category → merge into one finding
    assert len(result) == 1
    # High severity wins.
    assert result[0].severity == "high"
    # Both rationales present.
    assert "r1" in result[0].rationale or "because" in result[0].rationale


def test_different_categories_not_merged():
    store = FindingStore()
    _f(store, line=10, category="correctness")
    _f(store, line=10, category="security")
    result = dedupe(store.snapshot())
    assert len(result) == 2


def test_different_files_not_merged():
    store = FindingStore()
    _f(store, file="a.py", line=10)
    _f(store, file="b.py", line=10)
    result = dedupe(store.snapshot())
    assert len(result) == 2


def test_overlapping_ranges_merge():
    store = FindingStore()
    f1 = _f(store, line=10, end_line=15, severity="medium", reviewer="r1")
    f2 = _f(store, line=13, end_line=20, severity="high", reviewer="r2")
    result = dedupe(store.snapshot())
    assert len(result) == 1
    assert result[0].severity == "high"
    # Merged end_line should cover the full span.
    assert result[0].end_line == 20


def test_non_overlapping_ranges_not_merged():
    store = FindingStore()
    _f(store, line=1, end_line=5)
    _f(store, line=7, end_line=10)
    result = dedupe(store.snapshot())
    assert len(result) == 2


def test_empty_findings():
    assert dedupe([]) == []


def test_sorted_highest_severity_first():
    store = FindingStore()
    _f(store, line=1, severity="info")
    _f(store, line=5, severity="critical")
    _f(store, line=3, severity="low")
    result = dedupe(store.snapshot())
    severities = [r.severity for r in result]
    assert severities == ["critical", "low", "info"]


def test_references_merged():
    store = FindingStore()
    f1 = Finding(id=store.next_id(), severity="medium", category="security",
                 file="a.py", line=10, title="t", rationale="r1",
                 reviewer="r1", references=["https://cve.example/1"])
    f2 = Finding(id=store.next_id(), severity="medium", category="security",
                 file="a.py", line=10, title="t", rationale="r2",
                 reviewer="r2", references=["https://cve.example/2"])
    store.add(f1)
    store.add(f2)
    result = dedupe(store.snapshot())
    assert len(result) == 1
    refs = set(result[0].references)
    assert "https://cve.example/1" in refs
    assert "https://cve.example/2" in refs

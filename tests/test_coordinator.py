"""Tests for ParallelCoordinator (no LLM — uses stubbed engines)."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from review_agent.coordinator import ParallelCoordinator, build_default_registry
from review_agent.findings.model import Finding
from review_agent.findings.store import FindingStore
from review_agent.llm.groq_client import GroqClient, GroqConfig
from review_agent.types import FinalEvent


def _make_finding(store: FindingStore, reviewer: str, line: int = 1) -> Finding:
    f = Finding(
        id=store.next_id(),
        severity="medium",
        category="correctness",
        file="src/a.py",
        line=line,
        title=f"bug from {reviewer}",
        rationale="rationale",
        reviewer=reviewer,
    )
    store.add(f)
    return f


def _dummy_groq(model="llama-3.3-70b-versatile") -> GroqClient:
    return GroqClient(GroqConfig(api_key="test", model=model))


async def _fake_review_gen(findings_to_add, shared_store):
    """A fake review generator that adds findings to the shared store and yields a FinalEvent."""
    from review_agent.types import FinalEvent, FindingEvent

    for f in findings_to_add:
        f_copy = f.model_copy(update={"id": shared_store.next_id()})
        shared_store.add(f_copy)
        yield FindingEvent(finding=f_copy)
    yield FinalEvent(findings=shared_store.snapshot(), stop_reason="stop")


async def test_coordinator_merges_findings_from_multiple_reviewers(tmp_path):
    """Coordinator gathers findings from two reviewers and deduplicates."""
    registry = build_default_registry()
    groq = _dummy_groq()

    coord = ParallelCoordinator(
        groq=groq,
        registry=registry,
        cwd=tmp_path,
        reviewer_names=["correctness", "security"],
    )

    # We'll patch ReviewEngine to avoid actual LLM calls.
    correctness_findings = [
        Finding(id="C1", severity="medium", category="correctness",
                file="src/a.py", line=10, title="bad logic",
                rationale="wrong", reviewer="correctness"),
    ]
    security_findings = [
        Finding(id="S1", severity="high", category="security",
                file="src/a.py", line=10, title="injection",
                rationale="sqli", reviewer="security"),
        Finding(id="S2", severity="low", category="security",
                file="src/b.py", line=5, title="weak hash",
                rationale="md5", reviewer="security"),
    ]

    call_count = [0]

    def fake_engine_init(self, *, groq, registry, cwd, reviewer_name, reviewer_instructions, max_iterations=12):
        self.groq = groq
        self.registry = registry
        self.cwd = cwd
        self.reviewer_name = reviewer_name
        self.reviewer_instructions = reviewer_instructions
        self.max_iterations = max_iterations
        self.findings = FindingStore()

    async def fake_review(self, task, payload):
        findings = correctness_findings if self.reviewer_name == "correctness" else security_findings
        call_count[0] += 1
        async for event in _fake_review_gen(findings, coord.shared_findings):
            yield event

    from review_agent import engine as engine_mod

    with patch.object(engine_mod.ReviewEngine, "__init__", fake_engine_init), \
         patch.object(engine_mod.ReviewEngine, "review", fake_review):
        events = []
        async for event in coord.review(MagicMock(), "payload"):
            events.append(event)

    finals = [e for e in events if isinstance(e, FinalEvent)]
    assert len(finals) == 1
    final = finals[0]

    # 3 total findings: C1 (line 10, correctness) + S1 (line 10, security) + S2
    # C1 and S1 are same line but different category → NOT merged → 3 findings
    assert len(final.findings) == 3
    assert call_count[0] == 2  # both reviewers ran


async def test_coordinator_dedupes_same_file_line_category(tmp_path):
    """Two reviewers finding the same bug → deduplicated to one (highest severity)."""
    registry = build_default_registry()
    groq = _dummy_groq()

    coord = ParallelCoordinator(
        groq=groq,
        registry=registry,
        cwd=tmp_path,
        reviewer_names=["correctness", "style"],
    )

    shared_finding_template = dict(
        severity="low",
        category="correctness",
        file="src/a.py",
        line=5,
        title="off-by-one",
        rationale="loop goes one past end",
        reviewer="",
    )

    def fake_engine_init(self, *, groq, registry, cwd, reviewer_name, reviewer_instructions, max_iterations=12):
        self.groq = groq
        self.registry = registry
        self.cwd = cwd
        self.reviewer_name = reviewer_name
        self.reviewer_instructions = reviewer_instructions
        self.max_iterations = max_iterations
        self.findings = FindingStore()

    async def fake_review(self, task, payload):
        sev = "medium" if self.reviewer_name == "style" else "low"
        f = Finding(id="X", **{**shared_finding_template, "reviewer": self.reviewer_name, "severity": sev})
        async for event in _fake_review_gen([f], coord.shared_findings):
            yield event

    from review_agent import engine as engine_mod

    with patch.object(engine_mod.ReviewEngine, "__init__", fake_engine_init), \
         patch.object(engine_mod.ReviewEngine, "review", fake_review):
        events = []
        async for event in coord.review(MagicMock(), "payload"):
            events.append(event)

    final = next(e for e in events if isinstance(e, FinalEvent))
    assert len(final.findings) == 1
    assert final.findings[0].severity == "medium"


async def test_coordinator_handles_unknown_reviewer(tmp_path):
    """Unknown reviewer name results in an error event but doesn't crash."""
    from review_agent.types import ErrorEvent

    registry = build_default_registry()
    groq = _dummy_groq()
    coord = ParallelCoordinator(
        groq=groq,
        registry=registry,
        cwd=tmp_path,
        reviewer_names=["nonexistent_reviewer"],
    )

    events = []
    async for event in coord.review(MagicMock(), "payload"):
        events.append(event)

    error_events = [e for e in events if isinstance(e, ErrorEvent)]
    assert len(error_events) == 1
    assert "nonexistent_reviewer" in error_events[0].message


def test_build_default_registry_contains_phase1_tools():
    reg = build_default_registry()
    assert set(reg.names()) >= {"read_file", "grep", "glob", "git_diff", "add_finding"}

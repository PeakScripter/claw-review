"""ParallelCoordinator — runs multiple specialist reviewers concurrently.

This is the coordinator-mode analog from Claude Code's `coordinatorMode.ts`.
Instead of a coordinator LLM that dispatches tools, the `ParallelCoordinator`
directly launches one `ReviewEngine` per reviewer in parallel via
`asyncio.gather`. Findings from all reviewers land in a shared store, then
deduplication is applied.

A future LLM-driven coordinator that calls `SubReviewerTool` can be layered
on top without changing the per-reviewer engine or the dedup logic.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from pathlib import Path

from review_agent.engine import ReviewEngine
from review_agent.findings.dedupe import dedupe
from review_agent.findings.store import FindingStore
from review_agent.llm.groq_client import GroqClient, GroqConfig
from review_agent.registry import ToolRegistry
from review_agent.skills.loader import load_reviewer
from review_agent.tools import default_tools
from review_agent.types import (
    AssistantTextEvent,
    ErrorEvent,
    Event,
    FinalEvent,
    FindingEvent,
    ToolResultEvent,
    ToolUseEvent,
)


class CoordinatorEvent:
    """Wrapper that tags an event with its originating reviewer."""

    __slots__ = ("reviewer", "event")

    def __init__(self, reviewer: str, event: Event) -> None:
        self.reviewer = reviewer
        self.event = event


class ParallelCoordinator:
    """Runs a list of reviewer names in parallel and merges their findings.

    Events are yielded interleaved as they arrive from each reviewer (via an
    async queue), preserving responsiveness for the CLI progress display.
    """

    def __init__(
        self,
        *,
        groq: GroqClient,
        registry: ToolRegistry,
        cwd: Path,
        reviewer_names: list[str],
        sub_reviewer_model: str | None = None,
        max_iterations: int = 12,
    ) -> None:
        self.groq = groq
        self.registry = registry
        self.cwd = cwd
        self.reviewer_names = reviewer_names
        self.sub_reviewer_model = sub_reviewer_model
        self.max_iterations = max_iterations
        self.shared_findings = FindingStore()

    async def review(self, task, payload: str, *, min_confidence: float = 0.0) -> AsyncIterator[Event]:
        """Run all reviewers in parallel; yield events; yield deduplicated final."""
        queue: asyncio.Queue[CoordinatorEvent | None] = asyncio.Queue()
        remaining = len(self.reviewer_names)

        async def run_reviewer(name: str) -> None:
            try:
                manifest = load_reviewer(name)
            except FileNotFoundError:
                await queue.put(
                    CoordinatorEvent(
                        name, ErrorEvent(message=f"Reviewer manifest not found: {name!r}")
                    )
                )
                await queue.put(None)
                return

            # Restrict registry to tools allowed by this reviewer.
            allowed = set(manifest.tools)
            child_registry = ToolRegistry()
            for tname in self.registry.names():
                if allowed and tname not in allowed:
                    continue
                tool = self.registry.get(tname)
                if tool:
                    child_registry.register(tool)

            # Optionally use a different (cheaper) model for sub-reviewers.
            groq = self.groq
            target_model = self.sub_reviewer_model or manifest.model
            if target_model and target_model != self.groq.config.model:
                groq = GroqClient(
                    GroqConfig(
                        api_key=self.groq.config.api_key,
                        model=target_model,
                        temperature=self.groq.config.temperature,
                        max_tokens=self.groq.config.max_tokens,
                    )
                )

            child = ReviewEngine(
                groq=groq,
                registry=child_registry,
                cwd=self.cwd,
                reviewer_name=name,
                reviewer_instructions=manifest.instructions,
                max_iterations=self.max_iterations,
            )
            child.findings = self.shared_findings

            async for event in child.review(task, payload):
                await queue.put(CoordinatorEvent(name, event))
            await queue.put(None)

        # Launch all reviewers concurrently.
        tasks = [asyncio.create_task(run_reviewer(name)) for name in self.reviewer_names]

        prev_finding_count = 0
        while remaining > 0:
            item = await queue.get()
            if item is None:
                remaining -= 1
                continue
            event = item.event
            # Re-emit most event types to the caller for live progress display.
            if isinstance(event, (ToolUseEvent, ToolResultEvent, ErrorEvent)):
                yield event
            elif isinstance(event, AssistantTextEvent):
                # Tag assistant text with reviewer name for clarity.
                if event.text.strip():
                    yield AssistantTextEvent(text=f"[{item.reviewer}] {event.text}")
            elif isinstance(event, FinalEvent):
                pass  # Will emit our own merged final below.

            # Emit newly added findings as they land.
            current = self.shared_findings.snapshot()
            for f in current[prev_finding_count:]:
                yield FindingEvent(finding=f)
            prev_finding_count = len(current)

        # Wait for all tasks to complete (they should be done by now).
        await asyncio.gather(*tasks, return_exceptions=True)

        # Deduplicate across all reviewers and emit final event.
        deduped = dedupe(self.shared_findings.snapshot(), min_confidence=min_confidence)
        yield FinalEvent(findings=deduped, stop_reason="coordinator_complete")


def build_default_registry() -> ToolRegistry:
    """Convenience: build a registry with all Phase 1 tools registered."""
    reg = ToolRegistry()
    for tool in default_tools():
        reg.register(tool)
    return reg

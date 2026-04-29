"""ReviewEngine — the core agent loop.

Flow:
  1. Build system prompt (charter + rubric + reviewer instructions + tool inventory).
  2. Build user prompt (task framing + diff/files payload).
  3. Loop:
       a. Call Groq with messages + tool schemas.
       b. Yield the assistant text (if any).
       c. If no tool calls, stop.
       d. Otherwise dispatch each tool call, append the tool result, and loop.
  4. Yield the final findings snapshot.

Hard cap: `max_iterations` prevents infinite tool-call loops.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

from review_agent.findings.dedupe import dedupe
from review_agent.findings.store import FindingStore
from review_agent.llm.groq_client import GroqClient
from review_agent.llm.messages import system_message, tool_message, user_message
from review_agent.prompts.system import build_system_prompt, build_user_prompt
from review_agent.registry import ToolRegistry
from review_agent.tool import ToolContext
from review_agent.types import (
    AssistantTextEvent,
    DiffTask,
    ErrorEvent,
    Event,
    FilesTask,
    FinalEvent,
    FindingEvent,
    PRTask,
    RepoTask,
    ReviewTask,
    ToolResultEvent,
    ToolUseEvent,
)

DEFAULT_MAX_ITERATIONS = 12
CRITIQUE_MAX_ITERATIONS = 5


class ReviewEngine:
    def __init__(
        self,
        *,
        groq: GroqClient,
        registry: ToolRegistry,
        cwd: Path,
        reviewer_name: str = "coordinator",
        reviewer_instructions: str = "",
        max_iterations: int = DEFAULT_MAX_ITERATIONS,
    ) -> None:
        self.groq = groq
        self.registry = registry
        self.cwd = cwd
        self.reviewer_name = reviewer_name
        self.reviewer_instructions = reviewer_instructions
        self.max_iterations = max_iterations
        self.min_confidence = 0.0
        self.findings = FindingStore()

    async def review(self, task: ReviewTask, payload: str, *, min_confidence: float = 0.0) -> AsyncIterator[Event]:
        """Run a review. Yields events; the final event always has type `final`."""
        ctx = ToolContext(cwd=self.cwd, findings=self.findings, reviewer=self.reviewer_name)

        system = build_system_prompt(
            cwd=self.cwd,
            tool_names=self.registry.names(),
            reviewer_instructions=self.reviewer_instructions,
        )
        task_summary = _summarize_task(task)
        user = build_user_prompt(task_summary, payload)

        messages: list[dict] = [system_message(system), user_message(user)]
        tools_schema = self.registry.openai_schema()

        prev_finding_count = 0
        stop_reason = "max_iterations"

        for _ in range(self.max_iterations):
            try:
                assistant = await self.groq.complete(messages, tools=tools_schema)
            except Exception as e:
                yield ErrorEvent(message=str(e))
                stop_reason = "error"
                break

            if assistant.content:
                yield AssistantTextEvent(text=assistant.content)

            messages.append(assistant.to_wire())

            if not assistant.tool_calls:
                stop_reason = "stop"
                break

            for call in assistant.tool_calls:
                yield ToolUseEvent(
                    tool=call.name,
                    tool_call_id=call.id,
                    input=_safe_json(call.arguments),
                )
                result = await self.registry.dispatch(call.name, call.arguments, ctx)
                yield ToolResultEvent(
                    tool=call.name,
                    tool_call_id=call.id,
                    ok=result.ok,
                    summary=_truncate(result.summary, 2000),
                )
                messages.append(tool_message(call.id, result.to_llm_content()))

            # Emit any new findings produced during this iteration.
            current = self.findings.snapshot()
            for f in current[prev_finding_count:]:
                yield FindingEvent(finding=f)
            prev_finding_count = len(current)

        # Critique pass: ask the LLM to retract findings it cannot substantiate.
        if self.registry.has("retract_finding"):
            snapshot = self.findings.snapshot()
            if snapshot:
                messages.append(user_message(_build_critique_prompt(snapshot)))
                critique_schema = self.registry.openai_schema_for(["retract_finding"])
                for _ in range(CRITIQUE_MAX_ITERATIONS):
                    try:
                        assistant = await self.groq.complete(messages, tools=critique_schema)
                    except Exception as e:
                        yield ErrorEvent(message=str(e))
                        break
                    messages.append(assistant.to_wire())
                    if not assistant.tool_calls:
                        break
                    for call in assistant.tool_calls:
                        yield ToolUseEvent(
                            tool=call.name,
                            tool_call_id=call.id,
                            input=_safe_json(call.arguments),
                        )
                        result = await self.registry.dispatch(call.name, call.arguments, ctx)
                        yield ToolResultEvent(
                            tool=call.name,
                            tool_call_id=call.id,
                            ok=result.ok,
                            summary=result.summary,
                        )
                        messages.append(tool_message(call.id, result.to_llm_content()))

        yield FinalEvent(findings=dedupe(self.findings.snapshot(), min_confidence=min_confidence), stop_reason=stop_reason)


def _summarize_task(task: ReviewTask) -> str:
    if isinstance(task, DiffTask):
        return f"Review the diff `{task.base}..{task.head}` in `{task.cwd}`."
    if isinstance(task, PRTask):
        return f"Review pull request #{task.number} in `{task.repo}`."
    if isinstance(task, FilesTask):
        return f"Review these files in `{task.cwd}`: {', '.join(task.paths)}."
    if isinstance(task, RepoTask):
        return f"Review the repository at `{task.cwd}`."
    return "Review the provided code."


def _safe_json(raw: str) -> dict:
    import json

    try:
        loaded = json.loads(raw) if raw else {}
        return loaded if isinstance(loaded, dict) else {"value": loaded}
    except json.JSONDecodeError:
        return {"_raw": raw}


def _truncate(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + f"\n…[truncated {len(text) - max_chars} chars]"


def _build_critique_prompt(findings: list) -> str:
    lines = [
        f"You have emitted {len(findings)} finding(s). Before finalising, challenge each one:",
        "",
        "- Did you directly read the code at that location via `read_file`?",
        "- Can you quote the exact lines proving the issue exists in the current file (not just the diff)?",
        "- Is this a concrete defect or a hypothetical?",
        "",
        "Call `retract_finding` for any finding that does not meet the evidence bar. Be decisive.",
        "",
        "Current findings:",
    ]
    for f in findings:
        lines.append(f"  {f.id}  [{f.severity}]  {f.file}:{f.line}  —  {f.title}")
    return "\n".join(lines)

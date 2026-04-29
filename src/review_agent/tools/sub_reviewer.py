"""SubReviewerTool — the Agent-tool analog for the review agent.

Spawns a child ReviewEngine scoped to a single reviewer manifest, drains all
its events, and returns a summary. The coordinator (or a coordinator LLM) calls
this tool to delegate to a specialist reviewer.

The child engine shares the *coordinator's* FindingStore so findings accumulate
in one place. The child gets a filtered registry containing only the tools the
reviewer's manifest declares.
"""

from __future__ import annotations

from typing import ClassVar

from pydantic import BaseModel, Field

from review_agent.findings.store import FindingStore
from review_agent.llm.groq_client import GroqClient
from review_agent.registry import ToolRegistry
from review_agent.skills.loader import load_reviewer
from review_agent.tool import ReviewTool, ToolContext, ToolResult
from review_agent.types import ErrorEvent, FinalEvent


class SubReviewerInput(BaseModel):
    reviewer: str = Field(
        description="Name of the specialist reviewer to spawn (e.g. 'security', 'performance')."
    )
    additional_instructions: str = Field(
        default="",
        description="Optional extra guidance appended to the reviewer's system prompt.",
    )


class SubReviewerTool(ReviewTool[SubReviewerInput, str]):
    """Spawns a child ReviewEngine with a specialist reviewer's manifest.

    The child runs its own LLM loop over the same task + payload but with a
    focused system prompt and a restricted tool set. Findings land in the
    shared store.
    """

    name: ClassVar[str] = "sub_reviewer"
    description: ClassVar[str] = (
        "Delegate the current review task to a specialist reviewer. "
        "Available reviewers: correctness, security, performance, style, tests, architecture. "
        "The specialist runs independently and records its findings. "
        "Call each reviewer at most once per review."
    )
    input_model: ClassVar[type[BaseModel]] = SubReviewerInput
    is_read_only: ClassVar = True
    is_concurrency_safe: ClassVar[bool] = True

    def __init__(
        self,
        groq: GroqClient,
        base_registry: ToolRegistry,
        task,
        payload: str,
        model: str | None = None,
    ) -> None:
        self._groq = groq
        self._base_registry = base_registry
        self._task = task
        self._payload = payload
        self._model = model

    async def call(self, input: SubReviewerInput, ctx: ToolContext) -> ToolResult[str]:
        from review_agent.engine import ReviewEngine

        try:
            manifest = load_reviewer(input.reviewer)
        except FileNotFoundError:
            return ToolResult(
                ok=False,
                summary=f"Unknown reviewer: {input.reviewer!r}",
                error="reviewer_not_found",
            )

        # Build a restricted registry: only tools the reviewer's manifest allows.
        allowed_names = set(manifest.tools)
        child_registry = ToolRegistry()
        for name in self._base_registry.names():
            if allowed_names and name not in allowed_names:
                continue
            tool = self._base_registry.get(name)
            if tool:
                child_registry.register(tool)

        groq = self._groq
        if self._model or manifest.model:
            from review_agent.llm.groq_client import GroqConfig

            chosen_model = self._model or manifest.model
            groq = GroqClient(
                GroqConfig(
                    api_key=self._groq.config.api_key,
                    model=chosen_model,
                    temperature=self._groq.config.temperature,
                    max_tokens=self._groq.config.max_tokens,
                )
            )

        instructions = manifest.instructions
        if input.additional_instructions.strip():
            instructions += "\n\n" + input.additional_instructions.strip()

        child_engine = ReviewEngine(
            groq=groq,
            registry=child_registry,
            cwd=ctx.cwd,
            reviewer_name=manifest.name,
            reviewer_instructions=instructions,
        )
        # Share the coordinator's findings store.
        child_engine.findings = ctx.findings

        finding_count_before = len(ctx.findings)
        final: FinalEvent | None = None
        error_msg: str | None = None

        async for event in child_engine.review(self._task, self._payload):
            if isinstance(event, FinalEvent):
                final = event
            elif isinstance(event, ErrorEvent):
                error_msg = event.message

        new_findings = len(ctx.findings) - finding_count_before
        stop = final.stop_reason if final else "unknown"
        summary = (
            f"Reviewer '{manifest.name}' completed ({stop}). "
            f"Added {new_findings} finding(s)."
        )
        if error_msg:
            summary += f" Error encountered: {error_msg}"

        return ToolResult(ok=True, summary=summary, data=summary)

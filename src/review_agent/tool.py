"""ReviewTool ABC. Every tool the agent can call inherits from this.

Hard invariant: `is_read_only` MUST be the literal `True`. The registry rejects
anything else at registration time. There is no generic shell tool — analyzers
(git, linters, SAST) are individual subclasses with hardcoded argv lists.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, ClassVar, Generic, Literal, TypeVar

from pydantic import BaseModel

from review_agent.findings.store import FindingStore

I = TypeVar("I", bound=BaseModel)
O = TypeVar("O")


@dataclass
class ToolContext:
    """Per-call context passed into every tool invocation."""

    cwd: Path
    findings: FindingStore
    reviewer: str = "coordinator"
    extras: dict[str, Any] = field(default_factory=dict)


@dataclass
class ToolResult(Generic[O]):
    """Uniform return type. `summary` is what the LLM sees; `data` is structured payload."""

    ok: bool
    summary: str
    data: O | None = None
    error: str | None = None

    def to_llm_content(self) -> str:
        """Serialized form fed back to the LLM as the tool message content."""
        if not self.ok:
            return f"ERROR: {self.error or self.summary}"
        return self.summary


class ReviewTool(ABC, Generic[I, O]):
    name: ClassVar[str]
    description: ClassVar[str]
    input_model: ClassVar[type[BaseModel]]

    # Hard read-only invariant. Must be literal True.
    is_read_only: ClassVar[Literal[True]] = True
    is_concurrency_safe: ClassVar[bool] = True

    @abstractmethod
    async def call(self, input: I, ctx: ToolContext) -> ToolResult[O]: ...

    def openai_schema(self) -> dict[str, Any]:
        """Tool definition in OpenAI/Groq function-calling format."""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.input_model.model_json_schema(),
            },
        }

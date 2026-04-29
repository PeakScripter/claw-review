"""Core task and event types for the review engine.

A `ReviewTask` is the input to `ReviewEngine.review()`. An `Event` is what the
engine yields back — events are JSON-serializable so headless CLI mode can pipe
NDJSON straight to stdout.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field

from review_agent.findings.model import Finding


class PRTask(BaseModel):
    kind: Literal["pr"] = "pr"
    repo: str
    number: int


class DiffTask(BaseModel):
    kind: Literal["diff"] = "diff"
    base: str = "main"
    head: str = "HEAD"
    cwd: str = "."


class FilesTask(BaseModel):
    kind: Literal["files"] = "files"
    paths: list[str]
    cwd: str = "."


class RepoTask(BaseModel):
    kind: Literal["repo"] = "repo"
    cwd: str = "."


ReviewTask = Annotated[
    PRTask | DiffTask | FilesTask | RepoTask,
    Field(discriminator="kind"),
]


# --- Events emitted by ReviewEngine.review() -----------------------------------


class AssistantTextEvent(BaseModel):
    type: Literal["assistant_text"] = "assistant_text"
    text: str


class ToolUseEvent(BaseModel):
    type: Literal["tool_use"] = "tool_use"
    tool: str
    tool_call_id: str
    input: dict[str, Any]


class ToolResultEvent(BaseModel):
    type: Literal["tool_result"] = "tool_result"
    tool: str
    tool_call_id: str
    ok: bool
    summary: str
    data: Any | None = None


class FindingEvent(BaseModel):
    type: Literal["finding"] = "finding"
    finding: Finding


class FinalEvent(BaseModel):
    type: Literal["final"] = "final"
    findings: list[Finding]
    stop_reason: str


class ErrorEvent(BaseModel):
    type: Literal["error"] = "error"
    message: str
    recoverable: bool = False


Event = Annotated[
    AssistantTextEvent
    | ToolUseEvent
    | ToolResultEvent
    | FindingEvent
    | FinalEvent
    | ErrorEvent,
    Field(discriminator="type"),
]

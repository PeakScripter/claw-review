"""The `add_finding` tool — the only sink for review findings.

Every reviewer (coordinator and sub-reviewers alike) emits findings exclusively
through this tool. The store handles id assignment.
"""

from __future__ import annotations

from typing import ClassVar

from pydantic import BaseModel, Field

from review_agent.findings.model import Category, Finding, Severity
from review_agent.tool import ReviewTool, ToolContext, ToolResult


class AddFindingInput(BaseModel):
    severity: Severity
    category: Category
    file: str = Field(description="Repo-relative path where the issue lives.")
    line: int = Field(ge=1, description="1-indexed line number of the issue.")
    end_line: int | None = Field(default=None, ge=1)
    title: str = Field(min_length=1, max_length=200, description="One-line summary.")
    rationale: str = Field(min_length=1, description="Why this is a problem.")
    suggestion: str | None = Field(
        default=None,
        description="Suggested fix as PROSE only. Never include a code patch or diff.",
    )
    references: list[str] = Field(default_factory=list)
    confidence: float = Field(ge=0.0, le=1.0, default=0.7)
    evidence: str = Field(
        min_length=1,
        description=(
            "The exact line(s) you read from the file confirming this issue. "
            "You MUST call read_file first and quote the relevant code here. "
            "Example: 'Line 42: result = cache[key]  # KeyError if key absent'"
        ),
    )


class AddFindingTool(ReviewTool[AddFindingInput, Finding]):
    name: ClassVar[str] = "add_finding"
    description: ClassVar[str] = (
        "Record a code review finding. Use ONE call per distinct issue. "
        "`suggestion` must be prose; do not include code patches or diffs. "
        "Only emit findings you are at least 50% confident are real."
    )
    input_model: ClassVar[type[BaseModel]] = AddFindingInput
    is_read_only: ClassVar = True

    async def call(self, input: AddFindingInput, ctx: ToolContext) -> ToolResult[Finding]:
        finding = Finding(
            id=ctx.findings.next_id(),
            severity=input.severity,
            category=input.category,
            file=input.file,
            line=input.line,
            end_line=input.end_line,
            title=input.title,
            rationale=input.rationale,
            suggestion=input.suggestion,
            references=input.references,
            reviewer=ctx.reviewer,
            confidence=input.confidence,
            evidence=input.evidence,
        )
        ctx.findings.add(finding)
        return ToolResult(
            ok=True,
            summary=f"Recorded {finding.severity} finding {finding.id} at {finding.file}:{finding.line}",
            data=finding,
        )

"""RetractFindingTool — lets the LLM withdraw a previously emitted finding.

Used exclusively during the critique pass so the reviewer can remove findings
it cannot fully substantiate after re-examining the evidence.
"""

from __future__ import annotations

from typing import ClassVar

from pydantic import BaseModel, Field

from review_agent.tool import ReviewTool, ToolContext, ToolResult


class RetractFindingInput(BaseModel):
    id: str = Field(description="The finding id to retract, e.g. 'F0003-a1b2c3'.")
    reason: str = Field(min_length=1, description="Why this finding is being retracted.")


class RetractFindingTool(ReviewTool[RetractFindingInput, None]):
    name: ClassVar[str] = "retract_finding"
    description: ClassVar[str] = (
        "Withdraw a previously emitted finding that cannot be substantiated by "
        "directly-read code. Only call this during the critique pass. "
        "Provide the finding id and a brief reason."
    )
    input_model: ClassVar[type[BaseModel]] = RetractFindingInput
    is_read_only: ClassVar = True

    async def call(self, input: RetractFindingInput, ctx: ToolContext) -> ToolResult[None]:
        removed = ctx.findings.retract(input.id)
        if removed:
            return ToolResult(ok=True, summary=f"Retracted {input.id}: {input.reason}")
        return ToolResult(
            ok=False,
            summary=f"Finding {input.id!r} not found — already retracted or id is wrong.",
            error="not_found",
        )

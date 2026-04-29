"""The `Finding` schema — the single output type of every reviewer."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, field_validator

Severity = Literal["critical", "high", "medium", "low", "info"]
Category = Literal[
    "security",
    "correctness",
    "performance",
    "style",
    "tests",
    "architecture",
]

SEVERITY_ORDER: dict[str, int] = {
    "critical": 4,
    "high": 3,
    "medium": 2,
    "low": 1,
    "info": 0,
}


class Finding(BaseModel):
    """A single review finding. Suggestions are text-only — never a patch."""

    id: str = Field(description="Stable id within a single review.")
    severity: Severity
    category: Category
    file: str = Field(description="Repo-relative path.")
    line: int = Field(ge=1, description="1-indexed line number.")
    end_line: int | None = Field(default=None, ge=1)
    title: str = Field(min_length=1, max_length=200)
    rationale: str = Field(min_length=1, description="Why this is a problem.")
    suggestion: str | None = Field(
        default=None,
        description="Suggested fix as prose. NEVER a patch or replacement code block.",
    )
    references: list[str] = Field(default_factory=list)
    reviewer: str = Field(description="Name of the sub-reviewer that produced this.")
    confidence: float = Field(ge=0.0, le=1.0, default=0.7)
    evidence: str = Field(default="", description="Quoted code lines that confirm this issue.")

    @field_validator("end_line")
    @classmethod
    def _end_after_start(cls, v: int | None, info) -> int | None:
        if v is not None and "line" in info.data and v < info.data["line"]:
            raise ValueError("end_line must be >= line")
        return v

    def severity_rank(self) -> int:
        return SEVERITY_ORDER[self.severity]

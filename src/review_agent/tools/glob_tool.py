"""List files matching a glob pattern under the working directory."""

from __future__ import annotations

from typing import ClassVar

from pydantic import BaseModel, Field

from review_agent.tool import ReviewTool, ToolContext, ToolResult
from review_agent.tools._paths import PathOutsideRoot, resolve_within_cwd

MAX_RESULTS = 500
SKIP_DIRS = {".git", "node_modules", ".venv", "venv", "__pycache__", "dist", "build"}


class GlobInput(BaseModel):
    pattern: str = Field(description="Glob like `**/*.py` or `src/**/*.ts`.")
    path: str = Field(default=".", description="Base directory (repo-relative).")


class GlobTool(ReviewTool[GlobInput, list[str]]):
    name: ClassVar[str] = "glob"
    description: ClassVar[str] = (
        "List files matching a glob pattern under a directory. Useful before "
        "calling `read_file` to discover which files exist. Capped at 500 results."
    )
    input_model: ClassVar[type[BaseModel]] = GlobInput
    is_read_only: ClassVar = True

    async def call(self, input: GlobInput, ctx: ToolContext) -> ToolResult[list[str]]:
        try:
            base = resolve_within_cwd(ctx.cwd, input.path)
        except PathOutsideRoot as e:
            return ToolResult(ok=False, summary=str(e), error=str(e))
        if not base.is_dir():
            return ToolResult(
                ok=False, summary=f"Not a directory: {input.path}", error="not_a_dir"
            )

        results: list[str] = []
        for match in base.glob(input.pattern):
            if any(part in SKIP_DIRS for part in match.parts):
                continue
            if not match.is_file():
                continue
            rel = match.relative_to(ctx.cwd.resolve()).as_posix()
            results.append(rel)
            if len(results) >= MAX_RESULTS:
                break

        results.sort()
        truncated = len(results) >= MAX_RESULTS
        rendered = "\n".join(results) if results else "(no matches)"
        suffix = "\n…[truncated at 500 results]" if truncated else ""
        summary = f"{len(results)} file(s) matching {input.pattern!r}:\n{rendered}{suffix}"
        return ToolResult(ok=True, summary=summary, data=results)

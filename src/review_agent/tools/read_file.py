"""Read a file (or a line range of one). Output is line-numbered for citations."""

from __future__ import annotations

from typing import ClassVar

from pydantic import BaseModel, Field

from review_agent.tool import ReviewTool, ToolContext, ToolResult
from review_agent.tools._paths import PathOutsideRoot, resolve_within_cwd

MAX_LINES_PER_CALL = 800
MAX_BYTES_PER_LINE = 2000


class ReadFileInput(BaseModel):
    path: str = Field(description="Repo-relative path to read.")
    start_line: int | None = Field(
        default=None,
        ge=1,
        description="1-indexed first line to read. Default: from start.",
    )
    end_line: int | None = Field(
        default=None,
        ge=1,
        description="1-indexed last line (inclusive). Default: start_line+800.",
    )


class ReadFileTool(ReviewTool[ReadFileInput, str]):
    name: ClassVar[str] = "read_file"
    description: ClassVar[str] = (
        "Read a text file (or a line range). Output is line-numbered "
        "(`<lineno>: <content>`). Capped at 800 lines per call; pass "
        "`start_line`/`end_line` to page through larger files."
    )
    input_model: ClassVar[type[BaseModel]] = ReadFileInput
    is_read_only: ClassVar = True

    async def call(self, input: ReadFileInput, ctx: ToolContext) -> ToolResult[str]:
        try:
            full = resolve_within_cwd(ctx.cwd, input.path)
        except PathOutsideRoot as e:
            return ToolResult(ok=False, summary=str(e), error=str(e))

        if not full.is_file():
            return ToolResult(
                ok=False,
                summary=f"Not a file: {input.path}",
                error="not_a_file",
            )

        try:
            text = full.read_text(encoding="utf-8", errors="replace")
        except OSError as e:
            return ToolResult(ok=False, summary=f"Read error: {e}", error=str(e))

        lines = text.splitlines()
        start = (input.start_line or 1) - 1
        end = input.end_line if input.end_line is not None else start + MAX_LINES_PER_CALL
        end = min(end, start + MAX_LINES_PER_CALL, len(lines))
        start = max(start, 0)

        rendered: list[str] = []
        for i in range(start, end):
            line = lines[i]
            if len(line) > MAX_BYTES_PER_LINE:
                line = line[:MAX_BYTES_PER_LINE] + "  …[truncated]"
            rendered.append(f"{i + 1}: {line}")

        truncated = end < len(lines)
        header = f"{input.path} (lines {start + 1}-{end} of {len(lines)})"
        body = "\n".join(rendered)
        suffix = "\n…[truncated; call again with later start_line]" if truncated else ""
        return ToolResult(ok=True, summary=f"{header}\n{body}{suffix}", data=body)

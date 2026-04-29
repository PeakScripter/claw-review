"""Search the working tree for a regex.

Uses Python's `re` (not ripgrep) to avoid an external dependency. For the Phase 1
vertical slice this is fine; a ripgrep-backed variant can replace this without
changing the tool's input/output shape.
"""

from __future__ import annotations

import os
import re
from typing import ClassVar

from pydantic import BaseModel, Field

from review_agent.tool import ReviewTool, ToolContext, ToolResult
from review_agent.tools._paths import PathOutsideRoot, resolve_within_cwd

MAX_MATCHES = 200
MAX_FILES_SCANNED = 5000
MAX_BYTES_PER_FILE = 2_000_000  # 2 MB
SKIP_DIRS = {".git", "node_modules", ".venv", "venv", "__pycache__", "dist", "build", ".tox"}


class GrepInput(BaseModel):
    pattern: str = Field(description="Python regex pattern.")
    path: str = Field(default=".", description="Directory to search (repo-relative).")
    glob: str | None = Field(
        default=None,
        description="Optional path suffix filter, e.g. `.py` or `test_*.py`.",
    )
    case_sensitive: bool = Field(default=True)


class GrepMatch(BaseModel):
    file: str
    line: int
    text: str


class GrepTool(ReviewTool[GrepInput, list[GrepMatch]]):
    name: ClassVar[str] = "grep"
    description: ClassVar[str] = (
        "Search files under a path for a Python regex. Returns up to 200 matches "
        "as `<file>:<line>: <text>`. Use the `glob` field to restrict by extension "
        "or filename pattern."
    )
    input_model: ClassVar[type[BaseModel]] = GrepInput
    is_read_only: ClassVar = True
    is_concurrency_safe: ClassVar[bool] = True

    async def call(self, input: GrepInput, ctx: ToolContext) -> ToolResult[list[GrepMatch]]:
        try:
            root = resolve_within_cwd(ctx.cwd, input.path)
        except PathOutsideRoot as e:
            return ToolResult(ok=False, summary=str(e), error=str(e))

        if not root.exists():
            return ToolResult(ok=False, summary=f"No such path: {input.path}", error="missing")

        flags = 0 if input.case_sensitive else re.IGNORECASE
        try:
            pattern = re.compile(input.pattern, flags)
        except re.error as e:
            return ToolResult(ok=False, summary=f"Invalid regex: {e}", error=str(e))

        matches: list[GrepMatch] = []
        files_scanned = 0
        truncated = False

        if root.is_file():
            iterator = [root]
        else:
            iterator = _walk(root, input.glob)

        for path in iterator:
            if files_scanned >= MAX_FILES_SCANNED:
                truncated = True
                break
            files_scanned += 1
            try:
                if path.stat().st_size > MAX_BYTES_PER_FILE:
                    continue
                text = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            for lineno, line in enumerate(text.splitlines(), start=1):
                if pattern.search(line):
                    rel = str(path.relative_to(ctx.cwd.resolve()))
                    matches.append(GrepMatch(file=rel, line=lineno, text=line.rstrip()))
                    if len(matches) >= MAX_MATCHES:
                        truncated = True
                        break
            if truncated:
                break

        rendered = "\n".join(f"{m.file}:{m.line}: {m.text}" for m in matches) or "(no matches)"
        if truncated:
            rendered += f"\n…[truncated at {len(matches)} matches]"
        summary = f"{len(matches)} match(es) across {files_scanned} file(s).\n{rendered}"
        return ToolResult(ok=True, summary=summary, data=matches)


def _walk(root, glob_filter: str | None):
    from pathlib import Path

    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for fname in filenames:
            if glob_filter:
                # Simple suffix/wildcard match using fnmatch.
                from fnmatch import fnmatch

                if not (fnmatch(fname, glob_filter) or fname.endswith(glob_filter)):
                    continue
            yield Path(dirpath) / fname

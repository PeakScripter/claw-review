"""Git read-only operations: diff, show, log.

Hardcoded argv — never `shell=True`, never user-supplied executable. Refs and
paths are passed as positional arguments after `--`, so a user-controlled ref
that starts with `-` cannot be interpreted as a flag.
"""

from __future__ import annotations

import asyncio
import shutil
from typing import ClassVar, Literal

from pydantic import BaseModel, Field

from review_agent.tool import ReviewTool, ToolContext, ToolResult

MAX_OUTPUT_BYTES = 200_000

REF_RE = r"^[A-Za-z0-9._/\-@~^]+$"
RANGE_RE = r"^[A-Za-z0-9._/\-@~^]+\.\.[A-Za-z0-9._/\-@~^]+$"


class GitDiffInput(BaseModel):
    op: Literal["diff", "show", "log"] = Field(
        description="Which read-only git operation to run."
    )
    ref: str | None = Field(
        default=None,
        description="For `diff`: a range like `main..HEAD`. For `show`: a commit. "
        "For `log`: a ref or empty for HEAD.",
    )
    paths: list[str] = Field(default_factory=list, description="Limit to these paths.")
    max_count: int = Field(default=20, ge=1, le=200, description="For `log`: commit limit.")


class GitDiffTool(ReviewTool[GitDiffInput, str]):
    name: ClassVar[str] = "git_diff"
    description: ClassVar[str] = (
        "Run a read-only git operation: `diff` (with a `base..head` range), "
        "`show` (a commit), or `log` (recent commits). Output capped at 200KB. "
        "Refs and paths are passed positionally so they cannot be interpreted as flags."
    )
    input_model: ClassVar[type[BaseModel]] = GitDiffInput
    is_read_only: ClassVar = True
    is_concurrency_safe: ClassVar[bool] = True

    async def call(self, input: GitDiffInput, ctx: ToolContext) -> ToolResult[str]:
        import re

        git = shutil.which("git")
        if git is None:
            return ToolResult(
                ok=False, summary="git executable not found on PATH", error="no_git"
            )

        argv: list[str] = [git, "-C", str(ctx.cwd), "--no-pager"]
        if input.op == "diff":
            if input.ref and not re.match(RANGE_RE, input.ref) and not re.match(REF_RE, input.ref):
                return ToolResult(ok=False, summary="invalid ref", error="bad_ref")
            argv += ["diff", "--no-color"]
            if input.ref:
                argv.append(input.ref)
        elif input.op == "show":
            if not input.ref or not re.match(REF_RE, input.ref):
                return ToolResult(ok=False, summary="`show` needs a valid ref", error="bad_ref")
            argv += ["show", "--no-color", input.ref]
        elif input.op == "log":
            argv += [
                "log",
                "--no-color",
                f"--max-count={input.max_count}",
                "--pretty=format:%h %ad %an %s",
                "--date=short",
            ]
            if input.ref:
                if not re.match(REF_RE, input.ref):
                    return ToolResult(ok=False, summary="invalid ref", error="bad_ref")
                argv.append(input.ref)

        for p in input.paths:
            if p.startswith("-"):
                return ToolResult(ok=False, summary=f"invalid path: {p}", error="bad_path")
        if input.paths:
            argv.append("--")
            argv += input.paths

        proc = await asyncio.create_subprocess_exec(
            *argv,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=30)
        except TimeoutError:
            proc.kill()
            return ToolResult(ok=False, summary="git timed out", error="timeout")

        if proc.returncode != 0:
            err = stderr.decode("utf-8", errors="replace").strip()
            return ToolResult(
                ok=False, summary=f"git exit {proc.returncode}: {err}", error=err or "git_error"
            )

        body = stdout[:MAX_OUTPUT_BYTES].decode("utf-8", errors="replace")
        truncated = len(stdout) > MAX_OUTPUT_BYTES
        suffix = "\n…[truncated at 200KB]" if truncated else ""
        return ToolResult(ok=True, summary=body + suffix, data=body)

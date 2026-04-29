"""TypeCheckTool — mypy (Python) and tsc (TypeScript) wrappers.

Runs type checkers in read-only mode; never writes stubs or caches.
Output is parsed into Finding objects.
"""

from __future__ import annotations

import re
from typing import ClassVar, Literal

from pydantic import BaseModel, Field

from review_agent.findings.model import Finding
from review_agent.findings.store import FindingStore
from review_agent.tool import ReviewTool, ToolContext, ToolResult
from review_agent.tools._mypy_translate import translate as _mypy_translate
from review_agent.tools._subprocess import AnalyzerNotFound, run_analyzer


class TypeCheckInput(BaseModel):
    checker: Literal["auto", "mypy", "tsc"] = Field(
        default="auto",
        description=(
            "`auto` uses mypy if any .py files are present, tsc if tsconfig.json exists. "
            "Or specify `mypy` / `tsc` explicitly."
        ),
    )
    path: str = Field(
        default=".",
        description="Repo-relative path to type-check (file or directory).",
    )


class TypeCheckTool(ReviewTool[TypeCheckInput, list[Finding]]):
    name: ClassVar[str] = "type_check"
    description: ClassVar[str] = (
        "Run a type checker on the codebase. Python → mypy. TypeScript → tsc. "
        "Returns type errors as findings. Does not modify any files."
    )
    input_model: ClassVar[type[BaseModel]] = TypeCheckInput
    is_read_only: ClassVar = True

    async def call(self, input: TypeCheckInput, ctx: ToolContext) -> ToolResult[list[Finding]]:
        cwd = str(ctx.cwd)
        checker = input.checker
        if checker == "auto":
            checker = _detect_checker(ctx.cwd, input.path)
        if checker is None:
            return ToolResult(
                ok=False, summary="Cannot auto-detect type checker for this path.", error="no_checker"
            )
        if checker == "mypy":
            return await _run_mypy(input.path, cwd, ctx.findings, ctx.reviewer)
        if checker == "tsc":
            return await _run_tsc(cwd, ctx.findings, ctx.reviewer)
        return ToolResult(ok=False, summary=f"Unknown checker: {checker}", error="bad_checker")


def _detect_checker(cwd, path: str) -> str | None:
    from pathlib import Path
    p = (cwd / path) if path != "." else cwd
    if p.is_file() and p.suffix == ".py":
        return "mypy"
    if (cwd / "tsconfig.json").exists():
        return "tsc"
    if any(cwd.glob("**/*.py")):
        return "mypy"
    return None


# Mypy line format: path/to/file.py:10: error: message  [error-code]
_MYPY_LINE = re.compile(r"^(.+?):(\d+):\s+(error|warning|note):\s+(.+?)(?:\s+\[(.+?)\])?$")


async def _run_mypy(path: str, cwd: str, store: FindingStore, reviewer: str) -> ToolResult:
    try:
        result = await run_analyzer(
            "mypy",
            [
                "--no-color-output",
                "--no-error-summary",
                "--show-column-numbers",
                path,
            ],
            cwd=cwd,
        )
    except AnalyzerNotFound as e:
        return ToolResult(ok=False, summary=str(e), error="not_found")

    if result.timed_out:
        return ToolResult(ok=False, summary="mypy timed out", error="timeout")

    findings: list[Finding] = []
    for line in (result.stdout + result.stderr).splitlines():
        m = _MYPY_LINE.match(line)
        if not m:
            continue
        fpath, lineno, level, message, code = m.groups()
        if level == "note":
            continue
        t = _mypy_translate(message, code)
        f = Finding(
            id=store.next_id(),
            severity=t.severity,
            category=t.category,
            file=_relpath(fpath, cwd),
            line=int(lineno),
            title=t.title,
            rationale=t.rationale,
            reviewer=reviewer,
            confidence=0.85,
        )
        store.add(f)
        findings.append(f)

    summary = f"mypy found {len(findings)} issue(s)."
    return ToolResult(ok=True, summary=summary, data=findings)


# tsc output: path/to/file.ts(10,5): error TS1234: message
_TSC_LINE = re.compile(r"^(.+?)\((\d+),\d+\):\s+(?:error|warning)\s+(TS\d+):\s+(.+)$")


async def _run_tsc(cwd: str, store: FindingStore, reviewer: str) -> ToolResult:
    try:
        result = await run_analyzer(
            "tsc",
            ["--noEmit", "--pretty", "false"],
            cwd=cwd,
        )
    except AnalyzerNotFound as e:
        return ToolResult(ok=False, summary=str(e), error="not_found")

    if result.timed_out:
        return ToolResult(ok=False, summary="tsc timed out", error="timeout")

    findings: list[Finding] = []
    for line in (result.stdout + result.stderr).splitlines():
        m = _TSC_LINE.match(line)
        if not m:
            continue
        fpath, lineno, code, message = m.groups()
        f = Finding(
            id=store.next_id(),
            severity="medium",
            category="correctness",
            file=_relpath(fpath, cwd),
            line=int(lineno),
            title=f"[{code}] {message[:120]}",
            rationale=message,
            reviewer=reviewer,
            confidence=0.85,
        )
        store.add(f)
        findings.append(f)

    summary = f"tsc found {len(findings)} issue(s)."
    return ToolResult(ok=True, summary=summary, data=findings)


def _relpath(full: str, cwd: str) -> str:
    from pathlib import Path
    try:
        return str(Path(full).resolve().relative_to(Path(cwd).resolve()))
    except ValueError:
        return full

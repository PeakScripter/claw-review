"""LintTool — runs language linters and normalises output into Finding objects.

Supported adapters (auto-selected by file extension):
  - ruff   (Python)   — `ruff check --output-format json`
  - eslint (JS/TS)    — `eslint --format json`

The tool only runs the linter; it does NOT apply fixes.
"""

from __future__ import annotations

import json
from typing import ClassVar, Literal

from pydantic import BaseModel, Field

from review_agent.findings.model import Finding, Severity
from review_agent.findings.store import FindingStore
from review_agent.tool import ReviewTool, ToolContext, ToolResult
from review_agent.tools._paths import PathOutsideRoot, resolve_within_cwd
from review_agent.tools._subprocess import AnalyzerNotFound, run_analyzer


class LintInput(BaseModel):
    path: str = Field(description="Repo-relative file or directory to lint.")
    linter: Literal["auto", "ruff", "eslint"] = Field(
        default="auto",
        description="Linter to use. `auto` selects based on file extension.",
    )


class LintTool(ReviewTool[LintInput, list[Finding]]):
    name: ClassVar[str] = "lint"
    description: ClassVar[str] = (
        "Run a linter on a file or directory and return diagnostics as findings. "
        "Python → ruff. JavaScript/TypeScript → eslint. "
        "Only emits findings for real linter errors/warnings; does not apply fixes."
    )
    input_model: ClassVar[type[BaseModel]] = LintInput
    is_read_only: ClassVar = True

    async def call(self, input: LintInput, ctx: ToolContext) -> ToolResult[list[Finding]]:
        try:
            full = resolve_within_cwd(ctx.cwd, input.path)
        except PathOutsideRoot as e:
            return ToolResult(ok=False, summary=str(e), error=str(e))

        linter = input.linter
        if linter == "auto":
            linter = _detect_linter(input.path)

        if linter == "ruff":
            return await _run_ruff(str(full), str(ctx.cwd), ctx.findings, ctx.reviewer)
        if linter == "eslint":
            return await _run_eslint(str(full), str(ctx.cwd), ctx.findings, ctx.reviewer)
        return ToolResult(ok=False, summary=f"No linter for path: {input.path}", error="no_linter")


def _detect_linter(path: str) -> str | None:
    if path.endswith(".py"):
        return "ruff"
    if any(path.endswith(ext) for ext in (".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs")):
        return "eslint"
    return None


async def _run_ruff(path: str, cwd: str, store: FindingStore, reviewer: str) -> ToolResult:
    try:
        result = await run_analyzer(
            "ruff",
            ["check", "--output-format", "json", "--no-cache", path],
            cwd=cwd,
        )
    except AnalyzerNotFound as e:
        return ToolResult(ok=False, summary=str(e), error="not_found")

    if result.timed_out:
        return ToolResult(ok=False, summary="ruff timed out", error="timeout")

    if not result.stdout.strip():
        return ToolResult(ok=True, summary="ruff: no issues found.", data=[])

    try:
        items = json.loads(result.stdout)
    except json.JSONDecodeError:
        return ToolResult(ok=True, summary=f"ruff output (raw):\n{result.stdout}", data=[])

    findings: list[Finding] = []
    for item in items:
        sev: Severity = "low" if item.get("code", "").startswith(("W", "C")) else "medium"
        loc = item.get("location", {})
        f = Finding(
            id=store.next_id(),
            severity=sev,
            category="style",
            file=_relpath(item.get("filename", path), cwd),
            line=loc.get("row", 1),
            title=f"[{item.get('code', 'ruff')}] {item.get('message', '')}",
            rationale=item.get("message", ""),
            suggestion=item.get("fix", {}).get("message") if item.get("fix") else None,
            reviewer=reviewer,
            confidence=0.9,
        )
        store.add(f)
        findings.append(f)

    summary = f"ruff found {len(findings)} issue(s) in {path}."
    return ToolResult(ok=True, summary=summary, data=findings)


async def _run_eslint(path: str, cwd: str, store: FindingStore, reviewer: str) -> ToolResult:
    try:
        result = await run_analyzer(
            "eslint",
            ["--format", "json", "--no-eslintrc", path],
            cwd=cwd,
        )
    except AnalyzerNotFound as e:
        return ToolResult(ok=False, summary=str(e), error="not_found")

    if result.timed_out:
        return ToolResult(ok=False, summary="eslint timed out", error="timeout")

    try:
        items = json.loads(result.stdout or "[]")
    except json.JSONDecodeError:
        return ToolResult(ok=True, summary=f"eslint output (raw):\n{result.stdout}", data=[])

    findings: list[Finding] = []
    for file_result in items:
        fname = _relpath(file_result.get("filePath", path), cwd)
        for msg in file_result.get("messages", []):
            severity_num = msg.get("severity", 1)
            sev: Severity = "medium" if severity_num == 2 else "low"
            f = Finding(
                id=store.next_id(),
                severity=sev,
                category="style",
                file=fname,
                line=msg.get("line", 1),
                end_line=msg.get("endLine"),
                title=f"[{msg.get('ruleId', 'eslint')}] {msg.get('message', '')}",
                rationale=msg.get("message", ""),
                reviewer=reviewer,
                confidence=0.85,
            )
            store.add(f)
            findings.append(f)

    summary = f"eslint found {len(findings)} issue(s)."
    return ToolResult(ok=True, summary=summary, data=findings)


def _relpath(full: str, cwd: str) -> str:
    try:
        from pathlib import Path
        return str(Path(full).relative_to(Path(cwd)))
    except ValueError:
        return full

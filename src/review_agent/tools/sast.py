"""SASTTool — static analysis security testing wrappers.

Supported:
  - semgrep  (multi-language, OWASP rules)
  - bandit   (Python security linter)

Both tools are run in read-only mode.  If neither is installed the tool
returns a graceful error rather than crashing.
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

_SEMGREP_SEVERITY: dict[str, Severity] = {
    "ERROR": "high",
    "WARNING": "medium",
    "INFO": "low",
}

_BANDIT_SEVERITY: dict[str, Severity] = {
    "HIGH": "high",
    "MEDIUM": "medium",
    "LOW": "low",
}


class SASTInput(BaseModel):
    path: str = Field(default=".", description="Repo-relative path to scan.")
    tool: Literal["auto", "semgrep", "bandit"] = Field(
        default="auto",
        description="`auto` tries semgrep first, then bandit (Python only).",
    )
    ruleset: str = Field(
        default="p/owasp-top-ten",
        description="Semgrep ruleset / registry pack (ignored for bandit).",
    )


class SASTTool(ReviewTool[SASTInput, list[Finding]]):
    name: ClassVar[str] = "sast"
    description: ClassVar[str] = (
        "Run static analysis security testing (SAST) on the codebase. "
        "Finds vulnerabilities, injection flaws, hardcoded secrets, insecure patterns. "
        "Uses semgrep (multi-language) or bandit (Python)."
    )
    input_model: ClassVar[type[BaseModel]] = SASTInput
    is_read_only: ClassVar = True

    async def call(self, input: SASTInput, ctx: ToolContext) -> ToolResult[list[Finding]]:
        try:
            target = resolve_within_cwd(ctx.cwd, input.path)
        except PathOutsideRoot as e:
            return ToolResult(ok=False, summary=str(e), error=str(e))

        cwd = str(ctx.cwd)
        tool = input.tool

        if tool == "auto":
            # Prefer semgrep; fall back to bandit for Python paths.
            import shutil
            if shutil.which("semgrep"):
                tool = "semgrep"
            elif shutil.which("bandit"):
                tool = "bandit"
            else:
                return ToolResult(
                    ok=False,
                    summary="Neither semgrep nor bandit found on PATH. Install one to run SAST.",
                    error="not_found",
                )

        if tool == "semgrep":
            return await _run_semgrep(
                str(target), cwd, input.ruleset, ctx.findings, ctx.reviewer
            )
        if tool == "bandit":
            return await _run_bandit(str(target), cwd, ctx.findings, ctx.reviewer)
        return ToolResult(ok=False, summary=f"Unknown SAST tool: {tool}", error="bad_tool")


async def _run_semgrep(
    path: str, cwd: str, ruleset: str, store: FindingStore, reviewer: str
) -> ToolResult:
    try:
        result = await run_analyzer(
            "semgrep",
            ["--json", "--no-git-ignore", "--config", ruleset, path],
            cwd=cwd,
            timeout=120,
        )
    except AnalyzerNotFound as e:
        return ToolResult(ok=False, summary=str(e), error="not_found")

    if result.timed_out:
        return ToolResult(ok=False, summary="semgrep timed out (120s)", error="timeout")

    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError:
        return ToolResult(ok=True, summary=f"semgrep output (raw):\n{result.stdout[:2000]}", data=[])

    findings: list[Finding] = []
    for hit in data.get("results", []):
        meta = hit.get("extra", {})
        sev = _SEMGREP_SEVERITY.get(meta.get("severity", "WARNING").upper(), "medium")
        start = hit.get("start", {})
        end = hit.get("end", {})
        refs = []
        if meta.get("metadata", {}).get("cwe"):
            refs = meta["metadata"]["cwe"] if isinstance(meta["metadata"]["cwe"], list) else [meta["metadata"]["cwe"]]
        f = Finding(
            id=store.next_id(),
            severity=sev,
            category="security",
            file=_relpath(hit.get("path", path), cwd),
            line=start.get("line", 1),
            end_line=end.get("line") if end.get("line") != start.get("line") else None,
            title=f"[{hit.get('check_id', 'semgrep')}] {meta.get('message', '')[:120]}",
            rationale=meta.get("message", ""),
            references=refs,
            reviewer=reviewer,
            confidence=0.8,
        )
        store.add(f)
        findings.append(f)

    summary = f"semgrep found {len(findings)} issue(s) in {path}."
    return ToolResult(ok=True, summary=summary, data=findings)


async def _run_bandit(path: str, cwd: str, store: FindingStore, reviewer: str) -> ToolResult:
    try:
        result = await run_analyzer(
            "bandit",
            ["-r", "-f", "json", "-q", path],
            cwd=cwd,
            timeout=60,
        )
    except AnalyzerNotFound as e:
        return ToolResult(ok=False, summary=str(e), error="not_found")

    if result.timed_out:
        return ToolResult(ok=False, summary="bandit timed out", error="timeout")

    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError:
        return ToolResult(ok=True, summary=f"bandit output (raw):\n{result.stdout[:2000]}", data=[])

    findings: list[Finding] = []
    for issue in data.get("results", []):
        sev = _BANDIT_SEVERITY.get(issue.get("issue_severity", "MEDIUM").upper(), "medium")
        refs = [issue["more_info"]] if issue.get("more_info") else []
        f = Finding(
            id=store.next_id(),
            severity=sev,
            category="security",
            file=_relpath(issue.get("filename", path), cwd),
            line=issue.get("line_number", 1),
            title=f"[{issue.get('test_id', 'bandit')}] {issue.get('issue_text', '')[:120]}",
            rationale=issue.get("issue_text", ""),
            references=refs,
            reviewer=reviewer,
            confidence=float({"HIGH": 0.9, "MEDIUM": 0.75, "LOW": 0.6}.get(
                issue.get("issue_confidence", "MEDIUM"), 0.75
            )),
        )
        store.add(f)
        findings.append(f)

    summary = f"bandit found {len(findings)} issue(s) in {path}."
    return ToolResult(ok=True, summary=summary, data=findings)


def _relpath(full: str, cwd: str) -> str:
    from pathlib import Path
    try:
        return str(Path(full).resolve().relative_to(Path(cwd).resolve()))
    except ValueError:
        return full

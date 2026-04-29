"""DepAuditTool — dependency vulnerability auditing.

Adapters:
  pip-audit   Python (requires pip-audit ≥ 2.x)
  npm audit   Node.js
  govulncheck Go

All run in read-only mode; no packages are installed or updated.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import ClassVar, Literal

from pydantic import BaseModel, Field

from review_agent.findings.model import Finding
from review_agent.findings.store import FindingStore
from review_agent.tool import ReviewTool, ToolContext, ToolResult
from review_agent.tools._subprocess import AnalyzerNotFound, run_analyzer


class DepAuditInput(BaseModel):
    ecosystem: Literal["auto", "python", "node", "go"] = Field(
        default="auto",
        description="`auto` detects the ecosystem from manifest files in the working directory.",
    )


class DepAuditTool(ReviewTool[DepAuditInput, list[Finding]]):
    name: ClassVar[str] = "dep_audit"
    description: ClassVar[str] = (
        "Audit project dependencies for known CVEs / vulnerabilities. "
        "Python → pip-audit. Node.js → npm audit. Go → govulncheck. "
        "Only reads; does not update or install packages."
    )
    input_model: ClassVar[type[BaseModel]] = DepAuditInput
    is_read_only: ClassVar = True

    async def call(self, input: DepAuditInput, ctx: ToolContext) -> ToolResult[list[Finding]]:
        cwd = ctx.cwd
        eco = input.ecosystem
        if eco == "auto":
            eco = _detect_ecosystem(cwd)
        if eco is None:
            return ToolResult(
                ok=False,
                summary="Could not auto-detect ecosystem. Set ecosystem explicitly.",
                error="no_ecosystem",
            )
        if eco == "python":
            return await _pip_audit(str(cwd), ctx.findings, ctx.reviewer)
        if eco == "node":
            return await _npm_audit(str(cwd), ctx.findings, ctx.reviewer)
        if eco == "go":
            return await _govulncheck(str(cwd), ctx.findings, ctx.reviewer)
        return ToolResult(ok=False, summary=f"Unknown ecosystem: {eco}", error="bad_eco")


def _detect_ecosystem(cwd: Path) -> str | None:
    if (cwd / "requirements.txt").exists() or (cwd / "pyproject.toml").exists():
        return "python"
    if (cwd / "package.json").exists():
        return "node"
    if (cwd / "go.mod").exists():
        return "go"
    return None


async def _pip_audit(cwd: str, store: FindingStore, reviewer: str) -> ToolResult:
    try:
        result = await run_analyzer(
            "pip-audit",
            ["--format", "json", "--no-deps", "--progress-spinner", "off"],
            cwd=cwd,
            timeout=120,
        )
    except AnalyzerNotFound as e:
        return ToolResult(ok=False, summary=str(e), error="not_found")

    if result.timed_out:
        return ToolResult(ok=False, summary="pip-audit timed out", error="timeout")

    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError:
        return ToolResult(ok=True, summary=f"pip-audit (raw):\n{result.stdout[:2000]}", data=[])

    findings: list[Finding] = []
    for dep in data.get("dependencies", []):
        for vuln in dep.get("vulns", []):
            aliases = vuln.get("aliases", [])
            cve_ids = [a for a in aliases if a.startswith("CVE-")]
            refs = [f"https://osv.dev/vulnerability/{vuln['id']}"]
            if cve_ids:
                refs += [f"https://nvd.nist.gov/vuln/detail/{c}" for c in cve_ids]
            f = Finding(
                id=store.next_id(),
                severity="high",
                category="security",
                file="requirements.txt",
                line=1,
                title=f"{dep['name']}=={dep['version']} — {vuln['id']}",
                rationale=(
                    f"{dep['name']} version {dep['version']} has known vulnerability "
                    f"{vuln['id']}: {vuln.get('description', 'see references')}. "
                    f"Fixed in: {', '.join(vuln.get('fix_versions', ['unknown']))}."
                ),
                references=refs,
                reviewer=reviewer,
                confidence=0.95,
            )
            store.add(f)
            findings.append(f)

    summary = (
        f"pip-audit: {len(findings)} vulnerable package(s) found."
        if findings
        else "pip-audit: no vulnerabilities found."
    )
    return ToolResult(ok=True, summary=summary, data=findings)


async def _npm_audit(cwd: str, store: FindingStore, reviewer: str) -> ToolResult:
    try:
        result = await run_analyzer(
            "npm",
            ["audit", "--json"],
            cwd=cwd,
            timeout=60,
        )
    except AnalyzerNotFound as e:
        return ToolResult(ok=False, summary=str(e), error="not_found")

    if result.timed_out:
        return ToolResult(ok=False, summary="npm audit timed out", error="timeout")

    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError:
        return ToolResult(ok=True, summary=f"npm audit (raw):\n{result.stdout[:2000]}", data=[])

    findings: list[Finding] = []
    _severity_map = {"critical": "critical", "high": "high", "moderate": "medium", "low": "low", "info": "info"}

    # npm audit v7+ format uses "vulnerabilities" key
    vulns = data.get("vulnerabilities", {})
    for pkg_name, info in vulns.items():
        if not info.get("via"):
            continue
        sev = _severity_map.get(info.get("severity", "high"), "high")
        via = info.get("via", [])
        # "via" entries can be strings (transitive) or dicts (direct vuln).
        direct = [v for v in via if isinstance(v, dict)]
        if not direct:
            continue
        for v in direct:
            url = v.get("url", "")
            f = Finding(
                id=store.next_id(),
                severity=sev,
                category="security",
                file="package.json",
                line=1,
                title=f"{pkg_name}: {v.get('title', 'vulnerability')}",
                rationale=v.get("overview", f"Vulnerable package: {pkg_name}"),
                references=[url] if url else [],
                reviewer=reviewer,
                confidence=0.9,
            )
            store.add(f)
            findings.append(f)

    summary = (
        f"npm audit: {len(findings)} vulnerability(ies) found."
        if findings
        else "npm audit: no vulnerabilities found."
    )
    return ToolResult(ok=True, summary=summary, data=findings)


async def _govulncheck(cwd: str, store: FindingStore, reviewer: str) -> ToolResult:
    try:
        result = await run_analyzer(
            "govulncheck",
            ["-json", "./..."],
            cwd=cwd,
            timeout=120,
        )
    except AnalyzerNotFound as e:
        return ToolResult(ok=False, summary=str(e), error="not_found")

    if result.timed_out:
        return ToolResult(ok=False, summary="govulncheck timed out", error="timeout")

    findings: list[Finding] = []
    for line in result.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        vuln = obj.get("vulnerability")
        if not vuln:
            continue
        refs = [a.get("url", "") for a in vuln.get("aliases", []) if a.get("url")]
        f = Finding(
            id=store.next_id(),
            severity="high",
            category="security",
            file="go.mod",
            line=1,
            title=f"{vuln.get('id', 'GO-?')}: {vuln.get('details', '')[:80]}",
            rationale=vuln.get("details", ""),
            references=refs or [f"https://pkg.go.dev/vuln/{vuln.get('id', '')}"],
            reviewer=reviewer,
            confidence=0.9,
        )
        store.add(f)
        findings.append(f)

    summary = (
        f"govulncheck: {len(findings)} vulnerability(ies) found."
        if findings
        else "govulncheck: no vulnerabilities found."
    )
    return ToolResult(ok=True, summary=summary, data=findings)

"""TestRunTool — run the test suite (read-only) and report failures as findings.

Only executes test runners; never modifies source files.
Supported: pytest (Python), jest (Node.js).

Failures become `tests` category findings so the security/correctness reviewers
can correlate test failures with the code they're reviewing.
"""

from __future__ import annotations

import re
from typing import ClassVar, Literal

from pydantic import BaseModel, Field

from review_agent.findings.model import Finding
from review_agent.findings.store import FindingStore
from review_agent.tool import ReviewTool, ToolContext, ToolResult
from review_agent.tools._subprocess import AnalyzerNotFound, run_analyzer


class TestRunInput(BaseModel):
    runner: Literal["auto", "pytest", "jest"] = Field(
        default="auto",
        description="`auto` detects runner from pyproject.toml / package.json.",
    )
    path: str = Field(
        default=".",
        description="Repo-relative path to the test directory or specific test file.",
    )
    fail_fast: bool = Field(
        default=False,
        description="Stop after first failure (pytest -x / jest --bail).",
    )


class TestRunTool(ReviewTool[TestRunInput, list[Finding]]):
    name: ClassVar[str] = "test_run"
    description: ClassVar[str] = (
        "Run the test suite and report failures as findings. "
        "Python → pytest. Node.js → jest. "
        "Does not modify source files. "
        "Use to confirm whether the diff breaks existing tests."
    )
    input_model: ClassVar[type[BaseModel]] = TestRunInput
    is_read_only: ClassVar = True

    async def call(self, input: TestRunInput, ctx: ToolContext) -> ToolResult[list[Finding]]:
        cwd = str(ctx.cwd)
        runner = input.runner
        if runner == "auto":
            runner = _detect_runner(ctx.cwd)
        if runner is None:
            return ToolResult(
                ok=False,
                summary="Cannot detect test runner. Set runner explicitly.",
                error="no_runner",
            )
        if runner == "pytest":
            return await _run_pytest(input.path, cwd, input.fail_fast, ctx.findings, ctx.reviewer)
        if runner == "jest":
            return await _run_jest(input.path, cwd, input.fail_fast, ctx.findings, ctx.reviewer)
        return ToolResult(ok=False, summary=f"Unknown runner: {runner}", error="bad_runner")


def _detect_runner(cwd) -> str | None:
    from pathlib import Path
    if (cwd / "pyproject.toml").exists() or (cwd / "pytest.ini").exists() or (cwd / "setup.cfg").exists():
        return "pytest"
    if (cwd / "package.json").exists():
        try:
            import json
            pkg = json.loads((cwd / "package.json").read_text())
            if "jest" in pkg.get("devDependencies", {}):
                return "jest"
        except Exception:
            pass
    return None


# pytest short output: FAILED tests/test_foo.py::test_bar - AssertionError: ...
_PYTEST_FAILED = re.compile(r"^FAILED (.+?)::(.+?) - (.+)$")
# pytest location line: tests/test_foo.py:42
_PYTEST_LOCATION = re.compile(r"^(.+?):(\d+):")


async def _run_pytest(
    path: str, cwd: str, fail_fast: bool, store: FindingStore, reviewer: str
) -> ToolResult:
    args = ["-q", "--tb=short", "--no-header", path]
    if fail_fast:
        args.insert(0, "-x")
    try:
        result = await run_analyzer("pytest", args, cwd=cwd, timeout=120)
    except AnalyzerNotFound as e:
        return ToolResult(ok=False, summary=str(e), error="not_found")

    if result.timed_out:
        return ToolResult(ok=False, summary="pytest timed out", error="timeout")

    output = result.stdout + result.stderr
    findings: list[Finding] = []
    current_file, current_line = "unknown", 1

    for line in output.splitlines():
        # Try to grab a file:line reference from the traceback.
        loc_m = _PYTEST_LOCATION.match(line)
        if loc_m and loc_m.group(1).endswith(".py"):
            current_file, current_line = loc_m.group(1), int(loc_m.group(2))

        m = _PYTEST_FAILED.match(line)
        if m:
            test_path, test_name, reason = m.groups()
            f = Finding(
                id=store.next_id(),
                severity="high",
                category="tests",
                file=test_path,
                line=current_line,
                title=f"Test failure: {test_name}",
                rationale=reason.strip(),
                reviewer=reviewer,
                confidence=0.95,
            )
            store.add(f)
            findings.append(f)

    summary = (
        f"pytest: {len(findings)} test failure(s).\n{output[-3000:]}"
        if findings
        else f"pytest: all tests passed.\n{output[-1000:]}"
    )
    return ToolResult(ok=True, summary=summary, data=findings)


async def _run_jest(
    path: str, cwd: str, fail_fast: bool, store: FindingStore, reviewer: str
) -> ToolResult:
    args = ["--json", "--no-coverage", path]
    if fail_fast:
        args.append("--bail")
    try:
        result = await run_analyzer("jest", args, cwd=cwd, timeout=120)
    except AnalyzerNotFound as e:
        return ToolResult(ok=False, summary=str(e), error="not_found")

    if result.timed_out:
        return ToolResult(ok=False, summary="jest timed out", error="timeout")

    import json
    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError:
        return ToolResult(ok=True, summary=f"jest output (raw):\n{result.stdout[:2000]}", data=[])

    findings: list[Finding] = []
    for suite in data.get("testResults", []):
        for test in suite.get("testResults", []):
            if test.get("status") != "failed":
                continue
            f = Finding(
                id=store.next_id(),
                severity="high",
                category="tests",
                file=_relpath(suite.get("testFilePath", "unknown"), cwd),
                line=1,
                title=f"Test failure: {test.get('fullName', '?')}",
                rationale=" ".join(test.get("failureMessages", [])),
                reviewer=reviewer,
                confidence=0.95,
            )
            store.add(f)
            findings.append(f)

    summary = (
        f"jest: {len(findings)} test failure(s)."
        if findings
        else "jest: all tests passed."
    )
    return ToolResult(ok=True, summary=summary, data=findings)


def _relpath(full: str, cwd: str) -> str:
    from pathlib import Path
    try:
        return str(Path(full).resolve().relative_to(Path(cwd).resolve()))
    except ValueError:
        return full

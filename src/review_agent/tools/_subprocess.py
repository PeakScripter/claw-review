"""Shared subprocess helper for analyzer tools.

Every analyzer tool calls `run_analyzer()` which:
- Resolves the binary via shutil.which (no user-supplied executable names).
- Runs it with a hardcoded argv list (no shell=True, no string interpolation).
- Returns stdout, stderr, and returncode.
- Times out after `timeout` seconds and kills the process.
"""

from __future__ import annotations

import asyncio
import shutil
from dataclasses import dataclass


@dataclass
class AnalyzerResult:
    stdout: str
    stderr: str
    returncode: int
    timed_out: bool = False


class AnalyzerNotFound(RuntimeError):
    pass


async def run_analyzer(
    binary: str,
    args: list[str],
    cwd: str,
    timeout: float = 60.0,
    max_output_bytes: int = 500_000,
) -> AnalyzerResult:
    """Run a whitelisted binary with a hardcoded argv list.

    `binary` must be a plain name (no slashes). It is resolved via shutil.which
    so PATH injection via a crafted name is not possible.
    """
    if "/" in binary or "\\" in binary:
        raise ValueError(f"binary name must not contain path separators: {binary!r}")

    exe = shutil.which(binary)
    if exe is None:
        raise AnalyzerNotFound(f"`{binary}` not found on PATH.")

    proc = await asyncio.create_subprocess_exec(
        exe,
        *args,
        cwd=cwd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )

    try:
        raw_stdout, raw_stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except TimeoutError:
        proc.kill()
        await proc.communicate()
        return AnalyzerResult(stdout="", stderr="", returncode=-1, timed_out=True)

    stdout = raw_stdout[:max_output_bytes].decode("utf-8", errors="replace")
    stderr = raw_stderr[:100_000].decode("utf-8", errors="replace")
    return AnalyzerResult(stdout=stdout, stderr=stderr, returncode=proc.returncode)

"""Hook runner — execute lifecycle hooks from settings.toml.

Hooks are synchronous shell commands run via subprocess.  They receive the
event payload as JSON on stdin.  Pre-hooks (PreReview, PreToolUse) can block
by exiting non-zero.  Post-hooks (PostFinding, PostReview) are fire-and-forget.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any


class HookEvent(str, Enum):
    PRE_REVIEW = "PreReview"
    PRE_TOOL_USE = "PreToolUse"
    POST_FINDING = "PostFinding"
    POST_REVIEW = "PostReview"


@dataclass
class HookResult:
    blocked: bool = False
    reason: str = ""
    output: str = ""


@dataclass
class HookRunner:
    """Loaded from the `[hooks]` section of .review/settings.toml."""

    hooks: dict[str, list[str]] = field(default_factory=dict)
    cwd: Path = field(default_factory=Path.cwd)
    timeout: float = 30.0

    def fire(self, event: HookEvent, payload: Any) -> HookResult:
        """Run all hook commands for the given event.

        Pre-hooks (PreReview, PreToolUse) block on non-zero exit.
        Post-hooks are best-effort.
        """
        commands = self.hooks.get(event.value, [])
        is_pre = event in (HookEvent.PRE_REVIEW, HookEvent.PRE_TOOL_USE)
        payload_bytes = json.dumps(payload, default=str).encode()

        for cmd in commands:
            result = self._run(cmd, payload_bytes)
            if is_pre and result.returncode != 0:
                stderr_text = result.stderr.decode("utf-8", errors="replace") if isinstance(result.stderr, bytes) else result.stderr
                stdout_text = result.stdout.decode("utf-8", errors="replace") if isinstance(result.stdout, bytes) else result.stdout
                return HookResult(
                    blocked=True,
                    reason=f"Hook '{cmd}' blocked with exit {result.returncode}: "
                    + (stderr_text or stdout_text).strip(),
                    output=stdout_text,
                )
        return HookResult(blocked=False)

    def _run(self, cmd: str, stdin_bytes: bytes) -> subprocess.CompletedProcess:
        # Use sh for POSIX-compatible execution. If the command is a path to an
        # existing file, run it directly as `sh <posix_path>` so Windows-style
        # backslashes in the path don't get mangled by the shell interpreter.
        # For inline commands (e.g. "exit 0") use `sh -c <cmd>`.
        # Fall back to cmd.exe shell=True when sh is not available.
        sh = shutil.which("sh")
        cmd_path = Path(cmd)
        if sh:
            if cmd_path.is_file():
                argv: list[str] | str = [sh, cmd_path.as_posix()]
            else:
                argv = [sh, "-c", cmd]
            use_shell = False
        else:
            argv = cmd
            use_shell = True

        # Only pass cwd if the directory actually exists; a missing cwd (e.g.
        # /tmp on Windows) raises FileNotFoundError before the command runs.
        actual_cwd = self.cwd if self.cwd.is_dir() else None

        try:
            return subprocess.run(
                argv,
                shell=use_shell,
                input=stdin_bytes,
                capture_output=True,
                cwd=actual_cwd,
                timeout=self.timeout,
            )
        except subprocess.TimeoutExpired:
            return subprocess.CompletedProcess(cmd, returncode=-1, stdout=b"", stderr=b"timed out")
        except Exception as e:
            return subprocess.CompletedProcess(cmd, returncode=-1, stdout=b"", stderr=str(e).encode())


def load_hooks(cwd: Path) -> HookRunner:
    """Load hook definitions from .review/settings.toml."""
    settings_path = cwd / ".review" / "settings.toml"
    if not settings_path.is_file():
        return HookRunner(cwd=cwd)
    try:
        import tomllib
    except ImportError:
        try:
            import tomli as tomllib  # type: ignore[no-redef]
        except ImportError:
            return HookRunner(cwd=cwd)
    try:
        data = tomllib.loads(settings_path.read_text())
        raw_hooks = data.get("hooks", {})
        # Normalise: each value can be a string or a list of strings.
        hooks: dict[str, list[str]] = {}
        for k, v in raw_hooks.items():
            hooks[k] = [v] if isinstance(v, str) else list(v)
        return HookRunner(hooks=hooks, cwd=cwd)
    except Exception:
        return HookRunner(cwd=cwd)

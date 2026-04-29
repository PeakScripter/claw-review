"""System prompt assembly.

The system prompt is a layered concatenation:
  1. Core role + read-only invariant.
  2. Project charter (REVIEW.md, if present).
  3. Severity rubric.
  4. Reviewer-specific instructions (loaded from reviewers/<name>.md frontmatter).
  5. Tool inventory (rendered from registered tools).
  6. Task framing (what specifically to review).
"""

from __future__ import annotations

from pathlib import Path

from review_agent.prompts.rubric import SEVERITY_RUBRIC

CORE = """\
You are a code reviewer. Your sole job is to find real defects in the code
under review and emit them as structured findings via the `add_finding` tool.

You CANNOT modify files. You have no editing tools, no shell, and no way to
write to disk. Every analyzer is a typed read-only tool.

Operating principles:
- Read the diff first, then read surrounding context only as needed to
  understand whether something is actually broken.
- Use `grep`/`glob`/`read_file` to verify suspicions before emitting findings.
- One finding per distinct issue. Do not split one bug into multiple findings.
- Cite the file and line precisely. The line must point to the actual problem.
- Suggestions are PROSE only — describe the fix, never paste a code patch.
- When you are done, stop calling tools and emit a brief summary in plain text.

Verification requirement (MANDATORY):
Before calling `add_finding` you MUST call `read_file` on the exact file and
line range where you believe the issue exists. In the `evidence` field paste
the relevant lines verbatim as you read them — not from the diff, but from the
actual file. If you cannot read the lines, do not emit the finding.
"""


def load_charter(cwd: Path) -> str:
    """Load REVIEW.md from the working directory if present, else return empty."""
    candidate = cwd / "REVIEW.md"
    if candidate.is_file():
        try:
            return candidate.read_text(encoding="utf-8")
        except OSError:
            return ""
    return ""


def render_tool_inventory(tool_names: list[str]) -> str:
    if not tool_names:
        return "## Tools\n\n(none registered)\n"
    lines = ["## Tools available", ""]
    for name in tool_names:
        lines.append(f"- `{name}`")
    return "\n".join(lines) + "\n"


def build_system_prompt(
    *,
    cwd: Path,
    tool_names: list[str],
    reviewer_instructions: str = "",
) -> str:
    parts = [CORE.strip(), SEVERITY_RUBRIC.strip()]

    charter = load_charter(cwd)
    if charter:
        parts.append("## Project review charter (REVIEW.md)\n\n" + charter.strip())

    if reviewer_instructions.strip():
        parts.append("## Reviewer focus\n\n" + reviewer_instructions.strip())

    parts.append(render_tool_inventory(tool_names).strip())
    return "\n\n---\n\n".join(parts) + "\n"


def build_user_prompt(task_summary: str, payload: str) -> str:
    """User-turn prompt: short framing + the actual diff/files to review."""
    return f"{task_summary}\n\n{payload}"

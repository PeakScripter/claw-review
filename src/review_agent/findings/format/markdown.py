"""Render findings as a human-readable markdown report grouped by severity."""

from __future__ import annotations

import re
from collections import defaultdict
from collections.abc import Iterable

from review_agent.findings.model import SEVERITY_ORDER, Finding

_MAX_BULLETS = 6  # cap per group before "…and N more"


def format_markdown(findings: Iterable[Finding]) -> str:
    findings = list(findings)
    if not findings:
        return "# Code Review\n\nNo findings.\n"

    groups: dict[str, list[Finding]] = defaultdict(list)
    for f in findings:
        groups[f.severity].append(f)

    lines: list[str] = ["# Code Review", ""]
    lines.append(_summary_section(findings))
    lines.append("---")
    lines.append("")

    for severity in sorted(groups.keys(), key=lambda s: -SEVERITY_ORDER[s]):
        bucket = groups[severity]
        lines.append(f"## {severity.upper()} ({len(bucket)})")
        lines.append("")
        for f in bucket:
            lines.extend(_render_finding(f))
            lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _summary_section(findings: list[Finding]) -> str:
    """Opinionated digest grouped by semantic impact rather than raw severity label."""
    runtime   = [f for f in findings if f.severity in ("critical", "high")]
    risks     = [f for f in findings if f.severity == "medium"]
    systemic  = [f for f in findings if f.severity in ("low", "info")]

    lines: list[str] = ["## Summary", ""]

    if runtime:
        n = len(runtime)
        lines.append(f"**{n} {'issue' if n == 1 else 'issues'} affecting runtime behavior:**")
        lines.extend(_bullets(runtime))
        lines.append("")

    if risks:
        n = len(risks)
        lines.append(f"**{n} correctness {'risk' if n == 1 else 'risks'}:**")
        lines.extend(_bullets(risks))
        lines.append("")

    if systemic:
        total = sum(_occurrence_count(f) for f in systemic)
        n_patterns = len(systemic)
        if total == n_patterns:
            label = f"{total} systemic {'issue' if total == 1 else 'issues'}"
        else:
            label = f"{total} systemic {'occurrence' if total == 1 else 'occurrences'} across {n_patterns} {'pattern' if n_patterns == 1 else 'patterns'}"
        lines.append(f"**{label}:**")
        lines.extend(_bullets(systemic))
        lines.append("")

    return "\n".join(lines)


def _bullets(bucket: list[Finding]) -> list[str]:
    """Return bulleted title lines, capped at _MAX_BULLETS."""
    out = [f"- {f.title}" for f in bucket[:_MAX_BULLETS]]
    overflow = len(bucket) - _MAX_BULLETS
    if overflow > 0:
        out.append(f"- _…and {overflow} more_")
    return out


def _occurrence_count(f: Finding) -> int:
    """Total occurrences represented by a (possibly deduplicated) finding."""
    m = re.search(r"(\d+) additional location", f.rationale)
    return 1 + int(m.group(1)) if m else 1


def _render_finding(f: Finding) -> list[str]:
    range_str = f"{f.line}" if f.end_line is None else f"{f.line}-{f.end_line}"
    lines = [
        f"### `{f.id}` {f.title}",
        "",
        f"- **Where:** `{f.file}:{range_str}`",
        f"- **Category:** {f.category}",
        f"- **Reviewer:** {f.reviewer}",
        f"- **Confidence:** {f.confidence:.2f}",
        "",
        f"**Why:** {f.rationale}",
    ]
    if f.evidence:
        lines += ["", f"**Evidence:** `{f.evidence}`"]
    if f.suggestion:
        lines += ["", f"**Suggestion:** {f.suggestion}"]
    if f.references:
        lines += ["", "**References:**"]
        lines += [f"- {ref}" for ref in f.references]
    return lines

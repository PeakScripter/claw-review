"""GitHub PR review comment formatter.

Converts findings into the payload consumed by `action.yml`'s
`actions/github-script` step to post inline review comments.

The agent NEVER calls the GitHub API directly.  This formatter produces JSON
that the Action reads and posts using its own GITHUB_TOKEN.
"""

from __future__ import annotations

import json
from collections.abc import Iterable

from review_agent.findings.model import Finding, Severity

_EMOJI: dict[Severity, str] = {
    "critical": "🔴",
    "high": "🟠",
    "medium": "🟡",
    "low": "🔵",
    "info": "⚪",
}


def format_github(findings: Iterable[Finding], *, commit_id: str = "") -> str:
    """Return a JSON array of GitHub review comment objects.

    Each object has the shape expected by the GitHub REST API
    POST /repos/{owner}/{repo}/pulls/{pull_number}/reviews endpoint
    (specifically the `comments` array).
    """
    comments = []
    for f in findings:
        body = _build_body(f)
        comment: dict = {
            "path": f.file,
            "line": f.line,
            "body": body,
            # side is always RIGHT (new version of the file)
            "side": "RIGHT",
        }
        if f.end_line and f.end_line != f.line:
            comment["start_line"] = f.line
            comment["start_side"] = "RIGHT"
            comment["line"] = f.end_line
        if commit_id:
            comment["commit_id"] = commit_id
        comments.append(comment)

    return json.dumps({"comments": comments}, indent=2, ensure_ascii=False)


def _build_body(f: Finding) -> str:
    em = _EMOJI[f.severity]
    lines = [
        f"{em} **[{f.severity.upper()}]** {f.title}",
        "",
        f.rationale,
    ]
    if f.suggestion:
        lines += ["", f"**Suggestion:** {f.suggestion}"]
    if f.references:
        lines += ["", "**References:** " + " · ".join(f"[{r}]({r})" for r in f.references)]
    lines += [
        "",
        f"<sub>reviewer: {f.reviewer} · confidence: {f.confidence:.0%} · id: {f.id}</sub>",
    ]
    return "\n".join(lines)

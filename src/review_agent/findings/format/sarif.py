"""SARIF 2.1.0 formatter.

SARIF is the standard format for uploading static analysis results to GitHub
Code Scanning (via the upload-sarif action).  Producing valid SARIF lets CI
annotate PRs with inline findings at exactly the right file/line without any
additional tooling.

Reference: https://docs.oasis-open.org/sarif/sarif/v2.1.0/sarif-v2.1.0.html
"""

from __future__ import annotations

import json
from collections import defaultdict
from collections.abc import Iterable

from review_agent.findings.model import Finding, Severity

_SARIF_LEVEL: dict[Severity, str] = {
    "critical": "error",
    "high": "error",
    "medium": "warning",
    "low": "note",
    "info": "note",
}

_TOOL_NAME = "review-agent"
_TOOL_URI = "https://github.com/peakscripter/ai-code-review"
_SCHEMA = "https://raw.githubusercontent.com/oasis-tcs/sarif-spec/master/Schemata/sarif-schema-2.1.0.json"


def format_sarif(findings: Iterable[Finding], *, repo_root: str = "") -> str:
    """Return SARIF 2.1.0 JSON string suitable for upload to GitHub code scanning."""
    findings = list(findings)

    # Group findings by reviewer so each reviewer appears as a separate "run"
    # with its own rules list, which makes the Code Scanning UI cleaner.
    by_reviewer: dict[str, list[Finding]] = defaultdict(list)
    for f in findings:
        by_reviewer[f.reviewer].append(f)

    runs = [_build_run(reviewer, group) for reviewer, group in by_reviewer.items()]
    if not runs:
        # Always emit at least one empty run so the SARIF is valid.
        runs = [_build_run(_TOOL_NAME, [])]

    doc = {
        "$schema": _SCHEMA,
        "version": "2.1.0",
        "runs": runs,
    }
    return json.dumps(doc, indent=2, ensure_ascii=False)


def _build_run(reviewer: str, findings: list[Finding]) -> dict:
    # Collect unique rule IDs from findings.
    rule_ids: dict[str, str] = {}  # id → category
    for f in findings:
        rule_id = _rule_id(f)
        rule_ids[rule_id] = f.category

    rules = [
        {
            "id": rid,
            "name": rid,
            "properties": {"tags": [cat]},
        }
        for rid, cat in rule_ids.items()
    ]

    results = [_build_result(f) for f in findings]

    return {
        "tool": {
            "driver": {
                "name": f"{_TOOL_NAME}/{reviewer}",
                "informationUri": _TOOL_URI,
                "version": "0.1.0",
                "rules": rules,
            }
        },
        "results": results,
    }


def _build_result(f: Finding) -> dict:
    region: dict = {"startLine": f.line}
    if f.end_line:
        region["endLine"] = f.end_line

    message_text = f.rationale
    if f.suggestion:
        message_text += f"\n\nSuggestion: {f.suggestion}"

    result: dict = {
        "ruleId": _rule_id(f),
        "level": _SARIF_LEVEL[f.severity],
        "message": {"text": message_text},
        "locations": [
            {
                "physicalLocation": {
                    "artifactLocation": {
                        "uri": f.file,
                        "uriBaseId": "%SRCROOT%",
                    },
                    "region": region,
                }
            }
        ],
        "properties": {
            "severity": f.severity,
            "category": f.category,
            "reviewer": f.reviewer,
            "confidence": f.confidence,
            "findingId": f.id,
        },
    }

    if f.references:
        result["relatedLocations"] = []  # not the right field, but keep refs
        result["properties"]["references"] = f.references

    return result


def _rule_id(f: Finding) -> str:
    return f"review-agent/{f.category}/{f.severity}"

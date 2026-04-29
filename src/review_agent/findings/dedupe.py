"""Deduplicate findings from multiple sub-reviewers.

Two findings are considered overlapping if they share the same file, their
line ranges intersect, and they have the same category. When duplicates are
found the one with the higher severity is kept and all rationales are merged.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable

from review_agent.findings.model import Finding


def dedupe(findings: Iterable[Finding], *, min_confidence: float = 0.0) -> list[Finding]:
    """Return a deduplicated list, highest-severity version of each cluster first.

    Findings with confidence below `min_confidence` are dropped before grouping.
    """
    all_findings = [f for f in findings if f.confidence >= min_confidence]
    if not all_findings:
        return []

    # Group by (file, category) — only these can be duplicates.
    groups: dict[tuple[str, str], list[Finding]] = defaultdict(list)
    for f in all_findings:
        groups[(f.file, f.category)].append(f)

    result: list[Finding] = []
    for group in groups.values():
        result.extend(_dedupe_group(group))

    # Sort: highest severity first, then by file, then by line.
    result.sort(key=lambda f: (-f.severity_rank(), f.file, f.line))
    return result


def _dedupe_group(findings: list[Finding]) -> list[Finding]:
    """Cluster overlapping findings in one (file, category) group."""
    # Sort by line so we can do a single linear scan.
    findings = sorted(findings, key=lambda f: f.line)
    clusters: list[list[Finding]] = []

    for f in findings:
        placed = False
        for cluster in clusters:
            if _overlaps(f, cluster[-1]):
                cluster.append(f)
                placed = True
                break
        if not placed:
            clusters.append([f])

    return [_merge_cluster(c) for c in clusters]


def _overlaps(a: Finding, b: Finding) -> bool:
    """True if two findings cover any of the same lines."""
    a_start, a_end = a.line, a.end_line or a.line
    b_start, b_end = b.line, b.end_line or b.line
    return a_start <= b_end and b_start <= a_end


def _merge_cluster(cluster: list[Finding]) -> Finding:
    if len(cluster) == 1:
        return cluster[0]

    primary = max(cluster, key=lambda f: (f.severity_rank(), f.confidence))
    others = [f for f in cluster if f.id != primary.id]

    merged_rationale = primary.rationale + _suffix(primary, others)
    merged_refs = list({r for f in cluster for r in f.references})

    return primary.model_copy(
        update={
            "rationale": merged_rationale,
            "references": merged_refs,
            "end_line": max((f.end_line or f.line) for f in cluster),
        }
    )


def _title_root(title: str) -> str:
    """Return the part of a title before any backtick-quoted name.

    Used to detect whether two findings describe the same error pattern
    regardless of which specific identifier is involved.
    """
    return title.split("`")[0].strip().rstrip(":").lower()


def _suffix(primary: Finding, others: list[Finding]) -> str:
    """Build the explanatory suffix appended to the primary rationale."""
    if not others:
        return ""

    root = _title_root(primary.title)
    same_pattern = all(_title_root(f.title) == root for f in others)

    if same_pattern:
        # All findings are the same error type — note the extra locations.
        lines = sorted({f.line for f in others} - {primary.line})
        if lines:
            loc = ", ".join(str(ln) for ln in lines)
            n = len(lines)
            noun = "location" if n == 1 else "locations"
            return f"\n\nThe same issue appears at {n} additional {noun} (line {loc})."
        return ""

    # Different issues that happen to share the same file+category+line range.
    # Show only the first sentence of each extra rationale to avoid noise.
    snippets = []
    for f in others:
        first_sentence = f.rationale.split(".")[0].strip()
        if first_sentence and first_sentence.lower() != primary.rationale.split(".")[0].strip().lower():
            snippets.append(f"- {first_sentence}.")
    if snippets:
        return "\n\nAlso in this region:\n" + "\n".join(snippets)
    return ""

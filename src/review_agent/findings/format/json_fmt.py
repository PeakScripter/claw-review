"""Render findings as a JSON array."""

from __future__ import annotations

import json
from collections.abc import Iterable

from review_agent.findings.model import Finding


def format_json(findings: Iterable[Finding], *, indent: int | None = 2) -> str:
    return json.dumps(
        [f.model_dump(mode="json") for f in findings],
        indent=indent,
        ensure_ascii=False,
    )

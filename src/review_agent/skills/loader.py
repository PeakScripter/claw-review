"""Reviewer manifest loader: markdown file with YAML frontmatter.

Same convention as Claude Code's `.claude/agents/` — the body is the system
prompt fragment, the frontmatter declares name, description, allowed tools,
and model override.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import yaml


@dataclass
class ReviewerManifest:
    name: str
    description: str
    tools: list[str]
    model: str | None
    instructions: str  # body after frontmatter
    source_path: Path


_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n(.*)\Z", re.DOTALL)


def load_manifest(path: Path) -> ReviewerManifest:
    text = path.read_text(encoding="utf-8")
    m = _FRONTMATTER_RE.match(text)
    if not m:
        raise ValueError(f"{path}: missing YAML frontmatter delimited by `---` lines.")
    fm = yaml.safe_load(m.group(1)) or {}
    body = m.group(2)
    name = fm.get("name") or path.stem
    return ReviewerManifest(
        name=str(name),
        description=str(fm.get("description", "")),
        tools=list(fm.get("tools", []) or []),
        model=fm.get("model"),
        instructions=body.strip(),
        source_path=path,
    )


def builtin_reviewers_dir() -> Path:
    return Path(__file__).resolve().parent.parent / "reviewers"


def load_reviewer(name: str) -> ReviewerManifest:
    """Load a built-in reviewer manifest by name."""
    path = builtin_reviewers_dir() / f"{name}.md"
    if not path.is_file():
        raise FileNotFoundError(f"Reviewer manifest not found: {path}")
    return load_manifest(path)

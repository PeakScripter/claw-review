"""Path-safety helpers shared across tools.

Every file-touching tool funnels its path through `resolve_within_cwd()` so a
tool input cannot escape the review's working directory via `..` or absolute
paths.
"""

from __future__ import annotations

from pathlib import Path


class PathOutsideRoot(ValueError):
    pass


def resolve_within_cwd(cwd: Path, candidate: str) -> Path:
    """Resolve `candidate` relative to `cwd` and reject anything outside it."""
    cwd_resolved = cwd.resolve()
    p = Path(candidate)
    full = (cwd_resolved / p).resolve() if not p.is_absolute() else p.resolve()
    try:
        full.relative_to(cwd_resolved)
    except ValueError as e:
        raise PathOutsideRoot(
            f"Path {candidate!r} resolves outside the review root {cwd_resolved}."
        ) from e
    return full

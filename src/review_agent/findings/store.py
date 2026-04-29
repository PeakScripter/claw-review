"""Mutable findings collection scoped to a single review.

The store is the only allowed sink for findings. Tools call `store.add()`; the
engine emits `FindingEvent`s and includes the snapshot in the final event.
"""

from __future__ import annotations

import threading
import uuid
from collections.abc import Iterable

from review_agent.findings.model import Finding


class FindingStore:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._findings: list[Finding] = []
        self._counter = 0

    def next_id(self) -> str:
        with self._lock:
            self._counter += 1
            return f"F{self._counter:04d}-{uuid.uuid4().hex[:6]}"

    def add(self, finding: Finding) -> Finding:
        with self._lock:
            self._findings.append(finding)
        return finding

    def extend(self, findings: Iterable[Finding]) -> None:
        with self._lock:
            self._findings.extend(findings)

    def retract(self, finding_id: str) -> bool:
        """Remove a finding by id. Returns True if found and removed."""
        with self._lock:
            before = len(self._findings)
            self._findings = [f for f in self._findings if f.id != finding_id]
            return len(self._findings) < before

    def snapshot(self) -> list[Finding]:
        with self._lock:
            return list(self._findings)

    def __len__(self) -> int:
        with self._lock:
            return len(self._findings)

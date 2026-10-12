"""Novelty of observed source states and trusted, source-bound proof receipts."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping


def source_fingerprint(files: Mapping[str, str]) -> str:
    return hashlib.sha256(
        json.dumps(sorted(files.items()), ensure_ascii=True, separators=(",", ":")).encode()
    ).hexdigest()


class SourceNoveltyTracker:
    """Only callers with validated server receipts may supply verified_receipts.

    Model claims, tool counters and changing diagnostics are not receipts.
    Each receipt is consumed once per exact complete source state.
    """

    def __init__(self, initial: Mapping[str, str]) -> None:
        self._seen: dict[str, set[str]] = {source_fingerprint(initial): set()}

    def observe(
        self,
        files: Mapping[str, str],
        *,
        verified_receipts: frozenset[str] = frozenset(),
    ) -> bool:
        fingerprint = source_fingerprint(files)
        novel_source = fingerprint not in self._seen
        receipts = self._seen.setdefault(fingerprint, set())
        novel_proof = bool(verified_receipts - receipts)
        receipts.update(verified_receipts)
        return novel_source or novel_proof

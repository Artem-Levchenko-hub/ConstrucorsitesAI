"""Novelty of observed source states and trusted, source-bound proof receipts."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Awaitable, Callable, Mapping
from typing import TYPE_CHECKING, Any

import structlog

from yleum_api.services.generation.recovery_contract import (
    RecoveryBinding,
    RecoveryDirective,
    RecoveryObservation,
)

if TYPE_CHECKING:
    from yleum_api.services.agent_builder import Action

RecoveryObserver = Callable[[RecoveryObservation], Awaitable[RecoveryDirective]]
RecoveryBindingReader = Callable[[], Awaitable[RecoveryBinding]]


def shadow_recovery_executor(
    execute: Callable[[Action], Awaitable[dict[str, Any]]], *,
    recovery: RecoveryObserver | None, recovery_binding: RecoveryBindingReader | None,
) -> Callable[[Action], Awaitable[dict[str, Any]]]:
    """Observe both loop executors without changing their effects or return values.

    Binding comes only from the owning server caller. Tool output is never
    promoted into a witness; directives remain shadow. Cancellation propagates,
    ordinary observer failures cannot fail or replay the completed operation.
    """
    if recovery is None or recovery_binding is None:
        return execute

    async def observed(action: Action) -> dict[str, Any]:
        result = await execute(action)
        try:
            binding = await recovery_binding()
            signature = hashlib.sha256(json.dumps(
                {"tool": action.name, "args": action.args}, sort_keys=True,
                separators=(",", ":"), default=str,
            ).encode()).hexdigest()
            await recovery(RecoveryObservation(kind="tool", binding=binding,
                requirement_id=None, action_signature=signature,
                diagnostic_signatures=(), witness=None))
        except Exception:
            # No exception/response text: callbacks may contain private data.
            structlog.get_logger(__name__).warning("generation.recovery_shadow_unavailable")
        return result

    return observed


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

    def source_history(self) -> tuple[str, ...]:
        """Arithmetic/history data only; never serialize accepted proof receipts."""
        return tuple(self._seen)

    @classmethod
    def from_source_history(cls, history: tuple[str, ...]) -> SourceNoveltyTracker:
        if (not history or len(set(history)) != len(history)
            or any(type(value) is not str or len(value) != 64
                   or any(char not in "0123456789abcdef" for char in value) for value in history)):
            raise ValueError("invalid source novelty history")
        restored = cls.__new__(cls)
        restored._seen = {fingerprint: set() for fingerprint in history}
        return restored

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

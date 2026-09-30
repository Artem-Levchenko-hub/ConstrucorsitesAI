"""Bound terminal hand-offs without timing out progressing generations.

Editing, checking and repairing have no wall-clock lifetime. Individual model,
network and Project Cell operations remain bounded by their own watchdogs. A
sealed restoration proof keeps a finite recovery ceiling while the controller
owns the hand-off.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Literal

from yleum_api.core.config import get_settings
from yleum_api.models.generation_run import GenerationRun
from yleum_api.services.generation_runs import has_sealed_adaptation_proof

DeadlineStage = Literal["edit", "repair", "proof"]

_STATE_KEY = "max_finalization"
_BOOK_KEY = "deadline"
@dataclass(frozen=True, slots=True)
class GenerationDeadline:
    stage: DeadlineStage
    # Editing and repair deliberately have no wall-clock deadline.
    at: datetime | None


def _book(run: GenerationRun) -> dict[str, int]:
    root = run.agent_state if isinstance(run.agent_state, dict) else {}
    state = root.get(_STATE_KEY)
    raw = state.get(_BOOK_KEY) if isinstance(state, dict) else None
    if not isinstance(raw, dict):
        return {}
    return {key: value for key, value in raw.items() if type(value) is int and value >= 0}


def _write_book(run: GenerationRun, book: dict[str, int]) -> None:
    root = dict(run.agent_state) if isinstance(run.agent_state, dict) else {}
    raw_state = root.get(_STATE_KEY)
    state = dict(raw_state) if isinstance(raw_state, dict) else {}
    state[_BOOK_KEY] = book
    root[_STATE_KEY] = state
    run.agent_state = root


def _millis(moment: datetime) -> int:
    return int(moment.timestamp() * 1000)


def is_restoration_adaptation(run: GenerationRun) -> bool:
    root = run.agent_state if isinstance(run.agent_state, dict) else {}
    binding = root.get("restoration_adaptation")
    return isinstance(binding, dict) and binding.get("adaptation_run_id") == str(run.id)


def generation_deadline(run: GenerationRun) -> GenerationDeadline:
    started = run.started_at or run.created_at
    book = _book(run)
    stage: DeadlineStage = "repair" if "repair_started_at_ms" in book else "edit"

    # Cancellation remains fail-closed when its notification is lost. The
    # watchdog observes an already-expired instant and terminalizes under the
    # run row lock, which cannot overwrite an existing terminal result.
    if run.status == "cancel_requested":
        return GenerationDeadline(stage, started)
    if not is_restoration_adaptation(run):
        return GenerationDeadline("edit", None)
    if has_sealed_adaptation_proof(run):
        settings = get_settings()
        sealed_since_ms = book.get("sealed_since_ms")
        sealed_since = (
            datetime.fromtimestamp(sealed_since_ms / 1000, UTC)
            if sealed_since_ms is not None
            # A missing seal mark is corrupt recovery state. Keep the controller
            # hand-off bounded from the run start rather than leaking it forever.
            else started
        )
        return GenerationDeadline(
            "proof",
            sealed_since + timedelta(seconds=settings.restoration_adaptation_activation_seconds),
        )
    return GenerationDeadline(stage, None)


def note_repair_stage_started(run: GenerationRun, now: datetime | None = None) -> None:
    """Record that final checks and source repair have begun."""
    if not is_restoration_adaptation(run):
        return
    book = _book(run)
    if "repair_started_at_ms" in book:
        return
    book["repair_started_at_ms"] = _millis(now or datetime.now(UTC))
    _write_book(run, book)


def note_proof_sealed(run: GenerationRun, now: datetime | None = None) -> None:
    book = _book(run)
    book.setdefault("sealed_since_ms", _millis(now or datetime.now(UTC)))
    _write_book(run, book)


def note_proof_settled(run: GenerationRun, now: datetime | None = None) -> None:
    """A proof came back; editing or repair can continue without a lifetime limit."""
    _ = now  # Retained for call-site compatibility and deterministic test clocks.
    book = _book(run)
    if book.pop("sealed_since_ms", None) is None:
        return
    _write_book(run, book)


__all__ = [
    "DeadlineStage",
    "GenerationDeadline",
    "generation_deadline",
    "is_restoration_adaptation",
    "note_proof_sealed",
    "note_proof_settled",
    "note_repair_stage_started",
]

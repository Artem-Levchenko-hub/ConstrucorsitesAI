"""When a generation run is out of time.

An ordinary run has one limit. A restoration adaptation passes through three
stages with separate limits: the agent's own editing, the checks and repairs
that follow it, and the sealed proof/activation hand-off. The editing deadline
never cuts a durable intent — the hand-off has its own, much wider ceiling — and
the time it took is not charged to the repairs that may follow a rejected proof.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Literal

from omnia_api.core.config import get_settings
from omnia_api.models.generation_run import GenerationRun
from omnia_api.services.generation_runs import has_sealed_adaptation_proof

DeadlineStage = Literal["edit", "repair", "proof"]

_STATE_KEY = "max_finalization"
_BOOK_KEY = "deadline"


@dataclass(frozen=True, slots=True)
class GenerationDeadline:
    stage: DeadlineStage
    # None only when the run is already over.
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
    settings = get_settings()
    started = run.started_at or run.created_at
    edit_end = started + timedelta(seconds=settings.max_generation_deadline_seconds)
    if not is_restoration_adaptation(run):
        return GenerationDeadline("edit", edit_end)
    book = _book(run)
    credit = timedelta(milliseconds=book.get("sealed_ms", 0))
    repair_started_ms = book.get("repair_started_at_ms")
    if repair_started_ms is None:
        editing_end = edit_end + credit
    else:
        repair_end = datetime.fromtimestamp(repair_started_ms / 1000, UTC) + timedelta(
            seconds=settings.restoration_adaptation_repair_seconds
        )
        # Never shorter than the single limit it replaces.
        editing_end = max(edit_end, repair_end) + credit
    # A requested cancel keeps the ordinary limit: it has to end even when sealed.
    if run.status != "cancel_requested" and has_sealed_adaptation_proof(run):
        sealed_since_ms = book.get("sealed_since_ms")
        sealed_since = (
            datetime.fromtimestamp(sealed_since_ms / 1000, UTC)
            if sealed_since_ms is not None
            else editing_end
        )
        # Not the editing deadline: the hand-off has its own ceiling, wide enough for
        # the controller's own timeouts. Past it the run is terminalized with its proof
        # retained, so the reconciler still finishes forward and the Cell is released.
        return GenerationDeadline(
            "proof",
            sealed_since + timedelta(seconds=settings.restoration_adaptation_activation_seconds),
        )
    if repair_started_ms is None:
        return GenerationDeadline("edit", editing_end)
    return GenerationDeadline("repair", editing_end)


def note_repair_stage_started(run: GenerationRun, now: datetime | None = None) -> None:
    """The agent's own turn is over; checks and repairs get their own window."""
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
    """A proof came back; the time it was sealed is returned to the repair window."""
    book = _book(run)
    sealed_since_ms = book.pop("sealed_since_ms", None)
    if sealed_since_ms is None:
        return
    elapsed = max(0, _millis(now or datetime.now(UTC)) - sealed_since_ms)
    book["sealed_ms"] = book.get("sealed_ms", 0) + elapsed
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

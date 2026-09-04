from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import cast
from uuid import UUID

import structlog

from omnia_api.models.generation_run import GenerationRun

log = structlog.get_logger(__name__)

_STATE_KEY = "max_finalization"
_COUNTER_NAMES = frozenset({"bootstrap", "fast_check", "full_build", "runtime_probe", "proof_hit"})


class GenerationPhase(StrEnum):
    PREPARE = "prepare"
    EDIT = "edit"
    FAST_CHECK = "fast_check"
    FINAL_BUILD = "final_build"
    RUNTIME_PROBE = "runtime_probe"
    SNAPSHOT = "snapshot"
    PROMOTE = "promote"
    COMPLETE = "complete"


def record_phase_started(
    run: GenerationRun,
    phase: GenerationPhase,
    now: datetime | None = None,
) -> None:
    current_ms = _to_epoch_millis(now)
    state = _copy_finalization_state(run.agent_state)
    overall_started_ms = _read_int(state.get("started_at_ms"))
    if overall_started_ms is None:
        state["started_at_ms"] = current_ms
    elif current_ms < overall_started_ms:
        raise ValueError("max finalization clock cannot move backwards")

    active_phase = state.get("current_phase")
    active_started_ms = _read_int(state.get("current_phase_started_at_ms"))
    if active_phase == phase.value and active_started_ms is not None:
        if current_ms < active_started_ms:
            raise ValueError("phase start cannot move backwards")
    else:
        state["current_phase_started_at_ms"] = current_ms
    state["current_phase"] = phase.value
    _write_finalization_state(run, state)


def record_phase_finished(
    run: GenerationRun,
    phase: GenerationPhase,
    now: datetime | None = None,
) -> None:
    current_ms = _to_epoch_millis(now)
    state = _copy_finalization_state(run.agent_state)
    active_started_ms = _read_int(state.get("current_phase_started_at_ms"))
    if state.get("current_phase") != phase.value or active_started_ms is None:
        raise ValueError(f"phase {phase.value} was not started")
    if current_ms < active_started_ms:
        raise ValueError(f"phase {phase.value} cannot finish before it started")

    phase_ms = cast(dict[str, int], state.setdefault("phase_ms", {}))
    phase_ms[phase.value] = phase_ms.get(phase.value, 0) + (current_ms - active_started_ms)
    state["finished_at_ms"] = current_ms
    state.pop("current_phase_started_at_ms", None)
    _write_finalization_state(run, state)


def increment_generation_counter(run: GenerationRun, name: str) -> None:
    if name not in _COUNTER_NAMES:
        raise ValueError(f"unsupported generation counter: {name}")
    state = _copy_finalization_state(run.agent_state)
    counters = cast(dict[str, int], state.setdefault("counters", {}))
    counters[name] = counters.get(name, 0) + 1
    _write_finalization_state(run, state)


def record_terminal_reason(run: GenerationRun, reason: str | None) -> None:
    state = _copy_finalization_state(run.agent_state)
    if reason:
        state["terminal_reason"] = reason
    else:
        state.pop("terminal_reason", None)
    _write_finalization_state(run, state)


def log_finalization_outcome(
    run: GenerationRun,
    *,
    outcome: str,
    proof_key: str | None = None,
    operation_id: UUID | str | None = None,
) -> None:
    state = _copy_finalization_state(run.agent_state)
    started_at_ms = _read_int(state.get("started_at_ms"))
    finished_at_ms = _read_int(state.get("finished_at_ms"))
    phase_ms = cast(dict[str, int], state.get("phase_ms", {}))
    counters = cast(dict[str, int], state.get("counters", {}))
    total_ms = (
        finished_at_ms - started_at_ms
        if started_at_ms is not None and finished_at_ms is not None
        else sum(int(value) for value in phase_ms.values())
    )
    log.info(
        "max_finalization.terminal",
        outcome=outcome,
        operation_id=str(operation_id) if operation_id is not None else None,
        proof_key=proof_key,
        total_ms=total_ms,
        phase_ms={key: int(value) for key, value in phase_ms.items()},
        counters={key: int(value) for key, value in counters.items()},
        current_phase=state.get("current_phase"),
        terminal_reason=state.get("terminal_reason"),
    )


def _copy_finalization_state(agent_state: object) -> dict[str, object]:
    root = agent_state if isinstance(agent_state, dict) else {}
    raw = root.get(_STATE_KEY)
    state = dict(raw) if isinstance(raw, dict) else {}
    state["phase_ms"] = _copy_int_map(state.get("phase_ms"))
    state["counters"] = _copy_int_map(state.get("counters"))
    return state


def _copy_int_map(raw: object) -> dict[str, int]:
    if not isinstance(raw, dict):
        return {}
    copied: dict[str, int] = {}
    for key, value in raw.items():
        if isinstance(key, str) and isinstance(value, int):
            copied[key] = value
    return copied


def _write_finalization_state(run: GenerationRun, state: dict[str, object]) -> None:
    root_state = dict(run.agent_state) if isinstance(run.agent_state, dict) else {}
    root_state[_STATE_KEY] = state
    run.agent_state = root_state


def _read_int(value: object) -> int | None:
    return value if isinstance(value, int) else None


def _to_epoch_millis(now: datetime | None) -> int:
    current = now or datetime.now(UTC)
    if current.tzinfo is None:
        raise ValueError("max finalization timestamps must be timezone-aware")
    return int(current.timestamp() * 1000)

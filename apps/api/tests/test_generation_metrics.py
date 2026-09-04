from __future__ import annotations

from datetime import UTC, datetime

import pytest

from omnia_api.models.generation_run import GenerationRun
from omnia_api.services.generation_metrics import (
    GenerationPhase,
    increment_generation_counter,
    record_phase_finished,
    record_phase_started,
)


def test_phase_accounting_is_monotonic_and_counts_expensive_work() -> None:
    run = GenerationRun(agent_state={})

    record_phase_started(
        run,
        GenerationPhase.FINAL_BUILD,
        now=datetime(2026, 9, 4, tzinfo=UTC),
    )
    increment_generation_counter(run, "full_build")
    record_phase_finished(
        run,
        GenerationPhase.FINAL_BUILD,
        now=datetime(2026, 9, 4, 0, 3, tzinfo=UTC),
    )

    state = run.agent_state["max_finalization"]
    assert state["counters"]["full_build"] == 1
    assert state["phase_ms"]["final_build"] == 180_000


def test_phase_finish_rejects_backwards_time() -> None:
    run = GenerationRun(agent_state={})

    record_phase_started(run, GenerationPhase.EDIT, now=datetime(2026, 9, 4, 0, 3, tzinfo=UTC))

    with pytest.raises(ValueError, match="cannot finish before it started"):
        record_phase_finished(
            run,
            GenerationPhase.EDIT,
            now=datetime(2026, 9, 4, 0, 2, tzinfo=UTC),
        )


def test_unsupported_counter_rejected() -> None:
    run = GenerationRun(agent_state={})

    with pytest.raises(ValueError, match="unsupported generation counter"):
        increment_generation_counter(run, "unknown")

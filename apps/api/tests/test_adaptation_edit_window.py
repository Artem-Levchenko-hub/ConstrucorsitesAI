"""A progressing edit is not failed only because its run is old.

Several live runs reached the former 25-minute wall-clock boundary while they
were still producing steps. Operation-level watchdogs remain bounded; this
regression file protects the distinct run-lifetime contract.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from yleum_api.models.generation_run import GenerationRun
from yleum_api.services.generation_deadline import generation_deadline

_T0 = datetime(2026, 9, 25, 6, 51, tzinfo=UTC)


def _run(*, adaptation: bool) -> GenerationRun:
    run_id = uuid.uuid4()
    state: dict[str, object] = {}
    if adaptation:
        state["restoration_adaptation"] = {
            "operation_id": str(uuid.uuid4()),
            "adaptation_run_id": str(run_id),
        }
    return GenerationRun(
        id=run_id,
        project_id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        status="running",
        created_at=_T0,
        started_at=_T0,
        agent_state=state,
    )


def test_an_ordinary_edit_has_no_generation_lifetime() -> None:
    assert generation_deadline(_run(adaptation=False)).at is None


def test_an_adaptation_edit_has_no_generation_lifetime() -> None:
    assert generation_deadline(_run(adaptation=True)).at is None


def test_production_compose_bounds_operations_in_api_and_worker() -> None:
    from pathlib import Path

    compose = (
        Path(__file__).resolve().parents[2] / "llm-gateway/deploy/full/docker-compose.yml"
    ).read_text(encoding="utf-8")
    expected = (
        "PROJECT_CELL_ACTIVITY_LEASE_SECONDS: "
        "${PROJECT_CELL_ACTIVITY_LEASE_SECONDS:-1500}"
    )
    assert compose.count(expected) == 2
    assert "MAX_GENERATION_DEADLINE_SECONDS" not in compose

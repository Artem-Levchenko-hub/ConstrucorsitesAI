"""Repairs keep data checks but do not inherit a wall-clock lifetime."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from yleum_api.models.generation_run import GenerationRun
from yleum_api.services.generation_deadline import (
    generation_deadline,
    note_repair_stage_started,
)


def test_a_long_running_adaptation_repair_remains_active() -> None:
    started = datetime(2026, 9, 25, 6, 51, tzinfo=UTC)
    run_id = uuid.uuid4()
    run = GenerationRun(
        id=run_id,
        project_id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        status="running",
        created_at=started,
        started_at=started,
        agent_state={
            "restoration_adaptation": {
                "operation_id": str(uuid.uuid4()),
                "adaptation_run_id": str(run_id),
            }
        },
    )
    note_repair_stage_started(run, started + timedelta(minutes=20))

    deadline = generation_deadline(run)

    assert (deadline.stage, deadline.at) == ("repair", None)

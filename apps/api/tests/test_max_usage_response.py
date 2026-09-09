"""Frozen public JSON for the ledger endpoint; no database fixtures or network."""

import json
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from unittest.mock import AsyncMock, Mock
from uuid import UUID

import pytest

from omnia_api.core.errors import ApiError
from omnia_api.models.generation_run import GenerationRun
from omnia_api.models.project import Project
from omnia_api.models.usage import Usage
from omnia_api.models.user import User
from omnia_api.routers.max_studio import get_max_usage

PROJECT_ID, USER_ID, RUN_ID, OLD_RUN_ID = (UUID(int=value) for value in range(1, 5))
EXPECTED = json.loads(
    Path(__file__).with_name("fixtures").joinpath("max_usage_response.json").read_text("utf-8")
)


def ledger_row(stage: str | None, cost: str, run_id: UUID | None = RUN_ID) -> Usage:
    return Usage(
        project_id=PROJECT_ID,
        user_id=USER_ID,
        run_id=run_id,
        model_id="test-model",
        stage=stage,
        cost_rub=Decimal(cost),
        tokens_in=10,
        tokens_out=2,
        cache_read_tokens=3,
        cache_write_tokens=4,
        retry_count=1,
        # Tied timestamps deliberately allow either returned row order.
        created_at=datetime(2026, 9, 9, tzinfo=UTC),
    )


@pytest.mark.parametrize(
    "case",
    ["empty", "empty_run", "no_run", "latest_forward", "latest_reverse", "latest_no_rows"],
)
async def test_max_usage_complete_serialized_response(case: str) -> None:
    data = EXPECTED[case]
    latest = GenerationRun(id=RUN_ID, status="running") if data["latest_run"] else None
    run_ids = {"latest": RUN_ID, "old": OLD_RUN_ID, None: None}
    rows = [ledger_row(stage, cost, run_ids[run]) for stage, cost, run in data["rows"]]
    session = Mock(execute=AsyncMock(side_effect=[
        Mock(scalar_one_or_none=Mock(return_value=Project(template="max_miniapp"))),
        Mock(scalar_one_or_none=Mock(return_value=latest)),
        Mock(scalars=Mock(return_value=rows)),
    ]))

    result = await get_max_usage(PROJECT_ID, session, User(id=USER_ID))

    assert json.loads(result.model_dump_json()) == data["expected"]
    assert session.execute.await_count == 3


@pytest.mark.parametrize(
    "project, code, status_code, message",
    [
        (None, "not_found", 404, "project not found"),
        (
            Project(template="landing"),
            "max_project_required",
            409,
            "Настройки MAX доступны только для проекта MAX Mini App",
        ),
    ],
    ids=["missing-or-foreign-project", "non-max-project"],
)
async def test_max_usage_rejects_project_before_reading_ledger(
    project, code, status_code, message
) -> None:
    session = Mock(execute=AsyncMock(return_value=Mock(
        scalar_one_or_none=Mock(return_value=project)
    )))

    with pytest.raises(ApiError) as caught:
        await get_max_usage(PROJECT_ID, session, User(id=USER_ID))

    assert (caught.value.code, caught.value.status_code, caught.value.message) == (
        code, status_code, message
    )
    assert session.execute.await_count == 1

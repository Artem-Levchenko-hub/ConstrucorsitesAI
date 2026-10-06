"""Frozen public JSON for the ledger endpoint; no database fixtures or network."""

import json
from collections import defaultdict
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from unittest.mock import AsyncMock, Mock
from uuid import UUID

import pytest

from yleum_api.core.errors import ApiError
from yleum_api.models.generation_run import GenerationRun
from yleum_api.models.project import Project
from yleum_api.models.usage import Usage
from yleum_api.models.user import User
from yleum_api.routers.max_studio import get_max_usage

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


def aggregate_rows(rows, latest):
    grouped = defaultdict(list)
    for row in rows:
        known_stages = {"template", "native_agent", "build_plan", "verification", "media"}
        stage = row.stage if row.stage in known_stages else "other"
        grouped[(bool(latest and row.run_id == latest.id), stage)].append(row)
    return [(is_latest, stage, "unknown", len(items),
             sum((row.cost_rub for row in items), Decimal(0)),
             sum(row.tokens_in for row in items), sum(row.tokens_out for row in items),
             sum(row.cache_read_tokens for row in items),
             sum(row.cache_write_tokens for row in items),
             sum(row.retry_count for row in items))
            for (is_latest, stage), items in grouped.items()]


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
        Mock(all=Mock(return_value=aggregate_rows(rows, latest))),
    ]))

    result = await get_max_usage(PROJECT_ID, session, User(id=USER_ID))

    serialized = json.loads(result.model_dump_json())
    total = serialized.pop("total_cost_breakdown")
    run = serialized.pop("run_cost_breakdown")
    assert total["unknown"]["calls"] == len(rows)
    assert Decimal(total["unknown"]["cost_rub"]) == sum((r.cost_rub for r in rows), Decimal(0))
    assert run["unknown"]["calls"] == sum(bool(latest and r.run_id == latest.id) for r in rows)
    for stage in serialized["stages"]:
        breakdown = stage.pop("cost_breakdown")
        assert breakdown["unknown"]["calls"] == stage["calls"]
        assert float(breakdown["unknown"]["cost_rub"]) == stage["cost_rub"]
    # SQL Decimal summation removes binary-float noise, without changing amounts.
    for actual, expected in zip(serialized["stages"], data["expected"]["stages"], strict=True):
        assert actual["cost_rub"] == pytest.approx(expected["cost_rub"], abs=1e-12)
        actual["cost_rub"] = expected["cost_rub"]
    assert serialized == data["expected"]
    assert session.execute.await_count == 3


async def test_max_usage_mixed_evidence_keeps_run_and_stage_boundaries():
    session = Mock(execute=AsyncMock(side_effect=[
        Mock(scalar_one_or_none=Mock(return_value=Project(template="max_miniapp"))),
        Mock(scalar_one_or_none=Mock(return_value=GenerationRun(id=RUN_ID, status="completed"))),
        Mock(all=Mock(return_value=[
            (True, "native_agent", "confirmed", 2, Decimal("1.2500"), 10, 2, 0, 0, 0),
            (True, "native_agent", "estimated", 1, Decimal(".3500"), 20, 3, 0, 0, 0),
            (False, "verification", "unknown", 4, Decimal("8"), 5, 1, 0, 0, 0),
        ])),
    ]))
    result = await get_max_usage(PROJECT_ID, session, User(id=USER_ID))
    assert result.run_cost_rub == 1.6 and result.total_cost_rub == 9.6
    assert result.run_cost_breakdown.confirmed.calls == 2
    assert result.run_cost_breakdown.estimated.cost_rub == Decimal(".3500")
    assert result.run_cost_breakdown.unknown.calls == 0
    assert result.total_cost_breakdown.unknown.cost_rub == 8
    assert [stage.id for stage in result.stages] == ["template", "native_agent"]
    assert result.stages[1].cost_breakdown == result.run_cost_breakdown


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

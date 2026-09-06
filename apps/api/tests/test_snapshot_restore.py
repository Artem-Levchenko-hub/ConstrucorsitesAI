from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from omnia_api.core.errors import ApiError
from omnia_api.services import snapshot_restore as restore


def test_max_restore_is_rejected_before_activation():
    with pytest.raises(ApiError) as caught:
        restore.ensure_restore_supported("max_miniapp")
    assert caught.value.status_code == 409
    assert "Текущая версия сохранена" in caught.value.message


@pytest.mark.parametrize(
    "result",
    [
        {"package_exit_code": "1"},
        {"drizzle_exit_code": "1"},
        {"dropped": "src/app/page.tsx"},
        {"ok": False},
        {"written": "0"},
        {"state": "failed", "written": "2"},
    ],
)
async def test_legacy_result_failures_are_not_success(monkeypatch, result):
    apply = AsyncMock(side_effect=[result, {"written": "2"}])
    monkeypatch.setattr(restore.orchestrator_client, "hot_reload", apply)
    with pytest.raises(ApiError):
        await restore.apply_legacy_restore(
            uuid4(), "project", {"a": "target"}, {"a": "old", "orphan": "x"}
        )
    assert apply.await_count == 2
    assert apply.call_args.kwargs["files"] == {"a": "old", "orphan": "x"}


async def test_legacy_unchanged_files_not_reapplied(monkeypatch):
    apply = AsyncMock(return_value={"state": "hot_reloaded", "written": "2"})
    monkeypatch.setattr(restore.orchestrator_client, "hot_reload", apply)
    await restore.apply_legacy_restore(
        uuid4(),
        "project",
        {"a": "new", "schema": "same"},
        {"a": "old", "schema": "same", "orphan": "x"},
    )
    assert apply.call_args.kwargs["files"] == {"a": "new", "orphan": ""}


async def test_failed_compensation_is_explicit(monkeypatch):
    apply = AsyncMock(side_effect=RuntimeError("offline"))
    monkeypatch.setattr(restore.orchestrator_client, "hot_reload", apply)
    with pytest.raises(ApiError) as caught:
        await restore.apply_legacy_restore(uuid4(), "project", {"a": "new"}, {"a": "old"})
    assert caught.value.details["runtime_recovered"] is False

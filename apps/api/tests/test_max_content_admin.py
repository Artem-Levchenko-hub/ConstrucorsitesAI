"""Owner catalog CAS and durable metadata delivery without source/generation effects."""

from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from pydantic import ValidationError

from yleum_api.core.errors import ApiError
from yleum_api.routers import max_studio
from yleum_api.schemas.max_studio import MaxContentItem, MaxContentPatch, MaxProjectConfigPayload


def item(title="Tea"):
    return MaxContentItem(id="tea", title=title)


def test_duplicate_ids_rejected_in_both_catalog_and_full_config():
    with pytest.raises(ValidationError, match="duplicate"):
        MaxContentPatch(expected_config_version=1, content=[item(), item()])
    with pytest.raises(ValidationError, match="duplicate"):
        MaxProjectConfigPayload(
            app_name="Shop", app_type="catalog", summary="Shop", content=[item(), item()]
        )


def test_cas_equal_stale_content_converges_and_different_stale_content_conflicts():
    current = [item()]
    max_studio._check_content_version(current, current, current_version=4, expected_version=1)
    with pytest.raises(ApiError) as caught:
        max_studio._check_content_version(
            current, [item("Different")], current_version=4, expected_version=1
        )
    assert caught.value.status_code == 409
    with pytest.raises(ApiError):
        max_studio._check_content_version(
            current, [item("Different")], current_version=4, expected_version=None
        )


@pytest.mark.asyncio
async def test_post_commit_public_failure_does_not_prevent_preview_or_saved_response(monkeypatch):
    owner, project_id, workspace_id = uuid4(), uuid4(), uuid4()
    project = SimpleNamespace(id=project_id, owner_id=owner, current_snapshot_id=uuid4())
    record = SimpleNamespace(
        config_version=3, config=MaxProjectConfigPayload.default_for("Shop").model_dump()
    )
    selection = SimpleNamespace(workspace=SimpleNamespace(id=workspace_id))
    public = AsyncMock(side_effect=RuntimeError("private upstream message"))
    preview = AsyncMock(return_value=False)
    monkeypatch.setattr(max_studio.orchestrator_client, "configure_published_cell", public)
    monkeypatch.setattr(
        max_studio.orchestrator_client, "project_cell_apply_business_config", preview,
    )
    await max_studio._sync_content_environments(project, record, selection)
    preview.assert_awaited_once()


async def test_content_only_persistence_keeps_unrelated_raw_fields_and_semantic_noop():
    owner = uuid4()
    project = SimpleNamespace(id=uuid4(), owner_id=owner)
    payload = MaxProjectConfigPayload.default_for("Shop")
    config = {**payload.model_dump(), "content": [{"id": "tea", "title": "Tea"}],
              "future_setting": {"preserved": True}}
    record = SimpleNamespace(owner_id=owner, config=config, config_version=4)
    session = SimpleNamespace(get=AsyncMock(return_value=record))
    same = payload.model_copy(update={"content": [item()]})
    await max_studio._persist_business_config(project, same, session, content_only=True)
    assert record.config is config and record.config_version == 4
    changed = payload.model_copy(update={"content": [item("New tea")]})
    await max_studio._persist_business_config(project, changed, session, content_only=True)
    assert record.config_version == 5
    assert record.config["future_setting"] == {"preserved": True}
    assert record.config["summary"] == config["summary"]


async def test_changed_platform_version_during_readback_cannot_be_reported_applied(monkeypatch):
    owner = uuid4()
    project = SimpleNamespace(id=uuid4(), owner_id=owner, name="Shop")
    payload = MaxProjectConfigPayload.default_for("Shop")
    record = SimpleNamespace(owner_id=owner, config=payload.model_dump(), config_version=4)
    fresh = SimpleNamespace(
        owner_id=owner, config=payload.model_copy(update={"content": [item()]}).model_dump(),
        config_version=5,
    )
    session = SimpleNamespace(get=AsyncMock(return_value=fresh))
    selection = SimpleNamespace(workspace=None)
    monkeypatch.setattr(max_studio.orchestrator_client, "published_cell_config_status", AsyncMock(
        return_value={"state": "applied", "desired_config_version": 4,
                      "controller_config_version": 4, "confirmed_config_version": 4},
    ))
    result = await max_studio._content_response(project, record, selection, session)
    assert result.config_version == 5 and result.content[0].id == "tea"
    assert result.public.state == "unknown" and result.public.confirmed_config_version is None
    assert result.public.reason == "configuration_changed"

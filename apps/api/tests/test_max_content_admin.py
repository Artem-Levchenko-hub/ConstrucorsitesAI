"""Owner catalog CAS and durable metadata delivery without source/generation effects."""

from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastapi import Response
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
    assert caught.value.details == {"reason": "config_version_conflict"}
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


@pytest.fixture
def catalog_save(monkeypatch):
    owner = SimpleNamespace(id=uuid4(), is_anon=False)
    project = SimpleNamespace(
        id=uuid4(), owner_id=owner.id, name="Shop", current_snapshot_id=uuid4(),
    )
    state = SimpleNamespace(record=None, active=False, phase="done", commits=0)

    async def get_record(*_args, **_kwargs):
        return state.record

    def add_record(record):
        state.record = record

    async def commit():
        state.commits += 1

    async def active(*_args):
        return object() if state.active else None

    async def deployment(*_args):
        return {"phase": state.phase}

    async def public_status(_project, desired):
        return {"state": "pending", "desired_config_version": desired["desired_config_version"]}

    session = SimpleNamespace(
        get=get_record, add=add_record, commit=commit, refresh=AsyncMock(),
        execute=AsyncMock(return_value=SimpleNamespace(
            scalar_one_or_none=lambda: object() if state.active else None,
        )),
    )
    selection = SimpleNamespace(selected=True, workspace=None)
    monkeypatch.setattr(max_studio, "_owned_max_project", AsyncMock(return_value=project))
    monkeypatch.setattr(max_studio, "_lock_owned_max_project", AsyncMock(return_value=project))
    monkeypatch.setattr(max_studio, "_content_selection", AsyncMock(return_value=selection))
    monkeypatch.setattr(
        max_studio.project_cell_runtime, "resolve_project_cell_public_selection",
        AsyncMock(return_value=selection),
    )
    monkeypatch.setattr(max_studio.project_cell_runtime, "_try_preview_project_lock", AsyncMock())
    monkeypatch.setattr(max_studio.project_cell_runtime, "_active_generation", active)
    monkeypatch.setattr(max_studio.orchestrator_client, "get_deploy", deployment)
    monkeypatch.setattr(max_studio.orchestrator_client, "configure_published_cell", AsyncMock())
    monkeypatch.setattr(
        max_studio.orchestrator_client, "published_cell_config_status", public_status,
    )
    return state, project, owner, session, selection


@pytest.mark.parametrize("busy", ["generation", "publication"])
async def test_catalog_busy_is_typed_and_same_payload_saves_once_after_wait(catalog_save, busy):
    state, project, owner, session, _ = catalog_save
    state.active = busy == "generation"
    state.phase = "building" if busy == "publication" else "done"
    payload = MaxContentPatch(expected_config_version=0, content=[item()])
    with pytest.raises(ApiError) as rejected:
        await max_studio.patch_max_content(project.id, payload, Response(), session, owner)
    assert rejected.value.status_code == 409
    assert rejected.value.code == (
        "generation_active" if busy == "generation" else "operation_conflict"
    )
    if busy == "publication":
        assert rejected.value.details == {"reason": "publication_active"}
    assert state.record is None and state.commits == 0

    state.active, state.phase = False, "done"
    result = await max_studio.patch_max_content(project.id, payload, Response(), session, owner)
    assert result.config_version == 1 and result.content == [item()]
    assert state.record.config["content"][0]["id"] == "tea"
    assert state.commits == 1


@pytest.mark.parametrize("cell", [False, True])
async def test_full_config_generation_busy_does_not_claim_version_conflict(catalog_save, cell):
    state, project, owner, session, selection = catalog_save
    state.active, selection.selected = True, cell
    with pytest.raises(ApiError) as rejected:
        await max_studio._put_max_config(
            project.id, MaxProjectConfigPayload.default_for("Shop"), session, owner,
            x_max_config_version=0,
        )
    assert rejected.value.status_code == 409 and rejected.value.code == "generation_active"
    assert state.record is None and state.commits == 0


async def test_full_config_publication_busy_does_not_claim_version_conflict(catalog_save):
    state, project, owner, session, selection = catalog_save
    state.phase, selection.selected = "swapping", False
    with pytest.raises(ApiError) as rejected:
        await max_studio._put_max_config(
            project.id, MaxProjectConfigPayload.default_for("Shop"), session, owner,
            x_max_config_version=0,
        )
    assert rejected.value.status_code == 409 and rejected.value.code == "operation_conflict"
    assert rejected.value.details == {"reason": "publication_active"}
    assert state.record is None and state.commits == 0


@pytest.mark.parametrize("race", ["generation", "config"])
async def test_cell_post_persist_busy_keeps_saved_data_and_distinguishes_cas(
    catalog_save, monkeypatch, race,
):
    state, project, owner, session, selection = catalog_save
    selection.workspace = SimpleNamespace(id=uuid4())
    payload = MaxProjectConfigPayload.default_for("Shop").model_copy(update={"content": [item()]})

    async def race_after_commit(*_args):
        if race == "generation":
            state.active = True
        else:
            state.record.config_version = 2

    monkeypatch.setattr(
        max_studio.orchestrator_client, "configure_published_cell", race_after_commit,
    )
    with pytest.raises(ApiError) as rejected:
        await max_studio._save_cell_business_config(project, payload, session, owner, selection)
    assert rejected.value.status_code == 409
    if race == "generation":
        assert rejected.value.code == "generation_active"
        assert rejected.value.details == {"saved": True}
    else:
        assert rejected.value.code == "conflict"
        assert rejected.value.details == {"reason": "config_version_conflict"}
    assert state.record.config["content"][0]["id"] == "tea"
    assert state.record.synced_snapshot_id is None and state.commits == 1

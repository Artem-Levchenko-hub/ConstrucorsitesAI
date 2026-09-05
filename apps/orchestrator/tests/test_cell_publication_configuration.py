import asyncio
import json
from types import SimpleNamespace
from uuid import UUID

import pytest

from omnia_orchestrator.core.cell_resources import CellIdentityConflict
from omnia_orchestrator.services.cell_publication import CellPublicationService
from omnia_orchestrator.services.project_machine import write_controller_json
from tests.test_cell_publication import request


async def test_public_configuration_survives_restart_without_changing_release(tmp_path):
    service = CellPublicationService(SimpleNamespace(), root=tmp_path)
    value = request(
        runtime_env={"MAX_BOT_TOKEN": "old-test-token"}, business_config={"app_name": "Old"}
    )
    release = {"release_id": "release-one"}
    service._write(
        value.project_id,
        {
            "project_id": str(value.project_id),
            "owner_id": str(value.owner_id),
            "history": [],
            "active_release": release,
        },
    )
    calls = []

    async def refresh(project_id):
        calls.append(project_id)

    service._refresh_public_configuration = refresh
    await service.configure(
        value.project_id,
        value.owner_id,
        runtime_env={"MAX_BOT_TOKEN": "new-test-token"},
        business_config={"app_name": "New"},
        business_config_version=2,
    )
    restarted = CellPublicationService(SimpleNamespace(), root=tmp_path)
    effective = restarted._effective_request(value)
    assert effective.runtime_env["MAX_BOT_TOKEN"] == "new-test-token"
    assert effective.business_config == {"app_name": "New"}
    assert restarted._read(value.project_id)["active_release"] == release
    assert calls == [value.project_id]


async def test_revoke_clears_auth_even_for_old_release_request(tmp_path):
    service = CellPublicationService(SimpleNamespace(), root=tmp_path)
    value = request(runtime_env={"MAX_BOT_TOKEN": "old-test-token"})
    service._write(
        value.project_id,
        {
            "project_id": str(value.project_id),
            "owner_id": str(value.owner_id),
            "history": [{}],
            "active_release": None,
        },
    )
    await service.configure(value.project_id, value.owner_id, runtime_env={})
    assert service._effective_request(value).runtime_env == {}


async def test_other_owner_cannot_rotate_public_credentials(tmp_path):
    service = CellPublicationService(SimpleNamespace(), root=tmp_path)
    value = request()
    service._write(
        value.project_id,
        {
            "project_id": str(value.project_id),
            "owner_id": str(value.owner_id),
            "history": [],
            "active_release": {},
        },
    )
    with pytest.raises(CellIdentityConflict):
        await service.configure(value.project_id, UUID(int=999), runtime_env={})
    assert not (tmp_path / str(value.project_id) / "configuration.json").exists()


async def test_stale_business_configuration_does_not_replace_newer_pages(tmp_path):
    service = CellPublicationService(SimpleNamespace(), root=tmp_path)
    value = request()
    service._write(
        value.project_id,
        {
            "project_id": str(value.project_id),
            "owner_id": str(value.owner_id),
            "history": [{}],
            "active_release": None,
        },
    )
    path = tmp_path / str(value.project_id) / "configuration.json"
    write_controller_json(
        path,
        {
            "owner_id": str(value.owner_id),
            "business_config_version": 3,
            "business_config": {"app_name": "Current"},
        },
    )
    with pytest.raises(CellIdentityConflict):
        await service.configure(
            value.project_id,
            value.owner_id,
            business_config={"app_name": "Stale"},
            business_config_version=2,
        )
    assert json.loads(path.read_text())["business_config"] == {"app_name": "Current"}


async def test_revoke_during_first_activation_waits_and_revokes_started_release(tmp_path):
    service = CellPublicationService(SimpleNamespace(), root=tmp_path)
    value = request(runtime_env={"MAX_BOT_TOKEN": "old-test-token"})
    service._write(
        value.project_id,
        {
            "project_id": str(value.project_id),
            "owner_id": str(value.owner_id),
            "history": [{}],
            "active_release": None,
        },
    )
    started, resume = asyncio.Event(), asyncio.Event()
    accepted_credentials = []

    async def activate_locked(request, release):
        # Model the real awaited container/HTTPS startup after credentials were read.
        accepted_credentials.append(service._effective_request(request).runtime_env)
        started.set()
        await resume.wait()
        saved = service._read(request.project_id)
        saved["active_release"] = release
        service._write(request.project_id, saved)

    async def refresh(project_id):
        accepted_credentials.append(service._effective_request(value).runtime_env)

    service._activate_locked = activate_locked
    service._refresh_public_configuration = refresh
    activation = asyncio.create_task(service._activate(value, {"release_id": "one"}))
    await asyncio.wait_for(started.wait(), timeout=1)
    revoke = asyncio.create_task(
        service.configure(value.project_id, value.owner_id, runtime_env={})
    )
    try:
        with pytest.raises(TimeoutError):
            await asyncio.wait_for(asyncio.shield(revoke), timeout=0.5)
    finally:
        resume.set()
        await activation
    assert await revoke == {"applied": True}
    assert accepted_credentials == [{"MAX_BOT_TOKEN": "old-test-token"}, {}]


async def test_disabled_publication_cannot_be_submitted_or_reconciled(tmp_path):
    service = CellPublicationService(SimpleNamespace(), root=tmp_path)
    value = request()
    service._write(
        value.project_id,
        {
            "project_id": str(value.project_id),
            "owner_id": str(value.owner_id),
            "history": [],
            "active_release": {"release_id": "old"},
            "disabled": True,
        },
    )
    with pytest.raises(CellIdentityConflict, match="disabled"):
        await service.submit(value)
    assert await service.reconcile() == []


async def test_disable_tombstone_precedes_ingress_removal_and_is_retryable(tmp_path, monkeypatch):
    from unittest.mock import AsyncMock

    service = CellPublicationService(SimpleNamespace(), root=tmp_path)
    value = request()
    service._write(
        value.project_id,
        {
            "project_id": str(value.project_id),
            "owner_id": str(value.owner_id),
            "history": [],
            "active_release": None,
            "slug": value.slug,
        },
    )
    remove = AsyncMock(side_effect=RuntimeError("nginx temporarily unavailable"))
    monkeypatch.setattr(
        "omnia_orchestrator.services.cell_publication.nginx_writer.unpublish", remove
    )
    monkeypatch.setattr(
        "omnia_orchestrator.services.cell_publication.nginx_writer.prod_host",
        lambda slug: slug,
    )
    with pytest.raises(RuntimeError):
        await service.disable(value.project_id, value.slug)
    assert service._read(value.project_id)["disabled"] is True
    remove.side_effect = None
    await service.disable(value.project_id, value.slug)
    assert remove.await_count == 2
    assert service._read(value.project_id)["active_release"] is None

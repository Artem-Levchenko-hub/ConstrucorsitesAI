import asyncio
from types import SimpleNamespace
from uuid import UUID

import pytest

from omnia_orchestrator.schemas.cell_publication import CellDeployRequest


def request(**overrides):
    value = dict(
        workspace_id=UUID(int=1),
        project_id=UUID(int=2),
        owner_id=UUID(int=3),
        snapshot_id=UUID(int=4),
        candidate_id=UUID(int=5),
        slug="real-product",
        commit_sha="a" * 40,
        source_revision="b" * 64,
        fencing_epoch=3,
        proof_key="c" * 64,
        schema_data_digest="d" * 64,
        build_ref="built",
        verification_ref="verified",
        idempotency_key="publish-one",
    )
    return CellDeployRequest(**{**value, **overrides})


async def test_durable_submit_deduplicates_and_never_exposes_secrets(tmp_path):
    from omnia_orchestrator.services.cell_publication import CellPublicationService

    service = CellPublicationService(SimpleNamespace(), root=tmp_path)
    calls = []

    async def execute(value, run_id):
        calls.append(run_id)

    service._execute = execute
    value = request(runtime_env={"MAX_BOT_TOKEN": "private-token"})
    first = await service.submit(value)
    second = await service.submit(value)
    await service.drain()
    assert first.run_id == second.run_id
    assert len(calls) == 1
    assert "private-token" not in first.model_dump_json()
    restarted = CellPublicationService(SimpleNamespace(), root=tmp_path)
    assert restarted.get(value.project_id).run_id == first.run_id
    with pytest.raises(RuntimeError, match="idempotency"):
        await service.submit(request(commit_sha="e" * 40))


def test_production_identity_stable_across_new_releases(tmp_path):
    from omnia_orchestrator.services.cell_publication import CellPublicationService

    service = CellPublicationService(SimpleNamespace(), root=tmp_path)
    one = service.production_identity(request())
    two = service.production_identity(request(idempotency_key="publish-two"))
    assert one == two
    assert one != UUID(int=1)


async def test_delete_waits_for_inflight_prepare_and_prevents_late_admission(tmp_path, monkeypatch):
    from unittest.mock import AsyncMock

    from omnia_orchestrator.services.cell_publication import CellPublicationService

    service = CellPublicationService(SimpleNamespace(), root=tmp_path)
    value = request()
    service._write(value.project_id, {"project_id": str(value.project_id), "slug": value.slug})
    entered, finish = asyncio.Event(), asyncio.Event()
    effects = []

    async def prepare_locked(*_args):
        entered.set()
        await finish.wait()
        effects.append("admitted")
        return {}

    service._prepare_locked = prepare_locked
    monkeypatch.setattr(
        "omnia_orchestrator.services.cell_publication.nginx_writer.unpublish", AsyncMock()
    )
    monkeypatch.setattr(
        "omnia_orchestrator.services.cell_publication.nginx_writer.prod_host",
        lambda _: "owned-host",
    )
    preparing = asyncio.create_task(service._prepare(value, str(UUID(int=30))))
    try:
        await asyncio.wait_for(entered.wait(), timeout=1)
        deleting = asyncio.create_task(service.disable(value.project_id, value.slug))
        try:
            with pytest.raises(TimeoutError):
                await asyncio.wait_for(asyncio.shield(deleting), timeout=0.1)
        finally:
            finish.set()
            await preparing
            await deleting
    finally:
        if not preparing.done():
            preparing.cancel()
            await asyncio.gather(preparing, return_exceptions=True)
    assert effects == ["admitted"]
    with pytest.raises(RuntimeError, match="disabled"):
        await service._prepare(value, str(UUID(int=31)))
    assert effects == ["admitted"]


async def test_publication_failure_has_safe_internal_location_without_exception_body(
    tmp_path,
    monkeypatch,
):
    from omnia_orchestrator.core.cell_resources import CellResourceError
    from omnia_orchestrator.services.cell_publication import CellPublicationService

    entries = []
    logger = SimpleNamespace(warning=lambda event, **fields: entries.append((event, fields)))
    monkeypatch.setattr("structlog.get_logger", lambda *args: logger)
    service = CellPublicationService(SimpleNamespace(), root=tmp_path)

    async def fail_prepare(*args):
        raise CellResourceError("private-disposable-token-in-docker-error")

    service._prepare = fail_prepare
    value = request()
    await service.submit(value)
    await service.drain()
    assert service.get(value.project_id).phase == "failed"
    assert entries[0][0] == "public_release_failed"
    assert entries[0][1]["error_type"] == "CellResourceError"
    assert any(frame["function"] == "_execute" for frame in entries[0][1]["frames"])
    assert "private-disposable-token" not in str(entries)
    assert "private-disposable-token" not in service.get(value.project_id).model_dump_json()


async def test_crash_after_candidate_start_reconciles_previous_code_without_resetting_data(
    tmp_path, monkeypatch
):
    from unittest.mock import AsyncMock

    from omnia_orchestrator.services.cell_lock import WorkspaceOperationLock
    from omnia_orchestrator.services.cell_publication import CellPublicationService
    from omnia_orchestrator.services.project_machine import write_controller_json

    value = request()
    service = CellPublicationService(SimpleNamespace(), root=tmp_path)
    accepted = {"release_id": str(UUID(int=21)), "epoch": 1}
    service._write(
        value.project_id,
        {
            "project_id": str(value.project_id),
            "history": [],
            "active_release": accepted,
            "production_workspace_id": str(service.production_identity(value)),
            "activation_pending": str(UUID(int=22)),
        },
    )
    write_controller_json(
        tmp_path / str(value.project_id) / "requests" / f"{accepted['release_id']}.json",
        value.model_dump(mode="json"),
    )
    manager = SimpleNamespace(
        operation_lock=WorkspaceOperationLock(tmp_path),
        state_store=SimpleNamespace(load=lambda _id: object()),
    )
    service._production_manager = lambda _id: manager
    live = {"code": str(UUID(int=22)), "data": ["created-before-update", "written-during-update"]}
    gateway = SimpleNamespace(
        reload=lambda: None,
        attrs={"NetworkSettings": {"Networks": {"internal": {"IPAddress": "192.0.2.1"}}}},
    )

    async def start(_manager, _state, release, _request, *, switch):
        if switch:
            live["code"] = release["release_id"]
        return SimpleNamespace(
            _lookup=lambda *args: gateway,
            client=SimpleNamespace(containers=None),
            stem="owned",
            internal_network="internal",
        )

    service._start = start
    monkeypatch.setattr(
        "omnia_orchestrator.services.cell_publication.nginx_writer.ensure_tls",
        AsyncMock(return_value=True),
    )
    monkeypatch.setattr(
        "omnia_orchestrator.services.cell_publication.nginx_writer.prod_host",
        lambda _slug: "app.example.test",
    )
    result = await service.reconcile()
    assert result == [{"project_id": str(value.project_id), "state": "ready"}]
    assert live == {
        "code": str(UUID(int=21)),
        "data": ["created-before-update", "written-during-update"],
    }
    assert service._read(value.project_id).get("activation_pending") is None


async def test_failed_public_data_quiesce_retains_runtime_and_requires_recovery(tmp_path):
    from unittest.mock import AsyncMock

    from omnia_orchestrator.services.cell_lock import WorkspaceOperationLock
    from omnia_orchestrator.services.cell_publication import CellPublicationService
    from omnia_orchestrator.services.published_machine_backend import PublicationRecoveryRequired

    value = request()
    service = CellPublicationService(SimpleNamespace(), root=tmp_path)
    old = {
        "release_id": str(UUID(int=21)),
        "schema_digest": "schema",
        "data_contract_digest": "data",
    }
    candidate = {**old, "release_id": str(UUID(int=22))}
    service._write(value.project_id, {"project_id": str(value.project_id), "active_release": old})
    manager = SimpleNamespace(
        operation_lock=WorkspaceOperationLock(tmp_path),
        state_store=SimpleNamespace(load=lambda _id: object()),
    )
    service._production_manager = lambda _id: manager
    service._backend = lambda *_args: SimpleNamespace(
        schema_digest=lambda: "schema",
        _lookup=lambda *_args: None,
        client=SimpleNamespace(containers=None),
        stem="owned",
    )
    service._start = AsyncMock(
        side_effect=PublicationRecoveryRequired("production data quiesce failed")
    )
    service._rollback_code = AsyncMock()
    with pytest.raises(PublicationRecoveryRequired):
        await service._activate(value, candidate)
    saved = service._read(value.project_id)
    assert saved["active_release"] == old
    assert saved["activation_pending"] == candidate["release_id"]
    assert saved["recovery_required"] is True
    service._rollback_code.assert_not_awaited()


async def test_public_delete_releases_admission_without_restoring_business_data(
    tmp_path, monkeypatch
):
    from unittest.mock import AsyncMock

    from omnia_orchestrator.core.cell_resources import LifecycleMutation
    from omnia_orchestrator.services.cell_publication import CellPublicationService
    from tests.test_cell_checkpoint import _make_fixture, _spec

    manager, _, docker = _make_fixture(tmp_path / "manager")
    value = request()
    service = CellPublicationService(SimpleNamespace(), root=tmp_path / "publication")
    production_id = service.production_identity(value)
    spec = _spec(production_id)
    await manager.ensure(spec, LifecycleMutation(UUID(int=20), 1, "a" * 64))
    volumes = set(docker.volumes)
    service._write(
        value.project_id,
        {
            "project_id": str(value.project_id),
            "slug": value.slug,
            "source_workspace_id": str(value.workspace_id),
            "production_workspace_id": str(production_id),
            "active_release": {"release_id": str(UUID(int=21))},
        },
    )
    service._production_manager = lambda _: manager
    calls = []
    service._backend = lambda *_args: SimpleNamespace(retire_compute=lambda: calls.append("retire"))
    monkeypatch.setattr(
        "omnia_orchestrator.services.cell_publication.nginx_writer.unpublish", AsyncMock()
    )
    monkeypatch.setattr(
        "omnia_orchestrator.services.cell_publication.nginx_writer.prod_host",
        lambda _: "owned-host",
    )
    await service.disable(value.project_id, value.slug)
    assert calls == ["retire"]
    assert manager._capacity_reservation_store().load(production_id) is None
    assert not docker.containers
    assert not docker.networks
    assert set(docker.volumes) == volumes
    assert service._read(value.project_id)["deletion_completed"] is True
    await service.disable(value.project_id, value.slug)
    assert calls == ["retire"]


@pytest.mark.parametrize("removal_confirmed", [False, True])
async def test_delete_interrupted_first_seed_never_releases_capacity_with_live_helper(
    tmp_path, monkeypatch, removal_confirmed
):
    from unittest.mock import AsyncMock

    from omnia_orchestrator.core.cell_resources import LifecycleMutation
    from omnia_orchestrator.services.cell_publication import CellPublicationService
    from tests.test_cell_checkpoint import _make_fixture, _spec
    from tests.test_published_machine_backend import interrupted_seed_backend

    manager, _, _ = _make_fixture(tmp_path / "manager")
    value = request()
    service = CellPublicationService(SimpleNamespace(), root=tmp_path / "publication")
    production_id = service.production_identity(value)
    await manager.ensure(_spec(production_id), LifecycleMutation(UUID(int=20), 1, "a" * 64))
    runtime, resources = interrupted_seed_backend(
        tmp_path / "runtime", removal_confirmed=removal_confirmed, workspace_id=production_id
    )
    # Real runtime cleanup, reached before any prepared_release journal exists.
    manager.machine_runtime = SimpleNamespace(parts=lambda _state: (None, runtime))
    service._production_manager = lambda _: manager
    service._write(
        value.project_id,
        {
            "project_id": str(value.project_id),
            "slug": value.slug,
            "source_workspace_id": str(value.workspace_id),
            "production_workspace_id": str(production_id),
            "active_release": None,
        },
    )
    monkeypatch.setattr(
        "omnia_orchestrator.services.cell_publication.nginx_writer.unpublish", AsyncMock()
    )
    monkeypatch.setattr(
        "omnia_orchestrator.services.cell_publication.nginx_writer.prod_host", lambda _: "owned"
    )
    if removal_confirmed:
        await service.disable(value.project_id, value.slug)
        assert not resources
        assert manager._capacity_reservation_store().load(production_id) is None
        assert service._read(value.project_id)["deletion_completed"] is True
    else:
        with pytest.raises(RuntimeError, match=r"unverified|not confirmed"):
            await service.disable(value.project_id, value.slug)
        assert resources
        assert manager._capacity_reservation_store().load(production_id) is not None
        assert not service._read(value.project_id).get("deletion_completed")

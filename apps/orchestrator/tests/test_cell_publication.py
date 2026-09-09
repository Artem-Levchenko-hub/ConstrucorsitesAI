import asyncio
from types import SimpleNamespace
from uuid import UUID

import pytest

from omnia_orchestrator.schemas.cell_publication import CellDeployRequest


def test_startup_schema_proof_excludes_only_trusted_semantic_overlay():
    from omnia_orchestrator.services.cell_publication import _physical_schema
    from tests.test_restoration_database import contracts

    old, current = contracts()
    changed = current.model_dump(mode="json")
    changed["tables"][0]["columns"][3]["json_keys"] = ["note"]
    adapted = type(old).model_validate(changed)
    assert _physical_schema(current) == _physical_schema(adapted)
    changed["tables"][0]["columns"][0]["type"] = "text"
    assert _physical_schema(current) != _physical_schema(type(old).model_validate(changed))


def restored_request(**overrides):
    return request(
        restoration_operation_id=UUID(int=70),
        accepted_fencing_epoch=3,
        proof_key=None,
        schema_data_digest=None,
        build_ref=None,
        verification_ref=None,
        **overrides,
    )


@pytest.mark.parametrize("changed", [None, "state", "owner_id", "source_revision", "metadata"])
def test_restored_publication_requires_active_durable_exact_proof(tmp_path, changed):
    from omnia_orchestrator.services.cell_publication import CellPublicationService
    from omnia_orchestrator.services.project_machine import write_controller_json

    value = restored_request()
    service = CellPublicationService(SimpleNamespace(cell_state_path=tmp_path / "cells"))
    proof = {
        "operation_id": str(value.restoration_operation_id),
        "workspace_id": str(value.workspace_id),
        "project_id": str(value.project_id),
        "owner_id": str(value.owner_id),
        "candidate_id": str(value.candidate_id),
        "source_commit_sha": value.commit_sha,
        "fencing_epoch": value.accepted_fencing_epoch,
        "source_revision": value.source_revision,
    }
    active = {**proof, "state": "active"}
    if changed == "state":
        active["state"] = "switching"
    elif changed in {"owner_id", "source_revision"}:
        active[changed] = "different"
    path = tmp_path / "code-restoration-artifacts" / str(value.restoration_operation_id)
    write_controller_json(path / "activation.json", active)
    metadata = {"restoration_proof": proof if changed != "metadata" else {}}
    backend = SimpleNamespace(_metadata=lambda: metadata)
    if changed:
        with pytest.raises(RuntimeError, match="restoration publication"):
            service._verify_restoration_source(value, backend)
    else:
        service._verify_restoration_source(value, backend)


async def test_protected_publication_uses_live_public_contract_not_draft_schema(
    tmp_path, monkeypatch
):
    from omnia_orchestrator.services.cell_publication import CellPublicationService
    from omnia_orchestrator.services.restoration_data_contract import DataContract

    service = CellPublicationService(SimpleNamespace(), root=tmp_path)
    desired = DataContract(version=1, tables=[])
    monkeypatch.setattr(
        "omnia_orchestrator.services.cell_publication.catalog_contract",
        lambda backend, **kwargs: (desired, []),
    )
    monkeypatch.setattr("omnia_orchestrator.services.cell_publication.load_policy", lambda _: None)
    release = {
        "data_contract": desired.model_dump(mode="json"),
        "data_contract_digest": "mounts",
        "schema_digest": "draft-schema",
    }
    old = {"data_contract_digest": "mounts", "schema_digest": "live-schema"}
    await service._check_public_contract(SimpleNamespace(), old, release)
    assert release["blocked_deletes"] == []
    assert old["rollback_data_contract"] == desired.model_dump(mode="json")
    with pytest.raises(RuntimeError):
        await service._check_public_contract(
            SimpleNamespace(), {**old, "data_contract_digest": "other"}, release
        )


@pytest.mark.parametrize("incompatible", [False, True])
async def test_protected_start_installs_public_policy_before_product_services(
    tmp_path, monkeypatch, incompatible
):
    from unittest.mock import AsyncMock

    from omnia_orchestrator.services import cell_publication as module
    from omnia_orchestrator.services.restoration_data_contract import DataContract
    from tests.test_project_machine_manifest import payload

    calls = []
    service = module.CellPublicationService(SimpleNamespace(), root=tmp_path)
    value = restored_request()
    service._effective_request = lambda request: request
    service._write(value.project_id, {"project_id": str(value.project_id), "history": []})
    desired = DataContract(version=1, tables=[])
    backend = SimpleNamespace(
        project_postgres_password="private-test",
        stage_public_policy=lambda *args, **kwargs: calls.append("stage"),
        switch_code=lambda *args: calls.append("ensure"),
        start_service=lambda *args: calls.append("service"),
        service_status=lambda *args: {"ready": True},
    )
    service._backend = lambda *args: backend
    manager = SimpleNamespace(
        machine_runtime=SimpleNamespace(
            _start_boundary=lambda *args, **kwargs: calls.append("boundary")
        )
    )
    monkeypatch.setattr(module, "ensure_managed_infrastructure", AsyncMock())
    monkeypatch.setattr(
        module, "load_policy", lambda _: {"contract": desired.model_dump(mode="json")}
    )

    def read_catalog(backend, *, trusted_contract):
        assert trusted_contract == desired
        return desired, ["custom"] if incompatible else []

    monkeypatch.setattr(module, "catalog_contract", read_catalog)
    monkeypatch.setattr(module, "install_policy", lambda backend: calls.append("install"))
    release = {
        "manifest": payload(),
        "data_contract": desired.model_dump(mode="json"),
        "blocked_deletes": [],
        "policy_epoch": 9,
        "epoch": 2,
        "image_id": "image",
        "prod_url": "https://app.example.test",
    }
    if incompatible:
        with pytest.raises(RuntimeError, match="incompatible"):
            await service._start(manager, object(), release, value, switch=True)
        assert calls == ["stage", "ensure"]
    else:
        await service._start(manager, object(), release, value, switch=True)
        assert calls[:3] == ["stage", "ensure", "install"]
        assert calls[-1] == "boundary"
        assert "service" in calls[3:]
        assert service._read(value.project_id)["data_seeded"] is True


def test_public_recovery_keeps_current_policy_epoch_and_old_contract():
    from omnia_orchestrator.services.cell_publication import CellPublicationService

    old = {"release_id": "old", "rollback_data_contract": {"version": 1, "tables": []}}
    failed = {"release_id": "new", "data_contract": {"version": 1, "tables": []}, "policy_epoch": 8}
    recovered = CellPublicationService._recovery_release(old, failed)
    assert recovered["policy_epoch"] == 8
    assert recovered["data_contract"] == old["rollback_data_contract"]
    assert recovered["policy_recovery_operation_id"] == "publication-rollback:new"
    assert old.get("data_contract") is None


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


async def test_publication_records_prepare_activate_and_total_timings(tmp_path):
    from omnia_orchestrator.services.cell_publication import CellPublicationService

    value = request()
    run_id = str(UUID(int=25))
    service = CellPublicationService(SimpleNamespace(), root=tmp_path)
    service._write(
        value.project_id,
        {
            "project_id": str(value.project_id),
            "history": [
                {
                    "idempotency_key": value.idempotency_key,
                    "request_digest": "digest",
                    "response": {
                        "project_id": str(value.project_id),
                        "run_id": run_id,
                        "phase": "queued",
                    },
                }
            ],
        },
    )

    async def prepare(*_args):
        return {"prod_url": "https://app.example.test", "image_id": "sha256:" + "a" * 64}

    async def activate(*_args):
        return None

    service._prepare = prepare
    service._activate = activate
    await service._execute(value, run_id)

    result = service.get(value.project_id)
    assert result is not None and result.phase == "done"
    assert [entry.split("=", 1)[0] for entry in result.logs] == [
        "prepare_ms",
        "activate_ms",
        "total_ms",
    ]


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

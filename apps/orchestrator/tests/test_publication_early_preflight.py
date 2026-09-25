"""P03: a release that cannot be applied, or a host that cannot hold the
archives, fails before any export/import; a first publish is unaffected; the
compatibility check under the activation locks still runs after preflight."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from uuid import UUID

import pytest

from omnia_orchestrator.core.cell_resources import CellResourceError
from omnia_orchestrator.core.project_machine import MachineManifest
from omnia_orchestrator.services import cell_publication as module
from omnia_orchestrator.services.cell_lock import WorkspaceOperationLock
from omnia_orchestrator.services.cell_publication import CellPublicationService
from omnia_orchestrator.services.publication_trace import PublicationTrace
from omnia_orchestrator.services.published_machine_backend import (
    PublishedMachineBackend,
    data_contract_digest,
)
from tests.test_cell_publication import request
from tests.test_project_machine_manifest import payload

SOURCE_SCHEMA = "s" * 64
GIB = 1024**3


class Captured(Exception):
    """Raised by the fake checkpoint: the expensive capture was reached."""


class Source:
    def __init__(self, workspace_id: UUID, environment_ref: dict | None = None) -> None:
        self.workspace_id = workspace_id
        self.stem = "stem"
        self.workspace_volume = "stem-workspace"
        self.pnpm_cache_volume = "stem-pnpm"
        self.corepack_cache_volume = "stem-corepack"
        self.project_postgres_volume = "stem-postgres"
        self.disk_bytes = 8 * GIB
        self.environment_ref = environment_ref

    def _metadata(self) -> dict:
        return {"environment_ref": self.environment_ref} if self.environment_ref else {}


class Adapter:
    def __init__(self, root: Path, machine, source) -> None:
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)
        self.machine, self.source = machine, source
        self.checkpoints: list[dict] = []
        self.resumed = 0

    def parts(self, _state):
        return self.machine, self.source

    def preview(self, _state):
        return ("running", None)

    async def resume_preview(self, _state, *, epoch=None) -> None:
        self.resumed += 1

    def recovery_required(self, _state) -> bool:
        return False

    async def checkpoint(self, _state, **kwargs):
        self.checkpoints.append(kwargs)
        raise Captured()


def build(tmp_path, monkeypatch, *, active_release, production_digests, environment_ref=None):
    value = request()
    manifest = MachineManifest.model_validate(payload())
    machine = SimpleNamespace(
        state=lambda: {"epoch": value.fencing_epoch, "manifest": manifest.model_dump(mode="json")}
    )
    source = Source(value.workspace_id, environment_ref)
    adapter = Adapter(tmp_path / "machines", machine, source)
    source_state = SimpleNamespace(
        workspace_id=value.workspace_id,
        project_id=value.project_id,
        owner_id=value.owner_id,
        fencing_epoch=value.fencing_epoch,
        active_generation_run_id=None,
        phase="completed",
        bundle_state="resources_ready",
    )
    manager = SimpleNamespace(
        machine_runtime=adapter,
        operation_lock=WorkspaceOperationLock(tmp_path / "locks"),
        profile=SimpleNamespace(state_path=str(tmp_path / "cells.json")),
        state_store=SimpleNamespace(load=lambda _id: source_state),
    )
    service = CellPublicationService(
        SimpleNamespace(), root=tmp_path / "publications", manager_factory=lambda _id: manager
    )
    service._write(
        value.project_id,
        {
            "project_id": str(value.project_id),
            "history": [],
            "active_release": active_release,
            "data_seeded": active_release is not None,
        },
    )
    digests = iter(production_digests)
    production = SimpleNamespace(
        state_store=SimpleNamespace(
            load=lambda _id: SimpleNamespace(workspace_id=service.production_identity(value))
        ),
        operation_lock=WorkspaceOperationLock(tmp_path / "production-locks"),
        schema_calls=0,
    )

    class OldBackend:
        def schema_digest(self) -> str:
            production.schema_calls += 1
            return next(digests)

    service._production_manager = lambda _workspace_id: production
    service._backend = lambda _manager, _state, _release: OldBackend()

    async def no_files(*_args, **_kwargs):
        return {}

    monkeypatch.setattr(
        "omnia_orchestrator.routers.workspace._read_agent_workspace_files", no_files
    )
    monkeypatch.setattr(
        "omnia_orchestrator.routers.runtime._workspace_revision",
        lambda _files: value.source_revision,
    )
    monkeypatch.setattr(PublishedMachineBackend, "schema_digest", lambda _self: SOURCE_SCHEMA)
    monkeypatch.setattr(module.shutil, "disk_usage", lambda _path: SimpleNamespace(free=50 * GIB))
    return service, value, adapter, production, manifest


def release_for(manifest: MachineManifest, schema: str) -> dict:
    return {
        "release_id": str(UUID(int=77)),
        "schema_digest": schema,
        "data_contract_digest": data_contract_digest(manifest),
        "snapshot_id": str(UUID(int=4)),
    }


async def test_incompatible_release_fails_before_any_capture(tmp_path, monkeypatch):
    service, value, adapter, production, manifest = build(
        tmp_path, monkeypatch, active_release=None, production_digests=["p" * 64]
    )
    service._write(
        value.project_id,
        {
            "project_id": str(value.project_id),
            "history": [],
            "active_release": release_for(manifest, "p" * 64),
            "data_seeded": True,
        },
    )
    trace = PublicationTrace()
    with pytest.raises(CellResourceError, match="publication_migration_required"):
        await service._prepare_locked(value, str(UUID(int=30)), trace)
    assert adapter.checkpoints == []  # no export, no import, no source stop
    assert production.schema_calls == 1
    assert trace.current_stage() == "preflight_target"


async def test_compatible_warm_release_reaches_capture_with_the_warm_volume_set(
    tmp_path, monkeypatch
):
    service, value, adapter, production, manifest = build(
        tmp_path, monkeypatch, active_release=None, production_digests=[SOURCE_SCHEMA]
    )
    service._write(
        value.project_id,
        {
            "project_id": str(value.project_id),
            "history": [],
            "active_release": release_for(manifest, SOURCE_SCHEMA),
            "data_seeded": True,
        },
    )
    with pytest.raises(Captured):
        await service._prepare_locked(value, str(UUID(int=31)), PublicationTrace())
    assert production.schema_calls == 1
    (capture,) = adapter.checkpoints
    assert capture["persist"] is False
    # `python server.py` / `ruby worker.rb` never touch package stores (B2).
    assert set(capture["volumes"]) == {"stem-workspace", "stem-home"}


async def test_first_publish_never_consults_production_before_capture(tmp_path, monkeypatch):
    service, value, adapter, production, _manifest = build(
        tmp_path, monkeypatch, active_release=None, production_digests=[]
    )
    with pytest.raises(Captured):
        await service._prepare_locked(value, str(UUID(int=32)), PublicationTrace())
    assert production.schema_calls == 0
    (capture,) = adapter.checkpoints
    assert capture["persist"] is True and capture["volumes"] is None


async def test_missing_disk_space_fails_before_capture(tmp_path, monkeypatch):
    previous = {
        "workspace_id": str(UUID(int=1)),
        "image_id": "sha256:" + "a" * 64,
        "artifact_ref": "a" * 32 + ".tar",
        "sha256": "b" * 64,
        "size": GIB,
        "base_image": "sha256:" + "c" * 64,
        "manifest_digest": "d" * 64,
        "volumes": [
            {
                "name": "stem-workspace",
                "artifact_ref": "e" * 32 + ".tar",
                "sha256": "f" * 64,
                "size": 2 * GIB,
            }
        ],
    }
    service, value, adapter, _production, _manifest = build(
        tmp_path, monkeypatch, active_release=None, production_digests=[], environment_ref=previous
    )
    # 3 GiB package needs ~6 GiB; 5 GiB free is not enough, 7 GiB is.
    monkeypatch.setattr(module.shutil, "disk_usage", lambda _path: SimpleNamespace(free=5 * GIB))
    with pytest.raises(CellResourceError, match="free disk space"):
        await service._prepare_locked(value, str(UUID(int=33)), PublicationTrace())
    assert adapter.checkpoints == []
    monkeypatch.setattr(module.shutil, "disk_usage", lambda _path: SimpleNamespace(free=7 * GIB))
    with pytest.raises(Captured):
        await service._prepare_locked(value, str(UUID(int=34)), PublicationTrace())


async def test_production_drift_after_preflight_is_refused_at_activation(tmp_path, monkeypatch):
    service, value, _adapter, production, manifest = build(
        tmp_path, monkeypatch, active_release=None, production_digests=["z" * 64]
    )
    old = release_for(manifest, SOURCE_SCHEMA)
    service._write(
        value.project_id,
        {
            "project_id": str(value.project_id),
            "history": [],
            "active_release": old,
            "data_seeded": True,
        },
    )

    async def never_start(*_args, **_kwargs):
        raise AssertionError("activation must not start after a drifted schema")

    service._start = never_start
    candidate = {**release_for(manifest, SOURCE_SCHEMA), "release_id": str(UUID(int=78))}
    with pytest.raises(CellResourceError, match="publication_migration_required"):
        await service._activate_locked(value, candidate, PublicationTrace())
    assert production.schema_calls == 1
    assert service._read(value.project_id).get("activation_pending") is None

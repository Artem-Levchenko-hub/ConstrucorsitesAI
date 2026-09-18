"""P05/P12: the checkpoint taken when a machine halts after a generation records
the source revision and database schema it captured; a warm publication of
exactly that revision starts from those archives — the editor is neither
stopped nor woken — while any doubt falls back to the capture path."""

from __future__ import annotations

import json
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest

from omnia_orchestrator.core.project_machine import MachineManifest
from omnia_orchestrator.services import machine_adapter as adapter_module
from omnia_orchestrator.services.machine_environment import (
    MachineEnvironmentRef,
    MachineEnvironmentStore,
    VolumeEnvironmentRef,
)
from omnia_orchestrator.services.publication_trace import PublicationTrace
from omnia_orchestrator.services.published_machine_backend import PublishedMachineBackend
from tests.test_machine_environment import ArchiveBackend
from tests.test_project_machine_manifest import payload
from tests.test_publication_early_preflight import Captured, build, release_for

SEALED_SCHEMA = "e" * 64


async def test_persisted_checkpoint_records_revision_and_schema_but_warm_capture_does_not(
    tmp_path, monkeypatch
):
    manifest = MachineManifest.model_validate(payload())
    workspace_id = uuid4()
    reference = MachineEnvironmentRef(
        workspace_id=workspace_id,
        image_id="sha256:" + "a" * 64,
        artifact_ref="b" * 32 + ".tar",
        sha256="c" * 64,
        size=1,
        base_image="base",
        manifest_digest=manifest.digest(),
        volumes=(),
        manifest=manifest,
    )
    metadata_path = tmp_path / "machine.json"
    metadata_path.write_text(json.dumps({"environment_ref": None}))

    class Store:
        def __init__(self, *_args, **_kwargs):
            pass

        def capture(self, **_kwargs):
            return reference

    backend = SimpleNamespace(
        base_image="base",
        disk_bytes=1024,
        workspace_volume="stem-workspace",
        metadata_path=metadata_path,
        _container=lambda: object(),
        _metadata=lambda: json.loads(metadata_path.read_text()),
        snapshot_volume_names=lambda _manifest: ("stem-workspace",),
    )
    runtime = adapter_module.MachineAdapter(
        SimpleNamespace(state_store=SimpleNamespace(root=tmp_path / "state"), docker=object()),
        SimpleNamespace(),
    )
    runtime.exists = lambda _workspace_id: True
    runtime.parts = lambda _state: (
        SimpleNamespace(state=lambda: {"manifest": manifest.model_dump(mode="json")}),
        backend,
    )
    monkeypatch.setattr(adapter_module, "MachineEnvironmentStore", Store)

    async def files(_manager, _volume):
        return {"src/app/page.tsx": "export default () => null"}

    monkeypatch.setattr("omnia_orchestrator.routers.workspace._read_agent_workspace_files", files)
    monkeypatch.setattr(
        "omnia_orchestrator.routers.runtime._workspace_revision", lambda _f: "r" * 64
    )
    monkeypatch.setattr(PublishedMachineBackend, "schema_digest", lambda _self: SEALED_SCHEMA)
    state = SimpleNamespace(workspace_id=workspace_id)

    await runtime.checkpoint(state)  # the halt after a generation
    saved = json.loads(metadata_path.read_text())
    assert saved["environment_revision"] == "r" * 64
    assert saved["environment_schema_digest"] == SEALED_SCHEMA
    assert saved["environment_sealed_at"]

    await runtime.checkpoint(state, volumes=("stem-workspace",), persist=False)  # publication
    assert json.loads(metadata_path.read_text())["environment_revision"] == "r" * 64

    # An identity that cannot be read records nothing rather than something stale.
    async def broken(_manager, _volume):
        raise OSError("helper unavailable")

    monkeypatch.setattr("omnia_orchestrator.routers.workspace._read_agent_workspace_files", broken)
    await runtime.checkpoint(state)
    saved = json.loads(metadata_path.read_text())
    assert "environment_revision" not in saved and "environment_schema_digest" not in saved


def _seal_source(
    service,
    value,
    adapter,
    manifest,
    *,
    revision: str,
    volumes=("stem-workspace", "stem-home"),
):
    """Write real sealed archives for the fake source and point its metadata at them."""
    source = adapter.parts(None)[1]
    backend = ArchiveBackend()
    backend.volumes = {name: f"{name} bytes".encode() for name in volumes}
    store = MachineEnvironmentStore(
        adapter.root / "artifacts", source.workspace_id, backend, max_bytes=1 << 20
    )
    reference = store.capture(
        manifest_digest=manifest.digest(), base_image="sha256:" + "c" * 64, volumes=tuple(volumes)
    )
    source.environment_ref = reference.model_dump(mode="json")
    source.metadata_extra = {
        "environment_revision": revision,
        "environment_schema_digest": SEALED_SCHEMA,
    }
    return reference


def _sealed_build(tmp_path, monkeypatch):
    service, value, adapter, production, manifest = build(
        tmp_path,
        monkeypatch,
        active_release=None,
        production_digests=[SEALED_SCHEMA, SEALED_SCHEMA],
    )
    # The live source database matches what the checkpoint sealed.
    monkeypatch.setattr(PublishedMachineBackend, "schema_digest", lambda _self: SEALED_SCHEMA)
    source = adapter.parts(None)[1]
    original = source._metadata

    def metadata():
        value_ = dict(original())
        value_.update(getattr(source, "metadata_extra", {}))
        return value_

    source._metadata = metadata  # type: ignore[method-assign]
    service._write(
        value.project_id,
        {
            "project_id": str(value.project_id),
            "history": [],
            "active_release": release_for(manifest, SEALED_SCHEMA),
            "data_seeded": True,
        },
    )
    return service, value, adapter, production, manifest


async def test_publication_starts_from_the_halt_checkpoint_without_touching_the_editor(
    tmp_path, monkeypatch
):
    service, value, adapter, _production, manifest = _sealed_build(tmp_path, monkeypatch)
    _seal_source(service, value, adapter, manifest, revision=value.source_revision)
    adapter.preview = lambda _state: ("stopped", None)  # asleep since the generation
    seen: list[str] = []
    trace = PublicationTrace()
    original_stage = trace.stage

    def record(name, **kwargs):
        seen.append(name)
        if name == "prepare_target":
            raise Captured()  # the production side is not under test here
        original_stage(name, **kwargs)

    trace.stage = record  # type: ignore[method-assign]
    with pytest.raises(Captured):
        await service._prepare_locked(value, str(UUID(int=40)), trace)
    assert adapter.checkpoints == [] and adapter.resumed == 0
    assert "sealed_artifact" in seen
    assert not {"source_wake", "source_schema", "resume_source"} & set(seen)
    assert seen.index("sealed_artifact") < seen.index("preflight_target")


async def test_running_preview_is_not_stopped_when_the_checkpoint_is_current(
    tmp_path, monkeypatch
):
    service, value, adapter, _production, manifest = _sealed_build(tmp_path, monkeypatch)
    _seal_source(service, value, adapter, manifest, revision=value.source_revision)
    seen: list[str] = []
    trace = PublicationTrace()
    original_stage = trace.stage

    def record(name, **kwargs):
        seen.append(name)
        if name == "prepare_target":
            raise Captured()
        original_stage(name, **kwargs)

    trace.stage = record  # type: ignore[method-assign]
    with pytest.raises(Captured):
        await service._prepare_locked(value, str(UUID(int=41)), trace)
    assert adapter.checkpoints == [] and adapter.resumed == 0  # the editor keeps running


async def test_other_revision_or_missing_archive_falls_back_to_capture(tmp_path, monkeypatch):
    service, value, adapter, _production, manifest = _sealed_build(tmp_path, monkeypatch)
    reference = _seal_source(service, value, adapter, manifest, revision="f" * 64)
    with pytest.raises(Captured):  # another revision was checkpointed: capture as before
        await service._prepare_locked(value, str(UUID(int=42)), PublicationTrace())
    assert len(adapter.checkpoints) == 1

    reference = _seal_source(service, value, adapter, manifest, revision=value.source_revision)
    workspace_archive = next(v for v in reference.volumes if v.name == "stem-workspace")
    (adapter.root / "artifacts" / str(value.workspace_id) / workspace_archive.artifact_ref).unlink()
    with pytest.raises(Captured):  # archive gone: capture as before
        await service._prepare_locked(value, str(UUID(int=43)), PublicationTrace())
    assert len(adapter.checkpoints) == 2


async def test_checkpoint_seal_hands_over_only_the_warm_volumes(tmp_path, monkeypatch):
    service, value, adapter, _production, manifest = _sealed_build(tmp_path, monkeypatch)
    _seal_source(
        service,
        value,
        adapter,
        manifest,
        revision=value.source_revision,
        volumes=("stem-workspace", "stem-home", "stem-postgres", "stem-next"),
    )
    source = adapter.parts(None)[1]
    sealed = service._checkpoint_seal(value, source, adapter, manifest)
    assert sealed is not None and sealed["schema_digest"] == SEALED_SCHEMA
    assert [v.name for v in sealed["reference"].volumes] == ["stem-workspace", "stem-home"]
    assert all(isinstance(v, VolumeEnvironmentRef) for v in sealed["reference"].volumes)

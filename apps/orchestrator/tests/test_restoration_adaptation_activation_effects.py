from __future__ import annotations

import hashlib
import io
import json
import tarfile
from collections import Counter
from contextlib import asynccontextmanager
from dataclasses import dataclass, replace
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID, uuid5

import pytest

from yleum_orchestrator.core.cell_resources import (
    CellIdentityConflict,
    CellResourceError,
    LifecycleMutation,
)
from yleum_orchestrator.schemas.restoration_adaptation_activation import (
    ActivationBusinessProbe,
    ActivationPreparedTarget,
    RestorationAdaptationActivationRequest,
)
from yleum_orchestrator.services.restoration_adaptation_activation import (
    RestorationAdaptationActivationEngine,
)
from yleum_orchestrator.services.restoration_adaptation_activation_effects import (
    DockerRestorationAdaptationActivationEffects,
    live_database_volume_identity_digest,
)
from yleum_orchestrator.services.restoration_adaptation_probe import validate_probe_contract
from yleum_orchestrator.services.restoration_adaptation_source import source_manifest_digest
from yleum_orchestrator.services.restoration_binding import canonical_digest

PROBE_MANIFEST = json.dumps(
    {
        "version": 1,
        "endpoint": "/api/orders/restoration-probe",
        "witnesses": [
            {
                "entity": "orders",
                "id_column": "id",
                "owner_column": "max_user_id",
                "value_column": "probe_value",
                "create_values": {},
            }
        ],
        "max_payload_bytes": 4096,
    },
    sort_keys=True,
    separators=(",", ":"),
).encode()
DATA_CONTRACT = {
    "version": 1,
    "tables": [
        {
            "name": "orders",
            "columns": [
                {
                    "name": "id",
                    "type": "uuid",
                    "nullable": False,
                    "default": "gen_random_uuid()",
                },
                {"name": "max_user_id", "type": "text", "nullable": False},
                {"name": "probe_value", "type": "text", "nullable": False},
            ],
            "owner_column": "max_user_id",
            "primary_key": ["id"],
        }
    ],
}
SOURCE_FILES = {
    "src/index.ts": b"source",
    ".omnia/restoration-probe.json": PROBE_MANIFEST,
}
CANDIDATE_FILES = {
    "src/index.ts": b"candidate",
    ".omnia/restoration-probe.json": PROBE_MANIFEST,
}


def _archive(files: dict[str, bytes]) -> bytes:
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode="w") as archive:
        for path, payload in sorted(files.items()):
            info = tarfile.TarInfo(path)
            info.size = len(payload)
            info.mode = 0o644
            archive.addfile(info, io.BytesIO(payload))
    return output.getvalue()


ARCHIVE = _archive(CANDIDATE_FILES)


def _business_probe() -> ActivationBusinessProbe:
    return validate_probe_contract(_text_files(CANDIDATE_FILES), DATA_CONTRACT)


def _text_files(files: dict[str, bytes]) -> dict[str, str]:
    result: dict[str, str] = {}
    for path, content in files.items():
        try:
            result[path] = content.decode("utf-8")
        except UnicodeDecodeError:
            pass
    return result


def _files_digest(files: dict[str, bytes]) -> str:
    digest = hashlib.sha256()
    for path, content in sorted(_text_files(files).items()):
        path_bytes = path.encode()
        content_bytes = content.encode()
        digest.update(len(path_bytes).to_bytes(8, "big"))
        digest.update(path_bytes)
        digest.update(len(content_bytes).to_bytes(8, "big"))
        digest.update(content_bytes)
    return digest.hexdigest()


def _workspace_revision(files: dict[str, bytes]) -> str:
    return _files_digest(
        {
            path: content
            for path, content in files.items()
            if Path(path).name != "next-env.d.ts" and not path.endswith(".tsbuildinfo")
        }
    )


def _database_identity_digest() -> str:
    return canonical_digest(
        {
            "name": "live-db",
            "created_at": "fixed",
            "driver": None,
            "scope": None,
            "options": {},
            "labels": {"omnia.kind": "project-volume"},
        }
    )


def activation_request(**updates: object) -> RestorationAdaptationActivationRequest:
    value: dict[str, object] = {
        "operation_id": UUID(int=1),
        "generation_run_id": UUID(int=2),
        "project_id": UUID(int=3),
        "owner_id": UUID(int=4),
        "source_workspace_id": UUID(int=5),
        "expected_source_fencing_epoch": 11,
        "target_fencing_epoch": 12,
        "source_workspace_revision": _workspace_revision(SOURCE_FILES),
        "source_code_volume": "source-code",
        "live_database_volume": "live-db",
        "live_database_identity_digest": _database_identity_digest(),
        "candidate_workspace_id": UUID(int=6),
        "candidate_fencing_epoch": 1,
        "candidate_workspace_revision": _workspace_revision(CANDIDATE_FILES),
        "proof_digest": "4" * 64,
        "candidate_artifact_digest": _files_digest(CANDIDATE_FILES),
        "candidate_source_manifest_digest": source_manifest_digest(CANDIDATE_FILES),
        "candidate_code_digest": hashlib.sha256(ARCHIVE).hexdigest(),
        "business_probe": _business_probe(),
        "probe_rehearsal_digest": "9" * 64,
        "probe_rehearsal_database_digest": "a" * 64,
        "planned_commit_sha": "a" * 40,
        "activation_id": UUID(int=7),
        "activation_digest": "6" * 64,
    }
    value.update(updates)
    draft = RestorationAdaptationActivationRequest.model_construct(**value)
    value["activation_digest"] = hashlib.sha256(
        json.dumps(
            draft.activation_binding_payload(), sort_keys=True, separators=(",", ":")
        ).encode()
    ).hexdigest()
    return RestorationAdaptationActivationRequest.model_validate(value)


class Lock:
    def __init__(self) -> None:
        self.held = False

    @asynccontextmanager
    async def hold(self, _workspace_id):
        assert self.held is False
        self.held = True
        try:
            yield
        finally:
            self.held = False


class State:
    def __init__(
        self,
        *,
        workspace_id: UUID,
        request: RestorationAdaptationActivationRequest,
        fence: int,
        code_volume: str,
    ) -> None:
        self.workspace_id = workspace_id
        self.project_id = request.project_id
        self.owner_id = request.owner_id
        self.fencing_epoch = fence
        self.active_generation_run_id = request.generation_run_id
        self.active_generation_fencing_epoch = fence
        self.profile_version = 1
        self.resource_names = SimpleNamespace(workspace_volume=code_volume)
        self._operations: dict[UUID, SimpleNamespace] = {}
        self.last_operation_id: UUID | None = None

    def operation(self, operation_id: UUID):
        return self._operations.get(operation_id)


class Store:
    def __init__(self, states: dict[UUID, State]) -> None:
        self.states = states

    def load(self, workspace_id: UUID):
        return self.states.get(workspace_id)


class Volume:
    def __init__(
        self,
        name: str,
        labels: dict[str, str],
        volumes: dict[str, Volume],
        events: list[object],
    ) -> None:
        self.name = name
        self.labels = labels
        self.attrs = {"Name": name, "Labels": labels, "CreatedAt": "fixed"}
        self._volumes = volumes
        self._events = events

    def remove(self) -> None:
        self._events.append(("remove-volume", self.name))
        self._volumes.pop(self.name, None)


@dataclass
class Backend:
    workspace_volume: str
    project_postgres_volume: str
    docker: Docker
    events: list[object]
    archive: bytes = ARCHIVE
    import_files: dict[str, bytes] | None = None
    stem: str = "fixture"
    base_image: str = "base-image"
    running: bool = True

    def __post_init__(self) -> None:
        self.client = SimpleNamespace(volumes=self.docker.volumes)

    def _lookup(self, collection, name: str, _kind: str):
        return collection.get(name)

    def labels(self, kind: str) -> dict[str, str]:
        return {"omnia.kind": kind}

    def restoration_volume_labels(
        self,
        operation_id: UUID,
        *,
        purpose: str,
        binding_digest: str,
        artifact_digest: str,
    ) -> dict[str, str]:
        return {
            "omnia.restoration_operation_id": str(operation_id),
            "omnia.restoration_purpose": purpose,
            "omnia.restoration_binding_digest": binding_digest,
            "omnia.restoration_artifact_digest": artifact_digest,
        }

    def export_volume(self, name: str):
        self.events.append(("export-code", name))
        yield _archive(self.docker.files[name]) if name in self.docker.files else self.archive

    def import_volume(self, name: str, path: Path) -> None:
        self.events.append(("import-code", name, path.read_bytes()))
        restored: dict[str, bytes] = {}
        try:
            with tarfile.open(path, mode="r:*") as archive:
                for member in archive:
                    if not member.isfile():
                        continue
                    payload = archive.extractfile(member)
                    assert payload is not None
                    restored[member.name.removeprefix("./")] = payload.read()
        except tarfile.TarError:
            assert self.import_files is not None
            restored = dict(self.import_files)
        self.docker.files[name] = restored

    def _require_restoration_volume_detached(self, name: str, *, purpose: str) -> None:
        assert purpose == "code"
        if name == self.workspace_volume:
            raise CellIdentityConflict("target code is active")

    def is_running(self) -> bool:
        return self.running

    def remove(self, _epoch: int) -> None:
        self.events.append("stop-source")
        self.running = False


class Docker:
    def __init__(self, events: list[object]) -> None:
        self.events = events
        self.files: dict[str, dict[str, bytes]] = {}
        self.volumes: dict[str, Volume] = {}
        self.workspace_volumes: dict[UUID, list[Volume]] = {}

    async def read_workspace_source_files(self, volume: str) -> dict[str, bytes]:
        self.events.append(("read-code", volume))
        return dict(self.files[volume])

    async def list_workspace_volumes(self, workspace_id: UUID) -> list[Volume]:
        return [
            volume
            for volume in self.workspace_volumes.get(workspace_id, [])
            if volume.name in self.volumes
        ]

    async def remove_volume(self, name: str) -> None:
        self.events.append(("cleanup-candidate-volume", name))
        self.volumes.pop(name, None)


class Machine:
    def __init__(self, epoch: int) -> None:
        self.saved = {"epoch": epoch, "ready_epoch": epoch, "manifest": {"services": []}}

    def state(self) -> dict[str, object]:
        return self.saved


class Runtime:
    def __init__(self, machine: Machine, backend: Backend) -> None:
        self.machine = machine
        self.backend = backend

    def parts(self, _state):
        return self.machine, self.backend


class Manager:
    def __init__(
        self,
        *,
        state: State,
        machine: Machine,
        backend: Backend,
        docker: Docker,
        events: list[object],
    ) -> None:
        self.state_store = Store({state.workspace_id: state})
        self.machine_runtime = Runtime(machine, backend)
        self.docker = docker
        self.operation_lock = Lock()
        self.events = events

    def _state_labels(self, state: State, kind: str) -> dict[str, str]:
        return {
            "omnia.workspace_id": str(state.workspace_id),
            "omnia.project_id": str(state.project_id),
            "omnia.owner_id": str(state.owner_id),
            "omnia.kind": kind,
        }

    async def _ensure_volume(self, name: str, labels: dict[str, str]) -> None:
        self.events.append(("ensure-code-volume", name))
        self.docker.volumes.setdefault(name, Volume(name, labels, self.docker.volumes, self.events))

    async def release_generation(self, workspace_id: UUID, mutation, *, generation_run_id: UUID):
        self.events.append("release-candidate")
        state = self.state_store.load(workspace_id)
        assert state.active_generation_run_id == generation_run_id
        state.active_generation_run_id = None
        state.active_generation_fencing_epoch = None
        state.fencing_epoch = mutation.fencing_epoch
        state._operations[mutation.operation_id] = SimpleNamespace(
            fencing_epoch=mutation.fencing_epoch,
            kind="release",
            request_digest=mutation.request_digest,
            status="completed",
            generation_run_id=generation_run_id,
            checkpoint_ref=None,
        )
        state.last_operation_id = mutation.operation_id

    async def destroy_compute_without_lock(
        self,
        workspace_id: UUID,
        mutation,
        *,
        checkpoint_ref,
        record_operation: bool,
        capture: bool,
    ) -> None:
        assert checkpoint_ref is None and record_operation and not capture
        state = self.state_store.load(workspace_id)
        if state.operation(mutation.operation_id) is not None:
            self.events.append("reconcile-destroyed-candidate")
            state.operation(mutation.operation_id).status = "completed"
            return
        self.events.append("destroy-candidate")
        state._operations[mutation.operation_id] = SimpleNamespace(
            fencing_epoch=mutation.fencing_epoch,
            kind="destroy",
            request_digest=mutation.request_digest,
            status="completed",
            generation_run_id=None,
            checkpoint_ref=None,
        )
        state.last_operation_id = mutation.operation_id


class FakeCodeEngine:
    def __init__(self, source: Manager, events: list[object]) -> None:
        self.source = source
        self.events = events
        self.start_count = 0
        self.start_effect_count = 0

    def activation_manager(self, _workspace_id: UUID) -> Manager:
        return self.source

    async def activation_stop_source_application(
        self,
        manager: Manager,
        _state: State,
        *,
        code_volume: str,
        database_volume: str,
        epoch: int,
    ) -> None:
        assert manager.operation_lock.held
        backend = manager.machine_runtime.backend
        assert backend.workspace_volume == code_volume
        assert backend.project_postgres_volume == database_volume
        assert manager.machine_runtime.machine.saved["epoch"] == epoch
        if backend.running:
            self.events.append("stop-source-app")
            backend.running = False

    async def activation_start_code_only_target(
        self,
        manager: Manager,
        state: State,
        *,
        code_volume: str,
        database_volume: str,
        epoch: int,
    ) -> None:
        backend = manager.machine_runtime.backend
        assert manager.operation_lock.held
        assert database_volume == backend.project_postgres_volume
        files = manager.docker.files[code_volume]
        package = json.loads(files.get("package.json", b"{}").decode())
        if package.get("scripts", {}).get("start", "").startswith("next start") and (
            ".next/BUILD_ID" not in files
            or "node_modules/next/package.json" not in files
        ):
            raise CellResourceError("next start target is not runnable")
        self.start_count += 1
        if (
            backend.workspace_volume == code_volume
            and state.fencing_epoch == epoch
            and backend.running
        ):
            self.events.append("reconcile-running-target")
            return
        self.start_effect_count += 1
        self.events.append(("start-target", code_volume, database_volume, epoch))
        backend.workspace_volume = code_volume
        backend.running = True
        state.fencing_epoch = epoch
        operation_id = uuid5(state.workspace_id, "code-activation-" + str(epoch))
        state._operations[operation_id] = SimpleNamespace(
            kind="code_restore",
            status="completed",
            fencing_epoch=epoch,
            request_digest=hashlib.sha256(
                (str(state.workspace_id) + ":restore:" + str(epoch)).encode()
            ).hexdigest(),
        )
        state.last_operation_id = operation_id
        manager.machine_runtime.machine.saved.update(epoch=epoch, ready_epoch=epoch)

    def activation_assert_target_controller(
        self,
        _manager: Manager,
        state: State,
        *,
        epoch: int,
        generation_run_id: UUID,
        allow_running: bool = False,
    ) -> None:
        operation_id = uuid5(state.workspace_id, "code-activation-" + str(epoch))
        operation = state.operation(operation_id)
        if (
            state.fencing_epoch != epoch
            or state.active_generation_run_id != generation_run_id
            or state.last_operation_id != operation_id
            or operation is None
            or operation.status not in (
                {"running", "completed"} if allow_running else {"completed"}
            )
        ):
            raise CellIdentityConflict("activation target controller fence is incomplete")

    async def activation_restart_source(
        self,
        manager: Manager,
        _state: State,
        *,
        code_volume: str,
        database_volume: str,
    ) -> None:
        self.events.append("restart-source")
        assert manager.operation_lock.held
        backend = manager.machine_runtime.backend
        assert backend.workspace_volume == code_volume
        assert backend.project_postgres_volume == database_volume
        backend.running = True

    async def activation_running_matches(
        self,
        _manager,
        state: State,
        backend: Backend,
        *,
        code_volume: str,
        database_volume: str,
        epoch: int,
        manifest,
    ) -> bool:
        return bool(
            manifest == {"services": []}
            and state.fencing_epoch == epoch
            and backend.workspace_volume == code_volume
            and backend.project_postgres_volume == database_volume
            and backend.running
        )


class Prober:
    def __init__(self, events: list[object]) -> None:
        self.events = events
        self.fail_once = False
        self.counts: Counter[str] = Counter()

    async def _probe(self, name: str, _request, target) -> str:
        self.events.append(("probe", name, target.database_volume))
        self.counts[name] += 1
        if name == "readiness" and self.fail_once:
            self.fail_once = False
            raise RuntimeError("transient readiness")
        return hashlib.sha256(name.encode()).hexdigest()

    async def verify_service_readiness(self, request, target):
        return await self._probe("readiness", request, target)

    async def verify_signed_owner_read(self, request, target):
        return await self._probe("owner-read", request, target)

    async def verify_signed_owner_create_update_delete(self, request, target):
        return await self._probe("owner-create-update-delete", request, target)

    async def verify_signed_owner_reload(self, request, target):
        return await self._probe("owner-reload", request, target)

    async def verify_cross_owner_denial(self, request, target):
        return await self._probe("cross-owner-denial", request, target)

    async def verify_unauthenticated_denial(self, request, target):
        return await self._probe("unauth-denial", request, target)


def fixture(request: RestorationAdaptationActivationRequest):
    events: list[object] = []
    source_docker = Docker(events)
    candidate_docker = Docker(events)
    source_state = State(
        workspace_id=request.source_workspace_id,
        request=request,
        fence=request.expected_source_fencing_epoch,
        code_volume=request.source_code_volume,
    )
    candidate_state = State(
        workspace_id=request.candidate_workspace_id,
        request=request,
        fence=request.candidate_fencing_epoch,
        code_volume="candidate-code",
    )
    source_docker.files[request.source_code_volume] = dict(SOURCE_FILES)
    candidate_docker.files["candidate-code"] = dict(CANDIDATE_FILES)
    live_db = Volume(
        request.live_database_volume,
        {"omnia.kind": "project-volume"},
        source_docker.volumes,
        events,
    )
    source_docker.volumes[request.live_database_volume] = live_db
    candidate_code = Volume(
        "candidate-code",
        {
            "omnia.workspace_id": str(request.candidate_workspace_id),
            "omnia.project_id": str(request.project_id),
            "omnia.owner_id": str(request.owner_id),
        },
        candidate_docker.volumes,
        events,
    )
    candidate_db = Volume(
        "candidate-db",
        dict(candidate_code.labels),
        candidate_docker.volumes,
        events,
    )
    candidate_docker.volumes.update(
        {candidate_code.name: candidate_code, candidate_db.name: candidate_db}
    )
    candidate_docker.workspace_volumes[request.candidate_workspace_id] = [
        candidate_code,
        candidate_db,
    ]
    source_backend = Backend(
        request.source_code_volume,
        request.live_database_volume,
        source_docker,
        events,
        import_files=CANDIDATE_FILES,
        stem=f"omnia-machine-{request.source_workspace_id.hex}",
    )
    candidate_backend = Backend(
        "candidate-code",
        "candidate-db",
        candidate_docker,
        events,
    )
    source = Manager(
        state=source_state,
        machine=Machine(request.expected_source_fencing_epoch),
        backend=source_backend,
        docker=source_docker,
        events=events,
    )
    candidate = Manager(
        state=candidate_state,
        machine=Machine(request.candidate_fencing_epoch),
        backend=candidate_backend,
        docker=candidate_docker,
        events=events,
    )
    code_engine = FakeCodeEngine(source, events)
    prober = Prober(events)
    effects = DockerRestorationAdaptationActivationEffects(
        code_engine=code_engine,
        candidate_manager_factory=lambda _source: candidate,
        health_prober=prober,
    )
    return SimpleNamespace(
        events=events,
        source=source,
        candidate=candidate,
        source_backend=source_backend,
        code_engine=code_engine,
        prober=prober,
        effects=effects,
        live_db=live_db,
    )


def activation_engine(
    tmp_path: Path,
    value: RestorationAdaptationActivationRequest,
    effects: DockerRestorationAdaptationActivationEffects,
) -> RestorationAdaptationActivationEngine:
    artifacts = tmp_path / "offer-artifacts"
    artifacts.mkdir(parents=True, exist_ok=True)
    (artifacts / f"{value.activation_id}.tar").write_bytes(ARCHIVE)
    return RestorationAdaptationActivationEngine(
        root=tmp_path / "activation",
        offer_artifacts_root=artifacts,
        effects=effects,
        managed_root=tmp_path,
    )


@pytest.fixture(autouse=True)
def provider_observations(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(
        "yleum_orchestrator.services.restoration_adaptation_activation_effects.catalog_contract",
        lambda _backend: (DATA_CONTRACT, []),
    )
    monkeypatch.setattr(
        "yleum_orchestrator.services.restoration_adaptation_activation_effects.observe_live_source",
        lambda *_args, **_kwargs: {"database_identity_digest": "d" * 64},
    )


def test_effects_fail_closed_without_signed_health_prober():
    value = activation_request()
    setup = fixture(value)
    with pytest.raises(CellResourceError, match="health prober is required"):
        DockerRestorationAdaptationActivationEffects(
            code_engine=setup.code_engine,
            candidate_manager_factory=lambda _source: setup.candidate,
            health_prober=None,
        )


def test_offer_and_effects_share_real_machine_database_volume_identity(tmp_path: Path):
    from tests.test_docker_machine_backend import backend as real_machine_backend

    class Volumes:
        def __init__(self) -> None:
            self.values: dict[str, object] = {}

        def get(self, name: str):
            import docker

            if name not in self.values:
                raise docker.errors.NotFound("missing")
            return self.values[name]

    volumes = Volumes()
    runtime = real_machine_backend(
        tmp_path,
        client=SimpleNamespace(volumes=volumes),
    )
    database_volume = runtime.project_postgres_volume
    volume = SimpleNamespace(
        attrs={
            "Name": database_volume,
            "CreatedAt": "2026-09-20T00:00:00Z",
            "Driver": "local",
            "Scope": "local",
            "Options": {},
            "Labels": runtime.labels("project-volume"),
        }
    )
    volumes.values[database_volume] = volume
    offer_digest = live_database_volume_identity_digest(
        runtime,
        expected_volume=database_volume,
    )
    value = activation_request(
        source_workspace_id=runtime.workspace_id,
        project_id=runtime.project_id,
        owner_id=runtime.owner_id,
        source_code_volume=runtime.workspace_volume,
        live_database_volume=database_volume,
        live_database_identity_digest=offer_digest,
    )

    effects_digest = DockerRestorationAdaptationActivationEffects._require_database_volume(
        value, runtime
    )

    assert effects_digest == offer_digest
    assert len(offer_digest) == 64


async def test_real_effects_activate_code_only_on_same_live_database(tmp_path: Path):
    value = activation_request()
    setup = fixture(value)
    service = activation_engine(tmp_path, value, setup.effects)

    receipt = await service.activate(value)

    target = f"omnia-machine-{value.source_workspace_id.hex}-code-{value.activation_id.hex}"
    assert receipt.state == "activated"
    assert setup.source_backend.workspace_volume == target
    assert setup.source_backend.project_postgres_volume == value.live_database_volume
    assert setup.source.docker.volumes[value.live_database_volume] is setup.live_db
    assert ("import-code", target, ARCHIVE) in setup.events
    assert not any(
        isinstance(event, tuple)
        and event[0] in {"import-code", "remove-volume"}
        and len(event) > 1
        and event[1] == value.live_database_volume
        for event in setup.events
    )
    assert setup.events.index("stop-source-app") < setup.events.index(
        ("start-target", target, value.live_database_volume, value.target_fencing_epoch)
    )
    assert setup.events.index("destroy-candidate") < setup.events.index("stop-source-app")
    assert setup.prober.counts == Counter(
        {
            "readiness": 1,
            "owner-read": 1,
            "owner-create-update-delete": 1,
            "owner-reload": 1,
            "cross-owner-denial": 1,
            "unauth-denial": 1,
        }
    )


async def test_recover_after_candidate_cleanup_reuses_attested_prepared_target(
    tmp_path: Path,
) -> None:
    class CrashAfterCleanup(BaseException):
        pass

    value = activation_request()
    setup = fixture(value)
    service = activation_engine(tmp_path, value, setup.effects)
    cleanup_candidate = setup.effects.cleanup_candidate

    async def cleanup_then_crash(
        request: RestorationAdaptationActivationRequest,
        sealed_artifact: Path,
    ) -> None:
        await cleanup_candidate(request, sealed_artifact)
        setup.effects.cleanup_candidate = cleanup_candidate
        raise CrashAfterCleanup

    setup.effects.cleanup_candidate = cleanup_then_crash

    with pytest.raises(CrashAfterCleanup):
        await service.activate(value)

    assert (await service.status(value)).state == "target_prepared"
    candidate_state = setup.candidate.state_store.load(value.candidate_workspace_id)
    assert candidate_state.active_generation_run_id is None
    assert await setup.candidate.docker.list_workspace_volumes(value.candidate_workspace_id) == []
    export_count = setup.events.count(("export-code", "candidate-code"))
    import_count = len(
        [event for event in setup.events if isinstance(event, tuple) and event[0] == "import-code"]
    )

    recovered = await service.recover(value)

    assert recovered.state == "activated"
    assert setup.events.count(("export-code", "candidate-code")) == export_count
    assert len(
        [event for event in setup.events if isinstance(event, tuple) and event[0] == "import-code"]
    ) == import_count
    assert "stop-source-app" in setup.events


async def test_recover_after_candidate_cleanup_rejects_tampered_prepared_runtime(
    tmp_path: Path,
) -> None:
    class CrashAfterCleanup(BaseException):
        pass

    value = activation_request()
    setup = fixture(value)
    service = activation_engine(tmp_path, value, setup.effects)
    cleanup_candidate = setup.effects.cleanup_candidate

    async def cleanup_then_crash(
        request: RestorationAdaptationActivationRequest,
        sealed_artifact: Path,
    ) -> None:
        await cleanup_candidate(request, sealed_artifact)
        setup.effects.cleanup_candidate = cleanup_candidate
        raise CrashAfterCleanup

    setup.effects.cleanup_candidate = cleanup_then_crash

    with pytest.raises(CrashAfterCleanup):
        await service.activate(value)

    target_volume = setup.effects._target_code_volume(value)
    setup.source.docker.files[target_volume]["node_modules/next/stale.js"] = b"stale"

    with pytest.raises(
        CellIdentityConflict,
        match="prepared target runnable content changed",
    ):
        await service.recover(value)

    assert setup.source_backend.running is True
    assert "stop-source-app" not in setup.events


async def test_recover_after_source_stop_verifies_target_with_source_app_down(
    tmp_path: Path,
) -> None:
    class CrashAfterSourceStop(BaseException):
        pass

    value = activation_request()
    setup = fixture(value)
    service = activation_engine(tmp_path, value, setup.effects)
    stop_source = setup.effects.stop_source_writers

    async def stop_then_crash(
        request: RestorationAdaptationActivationRequest,
    ) -> None:
        await stop_source(request)
        setup.effects.stop_source_writers = stop_source
        raise CrashAfterSourceStop

    setup.effects.stop_source_writers = stop_then_crash

    with pytest.raises(CrashAfterSourceStop):
        await service.activate(value)

    assert (await service.status(value)).state == "writers_stopping"
    assert setup.source_backend.running is False

    recovered = await service.recover(value)

    assert recovered.state == "activated"
    assert setup.source_backend.workspace_volume == recovered.target_volume_identity.code_volume


async def test_standard_next_target_is_runnable_before_source_is_stopped(
    tmp_path: Path,
) -> None:
    from yleum_orchestrator.services.code_restoration_engine import (
        validate_supported_runtime,
    )

    manifest = {
        "version": 1,
        "tasks": [
            {
                "name": "install",
                "role": "bootstrap",
                "argv": ["pnpm", "install", "--frozen-lockfile"],
                "timeout_seconds": 900,
            },
            {
                "name": "build",
                "role": "full_build",
                "argv": ["pnpm", "build"],
                "timeout_seconds": 600,
            },
        ],
        "services": [
            {
                "name": "web",
                "argv": ["pnpm", "start"],
                "readiness": {"port": 3000, "path": "/api/omnia/health"},
            }
        ],
        "routes": [{"path": "/", "service": "web", "port": 3000}],
    }
    source_files = {
        **CANDIDATE_FILES,
        "package.json": json.dumps(
            {
                "scripts": {"build": "next build", "start": "next start"},
                "dependencies": {"next": "15.5.2"},
            }
        ).encode(),
        "pnpm-lock.yaml": b"lockfileVersion: '9.0'\n",
        ".omnia/cell.json": json.dumps(manifest).encode(),
    }
    runnable_files = {
        **source_files,
        ".next/BUILD_ID": b"verified-build\n",
        ".next/server/app-paths-manifest.json": b"{}\n",
        "node_modules/next/package.json": b'{"name":"next","version":"15.5.2"}\n',
    }
    validated = validate_supported_runtime(_text_files(source_files))
    assert validated.services[0].argv == ["pnpm", "start"]
    sealed = _archive(source_files)
    value = activation_request(
        candidate_workspace_revision=_workspace_revision(source_files),
        candidate_artifact_digest=_files_digest(source_files),
        candidate_source_manifest_digest=source_manifest_digest(source_files),
        candidate_code_digest=hashlib.sha256(sealed).hexdigest(),
    )
    setup = fixture(value)
    setup.candidate.docker.files["candidate-code"] = dict(runnable_files)
    setup.candidate.machine_runtime.backend.archive = _archive(runnable_files)
    setup.source_backend.import_files = dict(source_files)
    artifact = tmp_path / "sealed-next-source.tar"
    artifact.write_bytes(sealed)
    target_volume = setup.effects._target_code_volume(value)

    async with setup.effects.hold_transition(value):
        target = await setup.effects.prepare_target_code(
            value,
            artifact,
            target_volume,
        )
        await setup.effects.stop_source_writers(value)
        assert setup.source_backend.running is False
        await setup.effects.start_target_writers(value, target)

    assert setup.source_backend.running is True
    assert setup.source_backend.workspace_volume == target_volume
    assert ".next/BUILD_ID" in setup.source.docker.files[target_volume]
    assert "node_modules/next/package.json" in setup.source.docker.files[target_volume]
    assert setup.events.index(("export-code", "candidate-code")) < setup.events.index(
        "stop-source-app"
    )
    assert not any(
        isinstance(event, tuple)
        and event[0] in {"export-code", "import-code"}
        and len(event) > 1
        and event[1] in {value.live_database_volume, "candidate-db"}
        for event in setup.events
    )


async def test_post_ponr_health_retry_never_restarts_source_or_deletes_target(tmp_path: Path):
    value = activation_request()
    setup = fixture(value)
    setup.prober.fail_once = True
    service = activation_engine(tmp_path, value, setup.effects)

    with pytest.raises(CellResourceError, match="forward recovery"):
        await service.activate(value)
    interrupted = await service.status(value)
    assert interrupted.state == "target_writers_admitted"
    assert interrupted.effects_admitted is True
    assert "restart-source" not in setup.events
    assert not any(
        isinstance(event, tuple) and event[0] == "remove-volume"
        for event in setup.events
    )

    recovered = await service.recover(value)
    assert recovered.state == "activated"
    assert setup.code_engine.start_count == 2
    assert setup.code_engine.start_effect_count == 1
    assert "reconcile-running-target" in setup.events
    assert "restart-source" not in setup.events
    assert setup.source.docker.volumes[value.live_database_volume] is setup.live_db


async def test_changed_fence_rejects_before_export_or_volume_effect(tmp_path: Path):
    value = activation_request()
    setup = fixture(value)
    setup.source.state_store.load(value.source_workspace_id).fencing_epoch += 1
    service = activation_engine(tmp_path, value, setup.effects)

    with pytest.raises(CellIdentityConflict, match="source identity or fence changed"):
        await service.activate(value)

    assert not any(
        isinstance(event, tuple)
        and event[0] in {"export-code", "ensure-code-volume", "import-code"}
        for event in setup.events
    )


async def test_foreign_target_controller_operation_rejects_before_target_start():
    value = activation_request()
    setup = fixture(value)
    state = setup.source.state_store.load(value.source_workspace_id)
    state.fencing_epoch = value.target_fencing_epoch
    state.last_operation_id = UUID(int=999)
    target = ActivationPreparedTarget(
        workspace_id=value.source_workspace_id,
        fencing_epoch=value.target_fencing_epoch,
        code_volume=(
            f"omnia-machine-{value.source_workspace_id.hex}"
            f"-code-{value.activation_id.hex}"
        ),
        code_digest=value.candidate_code_digest,
        database_volume=value.live_database_volume,
        database_identity_digest=value.live_database_identity_digest,
    )

    with pytest.raises(CellIdentityConflict, match="controller fence is incomplete"):
        await setup.effects.start_target_writers(value, target)

    assert setup.code_engine.start_count == 0


async def test_candidate_controller_volume_mismatch_rejects_before_export(tmp_path: Path):
    value = activation_request()
    setup = fixture(value)
    setup.candidate.machine_runtime.backend.workspace_volume = "foreign-candidate-code"
    service = activation_engine(tmp_path, value, setup.effects)

    with pytest.raises(CellIdentityConflict, match="controller code volume identity changed"):
        await service.activate(value)

    assert ("export-code", "candidate-code") not in setup.events


async def test_candidate_database_alias_rejects_before_export(tmp_path: Path):
    value = activation_request()
    setup = fixture(value)
    setup.candidate.machine_runtime.backend.project_postgres_volume = value.live_database_volume
    service = activation_engine(tmp_path, value, setup.effects)

    with pytest.raises(CellIdentityConflict, match="candidate volume aliases source"):
        await service.activate(value)

    assert not any(
        isinstance(event, tuple) and event[0] == "export-code" for event in setup.events
    )


async def test_candidate_cleanup_retry_is_idempotent_after_release(tmp_path: Path):
    value = activation_request()
    setup = fixture(value)
    artifact = tmp_path / "adaptation-effects-code.tar"
    artifact.write_bytes(ARCHIVE)
    try:
        await setup.effects.cleanup_candidate(value, artifact)
        await setup.effects.cleanup_candidate(value, artifact)
    finally:
        artifact.unlink(missing_ok=True)

    assert setup.events.count("release-candidate") == 1
    assert setup.events.count("destroy-candidate") == 1
    assert setup.events.count("reconcile-destroyed-candidate") == 1
    assert setup.source.docker.volumes[value.live_database_volume] is setup.live_db


async def test_ephemeral_postgres_identity_change_does_not_change_volume_binding(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    value = activation_request()
    setup = fixture(value)
    observations = iter(("old-container", "new-container", "newer-container"))

    def observed(*_args, **_kwargs):
        incarnation = next(observations, "newest-container")
        return {
            "database_identity_digest": hashlib.sha256(incarnation.encode()).hexdigest(),
            "serving_route_digest": "a" * 64,
        }

    monkeypatch.setattr(
        "yleum_orchestrator.services.restoration_adaptation_activation_effects.observe_live_source",
        observed,
    )
    service = activation_engine(tmp_path, value, setup.effects)

    receipt = await service.activate(value)

    assert receipt.state == "activated"
    assert setup.source.docker.volumes[value.live_database_volume] is setup.live_db
    assert "stop-source-app" in setup.events
    assert "stop-source" not in setup.events


async def test_binary_asset_is_bound_and_preserved_by_sealed_activation(tmp_path: Path):
    candidate_with_binary = {
        **CANDIDATE_FILES,
        "public/favicon.ico": b"\x00\xff",
    }
    sealed = _archive(candidate_with_binary)
    value = activation_request(
        candidate_source_manifest_digest=source_manifest_digest(candidate_with_binary),
        candidate_code_digest=hashlib.sha256(sealed).hexdigest(),
    )
    setup = fixture(value)
    setup.candidate.docker.files["candidate-code"]["public/favicon.ico"] = b"\x00\xff"
    setup.source_backend.import_files = candidate_with_binary
    service = activation_engine(tmp_path, value, setup.effects)
    (service.offer_artifacts_root / f"{value.activation_id}.tar").write_bytes(sealed)

    receipt = await service.activate(value)

    assert receipt.state == "activated"
    assert ("import-code", receipt.target_volume_identity.code_volume, sealed) in setup.events
    assert setup.source.docker.files[receipt.target_volume_identity.code_volume][
        "public/favicon.ico"
    ] == b"\x00\xff"


async def test_runtime_cache_change_after_offer_uses_exact_sealed_source(tmp_path: Path):
    value = activation_request()
    setup = fixture(value)
    setup.candidate.docker.files["candidate-code"][".next/cache/runtime"] = b"changed"
    service = activation_engine(tmp_path, value, setup.effects)

    receipt = await service.activate(value)

    assert receipt.state == "activated"
    assert ("export-code", "candidate-code") not in setup.events
    assert ("export-code", receipt.target_volume_identity.code_volume) in setup.events
    assert ".next/cache/runtime" not in setup.source.docker.files[
        receipt.target_volume_identity.code_volume
    ]


async def test_candidate_cleanup_replays_release_crash_after_begin(tmp_path: Path):
    value = activation_request()
    setup = fixture(value)
    artifact = tmp_path / "adaptation-effects-code.tar"
    artifact.write_bytes(ARCHIVE)
    original = setup.candidate.release_generation
    attempts = 0

    async def crash_after_begin(workspace_id, mutation, *, generation_run_id):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            state = setup.candidate.state_store.load(workspace_id)
            state.fencing_epoch = mutation.fencing_epoch
            state.last_operation_id = mutation.operation_id
            state._operations[mutation.operation_id] = SimpleNamespace(
                fencing_epoch=mutation.fencing_epoch,
                kind="release",
                request_digest=mutation.request_digest,
                status="running",
                generation_run_id=generation_run_id,
                checkpoint_ref=None,
            )
            raise RuntimeError("crash after release begin")
        await original(
            workspace_id,
            mutation,
            generation_run_id=generation_run_id,
        )

    setup.candidate.release_generation = crash_after_begin
    try:
        with pytest.raises(RuntimeError, match="crash after release begin"):
            await setup.effects.cleanup_candidate(value, artifact)
        await setup.effects.cleanup_candidate(value, artifact)
    finally:
        artifact.unlink(missing_ok=True)

    assert attempts == 2
    assert setup.events.count("destroy-candidate") == 1


async def test_candidate_cleanup_replays_destroy_crash_after_begin(tmp_path: Path):
    value = activation_request()
    setup = fixture(value)
    artifact = tmp_path / "adaptation-effects-code.tar"
    artifact.write_bytes(ARCHIVE)
    original = setup.candidate.destroy_compute_without_lock
    attempts = 0

    async def crash_after_begin(workspace_id, mutation, **kwargs):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            state = setup.candidate.state_store.load(workspace_id)
            state.fencing_epoch = mutation.fencing_epoch
            state.last_operation_id = mutation.operation_id
            state._operations[mutation.operation_id] = SimpleNamespace(
                fencing_epoch=mutation.fencing_epoch,
                kind="destroy",
                request_digest=mutation.request_digest,
                status="running",
                generation_run_id=None,
                checkpoint_ref=None,
            )
            raise RuntimeError("crash after destroy begin")
        await original(workspace_id, mutation, **kwargs)

    setup.candidate.destroy_compute_without_lock = crash_after_begin
    try:
        with pytest.raises(RuntimeError, match="crash after destroy begin"):
            await setup.effects.cleanup_candidate(value, artifact)
        await setup.effects.cleanup_candidate(value, artifact)
    finally:
        artifact.unlink(missing_ok=True)

    assert attempts == 2
    assert setup.events.count("reconcile-destroyed-candidate") == 1


@pytest.mark.parametrize("kind", ["release", "destroy"])
async def test_candidate_cleanup_reconciles_real_indeterminate_manager_operation(
    tmp_path: Path,
    kind: str,
) -> None:
    from tests.test_docker_cell_resources import _make_manager, _spec

    candidate_workspace_id = UUID(int=106)
    base_spec = _spec(candidate_workspace_id)
    value = activation_request(
        project_id=base_spec.project_id,
        owner_id=base_spec.owner_id,
        candidate_workspace_id=candidate_workspace_id,
    )
    manager, docker, state_store, _lock = _make_manager(tmp_path / "real-manager")
    generation_spec = replace(base_spec, generation_run_id=value.generation_run_id)
    await manager.ensure(
        generation_spec,
        LifecycleMutation(UUID(int=101), value.candidate_fencing_epoch, "a" * 64),
    )
    state = state_store.load(candidate_workspace_id)
    assert state is not None and state.resource_names is not None
    release = LifecycleMutation(
        DockerRestorationAdaptationActivationEffects._cleanup_operation_id(value, "release"),
        state.fencing_epoch + 1,
        value.activation_digest,
    )
    if kind == "release":
        state_store.begin(
            generation_spec,
            release,
            kind="release",
            phase="planned",
            resource_names=state.resource_names,
        )
        state_store.mark_indeterminate(candidate_workspace_id, mutation=release)
    else:
        await manager.release_generation(
            candidate_workspace_id,
            release,
            generation_run_id=value.generation_run_id,
        )
        released = state_store.load(candidate_workspace_id)
        assert released is not None and released.resource_names is not None
        destroy = LifecycleMutation(
            DockerRestorationAdaptationActivationEffects._cleanup_operation_id(
                value, "destroy"
            ),
            released.fencing_epoch + 1,
            value.activation_digest,
        )
        state_store.begin(
            replace(base_spec, generation_run_id=None),
            destroy,
            kind="destroy",
            phase="planned",
            resource_names=released.resource_names,
        )
        state_store.mark_indeterminate(candidate_workspace_id, mutation=destroy)
    effects = DockerRestorationAdaptationActivationEffects(
        code_engine=SimpleNamespace(activation_manager=lambda _workspace: object()),
        candidate_manager_factory=lambda _source: manager,
        health_prober=SimpleNamespace(),
    )
    artifact = tmp_path / f"{kind}-candidate.tar"
    artifact.write_bytes(ARCHIVE)

    await effects.cleanup_candidate(value, artifact)

    final = state_store.load(candidate_workspace_id)
    assert final is not None
    latest = final.operation(final.last_operation_id)
    assert latest is not None and latest.status == "completed"
    assert latest.kind == "destroy"
    assert final.active_generation_run_id is None
    assert await docker.list_workspace_volumes(candidate_workspace_id) == []

"""Phase 3 / stage A: `CellPublicationService` placing releases in the runtime cluster."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from uuid import UUID, uuid4

import pytest
from pydantic import SecretStr

from omnia_orchestrator.core.cell_resources import CellIdentityConflict, CellResourceError
from omnia_orchestrator.core.project_machine import MachineManifest
from omnia_orchestrator.schemas.cell_publication import CellDeployRequest
from omnia_orchestrator.services import k8s_publication as kp
from omnia_orchestrator.services.cell_publication import CellPublicationService
from omnia_orchestrator.services.k8s_placement import KubernetesPlacement
from omnia_orchestrator.services.project_machine import write_controller_json
from omnia_orchestrator.services.publication_trace import PublicationTrace

PROJECT = UUID("6cd1e70b-55b8-4025-b703-63f6d51745a8")
OWNER = UUID("b2aec0fc-1dae-4498-8c49-8d655e24234c")
WORKSPACE = UUID("0f5f0f6a-0d3a-4d2e-9d5f-6a1c2b3d4e5f")
SCHEMA = "a" * 64


def _settings(tmp_path: Path, **overrides: Any) -> SimpleNamespace:
    values: dict[str, Any] = dict(
        publication_backend="kubernetes",
        cell_state_path=str(tmp_path / "cells"),
        k8s_kubeconfig_path=str(tmp_path / "kubeconfig"),
        k8s_context="",
        image_registry="registry.yleum.ru",
        image_registry_username="orchestrator",
        image_registry_password=SecretStr("pw"),
        public_host_suffix="apps.yleum.ru",
        runtime_host_suffix="dev.yleum.ru",
        artifact_base_url="http://10.10.0.1:8003",
        cell_postgres_image="postgres@sha256:" + "4" * 64,
        cell_redis_image="redis@sha256:" + "8" * 64,
        cell_public_core_image="sha256:" + "c" * 64,
        cell_machine_guard_image="omnia-project-machine-guard@sha256:" + "d" * 64,
        cell_public_core_memory_bytes=768 * 1024**2,
        k8s_app_cpu_cores=1.0,
        k8s_app_memory_bytes=1024**3,
    )
    values.update(overrides)
    return SimpleNamespace(**values)


def _manifest(*, mounts: bool = False) -> MachineManifest:
    service: dict[str, Any] = {
        "name": "web",
        "argv": ["node", "server.js"],
        "cwd": ".",
        "readiness": {"port": 3000, "path": "/api/omnia/health", "timeout_seconds": 60},
    }
    if mounts:
        service["mounts"] = [{"volume": "uploads", "target": "/data/uploads"}]
    return MachineManifest.model_validate(
        {
            "version": 1,
            "services": [service],
            "routes": [{"path": "/", "service": "web", "port": 3000}],
        }
    )


class FakeSource:
    """The accepted editor cell: names its volumes exactly like the Docker backend."""

    workspace_id = WORKSPACE
    project_id = PROJECT
    owner_id = OWNER
    project_postgres_password = "src-pg-pw"
    stem = "omnia-machine-" + WORKSPACE.hex
    workspace_volume = stem + "-workspace"
    project_postgres_volume = stem + "-app-postgres-data"
    client = SimpleNamespace(name="docker")

    def volume_mapping(self, manifest: MachineManifest) -> dict[str, dict[str, str]]:
        volumes = {
            self.workspace_volume: {"bind": "/workspace", "mode": "rw"},
            self.stem + "-home": {"bind": "/root", "mode": "rw"},
            self.stem + "-data-omnia-pnpm-store": {"bind": "/pnpm/store", "mode": "rw"},
            self.stem + "-data-omnia-corepack": {
                "bind": "/root/.cache/node/corepack",
                "mode": "rw",
            },
            self.stem + "-next-cache": {"bind": "/workspace/.next/cache", "mode": "rw"},
        }
        for service in manifest.services:
            for mount in service.mounts:
                volumes[self.stem + "-data-" + mount.volume] = {"bind": mount.target, "mode": "rw"}
        return volumes

    def labels(self, kind: str) -> dict[str, str]:
        return {"omnia.resource_kind": kind, "omnia.workspace_id": str(self.workspace_id)}


def _volume(name: str, size: int = 100) -> SimpleNamespace:
    return SimpleNamespace(
        name=name, artifact_ref=hashlib.md5(name.encode()).hexdigest() + ".tar", size=size
    )


def _reference(source: FakeSource, names: list[str]) -> SimpleNamespace:
    return SimpleNamespace(
        image_id="sha256:" + "e" * 64,
        artifact_ref="f" * 32 + ".tar",
        size=4096,
        workspace_id=source.workspace_id,
        volumes=tuple(_volume(name) for name in names),
    )


class FakeStore:
    def __init__(self, root: Path) -> None:
        self.root = root

    def artifact_path(self, reference: str) -> Path:
        path = self.root / reference
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.exists():
            path.write_bytes(b"tar")
        return path


def _cold_names(source: FakeSource, *, uploads: bool = False) -> list[str]:
    names = [
        source.workspace_volume,
        source.stem + "-home",
        source.stem + "-data-omnia-pnpm-store",
        source.stem + "-next-cache",
        source.project_postgres_volume,
    ]
    if uploads:
        names.append(source.stem + "-data-uploads")
    return names


# ------------------------------------------------------------------ seed plan


def test_cold_plan_seeds_business_data_once_and_code_per_release(tmp_path: Path) -> None:
    source = FakeSource()
    manifest = _manifest(mounts=True)
    plan = KubernetesPlacement.seed_plan(
        source,
        manifest,
        _reference(source, _cold_names(source, uploads=True)),
        FakeStore(tmp_path),
        seeded=False,
    )
    by_path = {item["mount_path"]: item for item in plan}

    assert set(by_path) == {
        "/workspace",
        "/root",
        "/pnpm/store",
        kp.PROJECT_POSTGRES_DATA,
        "/data/uploads",
    }
    assert "/workspace/.next/cache" not in by_path  # disposable, emptyDir in the pod
    assert by_path[kp.PROJECT_POSTGRES_DATA]["durable"] and by_path["/data/uploads"]["durable"]
    assert not by_path["/workspace"]["durable"] and not by_path["/root"]["durable"]
    assert all(item["artifact"] and Path(item["artifact"]).is_file() for item in plan)


def test_warm_plan_mounts_data_without_reseeding_it(tmp_path: Path) -> None:
    source = FakeSource()
    manifest = _manifest(mounts=True)
    plan = KubernetesPlacement.seed_plan(
        source,
        manifest,
        _reference(source, [source.workspace_volume, source.stem + "-home"]),
        FakeStore(tmp_path),
        seeded=True,
    )
    by_path = {item["mount_path"]: item for item in plan}

    assert set(by_path) == {"/workspace", "/root", "/data/uploads"}
    assert by_path["/data/uploads"] == {
        "mount_path": "/data/uploads",
        "artifact": None,
        "durable": True,
        "size": 0,
    }
    assert kp.PROJECT_POSTGRES_DATA not in by_path


def test_plan_refuses_unmapped_volumes_and_a_cold_release_without_the_database(
    tmp_path: Path,
) -> None:
    source = FakeSource()
    with pytest.raises(CellIdentityConflict, match="unmapped volume"):
        KubernetesPlacement.seed_plan(
            source, _manifest(), _reference(source, ["stranger"]), FakeStore(tmp_path), seeded=False
        )
    with pytest.raises(CellResourceError, match="dedicated project database"):
        KubernetesPlacement.seed_plan(
            source,
            _manifest(),
            _reference(source, [source.workspace_volume]),
            FakeStore(tmp_path),
            seeded=False,
        )


def test_seeds_issue_capability_links_unless_claims_are_already_present(tmp_path: Path) -> None:
    placement = KubernetesPlacement(_settings(tmp_path), root=tmp_path / "pub")
    archive = tmp_path / "x.tar"
    archive.write_bytes(b"tar")
    plan = [
        {"mount_path": "/workspace", "artifact": str(archive), "durable": False, "size": 3},
        {"mount_path": "/data/uploads", "artifact": None, "durable": True, "size": 0},
    ]

    fresh = placement.seeds(plan)
    assert fresh[0].artifact_url is not None
    assert fresh[0].artifact_url.startswith("http://10.10.0.1:8003/internal/publication-artifacts/")
    token = fresh[0].artifact_url.rsplit("/", 1)[-1]
    assert kp.artifact_capabilities().resolve(token) == archive
    assert fresh[1].artifact_url is None and fresh[1].durable

    present = placement.seeds(plan, present=True)
    assert [seed.artifact_url for seed in present] == [None, None]


# -------------------------------------------------------------------- secrets


def test_project_password_is_recorded_privately_once_then_read(tmp_path: Path) -> None:
    placement = KubernetesPlacement(_settings(tmp_path), root=tmp_path / "pub")
    with pytest.raises(CellIdentityConflict, match="credential missing"):
        placement.project_password(PROJECT)
    assert placement.project_password(PROJECT, FakeSource()) == "src-pg-pw"
    assert placement.project_password(PROJECT) == "src-pg-pw"
    secret = tmp_path / "pub" / str(PROJECT) / "seed-secret.json"
    assert json.loads(secret.read_text()) == {"password": "src-pg-pw"}


def test_auth_secret_rotates_only_when_the_bot_configuration_changes(tmp_path: Path) -> None:
    adapter_root = tmp_path / "adapter"
    secrets_seen: list[str] = []

    class Adapter:
        root = adapter_root

        def secret(self, workspace_id: UUID) -> str:
            path = adapter_root / "boundary-secrets" / f"{workspace_id}.json"
            if not path.exists():
                write_controller_json(path, {"postgres_password": "initial"})
            value = json.loads(path.read_text())["postgres_password"]
            secrets_seen.append(value)
            return str(value)

    production = uuid4()
    first = KubernetesPlacement.auth_secret(Adapter(), production, {"MAX_BOT_TOKEN": "a"})
    same = KubernetesPlacement.auth_secret(Adapter(), production, {"MAX_BOT_TOKEN": "a"})
    rotated = KubernetesPlacement.auth_secret(Adapter(), production, {"MAX_BOT_TOKEN": "b"})

    assert first == same == "initial"
    assert rotated != "initial" and len(rotated) > 20


# ------------------------------------------------------------------- registry


class FakeDocker:
    def __init__(self) -> None:
        self.tags: list[tuple[str, str, str]] = []
        self.pushed: list[tuple[str, str, dict[str, str] | None]] = []

    class _Image:
        def __init__(self, outer: FakeDocker, image_id: str) -> None:
            self.id = image_id
            self.outer = outer
            self.attrs = {"Config": {"Labels": {"omnia.resource_kind": "environment"}, "Env": None}}

        def tag(self, repository: str, tag: str) -> None:
            self.outer.tags.append((self.id, repository, tag))

    def __init_images(self) -> None:  # pragma: no cover - helper
        pass

    @property
    def images(self) -> Any:
        outer = self

        class Images:
            def get(self, ref: str) -> FakeDocker._Image:
                return FakeDocker._Image(outer, ref)

            def push(
                self, repository: str, *, tag: str, stream: bool, decode: bool, auth_config: Any
            ) -> list[dict[str, Any]]:
                outer.pushed.append((repository, tag, auth_config))
                return [{"status": "ok"}]

        return Images()


def test_push_release_images_publishes_app_core_and_guard_under_stable_tags(
    tmp_path: Path,
) -> None:
    placement = KubernetesPlacement(_settings(tmp_path), root=tmp_path / "pub")
    docker = FakeDocker()
    source = FakeSource()
    reference = _reference(source, [])
    archive = tmp_path / "env.tar"
    archive.write_bytes(b"tar")

    images = placement.push_release_images(
        docker,
        reference,
        provenance={"omnia.resource_kind": "environment"},
        archive=archive,
        project_id=PROJECT,
        release_id="11111111-2222-4333-8444-555555555555",
    )

    assert images == {
        "app_image": f"registry.yleum.ru/max-app/{PROJECT}:11111111-222",
        "core_image": "registry.yleum.ru/platform/max-public-core:" + "c" * 12,
        "guard_image": "registry.yleum.ru/platform/project-machine-guard:" + "d" * 12,
    }
    assert all(
        auth == {"username": "orchestrator", "password": "pw"} for _, _, auth in docker.pushed
    )


# ------------------------------------------------------ service: the K8s branch


class FakeRuntime:
    def __init__(self, *, present: bool = False, schema: str = SCHEMA) -> None:
        self.published: list[kp.PublicationSpec] = []
        self.destroyed: list[UUID] = []
        self.retired: list[UUID] = []
        self.pruned: list[tuple[UUID, str]] = []
        self.present = present
        self.schema = schema
        self.app_image: str | None = None
        self.ready = True

    def publish(self, spec: kp.PublicationSpec) -> kp.PlacementResult:
        self.published.append(spec)
        self.present = True
        self.app_image = spec.app_image
        return kp.PlacementResult(spec.namespace, spec.public_host, spec.epoch)

    def schema_digest(self, namespace: str, password: str) -> str:
        assert password == "src-pg-pw"
        return self.schema

    def retire(self, project_id: UUID) -> None:
        self.retired.append(project_id)

    def destroy(self, project_id: UUID) -> None:
        self.destroyed.append(project_id)
        self.present = False

    def prune_release_volumes(self, project_id: UUID, keep_release_id: str) -> list[str]:
        self.pruned.append((project_id, keep_release_id))
        return []

    def status(self, project_id: UUID) -> dict[str, Any]:
        return {
            "namespace": f"app-{project_id}",
            "present": self.present,
            "ready": {"boundary": self.ready, "app": self.ready, "core": self.ready},
            "app_image": self.app_image,
            "release_id": None,
        }


def _request(**overrides: Any) -> CellDeployRequest:
    values: dict[str, Any] = dict(
        project_id=PROJECT,
        owner_id=OWNER,
        workspace_id=WORKSPACE,
        slug="kanareika-c31c55",
        snapshot_id=uuid4(),
        candidate_id=uuid4(),
        commit_sha="c" * 40,
        idempotency_key="publish-kanareika-1",
        source_revision="7" * 64,
        fencing_epoch=3,
        proof_key="1" * 64,
        schema_data_digest="2" * 64,
        build_ref="build/1",
        verification_ref="verify/1",
        runtime_env={"MAX_BOT_TOKEN": "bot"},
        business_config={"operator": {"legal_name": "ООО Ромашка"}},
        business_config_version=1,
    )
    values.update(overrides)
    return CellDeployRequest.model_validate(values)


class FakeAdapter:
    def __init__(self, root: Path) -> None:
        self.root = root

    def secret(self, workspace_id: UUID) -> str:
        return "boundary-" + str(workspace_id)[:8]


class FakeManager:
    def __init__(self, root: Path) -> None:
        self.machine_runtime = FakeAdapter(root)
        self.credential_store = SimpleNamespace(
            load_or_create=lambda _id: SimpleNamespace(postgres_password="core-pw")
        )


def _service(tmp_path: Path, runtime: FakeRuntime) -> CellPublicationService:
    settings = _settings(tmp_path)
    service = CellPublicationService(
        settings,
        root=tmp_path / "pub",
        manager_factory=lambda _ws: FakeManager(tmp_path / "adapter"),
    )
    assert service.placement is not None
    service.placement._runtime = runtime  # type: ignore[assignment]
    service.heartbeat_seconds = 0.01
    return service


def _journal(service: CellPublicationService, request: CellDeployRequest, **extra: Any) -> None:
    service._write(
        request.project_id,
        {
            "project_id": str(request.project_id),
            "history": [],
            "active_release": None,
            "owner_id": str(request.owner_id),
            "source_workspace_id": str(request.workspace_id),
            "production_workspace_id": str(service.production_identity(request)),
            "slug": request.slug,
            **extra,
        },
    )


async def _prepared(
    service: CellPublicationService,
    request: CellDeployRequest,
    tmp_path: Path,
    run_id: str,
    *,
    monkeypatch: pytest.MonkeyPatch,
) -> dict[str, Any]:
    source = FakeSource()
    assert service.placement is not None
    monkeypatch.setattr(
        service.placement,
        "push_release_images",
        lambda *a, **kw: {
            "app_image": f"registry.yleum.ru/max-app/{PROJECT}:{run_id[:12]}",
            "core_image": "registry.yleum.ru/platform/max-public-core:c",
            "guard_image": "registry.yleum.ru/platform/project-machine-guard:d",
        },
    )
    write_controller_json(
        tmp_path / "pub" / str(request.project_id) / "requests" / f"{run_id}.json",
        request.model_dump(mode="json"),
    )
    return await service._prepare_kubernetes(
        request,
        run_id,
        PublicationTrace(),
        _manifest(),
        source,
        _reference(source, _cold_names(source)),
        FakeStore(tmp_path / "artifacts"),
        SCHEMA,
    )


@pytest.mark.asyncio
async def test_prepare_records_a_cluster_release_without_host_admission(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime = FakeRuntime()
    service = _service(tmp_path, runtime)
    request = _request()
    _journal(service, request)
    run_id = str(uuid4())

    release = await _prepared(service, request, tmp_path, run_id, monkeypatch=monkeypatch)

    assert release["prod_url"] == "https://kanareika-c31c55.apps.yleum.ru"
    assert release["resource_profile"] == {"backend": "kubernetes"}
    assert release["placement"]["namespace"] == f"app-{PROJECT}"
    assert release["placement"]["app_image"].endswith(f":{run_id[:12]}")
    mounts = {item["mount_path"] for item in release["placement"]["seeds"]}
    assert kp.PROJECT_POSTGRES_DATA in mounts and "/workspace" in mounts
    # the source database password stays in the private seed secret, not the release
    assert "src-pg-pw" not in json.dumps(release)
    assert service._read(PROJECT)["prepared_release"]["release_id"] == run_id
    assert runtime.published == []  # nothing placed before activation


@pytest.mark.asyncio
async def test_activation_places_the_release_verifies_schema_and_probes_public_url(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime = FakeRuntime()
    service = _service(tmp_path, runtime)
    request = _request()
    _journal(service, request)
    run_id = str(uuid4())
    release = await _prepared(service, request, tmp_path, run_id, monkeypatch=monkeypatch)
    probed: list[str] = []

    async def probe(url: str, *, timeout_seconds: float) -> None:
        probed.append(url)

    monkeypatch.setattr(service, "_probe_public", probe)
    await service._activate_kubernetes(request, release, PublicationTrace())

    assert len(runtime.published) == 1
    spec = runtime.published[0]
    assert spec.public_host == "kanareika-c31c55.apps.yleum.ru"
    assert spec.project_postgres_password == "src-pg-pw"
    assert spec.core_postgres_password == "core-pw"
    assert spec.boundary_secret.startswith("boundary-")
    assert spec.runtime_env == {"MAX_BOT_TOKEN": "bot"}
    seeded = [seed for seed in spec.seed_volumes if seed.artifact_url]
    assert {seed.mount_path for seed in seeded} >= {kp.PROJECT_POSTGRES_DATA, "/workspace", "/root"}
    assert probed == ["https://kanareika-c31c55.apps.yleum.ru"]
    saved = service._read(PROJECT)
    assert saved["active_release"]["release_id"] == run_id
    assert saved["data_seeded"] is True
    assert saved["activation_pending"] is None and saved["prepared_release"] is None
    assert runtime.pruned == []  # first release: nothing to prune


@pytest.mark.asyncio
async def test_failed_first_activation_destroys_the_namespace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime = FakeRuntime(schema="b" * 64)  # startup changed the schema
    service = _service(tmp_path, runtime)
    request = _request()
    _journal(service, request)
    run_id = str(uuid4())
    release = await _prepared(service, request, tmp_path, run_id, monkeypatch=monkeypatch)

    with pytest.raises(CellResourceError, match="changed database schema"):
        await service._activate_kubernetes(request, release, PublicationTrace())

    assert runtime.destroyed == [PROJECT]
    saved = service._read(PROJECT)
    assert saved["active_release"] is None
    assert saved["activation_pending"] is None
    assert saved["recovery_required"] is True


@pytest.mark.asyncio
async def test_failed_update_rolls_the_cluster_back_to_the_live_release(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime = FakeRuntime(present=True)
    service = _service(tmp_path, runtime)
    request = _request()
    _journal(service, request)
    old_id = str(uuid4())
    old = await _prepared(service, request, tmp_path, old_id, monkeypatch=monkeypatch)

    async def probe(url: str, *, timeout_seconds: float) -> None:
        return None

    monkeypatch.setattr(service, "_probe_public", probe)
    await service._activate_kubernetes(request, old, PublicationTrace())
    new_id = str(uuid4())
    new = await _prepared(
        service,
        request.model_copy(update={"idempotency_key": "publish-kanareika-2"}),
        tmp_path,
        new_id,
        monkeypatch=monkeypatch,
    )

    async def failing_probe(url: str, *, timeout_seconds: float) -> None:
        raise CellResourceError("public HTTPS bootstrap readiness failed")

    monkeypatch.setattr(service, "_probe_public", failing_probe)
    with pytest.raises(CellResourceError, match="bootstrap readiness"):
        await service._activate_kubernetes(request, new, PublicationTrace())

    assert [spec.release_id for spec in runtime.published] == [old_id, new_id, old_id]
    rollback = runtime.published[-1]
    assert all(seed.artifact_url is None for seed in rollback.seed_volumes)  # claims exist
    saved = service._read(PROJECT)
    assert saved["active_release"]["release_id"] == old_id
    assert saved["activation_pending"] is None
    assert runtime.destroyed == []


@pytest.mark.asyncio
async def test_moving_a_docker_release_into_the_cluster_retires_the_host_bundle(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime = FakeRuntime()
    service = _service(tmp_path, runtime)
    request = _request()
    docker_release = {"release_id": "docker-1", "prod_url": "https://old.apps.yleum.ru"}
    _journal(service, request, active_release=docker_release, data_seeded=True)
    run_id = str(uuid4())
    release = await _prepared(service, request, tmp_path, run_id, monkeypatch=monkeypatch)
    # a Docker release never counts as seeded in the cluster: the seed is cold
    assert kp.PROJECT_POSTGRES_DATA in {s["mount_path"] for s in release["placement"]["seeds"]}
    unpublished: list[str] = []
    retired: list[Any] = []

    async def unpublish(host: str) -> None:
        unpublished.append(host)

    async def retire(project_id: UUID, saved: dict[str, Any], old: Any) -> None:
        retired.append(old)
        raise RuntimeError("profile changed")  # host-side trouble must not undo the move

    async def probe(url: str, *, timeout_seconds: float) -> None:
        return None

    from omnia_orchestrator.services import nginx_writer

    monkeypatch.setattr(nginx_writer, "unpublish", unpublish)
    monkeypatch.setattr(nginx_writer, "prod_host", lambda slug: f"{slug}.apps.yleum.ru")
    monkeypatch.setattr(service, "_retire_docker_production", retire)
    monkeypatch.setattr(service, "_probe_public", probe)
    await service._activate_kubernetes(request, release, PublicationTrace())

    assert unpublished == ["kanareika-c31c55.apps.yleum.ru"]
    assert retired == [None]  # identity-only backend: no release layout / profile check
    assert service._read(PROJECT)["active_release"]["release_id"] == run_id
    assert runtime.destroyed == [] and len(runtime.published) == 1


@pytest.mark.asyncio
async def test_second_release_prunes_the_previous_code_claims(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime = FakeRuntime()
    service = _service(tmp_path, runtime)
    request = _request()
    _journal(service, request)

    async def probe(url: str, *, timeout_seconds: float) -> None:
        return None

    monkeypatch.setattr(service, "_probe_public", probe)
    first = str(uuid4())
    await service._activate_kubernetes(
        request,
        await _prepared(service, request, tmp_path, first, monkeypatch=monkeypatch),
        PublicationTrace(),
    )
    second = str(uuid4())
    await service._activate_kubernetes(
        request,
        await _prepared(service, request, tmp_path, second, monkeypatch=monkeypatch),
        PublicationTrace(),
    )

    assert runtime.pruned == [(PROJECT, second)]
    assert service._read(PROJECT)["active_release"]["release_id"] == second


@pytest.mark.asyncio
async def test_disable_retires_the_app_and_keeps_its_data(tmp_path: Path) -> None:
    runtime = FakeRuntime(present=True)
    service = _service(tmp_path, runtime)
    request = _request()
    _journal(
        service,
        request,
        active_release={"release_id": "x", "placement": {"backend": "kubernetes"}},
    )

    await service.disable(PROJECT, request.slug)

    assert runtime.retired == [PROJECT] and runtime.destroyed == []
    saved = service._read(PROJECT)
    assert saved["disabled"] is True and saved["deletion_completed"] is True
    with pytest.raises(CellIdentityConflict, match="hostname mismatch"):
        await service.disable(PROJECT, "other-slug")


@pytest.mark.asyncio
async def test_serving_current_needs_ready_workloads_on_the_release_image(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime = FakeRuntime(present=True)
    runtime.app_image = "registry.yleum.ru/max-app/x:1"
    service = _service(tmp_path, runtime)
    request = _request()
    active = {
        "prod_url": "https://kanareika-c31c55.apps.yleum.ru",
        "placement": {
            "backend": "kubernetes",
            "public_host": "kanareika-c31c55.apps.yleum.ru",
            "app_image": "registry.yleum.ru/max-app/x:1",
        },
    }

    class Client:
        def __init__(self, **kwargs: Any) -> None:
            pass

        async def __aenter__(self) -> Client:
            return self

        async def __aexit__(self, *args: Any) -> None:
            return None

        async def get(self, url: str, headers: Any) -> SimpleNamespace:
            return SimpleNamespace(status_code=200)

    import httpx

    monkeypatch.setattr(httpx, "AsyncClient", Client)
    assert await service._serving_kubernetes(request, active) is True
    runtime.app_image = "registry.yleum.ru/max-app/x:2"
    assert await service._serving_kubernetes(request, active) is False
    runtime.app_image = "registry.yleum.ru/max-app/x:1"
    runtime.ready = False
    assert await service._serving_kubernetes(request, active) is False
    # the public suffix moved (e.g. trial suffix → apps.yleum.ru): never "current"
    runtime.ready = True
    moved = {**active, "placement": {**active["placement"], "public_host": "old.host"}}
    assert await service._serving_kubernetes(request, moved) is False


@pytest.mark.asyncio
async def test_a_docker_release_is_never_current_once_the_target_is_the_cluster(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path, FakeRuntime())
    request = _request()
    docker_release = {"release_id": "x", "prod_url": "https://kanareika-c31c55.apps.yleum.ru"}
    assert await service._serving_current(request, docker_release) is False
    assert service._data_seeded({"data_seeded": True, "active_release": docker_release}) is False
    assert (
        service._data_seeded(
            {
                "data_seeded": True,
                "active_release": {**docker_release, "placement": {"backend": "kubernetes"}},
            }
        )
        is True
    )


@pytest.mark.asyncio
async def test_reconcile_restores_vanished_objects_and_settles_interrupted_activation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime = FakeRuntime()
    service = _service(tmp_path, runtime)
    request = _request()
    _journal(service, request)
    run_id = str(uuid4())
    release = await _prepared(service, request, tmp_path, run_id, monkeypatch=monkeypatch)

    async def probe(url: str, *, timeout_seconds: float) -> None:
        return None

    monkeypatch.setattr(service, "_probe_public", probe)
    await service._activate_kubernetes(request, release, PublicationTrace())
    path = service._project_path(PROJECT)

    # healthy cluster: nothing to do
    assert await service._reconcile_kubernetes(PROJECT, path) == {
        "project_id": str(PROJECT),
        "state": "ready",
    }
    assert len(runtime.published) == 1

    # objects gone (present=False): re-applied without re-seeding
    runtime.present = False
    assert (await service._reconcile_kubernetes(PROJECT, path))["state"] == "ready"
    assert len(runtime.published) == 2
    assert all(seed.artifact_url is None for seed in runtime.published[-1].seed_volumes)

    # interrupted first publication: the half-placed namespace is destroyed
    _journal(service, request, activation_pending="pending-id")
    assert await service._reconcile_kubernetes(PROJECT, path) is None
    assert runtime.destroyed == [PROJECT]
    assert service._read(PROJECT)["activation_pending"] is None


@pytest.mark.asyncio
async def test_refresh_reapplies_the_live_release_with_current_configuration(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime = FakeRuntime()
    service = _service(tmp_path, runtime)
    request = _request()
    _journal(service, request)
    run_id = str(uuid4())
    release = await _prepared(service, request, tmp_path, run_id, monkeypatch=monkeypatch)

    async def probe(url: str, *, timeout_seconds: float) -> None:
        return None

    monkeypatch.setattr(service, "_probe_public", probe)
    await service._activate_kubernetes(request, release, PublicationTrace())
    applied = await service.configure(
        PROJECT,
        OWNER,
        business_config={"operator": {"legal_name": "ООО Лютик"}},
        business_config_version=2,
    )

    assert applied == {"applied": True}
    assert len(runtime.published) == 2
    refreshed = runtime.published[-1]
    assert refreshed.business_config == {"operator": {"legal_name": "ООО Лютик"}}
    assert refreshed.release_id == run_id
    assert all(seed.artifact_url is None for seed in refreshed.seed_volumes)

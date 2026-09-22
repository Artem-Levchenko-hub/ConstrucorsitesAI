"""Kubernetes placement of published MAX apps — shapes and flow, no cluster needed."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any
from uuid import UUID

import pytest

from omnia_orchestrator.core.project_machine import MachineManifest
from omnia_orchestrator.services import k8s_publication as kp

PROJECT = UUID("6cd1e70b-55b8-4025-b703-63f6d51745a8")
OWNER = UUID("b2aec0fc-1dae-4498-8c49-8d655e24234c")


def _manifest() -> MachineManifest:
    return MachineManifest.model_validate(
        {
            "version": 1,
            "services": [
                {
                    "name": "web",
                    "argv": ["node", "server.js"],
                    "cwd": ".",
                    "readiness": {"port": 3000, "path": "/api/omnia/health", "timeout_seconds": 60},
                }
            ],
            "routes": [{"path": "/", "service": "web", "port": 3000}],
        }
    )


def _spec(**overrides: Any) -> kp.PublicationSpec:
    values: dict[str, Any] = dict(
        project_id=PROJECT,
        owner_id=OWNER,
        release_id="11111111-1111-4111-8111-111111111111",
        epoch=3,
        slug="kanareika-c31c55",
        public_host="kanareika-c31c55.apps.yleum.ru",
        manifest=_manifest(),
        app_image="registry.yleum.ru/apps/6cd1e70b:3",
        core_image="registry.yleum.ru/platform/max-public-core:abc",
        guard_image="registry.yleum.ru/platform/project-machine-guard:main",
        postgres_image="postgres@sha256:" + "4" * 64,
        redis_image="redis@sha256:" + "8" * 64,
        boundary_secret="s3cret",
        boundary_server_source="print('boundary')",
        runtime_env={"MAX_BOT_TOKEN": "bot-token"},
        business_config={"operator": {"legal_name": "ООО Ромашка"}},
        project_postgres_password="pg-app",
        core_postgres_password="pg-core",
        seed_volumes=(
            kp.SeedVolume(
                kp.PROJECT_POSTGRES_DATA,
                "http://10.10.0.1:8003/internal/publication-artifacts/t1",
                True,
                1024,
            ),
            kp.SeedVolume(
                "/workspace", "http://10.10.0.1:8003/internal/publication-artifacts/t2", False, 2048
            ),
            kp.SeedVolume(
                "/root", "http://10.10.0.1:8003/internal/publication-artifacts/t3", False, 512
            ),
        ),
    )
    values.update(overrides)
    return kp.PublicationSpec(**values)


def _by(objects: list[dict[str, Any]], kind: str, name: str) -> dict[str, Any]:
    return next(o for o in objects if o["kind"] == kind and o["metadata"]["name"] == name)


def _container(workload: dict[str, Any], name: str) -> dict[str, Any]:
    return next(c for c in workload["spec"]["template"]["spec"]["containers"] if c["name"] == name)


def test_build_objects_places_one_app_per_namespace_behind_the_boundary() -> None:
    objects = kp.build_objects(_spec())
    kinds = [(o["kind"], o["metadata"]["name"]) for o in objects]

    assert kinds[0] == ("Namespace", "app-6cd1e70b-55b8-4025-b703-63f6d51745a8")
    assert all(
        o["metadata"].get("namespace") == "app-6cd1e70b-55b8-4025-b703-63f6d51745a8"
        for o in objects[1:]
    )
    assert (
        ("Deployment", "app") in kinds
        and ("Deployment", "core") in kinds
        and ("Deployment", "boundary") in kinds
    )
    assert ("StatefulSet", "project-postgres") in kinds and (
        "StatefulSet",
        "core-postgres",
    ) in kinds

    ingress = _by(objects, "Ingress", "public")
    assert ingress["metadata"]["annotations"] == {
        "cert-manager.io/cluster-issuer": "letsencrypt-prod"
    }
    assert ingress["spec"]["rules"][0]["host"] == "kanareika-c31c55.apps.yleum.ru"
    assert ingress["spec"]["rules"][0]["http"]["paths"][0]["backend"]["service"] == {
        "name": "boundary",
        "port": {"number": 3000},
    }
    assert ingress["spec"]["tls"] == [
        {"hosts": ["kanareika-c31c55.apps.yleum.ru"], "secretName": "public-tls"}
    ]


def test_boundary_gets_the_same_config_contract_as_docker() -> None:
    objects = kp.build_objects(_spec())
    config = json.loads(_by(objects, "Secret", "boundary-config")["stringData"]["config.json"])

    assert config == {
        "secret": "s3cret",
        "project_id": str(PROJECT),
        "epoch": 3,
        "core_host": "core",
        "machine_host": "app",
        "routes": [{"path": "/", "service": "web", "port": 3000}],
        "public_mode": True,
        "public_origin": "https://kanareika-c31c55.apps.yleum.ru",
    }
    boundary = _container(_by(objects, "Deployment", "boundary"), "boundary")
    assert boundary["command"] == ["python3", "/run/omnia-boundary/server.py"]
    assert boundary["securityContext"]["readOnlyRootFilesystem"] is True
    assert _by(objects, "ConfigMap", "boundary-server")["data"]["server.py"] == "print('boundary')"


def test_core_runs_compiled_command_with_business_config_and_runtime_env() -> None:
    objects = kp.build_objects(_spec())
    core = _container(_by(objects, "Deployment", "core"), "core")
    env = {item["name"]: item for item in core["env"]}

    assert core["command"] == list(kp._PUBLIC_CORE_COMMAND)
    assert env["MAX_BOT_TOKEN"]["value"] == "bot-token"
    assert env["OMNIA_PUBLIC_APP_ORIGIN"]["value"] == "https://kanareika-c31c55.apps.yleum.ru"
    assert env["NODE_ENV"]["value"] == "production"
    assert env["AUTH_SECRET"]["valueFrom"]["secretKeyRef"] == {
        "name": "core-config",
        "key": "AUTH_SECRET",
    }
    assert env["DATABASE_URL"]["valueFrom"]["secretKeyRef"] == {
        "name": "core-config",
        "key": "DATABASE_URL",
    }
    mount = next(m for m in core["volumeMounts"] if m["name"] == "business-config")
    assert mount["mountPath"] == "/app/omnia-business-config.json"
    secret = _by(objects, "Secret", "core-config")["stringData"]
    assert json.loads(secret["omnia-business-config.json"]) == {
        "operator": {"legal_name": "ООО Ромашка"}
    }
    assert secret["DATABASE_URL"] == "postgresql://postgres:pg-core@core-postgres:5432/postgres"


def test_app_machine_is_seeded_from_warm_artifacts_and_supervised() -> None:
    objects = kp.build_objects(_spec())
    app = _by(objects, "Deployment", "app")
    container = _container(app, "app")
    inits = app["spec"]["template"]["spec"]["initContainers"]

    assert container["command"] == ["python3", "/omnia/supervisor.py"]
    assert container["readinessProbe"]["httpGet"] == {"path": "/api/omnia/health", "port": 3000}
    assert {m["mountPath"] for m in container["volumeMounts"]} >= {
        "/workspace",
        "/root",
        "/omnia",
        "/run/omnia-logs",
    }
    assert [c["env"][0]["value"] for c in inits] == [
        "http://10.10.0.1:8003/internal/publication-artifacts/t2",
        "http://10.10.0.1:8003/internal/publication-artifacts/t3",
    ]
    # code/home caches are re-seeded on every release: no "keep existing data" guard
    assert all("data present" not in c["command"][2] for c in inits)
    env = {item["name"]: item for item in container["env"]}
    assert env["DATABASE_URL"]["valueFrom"]["secretKeyRef"] == {
        "name": "app-config",
        "key": "DATABASE_URL",
    }
    assert env["PORT"]["value"] == "3000"
    manifest_map = _by(objects, "ConfigMap", "app-manifest")["data"]
    assert json.loads(manifest_map["order.json"]) == ["web"]
    assert "supervisor.py" in manifest_map and "manifest.json" in manifest_map


def test_project_postgres_is_seeded_once_and_owned_by_postgres_uid() -> None:
    objects = kp.build_objects(_spec())
    pg = _by(objects, "StatefulSet", "project-postgres")
    init = pg["spec"]["template"]["spec"]["initContainers"]

    assert len(init) == 1
    script = init[0]["command"][2]
    assert "data present" in script  # durable: never re-seed live business data
    assert "chown -R 70:70 /seed" in script
    assert init[0]["env"] == [
        {"name": "SEED_URL", "value": "http://10.10.0.1:8003/internal/publication-artifacts/t1"}
    ]
    assert (
        pg["spec"]["volumeClaimTemplates"][0]["spec"]["resources"]["requests"]["storage"] == "5Gi"
    )
    core_pg = _by(objects, "StatefulSet", "core-postgres")
    assert core_pg["spec"]["template"]["spec"]["initContainers"] == []


def test_network_is_default_deny_with_explicit_paths() -> None:
    objects = kp.build_objects(_spec())
    policies = {o["metadata"]["name"]: o for o in objects if o["kind"] == "NetworkPolicy"}

    assert policies["default-deny"]["spec"] == {
        "podSelector": {},
        "policyTypes": ["Ingress", "Egress"],
    }
    boundary = policies["boundary"]["spec"]
    assert boundary["ingress"][0]["from"] == [
        {"namespaceSelector": {"matchLabels": {"kubernetes.io/metadata.name": "kube-system"}}}
    ]
    app_targets = [
        e["to"][0]["podSelector"]["matchLabels"]["app.kubernetes.io/component"]
        for e in policies["app"]["spec"]["egress"]
        if "app.kubernetes.io/component" in e["to"][0].get("podSelector", {}).get("matchLabels", {})
    ]
    assert app_targets == ["project-postgres"]
    core_egress = policies["core"]["spec"]["egress"]
    assert any("ipBlock" in e["to"][0] and e["ports"] == [{"port": 443}] for e in core_egress)


class FakeApi:
    def __init__(
        self,
        *,
        health_status: int = 200,
        exec_output: str = "",
        pod: str | None = "project-postgres-0",
    ) -> None:
        self.applied: list[dict[str, Any]] = []
        self.deleted: list[tuple[str, str, str, str | None]] = []
        self.waited: list[tuple[str, str]] = []
        self.execs: list[list[str]] = []
        self.health_status = health_status
        self.exec_output = exec_output
        self.pod = pod
        self.objects: dict[tuple[str, str, str | None], dict[str, Any]] = {}

    def apply(self, obj: dict[str, Any]) -> None:
        self.applied.append(obj)
        self.objects[(obj["kind"], obj["metadata"]["name"], obj["metadata"].get("namespace"))] = obj

    def delete(self, api_version: str, kind: str, name: str, namespace: str | None) -> None:
        self.deleted.append((api_version, kind, name, namespace))

    def wait_ready(self, kind: str, name: str, namespace: str, timeout_seconds: float) -> None:
        self.waited.append((kind, name))

    def exec(
        self,
        namespace: str,
        pod: str,
        command: list[str],
        *,
        container: str | None = None,
        timeout_seconds: float = 60,
    ) -> tuple[int, str]:
        self.execs.append(command)
        return (0 if self.exec_output else 1), self.exec_output

    def pod_name(self, namespace: str, selector: dict[str, str]) -> str | None:
        return self.pod

    def proxy_get(
        self, namespace: str, service: str, port: int, path: str, *, timeout_seconds: float = 20
    ) -> tuple[int, bytes]:
        return self.health_status, b"{}"

    def get(
        self, api_version: str, kind: str, name: str, namespace: str | None
    ) -> dict[str, Any] | None:
        return self.objects.get((kind, name, namespace))


def test_publish_applies_everything_then_waits_data_before_app_before_probing() -> None:
    api = FakeApi()
    result = kp.KubernetesPublishedRuntime(api).publish(_spec())

    assert result.namespace == "app-6cd1e70b-55b8-4025-b703-63f6d51745a8"
    assert [o["kind"] for o in api.applied][:2] == ["Namespace", "Secret"]
    assert api.waited == [
        ("StatefulSet", "core-postgres"),
        ("StatefulSet", "project-postgres"),
        ("Deployment", "redis"),
        ("Deployment", "core"),
        ("Deployment", "app"),
        ("Deployment", "boundary"),
    ]


def test_publish_fails_closed_when_the_boundary_does_not_answer() -> None:
    api = FakeApi(health_status=503)
    with pytest.raises(kp.PublicationPlacementError, match="HTTP 503"):
        kp.KubernetesPublishedRuntime(api).publish(_spec())


def test_schema_digest_ignores_restriction_keys_and_comments() -> None:
    dump = "\\restrict abc\n-- comment\nCREATE TABLE t (id int);\n\\unrestrict abc\n"
    api = FakeApi(exec_output=dump)
    digest = kp.KubernetesPublishedRuntime(api).schema_digest("app-x", "pw")

    assert digest == hashlib.sha256(b"CREATE TABLE t (id int);").hexdigest()
    assert "pg_dumpall --schema-only" in api.execs[0][2]


def test_schema_digest_needs_a_postgres_pod() -> None:
    with pytest.raises(kp.PublicationPlacementError, match="requires postgres"):
        kp.KubernetesPublishedRuntime(FakeApi(pod=None)).schema_digest("app-x", "pw")


def test_retire_removes_ingress_and_workloads_but_keeps_volumes() -> None:
    api = FakeApi()
    api.apply({"apiVersion": "v1", "kind": "Namespace", "metadata": {"name": f"app-{PROJECT}"}})
    kp.KubernetesPublishedRuntime(api).retire(PROJECT)

    kinds = {(kind, name) for _, kind, name, _ in api.deleted}
    assert (
        ("Ingress", "public") in kinds
        and ("Deployment", "app") in kinds
        and ("StatefulSet", "project-postgres") in kinds
    )
    assert not any(kind == "PersistentVolumeClaim" for _, kind, _, _ in api.deleted)
    assert not any(kind == "Namespace" for _, kind, _, _ in api.deleted)
    assert api.applied[-1]["metadata"]["labels"] == {"omnia.retired": "true"}


def test_capability_links_are_single_use_and_expire(tmp_path: Path) -> None:
    artifact = tmp_path / "a.tar"
    artifact.write_bytes(b"tar")
    store = kp.ArtifactCapabilityStore(ttl_seconds=100, uses=2)
    token = store.issue(artifact)

    assert store.resolve(token) == artifact
    assert store.resolve(token) == artifact
    assert store.resolve(token) is None
    assert store.resolve("nope") is None

    expiring = kp.ArtifactCapabilityStore(ttl_seconds=0.0)
    token = expiring.issue(artifact)
    assert expiring.resolve(token) is None
    with pytest.raises(kp.PublicationPlacementError):
        store.issue(tmp_path / "missing.tar")


def test_push_image_tags_then_pushes_and_surfaces_registry_errors() -> None:
    class Image:
        def __init__(self) -> None:
            self.tags: list[tuple[str, str]] = []

        def tag(self, repository: str, tag: str) -> None:
            self.tags.append((repository, tag))

    class Images:
        def __init__(self, lines: list[dict[str, Any]]) -> None:
            self.image = Image()
            self.lines = lines
            self.pushed: list[tuple[str, str]] = []

        def get(self, image_id: str) -> Image:
            return self.image

        def push(self, repository: str, tag: str, stream: bool, decode: bool, auth_config: Any):
            self.pushed.append((repository, tag))
            yield from self.lines

    class Client:
        def __init__(self, lines: list[dict[str, Any]]) -> None:
            self.images = Images(lines)

    ok = Client([{"status": "Pushed"}])
    ref = kp.push_image(
        ok, "sha256:abc", "registry.yleum.ru/apps/p", "3", auth={"username": "max", "password": "x"}
    )
    assert ref == "registry.yleum.ru/apps/p:3"
    assert ok.images.image.tags == [("registry.yleum.ru/apps/p", "3")]
    assert ok.images.pushed == [("registry.yleum.ru/apps/p", "3")]

    bad = Client([{"error": "denied"}])
    with pytest.raises(kp.PublicationPlacementError, match="denied"):
        kp.push_image(bad, "sha256:abc", "registry.yleum.ru/apps/p", "3", auth=None)


def test_artifact_link_serves_the_archive_once_then_404(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The seeding init container gets the tar exactly through the capability link;
    the same link is dead afterwards and unknown links look identical to it."""
    from fastapi.testclient import TestClient

    from omnia_orchestrator.routers import publication_artifacts

    archive = tmp_path / "abc.tar"
    archive.write_bytes(b"warm-data")
    store = kp.ArtifactCapabilityStore(ttl_seconds=60, uses=1)
    monkeypatch.setattr(publication_artifacts, "artifact_capabilities", lambda: store)

    from fastapi import FastAPI

    from omnia_orchestrator.core.errors import OrchestratorError, orchestrator_error_handler

    app = FastAPI()
    app.add_exception_handler(OrchestratorError, orchestrator_error_handler)
    app.include_router(publication_artifacts.router)
    client = TestClient(app)
    token = store.issue(archive)

    served = client.get(f"/internal/publication-artifacts/{token}")
    assert served.status_code == 200
    assert served.content == b"warm-data"
    assert served.headers["content-type"].startswith("application/x-tar")
    assert client.get(f"/internal/publication-artifacts/{token}").status_code == 404
    assert client.get("/internal/publication-artifacts/does-not-exist").status_code == 404

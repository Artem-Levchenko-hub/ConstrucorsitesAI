"""Glue between `CellPublicationService` and the Kubernetes runtime cluster.

The publication service keeps its journal, locks, idempotency and trace; this
module owns everything that is specific to placing a sealed release in the
cluster: registry pushes, the seed plan (which captured volume lands on which
mount), the per-project secrets and the `PublicationSpec` itself.

Secrets never enter the durable release record: the record carries artifact
paths and image references only; passwords are read from controller-private
files at the moment a spec is built.
"""

from __future__ import annotations

import hashlib
import json
import secrets
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit
from uuid import UUID

from omnia_orchestrator.core.cell_resources import CellIdentityConflict, CellResourceError
from omnia_orchestrator.core.project_machine import MachineManifest
from omnia_orchestrator.schemas.cell_publication import CellDeployRequest
from omnia_orchestrator.services.k8s_publication import (
    PROJECT_POSTGRES_DATA,
    KubernetesClusterApi,
    KubernetesPublishedRuntime,
    PublicationSpec,
    SeedVolume,
    artifact_capabilities,
    ensure_release_image,
    push_image,
)
from omnia_orchestrator.services.project_machine import write_controller_json

NEXT_CACHE_PATH = "/workspace/.next/cache"
_ARTIFACT_ROUTE = "/internal/publication-artifacts/"


def _short(image_ref: str) -> str:
    """A registry tag from an image id (`sha256:…`) or a digest reference (`name@sha256:…`)."""
    digest = image_ref.rsplit("sha256:", 1)[-1]
    if len(digest) < 12:
        raise CellResourceError("pinned platform image is required for Kubernetes placement")
    return digest[:12]


class KubernetesPlacement:
    def __init__(
        self,
        settings: Any,
        *,
        root: Path,
        runtime: KubernetesPublishedRuntime | None = None,
    ) -> None:
        self.settings = settings
        self.root = root  # controller-private publication root
        self._runtime = runtime

    # ------------------------------------------------------------ cluster

    @property
    def runtime(self) -> KubernetesPublishedRuntime:
        if self._runtime is None:
            api = KubernetesClusterApi(
                self.settings.k8s_kubeconfig_path,
                context=self.settings.k8s_context or None,
            )
            self._runtime = KubernetesPublishedRuntime(api)
        return self._runtime

    @staticmethod
    def namespace(project_id: UUID) -> str:
        return f"app-{project_id}"

    @staticmethod
    def placed(release: dict[str, Any] | None) -> bool:
        """Whether a journaled release lives in the cluster (Docker releases carry no placement)."""
        return bool(release and (release.get("placement") or {}).get("backend") == "kubernetes")

    # ------------------------------------------------------------ naming

    def public_host(self, slug: str) -> str:
        suffix = self.settings.public_host_suffix or self.settings.runtime_host_suffix
        return f"{slug}.{suffix}"

    def prod_url(self, slug: str) -> str:
        return f"https://{self.public_host(slug)}"

    # ----------------------------------------------------------- registry

    def registry_auth(self) -> dict[str, str] | None:
        username = self.settings.image_registry_username
        if not username:
            return None
        return {
            "username": username,
            "password": self.settings.image_registry_password.get_secret_value(),
        }

    def push_release_images(
        self,
        docker_client: Any,
        reference: Any,
        *,
        provenance: dict[str, str],
        archive: Path,
        project_id: UUID,
        release_id: str,
    ) -> dict[str, str]:
        """Blocking: make the app, core and guard images pullable by the cluster."""
        ensure_release_image(docker_client, reference.image_id, archive, provenance=provenance)
        registry = self.settings.image_registry.rstrip("/")
        auth = self.registry_auth()
        core_ref = self.settings.cell_public_core_image
        guard_ref = self.settings.cell_machine_guard_image
        return {
            "app_image": push_image(
                docker_client,
                reference.image_id,
                f"{registry}/max-app/{project_id}",
                release_id[:12],
                auth=auth,
            ),
            "core_image": push_image(
                docker_client,
                core_ref,
                f"{registry}/platform/max-public-core",
                _short(core_ref),
                auth=auth,
            ),
            "guard_image": push_image(
                docker_client,
                guard_ref,
                f"{registry}/platform/project-machine-guard",
                _short(guard_ref),
                auth=auth,
            ),
        }

    # ---------------------------------------------------------- seed plan

    @staticmethod
    def seed_plan(
        source: Any,
        manifest: MachineManifest,
        reference: Any,
        store: Any,
        *,
        seeded: bool,
    ) -> list[dict[str, Any]]:
        """Which captured volume restores where.

        Business data (project Postgres, declared data mounts) is durable: seeded
        on the first publication only and mounted as-is afterwards. Code volumes
        are re-seeded for every release. The Next build cache is disposable.
        """
        binds = {name: bind["bind"] for name, bind in source.volume_mapping(manifest).items()}
        binds[source.project_postgres_volume] = PROJECT_POSTGRES_DATA
        durable_paths = {
            PROJECT_POSTGRES_DATA,
            *(mount.target for service in manifest.services for mount in service.mounts),
        }
        plan: list[dict[str, Any]] = []
        for volume in reference.volumes:
            path = binds.get(volume.name)
            if path is None:
                raise CellIdentityConflict("publication artifact has unmapped volume")
            if path == NEXT_CACHE_PATH:
                continue
            durable = path in durable_paths
            if seeded and durable:
                continue
            plan.append(
                {
                    "mount_path": path,
                    "artifact": str(store.artifact_path(volume.artifact_ref)),
                    "durable": durable,
                    "size": volume.size,
                }
            )
        planned = {item["mount_path"] for item in plan}
        for path in sorted(durable_paths - {PROJECT_POSTGRES_DATA} - planned):
            plan.append({"mount_path": path, "artifact": None, "durable": True, "size": 0})
        if not seeded and PROJECT_POSTGRES_DATA not in planned:
            raise CellResourceError("publication requires captured dedicated project database")
        return plan

    def seeds(self, plan: list[dict[str, Any]], *, present: bool = False) -> tuple[SeedVolume, ...]:
        """Turn a plan into spec volumes; `present` mounts everything without seeding
        (rollback, configuration refresh, recovery of claims that already exist)."""
        base = self.settings.artifact_base_url.rstrip("/")
        capabilities = artifact_capabilities()
        result = []
        for item in plan:
            url = None
            if not present and item.get("artifact"):
                token = capabilities.issue(Path(item["artifact"]))
                url = f"{base}{_ARTIFACT_ROUTE}{token}"
            result.append(
                SeedVolume(item["mount_path"], url, bool(item["durable"]), int(item["size"]))
            )
        return tuple(result)

    # ------------------------------------------------------------ secrets

    def project_password(self, project_id: UUID, source: Any = None) -> str:
        """The project database keeps the password baked into its seeded data
        directory. Recorded privately on the first publication, read afterwards."""
        path = self.root / str(project_id) / "seed-secret.json"
        if source is not None:
            write_controller_json(path, {"password": source.project_postgres_password})
            return str(source.project_postgres_password)
        if path.is_symlink() or not path.is_file():
            raise CellIdentityConflict("publication database credential missing")
        value = json.loads(path.read_text(encoding="utf-8"))
        password = value.get("password")
        if not isinstance(password, str) or not password:
            raise CellIdentityConflict("publication database credential missing")
        return password

    @staticmethod
    def auth_secret(adapter: Any, production_id: UUID, runtime_env: dict[str, str]) -> str:
        """Same contract as the Docker gateway: the public cookie key rotates when
        the bot configuration changes (or is revoked), never on a code release."""
        path = adapter.root / "public-boundary-auth" / f"{production_id}.json"
        if path.is_symlink():
            raise CellResourceError("unsafe public auth configuration")
        old = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
        digest = hashlib.sha256(json.dumps(runtime_env, sort_keys=True).encode()).hexdigest()
        if old and old.get("digest") != digest:
            write_controller_json(
                adapter.root / "boundary-secrets" / f"{production_id}.json",
                {"postgres_password": secrets.token_urlsafe(32)},
            )
        write_controller_json(path, {"digest": digest})
        return str(adapter.secret(production_id))

    # --------------------------------------------------------------- spec

    def build_spec(
        self,
        request: CellDeployRequest,
        release: dict[str, Any],
        *,
        seeds: tuple[SeedVolume, ...],
        boundary_secret: str,
        project_postgres_password: str,
        core_postgres_password: str,
    ) -> PublicationSpec:
        from omnia_orchestrator.services.machine_business_config import boundary_source

        placement = release["placement"]
        artifacts = urlsplit(self.settings.artifact_base_url)
        if not artifacts.hostname:
            raise CellResourceError("artifact base URL must name the orchestrator host")
        return PublicationSpec(
            project_id=request.project_id,
            owner_id=request.owner_id,
            release_id=release["release_id"],
            epoch=int(release["epoch"]),
            slug=request.slug,
            public_host=placement["public_host"],
            manifest=MachineManifest.model_validate(release["manifest"]),
            app_image=placement["app_image"],
            core_image=placement["core_image"],
            guard_image=placement["guard_image"],
            postgres_image=self.settings.cell_postgres_image,
            redis_image=self.settings.cell_redis_image,
            boundary_secret=boundary_secret,
            boundary_server_source=boundary_source(),
            runtime_env=dict(request.runtime_env),
            business_config=dict(request.business_config),
            project_postgres_password=project_postgres_password,
            core_postgres_password=core_postgres_password,
            seed_volumes=seeds,
            app_cpu_cores=float(self.settings.k8s_app_cpu_cores),
            app_memory_bytes=int(self.settings.k8s_app_memory_bytes),
            runtime_class=self.settings.k8s_app_runtime_class,
            core_memory_bytes=int(self.settings.cell_public_core_memory_bytes),
            tls_mode=self.settings.k8s_tls_mode,
            artifact_source_cidr=f"{artifacts.hostname}/32",
            artifact_port=artifacts.port or (443 if artifacts.scheme == "https" else 80),
        )


__all__ = ["NEXT_CACHE_PATH", "KubernetesPlacement"]

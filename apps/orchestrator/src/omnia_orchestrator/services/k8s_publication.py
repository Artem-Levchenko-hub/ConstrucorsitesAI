"""Kubernetes placement for published MAX apps (Phase 3, stage A).

`CellPublicationService` still seals a release exactly as for Docker: an image id
(code in `/workspace`, no runtime configuration baked in), the machine manifest
(services, routes, mounts), warm volume artifacts, the schema digest and the public
URL. This module turns such a release into objects in the *runtime* cluster — one
namespace per project holding the app machine, the trusted boundary, the managed
MAX core with its own Postgres and Redis, the project Postgres seeded from the warm
artifact, default-deny NetworkPolicies and an Ingress with a Let's Encrypt cert.

Design (docs/plans/2026-09-23-k8s-publication-stage-a.md):

* the placement is a pure function of a `PublicationSpec` → `build_objects()` returns
  plain Kubernetes dicts, so shapes are unit-tested without a cluster;
* the cluster is reached only through the tiny `KubernetesApi` protocol (server-side
  apply, delete, rollout wait, exec, API-server proxy GET) — tests use a fake;
* artifacts never go through the registry: the seeding init containers fetch them
  from the orchestrator over WireGuard with a single-use, expiring capability URL;
* images reach the cluster through the private registry (`docker tag` + `push`).
"""

from __future__ import annotations

import hashlib
import json
import secrets
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import quote
from uuid import UUID

import structlog

from omnia_orchestrator.core.errors import OrchestratorError
from omnia_orchestrator.core.project_machine import MachineManifest

log = structlog.get_logger(__name__)

FIELD_MANAGER = "omnia-orchestrator"
PROJECT_POSTGRES_DATA = "/var/lib/postgresql/data"
BOUNDARY_PORT = 3000
CORE_PORT = 3000
_PUBLIC_CORE_COMMAND = (
    "sh",
    "-ec",
    "timeout 45 node scripts/apply-migrations.mjs\nexec node server.js",
)
# Alpine postgres images run the server as uid 70; the seed init container restores
# the warm data directory as root and hands it over before postgres starts.
_POSTGRES_UID = 70


class PublicationPlacementError(OrchestratorError):
    def __init__(self, message: str, *, status_code: int = 503) -> None:
        super().__init__(code="container_failure", message=message, status_code=status_code)


# --------------------------------------------------------------------------- spec


@dataclass(frozen=True)
class SeedVolume:
    """One volume of the app machine and, optionally, the warm artifact that
    fills it before the app starts.

    * durable (business data: project Postgres, declared data mounts) — one PVC per
      project, seeded once and never re-seeded while data is present;
    * not durable (workspace, home, package stores) — one PVC per release, seeded
      when the release first starts, kept across pod restarts, pruned after the
      next release is live.
    `artifact_url=None` mounts an existing volume without seeding.
    """

    mount_path: str
    artifact_url: str | None
    durable: bool
    size_bytes: int


@dataclass(frozen=True)
class PublicationSpec:
    project_id: UUID
    owner_id: UUID
    release_id: str
    epoch: int
    slug: str
    public_host: str
    manifest: MachineManifest
    app_image: str  # registry reference the cluster can pull
    core_image: str
    guard_image: str
    postgres_image: str
    redis_image: str
    boundary_secret: str
    boundary_server_source: str
    runtime_env: dict[str, str]
    business_config: dict[str, Any]
    project_postgres_password: str
    core_postgres_password: str
    seed_volumes: tuple[SeedVolume, ...] = ()
    app_env: dict[str, str] = field(default_factory=dict)
    app_cpu_cores: float = 0.5
    app_memory_bytes: int = 1024**3
    core_memory_bytes: int = 768 * 1024**2
    project_postgres_storage: str = "5Gi"
    core_postgres_storage: str = "2Gi"
    ingress_class: str = "traefik"
    cluster_issuer: str = "letsencrypt-prod"
    # Where seeding init containers fetch archives from (the orchestrator over
    # WireGuard); the only egress the app and project-postgres pods get besides DNS.
    artifact_source_cidr: str = "10.10.0.1/32"
    artifact_port: int = 8003

    @property
    def namespace(self) -> str:
        return f"app-{self.project_id}"

    @property
    def public_origin(self) -> str:
        return f"https://{self.public_host}"


# ------------------------------------------------------------------ manifests


def _labels(spec: PublicationSpec, component: str) -> dict[str, str]:
    return {
        "app.kubernetes.io/managed-by": "omnia-orchestrator",
        "app.kubernetes.io/part-of": "max-app",
        "app.kubernetes.io/component": component,
        "omnia.project-id": str(spec.project_id),
        "omnia.owner-id": str(spec.owner_id),
        "omnia.release-id": spec.release_id,
        "omnia.epoch": str(spec.epoch),
    }


def _selector(spec: PublicationSpec, component: str) -> dict[str, str]:
    return {"omnia.project-id": str(spec.project_id), "app.kubernetes.io/component": component}


def _bytes(value: int) -> str:
    return f"{max(1, value // (1024**2))}Mi"


def _millicores(value: float) -> str:
    return f"{max(50, int(value * 1000))}m"


def _env(values: dict[str, str]) -> list[dict[str, str]]:
    return [{"name": key, "value": str(value)} for key, value in sorted(values.items())]


def _service(spec: PublicationSpec, component: str, port: int) -> dict[str, Any]:
    return {
        "apiVersion": "v1",
        "kind": "Service",
        "metadata": {
            "name": component,
            "namespace": spec.namespace,
            "labels": _labels(spec, component),
        },
        "spec": {
            "selector": _selector(spec, component),
            "ports": [
                {
                    "name": "http" if port != 5432 and port != 6379 else "tcp",
                    "port": port,
                    "targetPort": port,
                }
            ],
        },
    }


SEED_MARKER = ".omnia-seeded"
SEED_LINKS_SECRET = "seed-links"


def _seed_init_container(
    name: str, mount_name: str, link_key: str, *, chown: str | None
) -> dict[str, Any]:
    """Restore one archive into a volume exactly once.

    A half-extracted archive (pod killed mid-seed) must not pass as "data present":
    the marker is written only after a complete extraction, and its absence with
    stale content clears the volume before the archive is fetched again. The link
    itself comes from the `seed-links` Secret so a re-issued link never changes the
    pod template (no rollout of a healthy app on configuration refresh).
    """
    own = f" && chown -R {chown} /seed" if chown else ""
    script = (
        f'[ -f /seed/{SEED_MARKER} ] && {{ echo "seed: data present, keeping"; exit 0; }}; '
        "set -e; find /seed -mindepth 1 -delete; "
        'wget -q -O /tmp/seed.tar "$SEED_URL" && tar -xf /tmp/seed.tar -C /seed'
        f"{own} && rm -f /tmp/seed.tar && touch /seed/{SEED_MARKER}{own} && echo 'seed: restored'"
    )
    return {
        "name": name,
        "image": "alpine:3.20",
        "command": ["sh", "-c", script],
        "env": [
            {
                "name": "SEED_URL",
                "valueFrom": {"secretKeyRef": {"name": SEED_LINKS_SECRET, "key": link_key}},
            }
        ],
        "volumeMounts": [{"name": mount_name, "mountPath": "/seed"}],
        "resources": {"requests": {"cpu": "50m", "memory": "64Mi"}, "limits": {"memory": "256Mi"}},
    }


def _volume_name(spec: PublicationSpec, seed: SeedVolume) -> str:
    """Durable data keeps one claim per project; code claims carry the release."""
    digest = hashlib.sha256(seed.mount_path.encode()).hexdigest()[:8]
    if seed.durable:
        return f"data-{digest}"
    return f"code-{spec.release_id[:8]}-{digest}"


def _link_key(seed: SeedVolume) -> str:
    return "seed-" + hashlib.sha256(seed.mount_path.encode()).hexdigest()[:12]


def _claim(spec: PublicationSpec, name: str, seed: SeedVolume) -> dict[str, Any]:
    storage = max(seed.size_bytes * 3, 1024**3)
    return {
        "apiVersion": "v1",
        "kind": "PersistentVolumeClaim",
        "metadata": {
            "name": name,
            "namespace": spec.namespace,
            "labels": {
                **_labels(spec, "app"),
                "omnia.volume-kind": "data" if seed.durable else "code",
            },
        },
        "spec": {
            "accessModes": ["ReadWriteOnce"],
            "resources": {"requests": {"storage": _bytes(storage)}},
        },
    }


def _supervisor_source() -> str:
    """PID 1 of the app container: runs manifest services like the Docker exec wrapper."""
    return r"""
import json, os, signal, subprocess, sys, time
manifest = json.load(open("/omnia/manifest.json"))
order = json.load(open("/omnia/order.json"))
services = {s["name"]: s for s in manifest["services"]}
procs = {}
stop = False
def _term(*_):
    global stop
    stop = True
    for p in procs.values():
        try: p.terminate()
        except OSError: pass
signal.signal(signal.SIGTERM, _term)
signal.signal(signal.SIGINT, _term)
def start(name):
    s = services[name]
    cwd = os.path.normpath(os.path.join("/workspace", s.get("cwd", ".")))
    print(f"[omnia] start {name}: {' '.join(s['argv'])} (cwd {cwd})", flush=True)
    procs[name] = subprocess.Popen(s["argv"], cwd=cwd, stdout=sys.stdout, stderr=sys.stderr)
for name in order:
    start(name)
backoff = {name: 1.0 for name in order}
while not stop:
    time.sleep(1)
    for name, p in list(procs.items()):
        rc = p.poll()
        if rc is None:
            continue
        policy = services[name].get("restart", "on-failure")
        print(f"[omnia] {name} exited rc={rc} policy={policy}", flush=True)
        if policy == "never" or (policy == "on-failure" and rc == 0):
            procs.pop(name)
            continue
        time.sleep(backoff[name]); backoff[name] = min(30.0, backoff[name] * 2)
        start(name)
    if not procs:
        break
for p in procs.values():
    try: p.wait(timeout=20)
    except Exception: p.kill()
"""


def build_objects(spec: PublicationSpec) -> list[dict[str, Any]]:
    """Every Kubernetes object of one published app, in apply order."""
    ns = spec.namespace
    manifest = spec.manifest
    order = list(manifest.service_order())
    first = next(service for service in manifest.services if service.name == order[0])
    app_readiness = first.readiness
    routes = [route.model_dump() for route in manifest.routes]
    app_port = routes[0]["port"]

    boundary_config = {
        "secret": spec.boundary_secret,
        "project_id": str(spec.project_id),
        "epoch": spec.epoch,
        "core_host": "core",
        "machine_host": "app",
        "routes": routes,
        "public_mode": True,
        "public_origin": spec.public_origin,
    }

    objects: list[dict[str, Any]] = [
        {
            "apiVersion": "v1",
            "kind": "Namespace",
            "metadata": {
                "name": ns,
                "labels": {**_labels(spec, "namespace"), "omnia.retired": "false"},
            },
        },
        {
            "apiVersion": "v1",
            "kind": "Secret",
            "metadata": {
                "name": "boundary-config",
                "namespace": ns,
                "labels": _labels(spec, "boundary"),
            },
            "type": "Opaque",
            "stringData": {"config.json": json.dumps(boundary_config, ensure_ascii=False)},
        },
        {
            "apiVersion": "v1",
            "kind": "ConfigMap",
            "metadata": {
                "name": "boundary-server",
                "namespace": ns,
                "labels": _labels(spec, "boundary"),
            },
            "data": {"server.py": spec.boundary_server_source},
        },
        {
            "apiVersion": "v1",
            "kind": "ConfigMap",
            "metadata": {"name": "app-manifest", "namespace": ns, "labels": _labels(spec, "app")},
            "data": {
                "manifest.json": manifest.canonical_json(),
                "order.json": json.dumps(order),
                "supervisor.py": _supervisor_source(),
            },
        },
        {
            "apiVersion": "v1",
            "kind": "Secret",
            "metadata": {"name": "core-config", "namespace": ns, "labels": _labels(spec, "core")},
            "type": "Opaque",
            "stringData": {
                "omnia-business-config.json": json.dumps(spec.business_config, ensure_ascii=True),
                "AUTH_SECRET": spec.boundary_secret,
                "DATABASE_URL": "postgresql://postgres:"
                + quote(spec.core_postgres_password, safe="")
                + "@core-postgres:5432/postgres",
                "POSTGRES_PASSWORD": spec.core_postgres_password,
            },
        },
        {
            "apiVersion": "v1",
            "kind": "Secret",
            "metadata": {"name": "app-config", "namespace": ns, "labels": _labels(spec, "app")},
            "type": "Opaque",
            "stringData": {
                "DATABASE_URL": "postgresql://postgres:"
                + quote(spec.project_postgres_password, safe="")
                + "@project-postgres:5432/postgres",
                "POSTGRES_PASSWORD": spec.project_postgres_password,
            },
        },
    ]

    # --- seed links: single-use capability URLs, never part of a pod template ---
    seeds = sorted(spec.seed_volumes, key=lambda seed: seed.mount_path)
    links = {_link_key(seed): seed.artifact_url for seed in seeds if seed.artifact_url is not None}
    objects.append(
        {
            "apiVersion": "v1",
            "kind": "Secret",
            "metadata": {
                "name": SEED_LINKS_SECRET,
                "namespace": ns,
                "labels": _labels(spec, "app"),
            },
            "type": "Opaque",
            "stringData": links,
        }
    )

    # --- project postgres (business data, seeded once from the warm artifact) ---
    pg_seed = next((v for v in seeds if v.mount_path == PROJECT_POSTGRES_DATA), None)
    pg_init = (
        [
            _seed_init_container(
                "seed-data",
                "data",
                _link_key(pg_seed),
                chown=f"{_POSTGRES_UID}:{_POSTGRES_UID}",
            )
        ]
        if pg_seed is not None and pg_seed.artifact_url is not None
        else []
    )
    objects.append(
        _postgres_statefulset(
            spec, "project-postgres", spec.project_postgres_storage, "app-config", pg_init
        )
    )
    objects.append(_service(spec, "project-postgres", 5432))
    objects.append(
        _postgres_statefulset(spec, "core-postgres", spec.core_postgres_storage, "core-config", [])
    )
    objects.append(_service(spec, "core-postgres", 5432))

    # --- redis for the managed core ---
    objects.append(
        {
            "apiVersion": "apps/v1",
            "kind": "Deployment",
            "metadata": {"name": "redis", "namespace": ns, "labels": _labels(spec, "redis")},
            "spec": {
                "replicas": 1,
                "selector": {"matchLabels": _selector(spec, "redis")},
                "template": {
                    "metadata": {"labels": {**_labels(spec, "redis")}},
                    "spec": {
                        "containers": [
                            {
                                "name": "redis",
                                "image": spec.redis_image,
                                "args": ["--save", "", "--appendonly", "no", "--maxmemory", "96mb"],
                                "ports": [{"containerPort": 6379}],
                                "resources": {
                                    "requests": {"cpu": "20m", "memory": "32Mi"},
                                    "limits": {"memory": "128Mi"},
                                },
                                "securityContext": {
                                    "allowPrivilegeEscalation": False,
                                    "capabilities": {"drop": ["ALL"]},
                                },
                            }
                        ]
                    },
                },
            },
        }
    )
    objects.append(_service(spec, "redis", 6379))

    # --- managed MAX core ---
    core_env = {
        "NODE_ENV": "production",
        "OMNIA_PROJECT_ID": str(spec.project_id),
        "REDIS_URL": "redis://redis:6379/0",
        "HOSTNAME": "0.0.0.0",
        "PORT": str(CORE_PORT),
        "NODE_OPTIONS": "--max-old-space-size="
        + str(max(64, min(384, spec.core_memory_bytes // (2 * 1024**2)))),
        "OMNIA_PUBLIC_APP_ORIGIN": spec.public_origin,
        **spec.runtime_env,
    }
    objects.append(
        {
            "apiVersion": "apps/v1",
            "kind": "Deployment",
            "metadata": {"name": "core", "namespace": ns, "labels": _labels(spec, "core")},
            "spec": {
                "replicas": 1,
                "strategy": {"type": "Recreate"},
                "selector": {"matchLabels": _selector(spec, "core")},
                "template": {
                    "metadata": {
                        "labels": _labels(spec, "core"),
                        "annotations": {
                            "omnia.config-digest": _digest(
                                spec.business_config, spec.runtime_env, spec.boundary_secret
                            )
                        },
                    },
                    "spec": {
                        "securityContext": {"runAsUser": 1000, "runAsGroup": 1000, "fsGroup": 1000},
                        "containers": [
                            {
                                "name": "core",
                                "image": spec.core_image,
                                "command": list(_PUBLIC_CORE_COMMAND),
                                "workingDir": "/app",
                                "env": [
                                    *_env(core_env),
                                    {
                                        "name": "AUTH_SECRET",
                                        "valueFrom": {
                                            "secretKeyRef": {
                                                "name": "core-config",
                                                "key": "AUTH_SECRET",
                                            }
                                        },
                                    },
                                    {
                                        "name": "DATABASE_URL",
                                        "valueFrom": {
                                            "secretKeyRef": {
                                                "name": "core-config",
                                                "key": "DATABASE_URL",
                                            }
                                        },
                                    },
                                ],
                                "ports": [{"containerPort": CORE_PORT, "name": "http"}],
                                "volumeMounts": [
                                    {
                                        "name": "business-config",
                                        "mountPath": "/app/omnia-business-config.json",
                                        "subPath": "omnia-business-config.json",
                                    },
                                    {"name": "tmp", "mountPath": "/tmp"},
                                ],
                                "readinessProbe": {
                                    "httpGet": {"path": "/api/health", "port": CORE_PORT},
                                    "initialDelaySeconds": 5,
                                    "periodSeconds": 5,
                                    "failureThreshold": 24,
                                },
                                "resources": {
                                    "requests": {"cpu": "100m", "memory": "256Mi"},
                                    "limits": {"memory": _bytes(spec.core_memory_bytes)},
                                },
                                "securityContext": {
                                    "allowPrivilegeEscalation": False,
                                    "capabilities": {"drop": ["ALL"]},
                                },
                            }
                        ],
                        "volumes": [
                            {
                                "name": "business-config",
                                "secret": {
                                    "secretName": "core-config",
                                    "items": [
                                        {
                                            "key": "omnia-business-config.json",
                                            "path": "omnia-business-config.json",
                                        }
                                    ],
                                },
                            },
                            {"name": "tmp", "emptyDir": {}},
                        ],
                    },
                },
            },
        }
    )
    objects.append(_service(spec, "core", CORE_PORT))

    # --- the app machine ---
    app_volumes: list[dict[str, Any]] = [
        {"name": "omnia", "configMap": {"name": "app-manifest"}},
        {"name": "logs", "emptyDir": {}},
        {"name": "tmp", "emptyDir": {}},
    ]
    app_mounts: list[dict[str, Any]] = [
        {"name": "omnia", "mountPath": "/omnia", "readOnly": True},
        {"name": "logs", "mountPath": "/run/omnia-logs"},
        {"name": "tmp", "mountPath": "/tmp"},
    ]
    app_init: list[dict[str, Any]] = []
    for index, seed in enumerate(v for v in seeds if v.mount_path != PROJECT_POSTGRES_DATA):
        name = _volume_name(spec, seed)
        objects.append(_claim(spec, name, seed))
        app_volumes.append({"name": name, "persistentVolumeClaim": {"claimName": name}})
        # Parents before children: mounts are applied in list order.
        app_mounts.append({"name": name, "mountPath": seed.mount_path})
        if seed.artifact_url is not None:
            app_init.append(
                _seed_init_container(f"seed-{index}", name, _link_key(seed), chown=None)
            )
    # Next's build cache is disposable and release-local, like the Docker layout.
    app_volumes.append({"name": "next-cache", "emptyDir": {}})
    app_mounts.append({"name": "next-cache", "mountPath": "/workspace/.next/cache"})
    app_mounts.sort(key=lambda mount: (mount["mountPath"].count("/"), mount["mountPath"]))
    app_env = {
        "HOME": "/root",
        "CI": "1",
        "PYTHONUNBUFFERED": "1",
        "NODE_ENV": "production",
        "NEXT_TELEMETRY_DISABLED": "1",
        "COREPACK_HOME": "/root/.cache/node/corepack",
        "PORT": str(app_port),
        "HOSTNAME": "0.0.0.0",
        "OMNIA_PUBLIC_APP_ORIGIN": spec.public_origin,
        "PGHOST": "project-postgres",
        "PGPORT": "5432",
        "PGUSER": "postgres",
        "PGDATABASE": "postgres",
        **spec.app_env,
    }
    readiness = (
        {
            "httpGet": {"path": app_readiness.path, "port": app_readiness.port},
            "initialDelaySeconds": 5,
            "periodSeconds": 5,
            "failureThreshold": max(6, app_readiness.timeout_seconds // 5),
        }
        if app_readiness
        else {
            "tcpSocket": {"port": app_port},
            "initialDelaySeconds": 5,
            "periodSeconds": 5,
            "failureThreshold": 12,
        }
    )
    objects.append(
        {
            "apiVersion": "apps/v1",
            "kind": "Deployment",
            "metadata": {"name": "app", "namespace": ns, "labels": _labels(spec, "app")},
            "spec": {
                "replicas": 1,
                "strategy": {"type": "Recreate"},
                "selector": {"matchLabels": _selector(spec, "app")},
                "template": {
                    "metadata": {"labels": _labels(spec, "app")},
                    "spec": {
                        "initContainers": app_init,
                        "containers": [
                            {
                                "name": "app",
                                "image": spec.app_image,
                                "command": ["python3", "/omnia/supervisor.py"],
                                "workingDir": "/workspace",
                                "env": [
                                    *_env(app_env),
                                    {
                                        "name": "DATABASE_URL",
                                        "valueFrom": {
                                            "secretKeyRef": {
                                                "name": "app-config",
                                                "key": "DATABASE_URL",
                                            }
                                        },
                                    },
                                    {
                                        "name": "PGPASSWORD",
                                        "valueFrom": {
                                            "secretKeyRef": {
                                                "name": "app-config",
                                                "key": "POSTGRES_PASSWORD",
                                            }
                                        },
                                    },
                                ],
                                "ports": [{"containerPort": app_port, "name": "http"}],
                                "volumeMounts": app_mounts,
                                "readinessProbe": readiness,
                                "resources": {
                                    "requests": {
                                        "cpu": _millicores(spec.app_cpu_cores / 2),
                                        "memory": _bytes(spec.app_memory_bytes // 2),
                                    },
                                    "limits": {
                                        "cpu": _millicores(spec.app_cpu_cores),
                                        "memory": _bytes(spec.app_memory_bytes),
                                    },
                                },
                                "securityContext": {
                                    "allowPrivilegeEscalation": False,
                                    "capabilities": {"drop": ["ALL"]},
                                },
                            }
                        ],
                        "volumes": app_volumes,
                    },
                },
            },
        }
    )
    objects.append(_service(spec, "app", app_port))

    # --- trusted boundary (public entrypoint) ---
    objects.append(
        {
            "apiVersion": "apps/v1",
            "kind": "Deployment",
            "metadata": {"name": "boundary", "namespace": ns, "labels": _labels(spec, "boundary")},
            "spec": {
                "replicas": 1,
                "selector": {"matchLabels": _selector(spec, "boundary")},
                "template": {
                    "metadata": {
                        "labels": _labels(spec, "boundary"),
                        "annotations": {
                            "omnia.config-digest": _digest(
                                boundary_config, spec.boundary_server_source
                            )
                        },
                    },
                    "spec": {
                        "containers": [
                            {
                                "name": "boundary",
                                "image": spec.guard_image,
                                "command": ["python3", "/run/omnia-boundary/server.py"],
                                "ports": [{"containerPort": BOUNDARY_PORT, "name": "http"}],
                                "volumeMounts": [
                                    {
                                        "name": "boundary",
                                        "mountPath": "/run/omnia-boundary",
                                        "readOnly": True,
                                    }
                                ],
                                "readinessProbe": {
                                    "tcpSocket": {"port": BOUNDARY_PORT},
                                    "initialDelaySeconds": 2,
                                    "periodSeconds": 3,
                                },
                                "resources": {
                                    "requests": {"cpu": "50m", "memory": "32Mi"},
                                    "limits": {"memory": "96Mi"},
                                },
                                "securityContext": {
                                    "allowPrivilegeEscalation": False,
                                    "capabilities": {"drop": ["ALL"]},
                                    "readOnlyRootFilesystem": True,
                                },
                            }
                        ],
                        "volumes": [
                            {
                                "name": "boundary",
                                "projected": {
                                    "sources": [
                                        {
                                            "secret": {
                                                "name": "boundary-config",
                                                "items": [
                                                    {"key": "config.json", "path": "config.json"}
                                                ],
                                            }
                                        },
                                        {
                                            "configMap": {
                                                "name": "boundary-server",
                                                "items": [
                                                    {"key": "server.py", "path": "server.py"}
                                                ],
                                            }
                                        },
                                    ]
                                },
                            }
                        ],
                    },
                },
            },
        }
    )
    objects.append(_service(spec, "boundary", BOUNDARY_PORT))
    objects.append(
        {
            "apiVersion": "networking.k8s.io/v1",
            "kind": "Ingress",
            "metadata": {
                "name": "public",
                "namespace": ns,
                "labels": _labels(spec, "boundary"),
                "annotations": {"cert-manager.io/cluster-issuer": spec.cluster_issuer},
            },
            "spec": {
                "ingressClassName": spec.ingress_class,
                "tls": [{"hosts": [spec.public_host], "secretName": "public-tls"}],
                "rules": [
                    {
                        "host": spec.public_host,
                        "http": {
                            "paths": [
                                {
                                    "path": "/",
                                    "pathType": "Prefix",
                                    "backend": {
                                        "service": {
                                            "name": "boundary",
                                            "port": {"number": BOUNDARY_PORT},
                                        }
                                    },
                                }
                            ]
                        },
                    }
                ],
            },
        }
    )
    objects.extend(_network_policies(spec, app_port))
    return objects


def _postgres_statefulset(
    spec: PublicationSpec, name: str, storage: str, secret: str, init: list[dict[str, Any]]
) -> dict[str, Any]:
    return {
        "apiVersion": "apps/v1",
        "kind": "StatefulSet",
        "metadata": {"name": name, "namespace": spec.namespace, "labels": _labels(spec, name)},
        "spec": {
            "serviceName": name,
            "replicas": 1,
            "selector": {"matchLabels": _selector(spec, name)},
            "template": {
                "metadata": {"labels": _labels(spec, name)},
                "spec": {
                    "initContainers": init,
                    "containers": [
                        {
                            "name": "postgres",
                            "image": spec.postgres_image,
                            "command": [
                                "postgres",
                                "-D",
                                PROJECT_POSTGRES_DATA,
                                "-c",
                                "listen_addresses=*",
                            ],
                            "env": [
                                {"name": "PGDATA", "value": PROJECT_POSTGRES_DATA},
                                {
                                    "name": "POSTGRES_PASSWORD",
                                    "valueFrom": {
                                        "secretKeyRef": {"name": secret, "key": "POSTGRES_PASSWORD"}
                                    },
                                },
                            ],
                            "ports": [{"containerPort": 5432}],
                            "volumeMounts": [
                                {"name": "data", "mountPath": PROJECT_POSTGRES_DATA},
                                {"name": "tmp", "mountPath": "/tmp"},
                                {"name": "run", "mountPath": "/var/run/postgresql"},
                            ],
                            "readinessProbe": {
                                "exec": {
                                    "command": ["pg_isready", "-U", "postgres", "-h", "127.0.0.1"]
                                },
                                "periodSeconds": 5,
                            },
                            "resources": {
                                "requests": {"cpu": "50m", "memory": "128Mi"},
                                "limits": {"memory": "512Mi"},
                            },
                            "securityContext": {
                                "runAsUser": _POSTGRES_UID,
                                "runAsGroup": _POSTGRES_UID,
                                "allowPrivilegeEscalation": False,
                                "capabilities": {"drop": ["ALL"]},
                            },
                        }
                    ],
                    "volumes": [{"name": "tmp", "emptyDir": {}}, {"name": "run", "emptyDir": {}}],
                },
            },
            "volumeClaimTemplates": [
                {
                    "metadata": {"name": "data", "labels": _labels(spec, name)},
                    "spec": {
                        "accessModes": ["ReadWriteOnce"],
                        "resources": {"requests": {"storage": storage}},
                    },
                }
            ],
        },
    }


def _network_policies(spec: PublicationSpec, app_port: int) -> list[dict[str, Any]]:
    ns = spec.namespace

    def policy(name: str, component: str, body: dict[str, Any]) -> dict[str, Any]:
        return {
            "apiVersion": "networking.k8s.io/v1",
            "kind": "NetworkPolicy",
            "metadata": {"name": name, "namespace": ns, "labels": _labels(spec, component)},
            "spec": {"podSelector": {"matchLabels": _selector(spec, component)}, **body},
        }

    dns = {
        "to": [{"namespaceSelector": {}, "podSelector": {"matchLabels": {"k8s-app": "kube-dns"}}}],
        "ports": [{"protocol": "UDP", "port": 53}, {"protocol": "TCP", "port": 53}],
    }

    def peer(component: str, port: int) -> dict[str, Any]:
        return {
            "to": [{"podSelector": {"matchLabels": _selector(spec, component)}}],
            "ports": [{"port": port}],
        }

    def from_peer(component: str) -> dict[str, Any]:
        return {"podSelector": {"matchLabels": _selector(spec, component)}}

    artifacts = {
        "to": [{"ipBlock": {"cidr": spec.artifact_source_cidr}}],
        "ports": [{"port": spec.artifact_port}],
    }

    return [
        {
            "apiVersion": "networking.k8s.io/v1",
            "kind": "NetworkPolicy",
            "metadata": {
                "name": "default-deny",
                "namespace": ns,
                "labels": _labels(spec, "namespace"),
            },
            "spec": {"podSelector": {}, "policyTypes": ["Ingress", "Egress"]},
        },
        policy(
            "boundary",
            "boundary",
            {
                "policyTypes": ["Ingress", "Egress"],
                "ingress": [
                    {
                        "from": [
                            {
                                "namespaceSelector": {
                                    "matchLabels": {"kubernetes.io/metadata.name": "kube-system"}
                                }
                            }
                        ],
                        "ports": [{"port": BOUNDARY_PORT}],
                    }
                ],
                "egress": [peer("app", app_port), peer("core", CORE_PORT), dns],
            },
        ),
        policy(
            "app",
            "app",
            {
                "policyTypes": ["Ingress", "Egress"],
                "ingress": [{"from": [from_peer("boundary")], "ports": [{"port": app_port}]}],
                "egress": [peer("project-postgres", 5432), dns, artifacts],
            },
        ),
        policy(
            "core",
            "core",
            {
                "policyTypes": ["Ingress", "Egress"],
                "ingress": [{"from": [from_peer("boundary")], "ports": [{"port": CORE_PORT}]}],
                "egress": [
                    peer("core-postgres", 5432),
                    peer("redis", 6379),
                    dns,
                    {
                        "to": [
                            {
                                "ipBlock": {
                                    "cidr": "0.0.0.0/0",
                                    "except": ["10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16"],
                                }
                            }
                        ],
                        "ports": [{"port": 443}],
                    },
                ],
            },
        ),
        policy(
            "project-postgres",
            "project-postgres",
            {
                "policyTypes": ["Ingress", "Egress"],
                "ingress": [{"from": [from_peer("app")], "ports": [{"port": 5432}]}],
                "egress": [dns, artifacts],
            },
        ),
        policy(
            "core-postgres",
            "core-postgres",
            {
                "policyTypes": ["Ingress", "Egress"],
                "ingress": [{"from": [from_peer("core")], "ports": [{"port": 5432}]}],
                "egress": [dns],
            },
        ),
        policy(
            "redis",
            "redis",
            {
                "policyTypes": ["Ingress", "Egress"],
                "ingress": [{"from": [from_peer("core")], "ports": [{"port": 6379}]}],
                "egress": [dns],
            },
        ),
    ]


def _digest(*parts: Any) -> str:
    return hashlib.sha256(
        json.dumps(parts, sort_keys=True, ensure_ascii=True).encode()
    ).hexdigest()[:16]


# ---------------------------------------------------------------- cluster API


class KubernetesApi(Protocol):
    """The handful of cluster operations the placement needs (fakeable in tests)."""

    def apply(self, obj: dict[str, Any]) -> None: ...
    def delete(self, api_version: str, kind: str, name: str, namespace: str | None) -> None: ...
    def wait_ready(self, kind: str, name: str, namespace: str, timeout_seconds: float) -> None: ...
    def exec(
        self,
        namespace: str,
        pod: str,
        command: list[str],
        *,
        container: str | None = None,
        timeout_seconds: float = 60,
    ) -> tuple[int, str]: ...
    def pod_name(self, namespace: str, selector: dict[str, str]) -> str | None: ...
    def proxy_get(
        self, namespace: str, service: str, port: int, path: str, *, timeout_seconds: float = 20
    ) -> tuple[int, bytes]: ...
    def get(
        self, api_version: str, kind: str, name: str, namespace: str | None
    ) -> dict[str, Any] | None: ...
    def list_objects(
        self, api_version: str, kind: str, namespace: str | None, label_selector: str
    ) -> list[dict[str, Any]]: ...


class KubernetesClusterApi:
    """`KubernetesApi` over the official client: server-side apply + dynamic resources."""

    def __init__(self, kubeconfig_path: str, *, context: str | None = None) -> None:
        from kubernetes import client, config, dynamic

        self._api_client = config.new_client_from_config(
            config_file=kubeconfig_path, context=context
        )
        self._dynamic = dynamic.DynamicClient(self._api_client)
        self._core = client.CoreV1Api(self._api_client)

    def _resource(self, api_version: str, kind: str) -> Any:
        return self._dynamic.resources.get(api_version=api_version, kind=kind)

    def apply(self, obj: dict[str, Any]) -> None:
        resource = self._resource(obj["apiVersion"], obj["kind"])
        metadata = obj["metadata"]
        resource.server_side_apply(
            body=obj,
            name=metadata["name"],
            namespace=metadata.get("namespace"),
            field_manager=FIELD_MANAGER,
            force_conflicts=True,
        )

    def delete(self, api_version: str, kind: str, name: str, namespace: str | None) -> None:
        from kubernetes.dynamic.exceptions import NotFoundError

        try:
            self._resource(api_version, kind).delete(name=name, namespace=namespace)
        except NotFoundError:
            return

    def get(
        self, api_version: str, kind: str, name: str, namespace: str | None
    ) -> dict[str, Any] | None:
        from kubernetes.dynamic.exceptions import NotFoundError

        try:
            found = self._resource(api_version, kind).get(name=name, namespace=namespace)
        except NotFoundError:
            return None
        return dict(found.to_dict())

    def list_objects(
        self, api_version: str, kind: str, namespace: str | None, label_selector: str
    ) -> list[dict[str, Any]]:
        found = self._resource(api_version, kind).get(
            namespace=namespace, label_selector=label_selector
        )
        return [dict(item.to_dict()) for item in (found.items or [])]

    def wait_ready(self, kind: str, name: str, namespace: str, timeout_seconds: float) -> None:
        deadline = time.monotonic() + timeout_seconds
        while True:
            current = self.get("apps/v1", kind, name, namespace) or {}
            status = current.get("status") or {}
            spec = current.get("spec") or {}
            wanted = int(spec.get("replicas") or 1)
            observed = int(status.get("observedGeneration") or 0)
            generation = int((current.get("metadata") or {}).get("generation") or 0)
            ready = int(status.get("readyReplicas") or 0)
            updated = int(status.get("updatedReplicas") or 0)
            if observed >= generation and ready >= wanted and updated >= wanted:
                return
            if time.monotonic() >= deadline:
                raise PublicationPlacementError(
                    f"{kind}/{name} did not become ready in {int(timeout_seconds)}s"
                )
            time.sleep(2)

    def pod_name(self, namespace: str, selector: dict[str, str]) -> str | None:
        label = ",".join(f"{k}={v}" for k, v in sorted(selector.items()))
        pods = self._core.list_namespaced_pod(namespace, label_selector=label).items
        running = [p for p in pods if p.status and p.status.phase == "Running"]
        return running[0].metadata.name if running else (pods[0].metadata.name if pods else None)

    def exec(
        self,
        namespace: str,
        pod: str,
        command: list[str],
        *,
        container: str | None = None,
        timeout_seconds: float = 60,
    ) -> tuple[int, str]:
        from kubernetes.stream import stream

        client = stream(
            self._core.connect_get_namespaced_pod_exec,
            pod,
            namespace,
            command=command,
            container=container,
            stderr=True,
            stdin=False,
            stdout=True,
            tty=False,
            _preload_content=False,
        )
        output: list[str] = []
        client.run_forever(timeout=timeout_seconds)
        output.append(client.read_stdout() or "")
        rc = client.returncode
        client.close()
        return (rc if rc is not None else 1), "".join(output)

    def proxy_get(
        self, namespace: str, service: str, port: int, path: str, *, timeout_seconds: float = 20
    ) -> tuple[int, bytes]:
        path = path.lstrip("/")
        url = f"/api/v1/namespaces/{namespace}/services/{service}:{port}/proxy/{path}"
        response = self._api_client.call_api(
            url,
            "GET",
            response_type="str",
            _preload_content=False,
            _request_timeout=timeout_seconds,
            auth_settings=["BearerToken"],
        )
        raw = response[0] if isinstance(response, tuple) else response
        return int(getattr(raw, "status", 200)), bytes(getattr(raw, "data", b"") or b"")


# ------------------------------------------------------------- capabilities


@dataclass
class _Capability:
    path: Path
    expires_at: float
    uses_left: int


class ArtifactCapabilityStore:
    """Single-use, expiring links that let seeding init containers fetch a warm
    artifact from the orchestrator without any cluster-wide credential."""

    def __init__(self, ttl_seconds: float = 900.0, *, uses: int = 3) -> None:
        self._ttl = ttl_seconds
        self._uses = uses
        self._items: dict[str, _Capability] = {}

    def issue(self, path: Path) -> str:
        if not path.is_file() or path.is_symlink():
            raise PublicationPlacementError("publication artifact missing", status_code=500)
        token = secrets.token_urlsafe(32)
        self._items[token] = _Capability(path, time.monotonic() + self._ttl, self._uses)
        return token

    def resolve(self, token: str) -> Path | None:
        self._sweep()
        item = self._items.get(token)
        if item is None:
            return None
        item.uses_left -= 1
        if item.uses_left <= 0:
            self._items.pop(token, None)
        return item.path

    def _sweep(self) -> None:
        now = time.monotonic()
        for token in [t for t, item in self._items.items() if item.expires_at <= now]:
            self._items.pop(token, None)


_capabilities: ArtifactCapabilityStore | None = None


def artifact_capabilities() -> ArtifactCapabilityStore:
    global _capabilities
    if _capabilities is None:
        _capabilities = ArtifactCapabilityStore()
    return _capabilities


# ---------------------------------------------------------------- registry


def ensure_release_image(
    docker_client: Any, image_id: str, archive: Path, *, provenance: dict[str, str]
) -> None:
    """The captured environment image must be local before it can be pushed.

    Mirrors `PublishedMachineBackend.adopt_source_image`: load from the sealed
    archive when the daemon no longer has it, and refuse an image whose identity,
    provenance labels or baked-in runtime configuration differ from the capture.
    """
    from docker.errors import ImageNotFound  # type: ignore[import-untyped]

    try:
        image = docker_client.images.get(image_id)
    except ImageNotFound:
        with archive.open("rb") as handle:
            images = docker_client.images.load(handle)
        if len(images) != 1 or images[0].id != image_id:
            raise PublicationPlacementError(
                "publication image digest mismatch", status_code=409
            ) from None
        image = images[0]
    if image.id != image_id:
        raise PublicationPlacementError("publication image digest mismatch", status_code=409)
    config = image.attrs.get("Config") or {}
    if config.get("Env") or config.get("Entrypoint") or config.get("Cmd"):
        raise PublicationPlacementError(
            "publication image contains runtime configuration", status_code=409
        )
    labels = config.get("Labels") or {}
    if any(labels.get(key) != value for key, value in provenance.items()):
        raise PublicationPlacementError("publication image provenance mismatch", status_code=409)


def push_image(
    docker_client: Any, image_id: str, repository: str, tag: str, *, auth: dict[str, str] | None
) -> str:
    """Tag a local image and push it; returns the pullable `repository:tag`."""
    image = docker_client.images.get(image_id)
    image.tag(repository, tag)
    for line in docker_client.images.push(
        repository, tag=tag, stream=True, decode=True, auth_config=auth
    ):
        if isinstance(line, dict) and line.get("error"):
            raise PublicationPlacementError(f"image push failed: {line['error']}", status_code=500)
    return f"{repository}:{tag}"


# ------------------------------------------------------------------ runtime


@dataclass
class PlacementResult:
    namespace: str
    public_host: str
    epoch: int


class KubernetesPublishedRuntime:
    """Applies a `PublicationSpec` to the cluster and observes it.

    Timeouts are deliberately generous: a first publication pulls three images and
    seeds Postgres from an archive; later ones only roll the app/boundary pods.
    """

    def __init__(self, api: KubernetesApi, *, ready_timeout_seconds: float = 600.0) -> None:
        self.api = api
        self.ready_timeout = ready_timeout_seconds

    def publish(self, spec: PublicationSpec) -> PlacementResult:
        for obj in build_objects(spec):
            self.api.apply(obj)
        ns = spec.namespace
        for kind, name in (
            ("StatefulSet", "core-postgres"),
            ("StatefulSet", "project-postgres"),
            ("Deployment", "redis"),
        ):
            self.api.wait_ready(kind, name, ns, self.ready_timeout)
        for kind, name in (
            ("Deployment", "core"),
            ("Deployment", "app"),
            ("Deployment", "boundary"),
        ):
            self.api.wait_ready(kind, name, ns, self.ready_timeout)
        status, _body = self.api.proxy_get(ns, "boundary", BOUNDARY_PORT, "/api/omnia/health")
        if status != 200:
            raise PublicationPlacementError(f"public bootstrap readiness failed (HTTP {status})")
        log.info("k8s_publication.published", namespace=ns, host=spec.public_host, epoch=spec.epoch)
        return PlacementResult(ns, spec.public_host, spec.epoch)

    def schema_digest(self, namespace: str, password: str) -> str:
        pod = self.api.pod_name(namespace, {"app.kubernetes.io/component": "project-postgres"})
        if pod is None:
            raise PublicationPlacementError("production schema probe requires postgres")
        rc, output = self.api.exec(
            namespace,
            pod,
            [
                "sh",
                "-c",
                f"PGPASSWORD='{password}' pg_dumpall --schema-only --no-role-passwords "
                "--no-owner --no-privileges -h 127.0.0.1 -U postgres",
            ],
            container="postgres",
        )
        if rc != 0 or not output:
            raise PublicationPlacementError("publication schema inspection failed")
        lines = [
            line
            for line in output.splitlines()
            if not line.startswith(("\\restrict ", "\\unrestrict ", "--"))
        ]
        return hashlib.sha256("\n".join(lines).encode()).hexdigest()

    def retire(self, project_id: UUID) -> None:
        """Take the app off the internet; keep its data (PVCs) for recovery."""
        ns = f"app-{project_id}"
        for api_version, kind, name in (
            ("networking.k8s.io/v1", "Ingress", "public"),
            ("apps/v1", "Deployment", "boundary"),
            ("apps/v1", "Deployment", "app"),
            ("apps/v1", "Deployment", "core"),
            ("apps/v1", "Deployment", "redis"),
            ("apps/v1", "StatefulSet", "core-postgres"),
            ("apps/v1", "StatefulSet", "project-postgres"),
            ("v1", "Secret", "boundary-config"),
            ("v1", "Secret", "core-config"),
            ("v1", "ConfigMap", "boundary-server"),
            ("v1", "ConfigMap", "app-manifest"),
        ):
            self.api.delete(api_version, kind, name, ns)
        namespace = self.api.get("v1", "Namespace", ns, None)
        if namespace is not None:
            self.api.apply(
                {
                    "apiVersion": "v1",
                    "kind": "Namespace",
                    "metadata": {"name": ns, "labels": {"omnia.retired": "true"}},
                }
            )
        log.info("k8s_publication.retired", namespace=ns)

    def prune_release_volumes(self, project_id: UUID, keep_release_id: str) -> list[str]:
        """Drop code claims of retired releases; data claims are never touched."""
        ns = f"app-{project_id}"
        removed: list[str] = []
        for claim in self.api.list_objects(
            "v1", "PersistentVolumeClaim", ns, "omnia.volume-kind=code"
        ):
            metadata = claim.get("metadata") or {}
            labels = metadata.get("labels") or {}
            if labels.get("omnia.release-id") == keep_release_id:
                continue
            self.api.delete("v1", "PersistentVolumeClaim", metadata["name"], ns)
            removed.append(metadata["name"])
        return removed

    def destroy(self, project_id: UUID) -> None:
        """Retire and drop every claim, data included. Only for an app that never
        went live (a failed or interrupted first publication): its seeded data is
        a copy of the source cell and must not shadow the next attempt's seed.
        The namespace itself stays (retired): the orchestrator's cluster role
        deliberately cannot delete namespaces."""
        ns = f"app-{project_id}"
        self.retire(project_id)
        for claim in self.api.list_objects(
            "v1", "PersistentVolumeClaim", ns, "app.kubernetes.io/managed-by=omnia-orchestrator"
        ):
            self.api.delete("v1", "PersistentVolumeClaim", claim["metadata"]["name"], ns)
        log.info("k8s_publication.destroyed", namespace=ns)

    def status(self, project_id: UUID) -> dict[str, Any]:
        ns = f"app-{project_id}"
        result: dict[str, Any] = {
            "namespace": ns,
            "present": False,
            "ready": {},
            "app_image": None,
            "release_id": None,
        }
        if self.api.get("v1", "Namespace", ns, None) is None:
            return result
        result["present"] = True
        for kind, name in (
            ("Deployment", "boundary"),
            ("Deployment", "app"),
            ("Deployment", "core"),
            ("StatefulSet", "project-postgres"),
        ):
            current = self.api.get("apps/v1", kind, name, ns) or {}
            status = current.get("status") or {}
            result["ready"][name] = int(status.get("readyReplicas") or 0) >= 1
            if name == "app":
                template = ((current.get("spec") or {}).get("template") or {}).get("spec") or {}
                containers = template.get("containers") or []
                result["app_image"] = containers[0].get("image") if containers else None
                labels = (current.get("metadata") or {}).get("labels") or {}
                result["release_id"] = labels.get("omnia.release-id")
        return result


__all__ = [
    "SEED_LINKS_SECRET",
    "SEED_MARKER",
    "ArtifactCapabilityStore",
    "KubernetesApi",
    "KubernetesClusterApi",
    "KubernetesPublishedRuntime",
    "PlacementResult",
    "PublicationPlacementError",
    "PublicationSpec",
    "SeedVolume",
    "artifact_capabilities",
    "build_objects",
    "ensure_release_image",
    "push_image",
]

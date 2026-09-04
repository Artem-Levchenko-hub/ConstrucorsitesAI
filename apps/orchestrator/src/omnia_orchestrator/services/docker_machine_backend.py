"""Docker implementation of the project-owned persistent development machine.

All Docker identities/options come from the controller. Only argv, guest cwd,
service ports and named project volume requests come from the manifest.
"""

from __future__ import annotations

import hashlib
import io
import ipaddress
import json
import os
import re
import socket
import tarfile
import tempfile
import time
from collections.abc import Iterable
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any
from urllib.parse import quote
from uuid import UUID, uuid4

import docker  # type: ignore[import-untyped]

from omnia_orchestrator.core.cell_resources import CellIdentityConflict, CellResourceError
from omnia_orchestrator.core.project_machine import MachineManifest, MachineService
from omnia_orchestrator.services.machine_egress import GuardPolicy
from omnia_orchestrator.services.machine_environment import MachineEnvironmentRef
from omnia_orchestrator.services.machine_network_allocation import (
    choose_subnet,
    create_pool_network,
)
from omnia_orchestrator.services.project_machine import (
    machine_remaining_seconds,
    write_controller_json,
)

_PIN = re.compile(r"^(?:sha256:|[^\s@]+@sha256:)[0-9a-f]{64}$")
_PROJECT_POSTGRES_HOST = "127.0.0.1"
_PROJECT_POSTGRES_PORT = 5432
_PROJECT_POSTGRES_DB = "postgres"
_PROJECT_POSTGRES_USER = "postgres"
_PROJECT_POSTGRES_DATA = "/var/lib/postgresql/data"
_USERLAND_CAPS = [
    "CHOWN",
    "DAC_OVERRIDE",
    "FOWNER",
    "FSETID",
    "SETUID",
    "SETGID",
    "SETFCAP",
    "KILL",
    "NET_BIND_SERVICE",
    "SYS_CHROOT",
]
_LOG_LIMIT = 24000
_ENVIRONMENT_INVENTORY_LIMIT = 1024 * 1024
_ENVIRONMENT_REVISION = "project-machine-environment-v2"

_ENVIRONMENT_INVENTORY_SCRIPT = r"""
import json,subprocess
commands=[
 ["dpkg-query","-W","-f=${Package}=${Version}\\n"],
 ["python3","-m","pip","freeze","--all"],
 ["node","--version"],
 ["corepack","--version"],
 ["pnpm","--version"],
]
rows=[]
for command in commands:
 try:
  result=subprocess.run(command,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,
                        text=True,timeout=30,check=False)
  output="\\n".join(sorted(line.strip() for line in result.stdout.splitlines() if line.strip()))
  rows.append({"command":command,"exit_code":result.returncode,"output":output})
 except Exception as exc:
  rows.append({"command":command,"error":type(exc).__name__})
print(json.dumps(rows,sort_keys=True,separators=(",",":")))
"""

# A Docker exec survives the HTTP request. Output is drained continuously but
# only a bounded tail is retained; long-running descendants remain in the same
# container cgroup and cannot survive remove/lease transfer.
_EXEC_WRAPPER = r"""
import json,os,subprocess,sys
argv,cwd,log,pidfile=json.loads(sys.argv[1])
open(log,'wb').close()
p=subprocess.Popen(argv,cwd=cwd,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,
                   start_new_session=True)
with open(pidfile,'w') as f: f.write(str(p.pid))
tail=b''
while True:
    chunk=p.stdout.read1(8192)
    if not chunk: break
    tail=(tail+chunk)[-24000:]
    with open(log,'wb') as f: f.write(tail)
sys.exit(p.wait())
"""

_SERVICE_WRAPPER = r"""
import json,os,subprocess,sys,time
argv,cwd,log,restart=json.loads(sys.argv[1])
open(log,'wb').close()
attempt=0
while True:
    p=subprocess.Popen(argv,cwd=cwd,stdout=subprocess.PIPE,stderr=subprocess.STDOUT)
    tail=b''
    while True:
        chunk=p.stdout.read1(8192)
        if not chunk: break
        tail=(tail+chunk)[-24000:]
        with open(log,'wb') as f: f.write(tail)
    code=p.wait()
    if restart=='never' or (restart=='on-failure' and code==0) or attempt>=5: sys.exit(code)
    attempt+=1
    time.sleep(min(2**attempt,30))
"""


def _archive_file(name: str, content: bytes) -> bytes:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w") as archive:
        entry = tarfile.TarInfo(name)
        entry.size = len(content)
        entry.mode = 0o600
        archive.addfile(entry, io.BytesIO(content))
    return buffer.getvalue()


@dataclass
class DockerMachineBackend:
    client: Any
    workspace_id: UUID
    project_id: UUID
    owner_id: UUID
    root: Path
    internal_network: str
    workspace_volume: str
    base_image: str
    guard_image: str
    postgres_image: str
    project_postgres_password: str
    project_postgres_memory_bytes: int
    project_postgres_cpu_cores: float
    network_pool: str
    denied_cidrs: tuple[str, ...]
    cpu_cores: float
    memory_bytes: int
    disk_bytes: int
    pids: int
    resource_profile_version: str = "docker-owner-cell-resources-v1"
    namespace: str = "prod"

    def __post_init__(self) -> None:
        if (
            not _PIN.fullmatch(self.base_image)
            or not _PIN.fullmatch(self.guard_image)
            or not _PIN.fullmatch(self.postgres_image)
        ):
            raise ValueError("machine, guard and postgres images must be digest pinned")
        if not self.denied_cidrs:
            raise ValueError("machine public host/platform destination denies are required")
        if not self.project_postgres_password:
            raise ValueError("project postgres password is required")
        if self.project_postgres_memory_bytes <= 0 or self.project_postgres_cpu_cores <= 0:
            raise ValueError("project postgres resource limits must be positive")
        for cidr in self.denied_cidrs:
            ipaddress.ip_network(cidr)
        choose_subnet(self.network_pool, [], str(self.workspace_id))

    @property
    def stem(self) -> str:
        prefix = "omnia-machine-test" if self.namespace == "test" else "omnia-machine"
        return f"{prefix}-{self.workspace_id.hex}"

    @property
    def machine_name(self) -> str:
        return self.stem + "-dev"

    @property
    def project_postgres_name(self) -> str:
        return self.stem + "-project-postgres"

    @property
    def project_postgres_volume(self) -> str:
        return self.stem + "-app-postgres-data"

    @property
    def pnpm_cache_volume(self) -> str:
        return self.stem + "-data-omnia-pnpm-store"

    @property
    def corepack_cache_volume(self) -> str:
        return self.stem + "-data-omnia-corepack"

    @property
    def next_cache_volume(self) -> str:
        cache_key = self._metadata().get("next_cache_key")
        if not isinstance(cache_key, str) or re.fullmatch(r"[0-9a-f]{24}", cache_key) is None:
            cache_key = hashlib.sha256(
                f"{self.base_image}:{self.resource_profile_version}".encode()
            ).hexdigest()[:24]
        return self.stem + "-data-omnia-next-" + cache_key

    def configure_cache_identity(
        self,
        *,
        dependency_digest: str,
        build_config_digest: str,
        manifest_digest: str,
    ) -> None:
        values = (dependency_digest, build_config_digest, manifest_digest)
        if any(re.fullmatch(r"[0-9a-f]{64}", value) is None for value in values):
            raise ValueError("cache identity requires lowercase sha256 digests")
        cache_key = hashlib.sha256(
            json.dumps(
                {
                    "base_image": self.base_image,
                    "build_config": build_config_digest,
                    "dependencies": dependency_digest,
                    "manifest": manifest_digest,
                    "profile": self.resource_profile_version,
                },
                sort_keys=True,
                separators=(",", ":"),
            ).encode()
        ).hexdigest()[:24]
        metadata = self._metadata()
        metadata["next_cache_key"] = cache_key
        write_controller_json(self.metadata_path, metadata)

    @property
    def metadata_path(self) -> Path:
        return self.root / str(self.workspace_id) / "docker.json"

    def _metadata(self) -> dict[str, Any]:
        from omnia_orchestrator.services.cell_state import _read_plain_json_file

        if not self.metadata_path.exists():
            return {"services": {}, "exec_logs": {}, "exec_pids": {}}
        payload = _read_plain_json_file(self.metadata_path)
        payload.setdefault("services", {})
        payload.setdefault("exec_logs", {})
        payload.setdefault("exec_pids", {})
        return payload

    def labels(self, kind: str) -> dict[str, str]:
        return {
            "omnia.managed": "true",
            "omnia.project_machine": "true",
            "omnia.workspace_id": str(self.workspace_id),
            "omnia.project_id": str(self.project_id),
            "omnia.owner_id": str(self.owner_id),
            "omnia.resource_kind": kind,
            "omnia.namespace": self.namespace,
        }

    def _lookup(self, collection: Any, name: str, kind: str) -> Any | None:
        try:
            resource = collection.get(name)
        except docker.errors.NotFound:
            return None
        labels = (
            resource.attrs.get("Config", {}).get("Labels") or resource.attrs.get("Labels") or {}
        )
        if any(labels.get(key) != value for key, value in self.labels(kind).items()):
            raise CellIdentityConflict(f"machine {kind} identity mismatch")
        return resource

    def _container(self) -> Any | None:
        return self._lookup(self.client.containers, self.machine_name, "development")

    def _project_postgres(self) -> Any | None:
        return self._lookup(self.client.containers, self.project_postgres_name, "project-postgres")

    def _network(self, name: str, *, internal: bool) -> Any:
        found = self._lookup(self.client.networks, name, "public-egress")
        if found is not None:
            if found.attrs.get("Internal") is not internal:
                raise CellIdentityConflict("machine network policy mismatch")
            return found
        return create_pool_network(
            self.client,
            self.network_pool,
            name,
            driver="bridge",
            internal=internal,
            enable_ipv6=False,
            labels=self.labels("public-egress"),
        )

    def _volume(self, name: str) -> Any:
        found = self._lookup(self.client.volumes, name, "project-volume")
        if found is not None:
            return found
        return self.client.volumes.create(name=name, labels=self.labels("project-volume"))

    def volume_mapping(self, manifest: MachineManifest) -> dict[str, dict[str, str]]:
        volumes = {
            self.workspace_volume: {"bind": "/workspace", "mode": "rw"},
            self.stem + "-home": {"bind": "/root", "mode": "rw"},
            self.pnpm_cache_volume: {"bind": "/pnpm/store", "mode": "rw"},
            self.corepack_cache_volume: {
                "bind": "/root/.cache/node/corepack",
                "mode": "rw",
            },
            self.next_cache_volume: {"bind": "/workspace/.next/cache", "mode": "rw"},
        }
        for service in manifest.services:
            for mount in service.mounts:
                name = self.stem + "-data-" + mount.volume
                if name in volumes and volumes[name]["bind"] != mount.target:
                    raise ValueError("one volume cannot use multiple guest targets in one machine")
                volumes[name] = {"bind": mount.target, "mode": "rw"}
        return volumes

    def environment_volume_names(self, manifest: MachineManifest) -> tuple[str, ...]:
        return (*self.volume_mapping(manifest), self.project_postgres_volume)

    def snapshot_volume_names(self, manifest: MachineManifest) -> tuple[str, ...]:
        # A machine created before dedicated PostgreSQL has no database volume.
        # Keep its last snapshot restorable; every post-upgrade ensure creates
        # the volume, so subsequent captures include it.
        if (
            self._lookup(self.client.volumes, self.project_postgres_volume, "project-volume")
            is None
        ):
            return tuple(self.volume_mapping(manifest))
        return self.environment_volume_names(manifest)

    def project_database_env(self) -> dict[str, str]:
        database_url = (
            "postgresql://"
            + _PROJECT_POSTGRES_USER
            + ":"
            + quote(self.project_postgres_password, safe="")
            + "@"
            + _PROJECT_POSTGRES_HOST
            + ":"
            + str(_PROJECT_POSTGRES_PORT)
            + "/"
            + _PROJECT_POSTGRES_DB
        )
        return {
            "DATABASE_URL": database_url,
            "PGHOST": _PROJECT_POSTGRES_HOST,
            "PGPORT": str(_PROJECT_POSTGRES_PORT),
            "PGUSER": _PROJECT_POSTGRES_USER,
            "PGPASSWORD": self.project_postgres_password,
            "PGDATABASE": _PROJECT_POSTGRES_DB,
        }

    def environment_digest(self) -> str:
        """Hash controller-selected runtime and package inventory without environment values."""

        machine = self._container()
        if machine is None:
            cached = self._metadata().get("environment_digest")
            if isinstance(cached, str) and re.fullmatch(r"[0-9a-f]{64}", cached):
                return cached
            return hashlib.sha256(
                json.dumps(
                    {
                        "base_image": self.base_image,
                        "controller_revision": _ENVIRONMENT_REVISION,
                        "inventory": None,
                    },
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode("utf-8")
            ).hexdigest()
        result = machine.exec_run(
            ["python3", "-I", "-S", "-c", _ENVIRONMENT_INVENTORY_SCRIPT],
            user="0:0",
            workdir="/workspace",
        )
        if isinstance(result, tuple):
            exit_code, output = int(result[0]), result[1]
        else:
            exit_code = int(getattr(result, "exit_code", 1))
            output = getattr(result, "output", b"")
        raw = output.encode("utf-8") if isinstance(output, str) else bytes(output)
        if exit_code != 0 or len(raw) > _ENVIRONMENT_INVENTORY_LIMIT:
            raise CellResourceError("machine environment inventory is unavailable or unbounded")
        try:
            inventory = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise CellResourceError("machine environment inventory is invalid") from exc
        canonical = json.dumps(
            {
                "base_image": self.base_image,
                "controller_revision": _ENVIRONMENT_REVISION,
                "inventory": inventory,
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        digest = hashlib.sha256(canonical).hexdigest()
        metadata = self._metadata()
        metadata["environment_digest"] = digest
        write_controller_json(self.metadata_path, metadata)
        return digest

    def container_options(
        self, manifest: MachineManifest, namespace_id: str, epoch: int
    ) -> dict[str, Any]:
        requested = manifest.resource_request()
        if (
            requested.cpu_cores > self.cpu_cores
            or requested.memory_bytes > self.memory_bytes
            or requested.disk_bytes > self.disk_bytes
            or requested.pids > self.pids
        ):
            raise ValueError("machine service resource request exceeds admitted bundle budget")
        metadata = self._metadata()
        proxy_ip = metadata.get("proxy_ip", "127.0.0.1")
        return {
            "name": self.machine_name,
            "detach": True,
            "user": "0:0",
            "labels": {**self.labels("development"), "omnia.fencing_epoch": str(epoch)},
            "entrypoint": ["python3", "-c"],
            "command": ["import signal; signal.pause()"],
            "working_dir": "/workspace",
            "network_mode": "container:" + namespace_id,
            "privileged": False,
            "cap_drop": ["ALL"],
            "cap_add": list(_USERLAND_CAPS),
            "security_opt": ["no-new-privileges:true"],
            "ports": {},
            "read_only": False,
            "mem_limit": self.memory_bytes,
            "memswap_limit": self.memory_bytes,
            "nano_cpus": int(self.cpu_cores * 1_000_000_000),
            "pids_limit": self.pids,
            "volumes": {
                **self.volume_mapping(manifest),
                self.stem + "-logs": {"bind": "/run/omnia-logs", "mode": "rw"},
            },
            "tmpfs": {"/run": "rw,nosuid,nodev,size=32m", "/tmp": "rw,nosuid,nodev,size=128m"},
            "environment": {
                "HOME": "/root",
                "CI": "1",
                "PYTHONUNBUFFERED": "1",
                # pnpm9 subtracts PNPM_WORKERS from host parallelism. Reduce its
                # tarball worker pool to one even on a many-core Docker host.
                "PNPM_WORKERS": str(os.cpu_count() or 1024),
                "npm_config_child_concurrency": "1",
                # Keep cold installs fast enough for the two-core cell, while
                # avoiding bursty proxy traffic. Transient registry resets get
                # bounded exponential retries instead of failing generation.
                "npm_config_network_concurrency": "2",
                "npm_config_fetch_retries": "5",
                "npm_config_fetch_retry_factor": "2",
                "npm_config_fetch_retry_mintimeout": "2000",
                "npm_config_fetch_retry_maxtimeout": "20000",
                "npm_config_fetch_timeout": "120000",
                "npm_config_store_dir": "/pnpm/store",
                "COREPACK_HOME": "/root/.cache/node/corepack",
                "NODE_OPTIONS": "--max-old-space-size="
                + str(
                    1280
                    if self.resource_profile_version == "docker-owner-cell-resources-v2"
                    else min(512, max(128, self.memory_bytes // 1024**2 * 3 // 5))
                ),
                "NEXT_TELEMETRY_DISABLED": "1",
                "HTTP_PROXY": f"http://{proxy_ip}:3128",
                "HTTPS_PROXY": f"http://{proxy_ip}:3128",
                "http_proxy": f"http://{proxy_ip}:3128",
                "https_proxy": f"http://{proxy_ip}:3128",
                "NO_PROXY": "127.0.0.1,localhost",
                "no_proxy": "127.0.0.1,localhost",
                **self.project_database_env(),
            },
            "log_config": docker.types.LogConfig(
                type="json-file", config={"max-size": "1m", "max-file": "2"}
            ),
        }

    def ensure(self, manifest: MachineManifest, epoch: int) -> None:
        if self._metadata().get("restore_in_progress"):
            raise CellResourceError("environment restore is incomplete; startup is fenced")
        if self._metadata().get("quiesce_state") in {"pending", "failed"}:
            raise CellResourceError("quiesce is incomplete; restore a known complete checkpoint")
        # Validate resource contract before the first Docker mutation.
        self.container_options(manifest, "pending", epoch)
        existing = self._container()
        reuse_existing = False
        if existing is not None:
            physical_epoch = int(existing.labels.get("omnia.fencing_epoch", "0"))
            if physical_epoch > epoch:
                raise CellIdentityConflict("a newer physical machine already exists")
            project_postgres = self._project_postgres()
            if project_postgres is not None and project_postgres.labels.get(
                "omnia.fencing_epoch"
            ) != str(physical_epoch):
                raise CellIdentityConflict("machine and project postgres epochs differ")
            metadata = self._metadata()
            previous_manifest = MachineManifest.model_validate(metadata["manifest"])
            if physical_epoch != epoch or previous_manifest.digest() != manifest.digest():
                self._checkpoint_for_recreate(previous_manifest)
                self.remove(expected_epoch=physical_epoch)
            else:
                existing.reload()
                if existing.status != "running":
                    metadata["quiesce_state"] = None
                    write_controller_json(self.metadata_path, metadata)
                    existing.start()
                reuse_existing = True
        internal = self.client.networks.get(self.internal_network)
        if not internal.attrs.get("Internal"):
            raise CellIdentityConflict("project data network must stay internal")
        network_labels = internal.attrs.get("Labels") or {}
        if network_labels.get("omnia.workspace_id") != str(self.workspace_id):
            raise CellIdentityConflict("project internal network owner mismatch")
        outward = self._network(self.stem + "-public", internal=False)
        proxy = self._lookup(self.client.containers, self.stem + "-proxy", "egress-proxy")
        if proxy is None:
            proxy = self.client.containers.create(
                self.guard_image,
                [
                    "python3",
                    "/opt/omnia/machine_egress.py",
                    "--bind",
                    "0.0.0.0",
                    *[arg for cidr in self.denied_cidrs for arg in ("--deny", cidr)],
                ],
                name=self.stem + "-proxy",
                labels=self.labels("egress-proxy"),
                detach=True,
                network=outward.name,
                user="65534:65534",
                cap_drop=["ALL"],
                privileged=False,
                security_opt=["no-new-privileges:true"],
                read_only=True,
                mem_limit=64 * 1024**2,
                memswap_limit=64 * 1024**2,
                nano_cpus=100_000_000,
                pids_limit=64,
                tmpfs={"/tmp": "size=8m"},
            )
            internal.connect(proxy)
            proxy.start()
        proxy.reload()
        if proxy.status != "running":
            proxy.start()
            proxy.reload()
        proxy_ip = proxy.attrs["NetworkSettings"]["Networks"][self.internal_network]["IPAddress"]
        self._wait_proxy_ready(proxy, proxy_ip)
        policy = GuardPolicy(workspace_id=str(self.workspace_id), proxy_ip=proxy_ip)
        self._volume(self.stem + "-logs")
        guard = self._lookup(self.client.containers, self.stem + "-guard", "namespace-guard")
        if guard is None:
            guard = self.client.containers.create(
                self.guard_image,
                [
                    "python3",
                    "/opt/omnia/project_machine_namespace_guard.py",
                    json.dumps(asdict(policy), sort_keys=True),
                ],
                name=self.stem + "-guard",
                labels={**self.labels("namespace-guard"), "omnia.policy_digest": policy.digest()},
                detach=True,
                network=self.internal_network,
                user="0:0",
                cap_drop=["ALL"],
                cap_add=["NET_ADMIN"],
                privileged=False,
                read_only=True,
                security_opt=["no-new-privileges:true"],
                pids_limit=32,
                mem_limit=32 * 1024**2,
                memswap_limit=32 * 1024**2,
                nano_cpus=50_000_000,
                volumes={self.stem + "-logs": {"bind": "/run/omnia-logs", "mode": "ro"}},
            )
            guard.start()
        deadline = time.monotonic() + machine_remaining_seconds(30)
        while time.monotonic() < deadline:
            guard.reload()
            if guard.status != "running":
                raise CellResourceError("namespace guard failed before machine attachment")
            if f"POLICY_READY={policy.digest()}".encode() in guard.logs(tail=5):
                break
            time.sleep(max(0, min(0.1, deadline - time.monotonic())))
        else:
            machine_remaining_seconds(30)
            raise CellResourceError("namespace guard readiness is unverified")
        if guard.labels.get("omnia.policy_digest") != policy.digest():
            raise CellIdentityConflict("namespace guard policy changed")
        for name in self.volume_mapping(manifest):
            if name != self.workspace_volume:
                self._volume(name)
        self._ensure_project_postgres(guard.id, epoch)
        metadata = self._metadata()
        metadata.update(
            proxy_ip=proxy_ip,
            guard_id=guard.id,
            manifest=manifest.model_dump(mode="json"),
            epoch=epoch,
            services={},
            exec_logs={},
            exec_pids={},
            quiesce_state=None,
        )
        write_controller_json(self.metadata_path, metadata)
        if reuse_existing:
            return
        image = metadata.get("restored_image", self.base_image)
        machine = self.client.containers.create(
            image,
            **self.container_options(manifest, guard.id, epoch),
        )
        machine.start()

    def _project_postgres_options(self, namespace_id: str, epoch: int) -> dict[str, Any]:
        return {
            "name": self.project_postgres_name,
            "detach": True,
            "labels": {
                **self.labels("project-postgres"),
                "omnia.fencing_epoch": str(epoch),
                "omnia.image_ref": self.postgres_image,
            },
            "entrypoint": [],
            "command": [
                "postgres",
                "-D",
                _PROJECT_POSTGRES_DATA,
                "-c",
                f"listen_addresses={_PROJECT_POSTGRES_HOST}",
                "-c",
                "unix_socket_directories=",
            ],
            "network_mode": "container:" + namespace_id,
            "user": _PROJECT_POSTGRES_USER,
            "cap_drop": ["ALL"],
            "cap_add": [],
            "privileged": False,
            "security_opt": ["no-new-privileges:true"],
            "ports": {},
            "read_only": True,
            "mem_limit": self.project_postgres_memory_bytes,
            "memswap_limit": self.project_postgres_memory_bytes,
            "nano_cpus": int(self.project_postgres_cpu_cores * 1_000_000_000),
            "pids_limit": 128,
            "volumes": {
                self.project_postgres_volume: {
                    "bind": _PROJECT_POSTGRES_DATA,
                    "mode": "rw",
                }
            },
            "tmpfs": {
                "/tmp": "rw,nosuid,nodev,size=32m",
            },
            "environment": {"PGDATA": _PROJECT_POSTGRES_DATA},
            "log_config": docker.types.LogConfig(
                type="json-file", config={"max-size": "1m", "max-file": "2"}
            ),
        }

    def _prepare_project_postgres_volume(self) -> None:
        self._volume(self.project_postgres_volume)
        self._ensure_project_postgres_permissions()
        self._initialize_project_postgres_volume()

    def _ensure_project_postgres_permissions(self) -> None:
        helper_name = self.stem + "-project-postgres-prepare"
        helper = self._lookup(self.client.containers, helper_name, "project-postgres-prepare")
        if helper is not None:
            helper.remove(force=True)
        helper = self.client.containers.create(
            self.postgres_image,
            [
                "sh",
                "-eu",
                "-c",
                f"mkdir -p {_PROJECT_POSTGRES_DATA} "
                f"&& chown -R postgres:postgres {_PROJECT_POSTGRES_DATA}",
            ],
            name=helper_name,
            labels=self.labels("project-postgres-prepare"),
            detach=True,
            entrypoint=[],
            network_mode="none",
            user="0:0",
            cap_drop=["ALL"],
            # CHOWN authorizes ownership changes; DAC_OVERRIDE is separately
            # required to traverse restored PGDATA (postgres-owned, mode 0700).
            cap_add=["CHOWN", "DAC_OVERRIDE"],
            privileged=False,
            security_opt=["no-new-privileges:true"],
            read_only=True,
            volumes={
                self.project_postgres_volume: {
                    "bind": _PROJECT_POSTGRES_DATA,
                    "mode": "rw",
                }
            },
            tmpfs={"/tmp": "rw,nosuid,nodev,size=8m"},
            mem_limit=32 * 1024**2,
            memswap_limit=32 * 1024**2,
            nano_cpus=50_000_000,
            pids_limit=32,
            log_config=docker.types.LogConfig(
                type="json-file", config={"max-size": "256k", "max-file": "1"}
            ),
        )
        try:
            helper.start()
            outcome = helper.wait(timeout=machine_remaining_seconds(30))
            if outcome.get("StatusCode") != 0:
                raise CellResourceError("project postgres volume ownership setup failed")
        finally:
            if (
                self._lookup(
                    self.client.containers, helper_name, "project-postgres-prepare"
                )
                is not None
            ):
                helper.remove(force=True)

    def _initialize_project_postgres_volume(self) -> None:
        helper_name = self.stem + "-project-postgres-init"
        helper = self._lookup(self.client.containers, helper_name, "project-postgres-init")
        if helper is not None:
            helper.remove(force=True)
        script = " ".join(
            [
                "if [ ! -f \"$PGDATA/PG_VERSION\" ]; then",
                "umask 077;",
                "printf '%s' \"$POSTGRES_PASSWORD\" > /tmp/postgres-password;",
                "initdb --username=postgres --auth-host=scram-sha-256",
                "--pwfile=/tmp/postgres-password -D \"$PGDATA\";",
                "rm -f /tmp/postgres-password;",
                "fi",
            ]
        )
        helper = self.client.containers.create(
            self.postgres_image,
            ["sh", "-eu", "-c", script],
            name=helper_name,
            labels=self.labels("project-postgres-init"),
            detach=True,
            entrypoint=[],
            network_mode="none",
            user=_PROJECT_POSTGRES_USER,
            cap_drop=["ALL"],
            cap_add=[],
            privileged=False,
            security_opt=["no-new-privileges:true"],
            read_only=True,
            volumes={
                self.project_postgres_volume: {
                    "bind": _PROJECT_POSTGRES_DATA,
                    "mode": "rw",
                }
            },
            tmpfs={"/tmp": "rw,nosuid,nodev,size=8m"},
            environment={
                "PGDATA": _PROJECT_POSTGRES_DATA,
                "POSTGRES_PASSWORD": self.project_postgres_password,
            },
            mem_limit=128 * 1024**2,
            memswap_limit=128 * 1024**2,
            nano_cpus=100_000_000,
            pids_limit=64,
            log_config=docker.types.LogConfig(
                type="json-file", config={"max-size": "256k", "max-file": "1"}
            ),
        )
        try:
            helper.start()
            outcome = helper.wait(timeout=machine_remaining_seconds(60))
            if outcome.get("StatusCode") != 0:
                raise CellResourceError("project postgres initialization failed")
        finally:
            if (
                self._lookup(self.client.containers, helper_name, "project-postgres-init")
                is not None
            ):
                helper.remove(force=True)

    def _ensure_project_postgres(self, namespace_id: str, epoch: int) -> None:
        postgres = self._project_postgres()
        if postgres is not None:
            physical_epoch = int(postgres.labels.get("omnia.fencing_epoch", "0"))
            if physical_epoch > epoch:
                raise CellIdentityConflict("a newer project postgres already exists")
            if physical_epoch != epoch or not self._project_postgres_matches(
                postgres, namespace_id, epoch
            ):
                postgres.remove(force=True)
                postgres = None
        if postgres is None:
            self._prepare_project_postgres_volume()
            postgres = self.client.containers.create(
                self.postgres_image,
                **self._project_postgres_options(namespace_id, epoch),
            )
        postgres.reload()
        if postgres.status != "running":
            postgres.start()
        self._wait_project_postgres_ready(postgres)

    def _project_postgres_matches(self, postgres: Any, namespace_id: str, epoch: int) -> bool:
        postgres.reload()
        attrs = postgres.attrs
        config = attrs.get("Config", {})
        labels = config.get("Labels") or postgres.labels
        host = attrs.get("HostConfig", {})
        mounts = attrs.get("Mounts") or []
        expected = self._project_postgres_options(namespace_id, epoch)
        volume_matches = any(
            item.get("Name") == self.project_postgres_volume
            and item.get("Destination") == _PROJECT_POSTGRES_DATA
            and item.get("RW") is True
            for item in mounts
        )
        return bool(
            labels.get("omnia.fencing_epoch") == str(epoch)
            and labels.get("omnia.image_ref") == self.postgres_image
            and config.get("User") == _PROJECT_POSTGRES_USER
            and not (config.get("Entrypoint") or [])
            and config.get("Cmd") == expected["command"]
            and host.get("NetworkMode") == "container:" + namespace_id
            and host.get("Privileged") is False
            and host.get("ReadonlyRootfs") is True
            and not (host.get("CapAdd") or [])
            and "ALL" in (host.get("CapDrop") or [])
            and not (host.get("PortBindings") or {})
            and "no-new-privileges:true" in (host.get("SecurityOpt") or [])
            and "/tmp" in (host.get("Tmpfs") or {})
            and host.get("Memory") == self.project_postgres_memory_bytes
            and host.get("MemorySwap") == self.project_postgres_memory_bytes
            and host.get("NanoCpus")
            == int(self.project_postgres_cpu_cores * 1_000_000_000)
            and host.get("PidsLimit") == 128
            and volume_matches
        )

    def _wait_project_postgres_ready(self, postgres: Any) -> None:
        deadline = time.monotonic() + machine_remaining_seconds(60)
        while time.monotonic() < deadline:
            postgres.reload()
            if postgres.status != "running":
                raise CellResourceError("project postgres stopped during startup")
            result = postgres.exec_run(
                [
                    "pg_isready",
                    "-h",
                    _PROJECT_POSTGRES_HOST,
                    "-U",
                    _PROJECT_POSTGRES_USER,
                    "-d",
                    _PROJECT_POSTGRES_DB,
                ],
                environment={"PGPASSWORD": self.project_postgres_password},
                user=_PROJECT_POSTGRES_USER,
            )
            if result.exit_code == 0:
                return
            time.sleep(max(0, min(0.2, deadline - time.monotonic())))
        machine_remaining_seconds(60)
        raise CellResourceError("project postgres readiness is unverified")

    def _wait_proxy_ready(self, proxy: Any, address: str) -> None:
        deadline = time.monotonic() + machine_remaining_seconds(30)
        while time.monotonic() < deadline:
            proxy.reload()
            if proxy.status != "running":
                raise CellResourceError("public egress proxy failed before machine attachment")
            try:
                with socket.create_connection(
                    (address, 3128), timeout=machine_remaining_seconds(1)
                ):
                    return
            except OSError:
                time.sleep(max(0, min(0.1, deadline - time.monotonic())))
        machine_remaining_seconds(30)
        raise CellResourceError("public egress proxy readiness is unverified")

    def _checkpoint_for_recreate(self, manifest: MachineManifest) -> None:
        from omnia_orchestrator.services.machine_environment import MachineEnvironmentStore

        store = MachineEnvironmentStore(
            self.root / "artifacts", self.workspace_id, self, max_bytes=self.disk_bytes
        )
        reference = store.capture(
            manifest_digest=manifest.digest(),
            base_image=self.base_image,
            volumes=self.snapshot_volume_names(manifest),
            manifest=manifest,
        )
        metadata = self._metadata()
        metadata.update(
            environment_ref=reference.model_dump(mode="json"), restored_image=reference.image_id
        )
        write_controller_json(self.metadata_path, metadata)

    def exec_start(self, argv: list[str], cwd: str, operation_id: str) -> str:
        machine = self._container()
        if machine is None:
            raise CellResourceError("machine is missing")
        log = "/run/omnia-logs/command-" + str(UUID(operation_id)) + ".log"
        pidfile = "/run/omnia-logs/command-" + str(UUID(operation_id)) + ".pid"
        response = self.client.api.exec_create(
            machine.id,
            [
                "python3",
                "-c",
                _EXEC_WRAPPER,
                json.dumps(
                    [
                        argv,
                        "/workspace" + ("/" + cwd if cwd != "." else ""),
                        log,
                        pidfile,
                    ]
                ),
            ],
            user="0:0",
            workdir="/workspace",
            stdout=False,
            stderr=False,
        )
        exec_id = response["Id"]
        metadata = self._metadata()
        metadata["exec_logs"][exec_id] = log
        metadata["exec_pids"][exec_id] = pidfile
        write_controller_json(self.metadata_path, metadata)
        self.client.api.exec_start(exec_id, detach=True)
        return str(exec_id)

    def _read_log(self, path: str) -> str:
        guard = self._lookup(self.client.containers, self.stem + "-guard", "namespace-guard")
        if guard is None:
            return "command log reader is stopped or removed"
        # Docker29's containerd archive API does not observe live /run tmpfs.
        # Read through exec with a hard byte bound, no shell and no symlink follow.
        if not re.fullmatch(r"/run/omnia-logs/[a-zA-Z0-9_-]+\.log", path):
            raise CellIdentityConflict("invalid command log reference")
        script = (
            "import os,stat,sys,signal; signal.alarm(2); p=sys.argv[1]; "
            'exec("try:\\n fd=os.open(p,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)\\n '
            "assert stat.S_ISREG(os.fstat(fd).st_mode)\\n "
            "os.lseek(fd,max(0,os.fstat(fd).st_size-24000),0)\\n "
            "sys.stdout.buffer.write(os.read(fd,24000))\\n os.close(fd)\\n"
            'except FileNotFoundError: pass")'
        )
        result = guard.exec_run(["python3", "-I", "-S", "-c", script, path], user="0:0")
        if result.exit_code:
            return "command log is unavailable (bounded read failed)"
        return bytes(result.output)[-_LOG_LIMIT:].decode("utf-8", "replace")

    def exec_status(self, exec_id: str) -> dict[str, Any]:
        metadata = self._metadata()
        if exec_id not in metadata["exec_logs"]:
            raise CellIdentityConflict("unknown machine command")
        status = self.client.api.exec_inspect(exec_id)
        machine = self._container()
        if machine is None or status.get("ContainerID") != machine.id:
            raise CellIdentityConflict("command container identity changed")
        return {
            "running": bool(status["Running"]),
            "exit_code": status.get("ExitCode"),
            "output": self._read_log(metadata["exec_logs"][exec_id]),
        }

    def terminate_exec(self, exec_id: str, grace_seconds: int) -> None:
        """Send SIGTERM, then SIGKILL only if this command remains alive."""
        metadata = self._metadata()
        pidfile = metadata["exec_pids"].get(exec_id)
        machine = self._container()
        if machine is None or not pidfile:
            raise CellIdentityConflict("unknown machine command process")
        inspected = self.client.api.exec_inspect(exec_id)
        if inspected.get("ContainerID") != machine.id:
            raise CellIdentityConflict("command container identity changed")
        script = (
            "import os,signal,sys,time; p=sys.argv[1]; grace=float(sys.argv[2]); "
            "pid=int(open(p).read()); os.killpg(pid,signal.SIGTERM); "
            "deadline=time.monotonic()+grace; "
            "exec(\"while time.monotonic()<deadline:\\n"
            " try: os.kill(pid,0)\\n"
            " except ProcessLookupError: break\\n"
            " time.sleep(.1)\\n"
            "else:\\n os.killpg(pid,signal.SIGKILL)\")"
        )
        result = machine.exec_run(
            ["python3", "-I", "-S", "-c", script, pidfile, str(grace_seconds)],
            user="0:0",
        )
        if result.exit_code:
            raise CellResourceError("command termination could not be confirmed")

    def start_service(self, service: MachineService, epoch: int) -> None:
        machine = self._container()
        if machine is None:
            raise CellResourceError("machine is missing")
        metadata = self._metadata()
        previous = metadata["services"].get(service.name)
        if previous:
            info = self.client.api.exec_inspect(previous["exec_id"])
            if info["Running"]:
                return
        log = f"/run/omnia-logs/service-{service.name}-{epoch}.log"
        response = self.client.api.exec_create(
            machine.id,
            [
                "python3",
                "-c",
                _SERVICE_WRAPPER,
                json.dumps([service.argv, "/workspace/" + service.cwd, log, service.restart]),
            ],
            user="0:0",
            workdir="/workspace",
            stdout=False,
            stderr=False,
        )
        metadata["services"][service.name] = {"exec_id": response["Id"], "log": log, "epoch": epoch}
        write_controller_json(self.metadata_path, metadata)
        self.client.api.exec_start(response["Id"], detach=True)

    def service_status(self, service: MachineService, epoch: int) -> dict[str, Any]:
        metadata = self._metadata()
        record = metadata["services"].get(service.name)
        if record is None or record["epoch"] != epoch:
            return {"name": service.name, "state": "missing", "ready": False, "log_tail": ""}
        ready = False
        deadline = time.monotonic() + machine_remaining_seconds(
            service.readiness.timeout_seconds if service.readiness else 0
        )
        while True:
            machine_remaining_seconds(1)
            info = self.client.api.exec_inspect(record["exec_id"])
            running = bool(info["Running"])
            if not running:
                break
            if service.readiness is None:
                ready = True
                break
            # Controller-side HTTP proof, never a generated readiness script.
            import http.client

            connection = http.client.HTTPConnection(
                self.address(), service.readiness.port, timeout=machine_remaining_seconds(2)
            )
            try:
                connection.request("GET", service.readiness.path)
                response = connection.getresponse()
                ready = 200 <= response.status < 400
                response.read(4096)
            except OSError:
                ready = False
            finally:
                connection.close()
            if ready or time.monotonic() >= deadline:
                break
            time.sleep(max(0, min(0.2, deadline - time.monotonic())))
        machine_remaining_seconds(1)
        return {
            "name": service.name,
            "state": "running" if running else "failed",
            "ready": ready,
            "log_tail": self._read_log(record["log"]),
        }

    def address(self) -> str:
        guard = self._lookup(self.client.containers, self.stem + "-guard", "namespace-guard")
        if guard is None:
            raise CellResourceError("namespace guard is missing")
        guard.reload()
        return str(guard.attrs["NetworkSettings"]["Networks"][self.internal_network]["IPAddress"])

    def stop_services(self) -> None:
        # Stop the whole container so daemonized descendants cannot keep writing.
        self.stop()

    def stop(self) -> None:
        machine = self._container()
        if machine is not None:
            machine.stop(timeout=10)
        project_postgres = self._project_postgres()
        if project_postgres is not None:
            project_postgres.stop(timeout=10)

    def remove(self, expected_epoch: int | None = None) -> None:
        machine = self._container()
        project_postgres = self._project_postgres()
        resources = tuple(item for item in (machine, project_postgres) if item is not None)
        if expected_epoch is not None and any(
            item.labels.get("omnia.fencing_epoch") != str(expected_epoch) for item in resources
        ):
            return
        for item in resources:
            item.remove(force=True)
        if machine is not None and self._container() is not None:
            raise CellResourceError("machine removal was not confirmed")
        if project_postgres is not None and self._project_postgres() is not None:
            raise CellResourceError("project postgres removal was not confirmed")

    def is_running(self) -> bool:
        machine = self._container()
        if machine is not None:
            machine.reload()
            if machine.status == "running":
                return True
        project_postgres = self._project_postgres()
        if project_postgres is None:
            return False
        project_postgres.reload()
        return bool(project_postgres.status == "running")

    def export_image(self) -> tuple[str, Iterable[bytes]]:
        machine = self._container()
        if machine is None:
            raise CellResourceError("cannot snapshot a missing machine")
        machine.reload()
        if machine.status == "running":
            raise CellResourceError("mutable machine cannot be snapshotted")
        # Docker commit merges empty arrays with inherited runtime configuration.
        # Export/import rootfs instead, with *only* trusted ownership labels.
        # Named volumes/tmpfs (including logs) are absent from Docker rootfs export.
        with tempfile.TemporaryFile(dir=self.root) as rootfs:
            size = 0
            for chunk in machine.export():
                machine_remaining_seconds(1)
                size += len(chunk)
                if size > self.disk_bytes:
                    raise CellResourceError("rootfs export exceeds admitted disk budget")
                rootfs.write(chunk)
            rootfs.seek(0)
            machine_remaining_seconds(1)
            response = self.client.api.import_image(
                src=rootfs,
                changes=[
                    f"LABEL {key}={value}" for key, value in self.labels("environment").items()
                ],
            )
        if isinstance(response, bytes):
            response = response.decode("utf-8")
        records = [json.loads(line) for line in response.splitlines() if line.strip()]
        image_id = next(
            (
                item.get("status", "")
                for item in records
                if re.fullmatch(r"sha256:[0-9a-f]{64}", item.get("status", ""))
            ),
            None,
        )
        if image_id is None or any("error" in item for item in records):
            raise CellResourceError("sanitized rootfs import failed")
        image = self.client.images.get(image_id)
        return image.id, image.save(named=False)

    def prepare_capture(self) -> None:
        metadata = self._metadata()
        if metadata.get("restore_in_progress"):
            raise CellResourceError("incomplete restore cannot be captured")
        if metadata.get("quiesce_state") in {"pending", "failed"}:
            raise CellResourceError("quiesce is incomplete; restore a known complete checkpoint")
        machine = self._container()
        if machine is None:
            return
        machine.reload()
        manifest = MachineManifest.model_validate(metadata["manifest"])
        if not manifest.data_stores:
            return
        if machine.status != "running":
            if (
                metadata.get("quiesce_container") != machine.id
                or metadata.get("quiesce_state") != "complete"
            ):
                raise CellResourceError("stopped data store has no successful quiesce proof")
            return
        metadata.update(quiesce_state="pending", quiesce_container=machine.id)
        write_controller_json(self.metadata_path, metadata)
        tasks = {task.name: task for task in manifest.tasks}
        try:
            for name in dict.fromkeys(store.quiesce_task for store in manifest.data_stores):
                task = tasks[name]
                operation = self.exec_start(task.argv, task.cwd, str(uuid4()))
                deadline = time.monotonic() + machine_remaining_seconds(task.timeout_seconds)
                while time.monotonic() < deadline:
                    result = self.exec_status(operation)
                    if not result["running"]:
                        if result["exit_code"] != 0:
                            raise CellResourceError(
                                f"quiesce task {name} failed: {result['output']}"
                            )
                        break
                    time.sleep(max(0, min(0.2, deadline - time.monotonic())))
                else:
                    machine_remaining_seconds(1)
                    raise CellResourceError(f"quiesce task {name} timed out")
        except BaseException:
            metadata = self._metadata()
            metadata["quiesce_state"] = "failed"
            write_controller_json(self.metadata_path, metadata)
            try:
                self.stop()
            finally:
                raise
        metadata = self._metadata()
        metadata["quiesce_state"] = "complete"
        write_controller_json(self.metadata_path, metadata)

    def _reconcile_recovery_helpers(self) -> None:
        # Names/labels are durable Docker identity even if the controller died
        # after create but before recording the returned id. Reconcile legacy
        # random-name helpers as well as the current deterministic names.
        selector = [
            f"{key}={value}"
            for key, value in self.labels("restore-check").items()
            if key != "omnia.resource_kind"
        ]
        for helper in self.client.containers.list(all=True, filters={"label": selector}):
            labels = helper.attrs.get("Config", {}).get("Labels") or {}
            kind = labels.get("omnia.resource_kind")
            if kind not in {
                "restore-check",
                "archive",
                "project-postgres-prepare",
                "project-postgres-init",
                "project-postgres-restore",
            }:
                continue
            if any(labels.get(key) != value for key, value in self.labels(kind).items()):
                raise CellIdentityConflict("recovery helper identity mismatch")
            helper.remove(force=True)
            try:
                self.client.containers.get(helper.id)
            except docker.errors.NotFound:
                continue
            raise CellResourceError("recovery helper process death is unverified")

    def validate_restore(self, reference: MachineEnvironmentRef) -> None:
        self._reconcile_recovery_helpers()
        self._prepare_project_postgres_volume()
        helper_name = self.stem + "-project-postgres-restore-check"
        helper = self._lookup(self.client.containers, helper_name, "project-postgres-restore")
        if helper is not None:
            helper.remove(force=True)
        helper = self.client.containers.create(
            self.postgres_image,
            [
                "postgres",
                "-D",
                _PROJECT_POSTGRES_DATA,
                "-c",
                f"listen_addresses={_PROJECT_POSTGRES_HOST}",
                "-c",
                "unix_socket_directories=",
            ],
            name=helper_name,
            labels=self.labels("project-postgres-restore"),
            detach=True,
            entrypoint=[],
            network_mode="none",
            user=_PROJECT_POSTGRES_USER,
            cap_drop=["ALL"],
            privileged=False,
            security_opt=["no-new-privileges:true"],
            read_only=True,
            mem_limit=self.project_postgres_memory_bytes,
            memswap_limit=self.project_postgres_memory_bytes,
            nano_cpus=int(self.project_postgres_cpu_cores * 1_000_000_000),
            pids_limit=128,
            volumes={
                self.project_postgres_volume: {
                    "bind": _PROJECT_POSTGRES_DATA,
                    "mode": "rw",
                }
            },
            tmpfs={
                "/tmp": "size=32m",
            },
            environment={"PGDATA": _PROJECT_POSTGRES_DATA},
            log_config=docker.types.LogConfig(
                type="json-file", config={"max-size": "1m", "max-file": "1"}
            ),
        )
        try:
            helper.start()
            self._wait_project_postgres_ready(helper)
            smoke = helper.exec_run(
                [
                    "psql",
                    "-h",
                    _PROJECT_POSTGRES_HOST,
                    "-U",
                    _PROJECT_POSTGRES_USER,
                    "-d",
                    _PROJECT_POSTGRES_DB,
                    "-Atc",
                    "select 1",
                ],
                environment={"PGPASSWORD": self.project_postgres_password},
                user=_PROJECT_POSTGRES_USER,
            )
            output = smoke.output if isinstance(smoke.output, bytes) else str(smoke.output).encode()
            if smoke.exit_code != 0 or output.strip() != b"1":
                raise CellResourceError("project postgres restore smoke failed")
        finally:
            if (
                self._lookup(
                    self.client.containers, helper_name, "project-postgres-restore"
                )
                is not None
            ):
                helper.remove(force=True)
        manifest = reference.manifest
        if manifest is None:
            # Older environment artifacts had no declared data-store checks.
            if MachineManifest.model_validate(self._metadata()["manifest"]).data_stores:
                raise CellResourceError("data-store restore requires its captured manifest")
            return
        tasks = {task.name: task for task in manifest.tasks}
        for name in dict.fromkeys(store.restore_check_task for store in manifest.data_stores):
            task = tasks[name]
            helper = self.client.containers.create(
                self._metadata()["pending_image"],
                task.argv,
                entrypoint=[],
                detach=True,
                name=self.stem + "-restore-check",
                labels=self.labels("restore-check"),
                working_dir="/workspace/" + task.cwd,
                network_mode="none",
                cap_drop=["ALL"],
                cap_add=list(_USERLAND_CAPS),
                privileged=False,
                security_opt=["no-new-privileges:true"],
                user="0:0",
                volumes=self.volume_mapping(manifest),
                mem_limit=self.memory_bytes,
                memswap_limit=self.memory_bytes,
                nano_cpus=int(self.cpu_cores * 1_000_000_000),
                pids_limit=self.pids,
                tmpfs={"/tmp": "size=128m", "/run": "size=32m"},
                log_config=docker.types.LogConfig(
                    type="json-file", config={"max-size": "1m", "max-file": "1"}
                ),
            )
            try:
                helper.start()
                outcome = helper.wait(timeout=machine_remaining_seconds(task.timeout_seconds))
                if outcome.get("StatusCode") != 0:
                    raise CellResourceError(f"restore check {name} failed")
            finally:
                self._reconcile_recovery_helpers()

    def import_image(self, path: Path, image_id: str) -> None:
        with path.open("rb") as handle:
            loaded = self.client.images.load(handle)
        if len(loaded) != 1 or loaded[0].id != image_id:
            raise CellIdentityConflict("environment image identity mismatch")
        config = loaded[0].attrs.get("Config", {})
        if config.get("Env") or config.get("Entrypoint") or config.get("Cmd"):
            raise CellIdentityConflict(
                "environment image contains unexpected runtime configuration"
            )
        labels = config.get("Labels") or {}
        if any(labels.get(key) != value for key, value in self.labels("environment").items()):
            raise CellIdentityConflict("environment image project identity mismatch")
        metadata = self._metadata()
        if not metadata.get("restore_in_progress"):
            raise CellResourceError("image import requires a durable restore barrier")
        metadata["pending_image"] = image_id
        write_controller_json(self.metadata_path, metadata)

    def validate_restore_reference(self, reference: MachineEnvironmentRef) -> None:
        if reference.workspace_id != self.workspace_id or reference.base_image != self.base_image:
            raise CellIdentityConflict("environment restore identity mismatch")
        if reference.manifest is not None:
            actual = {volume.name for volume in reference.volumes}
            current = set(self.environment_volume_names(reference.manifest))
            legacy = set(self.volume_mapping(reference.manifest))
            if actual not in (current, legacy):
                raise CellIdentityConflict("environment volume set differs from captured manifest")
        allowed_prefix = self.stem + "-data-"
        if any(
            volume.name
            not in (self.workspace_volume, self.stem + "-home", self.project_postgres_volume)
            and not re.fullmatch(re.escape(allowed_prefix) + r"[a-z][a-z0-9_-]{0,62}", volume.name)
            for volume in reference.volumes
        ):
            raise CellIdentityConflict("environment volume identity mismatch")

    def begin_restore(self, reference: MachineEnvironmentRef) -> None:
        self.validate_restore_reference(reference)
        metadata = self._metadata()
        self._reconcile_recovery_helpers()
        if not metadata.get("restore_in_progress"):
            if self._container() is not None and metadata.get("quiesce_state") not in {
                "pending",
                "failed",
            }:
                # Keep the previous complete data/rootfs pair recoverable if any
                # new volume import fails. These private immutable artifacts are
                # retained until an operator chooses a recovery revision.
                self._checkpoint_for_recreate(MachineManifest.model_validate(metadata["manifest"]))
                metadata = self._metadata()
            metadata["rollback_ref"] = metadata.get("environment_ref")
            metadata["restore_in_progress"] = True
            write_controller_json(self.metadata_path, metadata)
        metadata["restore_target"] = reference.model_dump(mode="json")
        metadata.pop("pending_image", None)
        write_controller_json(self.metadata_path, metadata)
        self.remove()
        if self.project_postgres_volume not in {item.name for item in reference.volumes}:
            self._reset_project_postgres_volume()

    def _reset_project_postgres_volume(self) -> None:
        volume = self._lookup(
            self.client.volumes, self.project_postgres_volume, "project-volume"
        )
        if volume is not None:
            volume.remove()
            if (
                self._lookup(
                    self.client.volumes, self.project_postgres_volume, "project-volume"
                )
                is not None
            ):
                raise CellResourceError("project postgres volume removal was not confirmed")
        self._volume(self.project_postgres_volume)

    def finish_restore(self) -> None:
        self._reconcile_recovery_helpers()
        metadata = self._metadata()
        if not metadata.get("restore_in_progress") or not metadata.get("pending_image"):
            raise CellResourceError("restore cannot activate an incomplete environment")
        metadata["restored_image"] = metadata.pop("pending_image")
        metadata["environment_ref"] = metadata.pop("restore_target")
        metadata["restore_in_progress"] = False
        metadata["quiesce_state"] = None
        metadata.pop("quiesce_container", None)
        metadata["services"] = {}
        write_controller_json(self.metadata_path, metadata)

    def _archive_helper(self, volume: str, *, writable: bool) -> Any:
        metadata = self._metadata()
        manifest = MachineManifest.model_validate(metadata["manifest"])
        allowed: dict[str, dict[str, str]] = {
            name: {} for name in self.environment_volume_names(manifest)
        }
        if metadata.get("restore_in_progress"):
            allowed = {item["name"]: {} for item in metadata["restore_target"]["volumes"]}
        if volume not in allowed:
            raise CellIdentityConflict("volume is not owned by this machine")
        return self.client.containers.create(
            self.base_image,
            ["python3", "-c", "import signal; signal.pause()"],
            entrypoint=[],
            name=self.stem + "-archive",
            labels=self.labels("archive"),
            network_mode="none",
            cap_drop=["ALL"],
            # PostgreSQL owns PGDATA as uid 70 with mode 0700.  The restore
            # helper is root but, after cap_drop=ALL, root cannot traverse or
            # clear that directory.  Grant the one filesystem capability only
            # to writable restore helpers; they remain networkless and receive
            # exactly one controller-owned volume.
            cap_add=["DAC_OVERRIDE"] if writable else [],
            privileged=False,
            read_only=True,
            security_opt=["no-new-privileges:true"],
            user="0:0",
            pids_limit=16,
            mem_limit=64 * 1024**2,
            volumes={volume: {"bind": "/volume", "mode": "rw" if writable else "ro"}},
        )

    def export_volume(self, name: str) -> Iterable[bytes]:
        self._reconcile_recovery_helpers()
        helper = self._archive_helper(name, writable=False)
        try:
            chunks, _stat = helper.get_archive("/volume/.")
            yield from chunks
        finally:
            helper.remove(force=True)

    def import_volume(self, name: str, path: Path) -> None:
        self._reconcile_recovery_helpers()
        helper = self._archive_helper(name, writable=True)
        try:
            # Import into a new/empty named volume; never merge an old revision.
            helper.start()
            result = helper.exec_run(
                [
                    "python3",
                    "-c",
                    "import os,shutil; "
                    "[(shutil.rmtree('/volume/'+p) if os.path.isdir('/volume/'+p) "
                    "and not os.path.islink('/volume/'+p) else "
                    "os.unlink('/volume/'+p)) for p in os.listdir('/volume')]",
                ]
            )
            if result.exit_code != 0:
                raise CellResourceError("cannot clear owned volume for restore")
            with path.open("rb") as handle:
                if not helper.put_archive("/volume", handle):
                    raise CellResourceError("volume archive restore failed")
        finally:
            helper.remove(force=True)

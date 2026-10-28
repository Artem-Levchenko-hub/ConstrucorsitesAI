"""Private authenticated backup and exact-owned, network-none restore proof."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shlex
import shutil
import socket
import stat
import struct
import time
from contextlib import AbstractContextManager
from pathlib import Path
from typing import Any
from uuid import UUID

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from pglast import ast, parse_sql

from yleum_orchestrator.services.cell_state import _ensure_secure_dir
from yleum_orchestrator.services.generation_reader_inventory import reader_dispatch
from yleum_orchestrator.services.project_database_schema_proof import (
    _statements,
    _sync_directory,
    legacy_schema_digest,
)
from yleum_orchestrator.services.publication_migration_pg import (
    MAX_DUMP,
    MAX_OUTPUT,
    MIGRATION_PGOPTIONS,
    PUBLIC_SCHEMA_OWNER_STATEMENTS,
    MigrationIOError,
    psql_command,
    read_public_schema_owner,
    require,
    schema_command,
    validated_public_schema_owner,
)
from yleum_orchestrator.services.restoration_database import (
    close_controller_socket,
    read_controller_output,
)


class PrivateBackup:
    def __init__(self, root: Path, key: bytes, *, max_bytes: int = MAX_DUMP) -> None:
        require(len(key) == 32, "migration_backup_key_invalid")
        self.root, self.max_bytes = root, max_bytes
        self.key = HKDF(
            algorithm=hashes.SHA256(),
            length=32,
            salt=None,
            info=b"yleum:publication-migration:backup:v1",
        ).derive(key)

    def path(self, project: UUID, operation: UUID) -> Path:
        require(
            type(project) is UUID and type(operation) is UUID, "migration_backup_identity_invalid"
        )
        return self.root / str(project) / "migrations" / str(operation) / "backup.aead"

    @staticmethod
    def _aad(project: UUID, operation: UUID, binding: dict[str, Any]) -> bytes:
        return json.dumps(
            {
                "version": 1,
                "project": str(project),
                "operation": str(operation),
                "binding": binding,
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode()

    def save(
        self, project: UUID, operation: UUID, data: bytes, binding: dict[str, Any]
    ) -> dict[str, Any]:
        require(0 < len(data) <= self.max_bytes, "migration_backup_budget")
        path = self.path(project, operation)
        try:
            _ensure_secure_dir(path.parent, create=True)
            siblings = list(path.parent.parent.iterdir())
            require(len(siblings) <= 256, "migration_backup_project_budget")
            retained = 0
            for sibling in siblings:
                if not sibling.is_dir():
                    continue
                require(not sibling.is_symlink(), "migration_backup_directory_invalid")
                candidate = sibling / "backup.aead"
                if candidate.exists():
                    require(not candidate.is_symlink(), "migration_backup_invalid")
                    retained += 1
            require(retained < 4, "migration_backup_project_budget")
            require(
                shutil.disk_usage(path.parent).free > len(data) + 1024**3,
                "migration_backup_disk_budget",
            )
            nonce = os.urandom(12)
            blob = (
                b"OMNIA-PG1"
                + nonce
                + AESGCM(self.key).encrypt(nonce, data, self._aad(project, operation, binding))
            )
            fd = os.open(
                path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600
            )
            with os.fdopen(fd, "wb") as handle:
                handle.write(blob)
                handle.flush()
                os.fsync(handle.fileno())
            _sync_directory(path)
            return {
                "sha256": hashlib.sha256(data).hexdigest(),
                "size": len(data),
                "ciphertext_sha256": hashlib.sha256(blob).hexdigest(),
            }
        except Exception:
            raise MigrationIOError("migration_backup_write_failed") from None

    def load(
        self, project: UUID, operation: UUID, receipt: dict[str, Any], binding: dict[str, Any]
    ) -> bytes:
        path = self.path(project, operation)
        try:
            _ensure_secure_dir(path.parent, create=False)
            require(
                not path.is_symlink() and path.stat().st_size <= self.max_bytes + 128,
                "migration_backup_invalid",
            )
            fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
            with os.fdopen(fd, "rb") as handle:
                info = os.fstat(handle.fileno())
                require(
                    stat.S_ISREG(info.st_mode)
                    and info.st_nlink == 1
                    and info.st_size <= self.max_bytes + 128,
                    "migration_backup_invalid",
                )
                blob = handle.read(self.max_bytes + 129)
            require(
                blob.startswith(b"OMNIA-PG1")
                and hashlib.sha256(blob).hexdigest() == receipt["ciphertext_sha256"],
                "migration_backup_invalid",
            )
            data = AESGCM(self.key).decrypt(
                blob[9:21], blob[21:], self._aad(project, operation, binding)
            )
            require(
                len(data) == receipt["size"] <= self.max_bytes
                and hashlib.sha256(data).hexdigest() == receipt["sha256"],
                "migration_backup_invalid",
            )
            return data
        except Exception:
            raise MigrationIOError("migration_backup_authentication_failed") from None


def restore_globals(dump: str) -> str:
    """Only the initdb-created postgres role may collide; all else must restore."""
    result, skipped = [], 0
    try:
        for statement in _statements(dump):
            require(not statement.startswith("\\"), "migration_globals_unsupported")
            parsed = parse_sql(statement)
            require(len(parsed) == 1, "migration_globals_unsupported")
            node = parsed[0].stmt
            if isinstance(node, ast.CreateRoleStmt) and node.role == "postgres":
                require(not node.options, "migration_globals_unsupported")
                skipped += 1
                continue
            result.append(statement)
        require(skipped == 1, "migration_globals_unsupported")
        return "\n".join(result)
    except Exception:
        raise MigrationIOError("migration_globals_unsupported") from None


def pack_backup(globals_dump: bytes, database_dump: bytes) -> bytes:
    require(
        0 < len(globals_dump) <= MAX_OUTPUT and 0 < len(database_dump) <= MAX_DUMP,
        "migration_backup_budget",
    )
    return struct.pack("!QQ", len(globals_dump), len(database_dump)) + globals_dump + database_dump


def unpack_backup(data: bytes) -> tuple[bytes, bytes]:
    require(16 < len(data) <= MAX_DUMP, "migration_backup_budget")
    globals_size, database_size = struct.unpack("!QQ", data[:16])
    require(
        0 < globals_size <= MAX_OUTPUT
        and 0 < database_size
        and len(data) == 16 + globals_size + database_size,
        "migration_backup_invalid",
    )
    return data[16 : 16 + globals_size], data[16 + globals_size :]


def require_schema(actual: str, expected: str) -> None:
    require(
        actual == expected and re.fullmatch(r"[0-9a-f]{64}", actual) is not None,
        "migration_full_schema_mismatch",
    )


class DockerPG:
    """One bounded exec per statement for idle source and private restore sandbox."""

    def __init__(self, client: Any, container: Any) -> None:
        self.client, self.container = client, container

    def command(
        self, argv: list[str], *, data: bytes | None = None, max_bytes: int = MAX_OUTPUT
    ) -> bytes:
        execution = reader_dispatch(
            self.client.api.exec_create,
            self.container.id,
            ["env", "PGOPTIONS=" + MIGRATION_PGOPTIONS, "timeout", "-s", "KILL", "180", *argv],
            stdin=data is not None,
            stdout=True,
            stderr=True,
            user="postgres",
        )
        connection = reader_dispatch(self.client.api.exec_start, execution["Id"], socket=True)
        try:
            connection._sock.settimeout(190)
            if data is not None:
                require(len(data) <= MAX_DUMP, "migration_input_budget")
                connection._sock.sendall(data)
                connection._sock.shutdown(socket.SHUT_WR)
            output = read_controller_output(connection, max_bytes=max_bytes, error_states=[])
        finally:
            close_controller_socket(connection)
        outcome = self.client.api.exec_inspect(execution["Id"])
        require(
            outcome.get("Running") is False and outcome.get("ExitCode") == 0,
            "migration_docker_sql_failed",
        )
        return output

    def sql(self, sql: str, *, max_bytes: int = MAX_OUTPUT) -> bytes:
        # psql_command supplies the trusted startup options in the wrapper above.
        argv = psql_command()[6:]
        return self.command(argv, data=sql.encode(), max_bytes=max_bytes)

    def json(self, sql: str) -> Any:
        try:
            return json.loads(self.sql(sql))
        except Exception:
            raise MigrationIOError("migration_observation_invalid") from None

    def full_schema(self) -> str:
        return legacy_schema_digest(self.command(schema_command()).decode("utf-8"))


SANDBOX_POSTGRES_COMMAND = (
    "postgres",
    "-c",
    "listen_addresses=",
    "-c",
    "unix_socket_directories=/tmp,/var/run/postgresql",
    "-c",
    "shared_buffers=16MB",
    "-c",
    "max_wal_size=64MB",
    "-c",
    "min_wal_size=32MB",
)


def final_postmaster_options(options: object) -> bool:
    """The official entrypoint's temporary server appends options, including -p.

    Require the exact final argv instead of accepting a temporary server's SELECT 1.
    Both bootstrap and final sockets remain inside the owned tmpfs mounts.
    """
    # The bounded file read cannot prove that an exactly-full response was complete.
    if not isinstance(options, str) or len(options) >= 4096:
        return False
    try:
        argv = shlex.split(options)
    except ValueError:
        return False
    return bool(
        argv
        and argv[0].rsplit("/", 1)[-1] == "postgres"
        and tuple(argv[1:]) == SANDBOX_POSTGRES_COMMAND[1:]
    )


def _pinned_image_identity(value: object) -> tuple[str, str] | None:
    """Normalize Docker Hub spelling only; keep repository and digest identities exact."""
    if not isinstance(value, str) or len(value) > 512:
        return None
    component = r"[a-z0-9]+(?:(?:[._]|__|-+)[a-z0-9]+)*"
    match = re.fullmatch(
        rf"({component}(?::[0-9]+)?(?:/{component})*)@sha256:([0-9a-f]{{64}})", value
    )
    if match is None:
        return None
    repository, digest = match.groups()
    first, *rest = repository.split("/")
    if first in ("docker.io", "index.docker.io"):
        if not rest:
            return None
        repository = "/".join(rest)
    elif "." in first or ":" in first or first == "localhost":
        return repository, digest  # Other registries never alias Docker Hub.
    if "/" not in repository:
        repository = "library/" + repository
    return "docker.io/" + repository, digest


def _has_exact_local_image(reference: str, repo_digests: object) -> bool:
    expected = _pinned_image_identity(reference)
    if expected is None or not isinstance(repo_digests, list) or not repo_digests:
        return False
    identities = [_pinned_image_identity(item) for item in repo_digests]
    return all(identity is not None for identity in identities) and expected in identities


class RestoreSandbox(AbstractContextManager[DockerPG]):
    """New tmpfs only: neither a live volume nor a host path is ever accepted."""

    def __init__(self, client: Any, project: UUID, operation: UUID, image: str) -> None:
        require(
            re.fullmatch(r"[^\s]+@sha256:[0-9a-f]{64}", image) is not None,
            "migration_verifier_image_unpinned",
        )
        self.client, self.image = client, image
        self.name = "omnia-publication-migration-" + operation.hex
        self.labels = {
            "omnia.project_id": str(project),
            "omnia.operation_id": str(operation),
            "omnia.resource_kind": "publication-migration-verifier",
        }
        self.container: Any = None
        self.cleaned = False

    def __enter__(self) -> DockerPG:
        import docker  # type: ignore[import-untyped]

        # Never implicitly pull or retag: deployment must provision the actual image digest.
        image = self.client.images.get(self.image)
        require(
            _has_exact_local_image(self.image, image.attrs.get("RepoDigests")),
            "migration_verifier_image_unavailable",
        )
        try:
            self.client.containers.get(self.name)
        except docker.errors.NotFound:
            pass
        else:
            raise MigrationIOError("migration_verifier_name_occupied")
        self.container = reader_dispatch(
            self.client.containers.create,
            self.image,
            list(SANDBOX_POSTGRES_COMMAND),
            name=self.name,
            # Daemon-enforced lifespan also covers controller death during verification.
            entrypoint=["timeout", "-s", "KILL", "300", "docker-entrypoint.sh"],
            auto_remove=True,
            labels=self.labels,
            detach=True,
            network_mode="none",
            # Matches production Kubernetes PostgreSQL's security context (Alpine).
            user="70:70",
            cap_drop=["ALL"],
            privileged=False,
            security_opt=["no-new-privileges:true"],
            read_only=True,
            mem_limit=768 * 1024**2,
            memswap_limit=768 * 1024**2,
            nano_cpus=1_000_000_000,
            pids_limit=64,
            tmpfs={
                "/var/lib/postgresql/data": "size=384m,uid=70,gid=70,mode=0700",
                "/tmp": "size=16m,uid=70,gid=70,mode=0700",
                "/var/run/postgresql": "size=1m,uid=70,gid=70,mode=0700",
            },
            environment={
                "PGDATA": "/var/lib/postgresql/data/pgdata",
                "POSTGRES_HOST_AUTH_METHOD": "trust",
            },
            log_config=docker.types.LogConfig(type="none"),
        )
        try:
            reader_dispatch(self.container.start)
            pg = DockerPG(self.client, self.container)
            deadline = time.monotonic() + 45
            while time.monotonic() < deadline:
                try:
                    options = pg.json(
                        "SELECT to_json(pg_catalog.pg_read_file("
                        "current_setting('data_directory')||'/postmaster.opts',0,4096));"
                    )
                    if final_postmaster_options(options) and pg.sql("SELECT 1;").strip() == b"1":
                        return pg
                except Exception:
                    pass
                # Poll the deterministic argv proof; elapsed stability proves nothing.
                time.sleep(0.5)
            raise MigrationIOError("migration_verifier_start_failed")
        except Exception:
            self.__exit__()
            raise MigrationIOError("migration_verifier_start_failed") from None

    def __exit__(self, *_: Any) -> None:
        import docker

        if self.container is None:
            return
        try:
            found = self.client.containers.get(self.container.id)
            require(
                found.id == self.container.id
                and found.attrs.get("Config", {}).get("Labels") == self.labels,
                "migration_verifier_ownership_changed",
            )
            reader_dispatch(found.remove, force=True)
            try:
                self.client.containers.get(self.container.id)
            except docker.errors.NotFound:
                self.cleaned = True
            require(self.cleaned, "migration_verifier_death_unconfirmed")
        except docker.errors.NotFound:
            self.cleaned = True
        except Exception:
            raise MigrationIOError("migration_verifier_cleanup_unconfirmed") from None


def restore_backup(pg: DockerPG, packed: bytes, *, public_schema_owner: str) -> None:
    owner = validated_public_schema_owner(public_schema_owner)
    globals_dump, database_dump = unpack_backup(packed)
    pg.sql(restore_globals(globals_dump.decode("utf-8")))
    pg.command(
        [
            "pg_restore",
            "--exit-on-error",
            "--no-owner",
            "--no-privileges",
            "-h",
            "/tmp",
            "-U",
            "postgres",
            "-d",
            "postgres",
        ],
        data=database_dump,
    )
    # Keep global no-owner/no-privileges restoration. Only public's validated
    # owner must match: pg_dump emits its schema TOC entry for nondefault owners.
    pg.sql(PUBLIC_SCHEMA_OWNER_STATEMENTS[owner])
    require(read_public_schema_owner(pg) == owner, "migration_public_schema_owner_changed")

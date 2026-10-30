#!/usr/bin/env python3
"""Logical nightly backup of every Project Cell database plus the state journal.

Runs ON the production host as the orchestrator's unix user, which owns
``/opt/omnia-runtime/state`` (dirs 0700, files 0600). Every database becomes a
logical dump: a cell whose PostgreSQL is running is dumped through ``docker
exec``; a halted one is copied out of its volume READ-ONLY into a throwaway
scratch volume and dumped by a disposable PostgreSQL. No user container or
volume is ever started, written to, stopped or removed — only resources named
``omnia-backup-scratch-*``, which this tool created itself, are.

    backup_cells.py backup --state-root /opt/omnia-runtime/state --out DIR
    backup_cells.py verify --backup DIR

Exit codes: 0 every database dumped (or restored), 2 partial (a busy or
deferred database), 1 hard failure. ``infra/backup/backup-omnia.sh`` is the
intended caller; images come from CELL_POSTGRES_IMAGE / CELL_BACKUP_IMAGE.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import secrets
import shutil
import stat
import subprocess
import tarfile
import tempfile
import threading
import time
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager, nullcontext
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import IO, Any, Literal
from uuid import UUID

SCHEMA_VERSION = 1

# Live layouts, taken from the orchestrator: a cell mounts its retained volume
# at /var/lib/postgresql with PGDATA in the PGDATA/ subdirectory, a project
# database mounts its volume directly ON /var/lib/postgresql/data. Hence the two
# different source directories below; a scratch copy always holds PGDATA itself.
_SOURCE_MOUNT = "/source"
_CELL_SOURCE_DIR = f"{_SOURCE_MOUNT}/PGDATA"
_PROJECT_SOURCE_DIR = _SOURCE_MOUNT
_TARGET_MOUNT = "/target"
_SCRATCH_PGDATA = "/var/lib/postgresql/omnia-backup"
_SCRATCH_SOCKET = "/tmp"
_SCRATCH_HBA = "/tmp/omnia-backup-hba.conf"

_SCRATCH_PREFIX = "omnia-backup-scratch-"
_SCRATCH_LABEL = "omnia.backup=scratch"

_CORE_VOLUME_RE = re.compile(r"^omnia-cell-([0-9a-f]{32})-postgres$")
_PROJECT_VOLUME_RE = re.compile(r"^omnia-machine-([0-9a-f]{32})-app-postgres-data$")
_PROJECT_DB_VOLUME_RE = re.compile(r"^omnia-machine-([0-9a-f]{32})-db-[0-9a-f]{32}$")
_DATA_VOLUME_RE = re.compile(r"^omnia-machine-([0-9a-f]{32})-data-([a-z][a-z0-9_-]{0,62})$")
# pnpm/corepack/Next.js caches are rebuildable and large; they are not data.
_CACHE_DATA_RE = re.compile(r"^omnia-(?:pnpm-store|corepack|next-[0-9a-f]{24})$")
_VOLUME_NAME_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_.-]{1,127}$")
_CONTAINER_ID_RE = re.compile(r"^[0-9a-f]{12,64}$")
_IMAGE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/@-]{1,255}$")
_DOCKER_BIN_RE = re.compile(r"^[A-Za-z0-9._/-]{1,256}$")
_UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
_ARTIFACT_RE = re.compile(
    r"^(?:state\.tar\.gz|[0-9a-f-]{36}-(?:core\.dump|project\.sql)"
    r"|[0-9a-f-]{36}-data-[a-z][a-z0-9_-]{0,62}\.tar\.gz)$"
)

_STATE_EXCLUDES = ("project-machines/artifacts", "locks", "project-cells-capacity-reservations")

_FILE_MODE = 0o600
_DIR_MODE = 0o700
_CHUNK_BYTES = 1024 * 1024
_STDERR_TAIL_BYTES = 4096
_DETAIL_LIMIT = 240

_QUERY_TIMEOUT = 60.0
_DUMP_TIMEOUT = 1800.0
_COPY_TIMEOUT = 1800.0
_START_TIMEOUT = 120.0
_READY_TIMEOUT = 90.0
_READY_POLL_SECONDS = 0.5
_STOP_TIMEOUT = 60.0
# pg_dump takes only ACCESS SHARE locks, but a migration holding ACCESS
# EXCLUSIVE would queue it behind itself — and every reader behind that.
_LOCK_WAIT = "30s"
_CENSUS_SCOPE = "complete_platform_project_workspace_census"
_CENSUS_MAX_AGE_SECONDS = 3600
_DOCKER_OBSERVATION_SCOPE = "complete_local_docker_inventory"

_TABLE_COUNT_SQL = (
    "SELECT count(*) FROM pg_catalog.pg_class c "
    "JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace "
    "WHERE c.relkind IN ('r', 'p') "
    "AND n.nspname NOT IN ('pg_catalog', 'information_schema') "
    "AND n.nspname !~ '^pg_'"
)

# One script serves the scratch server in both modes: an empty volume (verify)
# is initialized, a copied PGDATA (backup) is served as it is. The HBA file is
# ours, so neither dump nor restore needs the cell's password.
_SCRATCH_SERVER_SCRIPT = (
    'if [ ! -f "$PGDATA/PG_VERSION" ]; then\n'
    '  initdb --username=postgres --auth-local=trust --auth-host=reject -D "$PGDATA" >/dev/null\n'
    "fi\n"
    f"printf '%s\\n' 'local all all trust' > {_SCRATCH_HBA}\n"
    'exec postgres -D "$PGDATA" -c listen_addresses= '
    f"-c unix_socket_directories={_SCRATCH_SOCKET} -c hba_file={_SCRATCH_HBA}\n"
)
_OWNERSHIP_SCRIPT = 'chown -R postgres:postgres "$1" && chmod 0700 "$1"'
_EXPORT_SCRIPT = 'test -f "$1/PG_VERSION" || exit 3\nexec tar -cf - -C "$1" .\n'

DumpFormat = Literal["custom", "plain"]
Status = Literal["ok", "busy", "deferred_active_generation", "failed"]


class BackupError(RuntimeError):
    """One unit of work failed; the run continues with the next one."""


# --------------------------------------------------------------------------- #
# process plumbing
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class CommandResult:
    argv: tuple[str, ...]
    exit_code: int
    stdout: bytes
    stderr: bytes

    @property
    def ok(self) -> bool:
        return self.exit_code == 0

    def text(self) -> str:
        return self.stdout.decode("utf-8", "replace")


@dataclass(frozen=True, slots=True)
class StreamResult:
    argv: tuple[str, ...]
    exit_code: int
    size_bytes: int
    sha256: str
    stderr: bytes

    @property
    def ok(self) -> bool:
        return self.exit_code == 0


def _close_stream_process(process: subprocess.Popen[bytes], watchdog: threading.Timer) -> None:
    watchdog.cancel()
    if process.stdout is not None:
        process.stdout.close()
    if process.poll() is None:
        process.kill()
        process.wait()
    if process.stdin is not None:
        try:
            process.stdin.close()
        except OSError:
            pass


class DockerRunner:
    """The only place that talks to docker, so tests can replace it wholesale."""

    def __init__(self, binary: str = "docker") -> None:
        self.binary = _require_name(binary, _DOCKER_BIN_RE, "docker binary")

    def run(
        self,
        args: Sequence[str],
        *,
        timeout: float,
        env: Mapping[str, str] | None = None,
        stdin_path: Path | None = None,
    ) -> CommandResult:
        """Run a docker command whose output is small (never a dump)."""
        argv = (self.binary, *args)
        source: IO[bytes] | int = subprocess.DEVNULL
        handle: IO[bytes] | None = None
        try:
            if stdin_path is not None:
                handle = stdin_path.open("rb")
                source = handle
            completed = subprocess.run(
                argv,
                stdin=source,
                capture_output=True,
                timeout=timeout,
                env=_child_env(env),
                check=False,
            )
        except subprocess.TimeoutExpired:
            return CommandResult(argv, 124, b"", b"timeout")
        except OSError as error:
            return CommandResult(argv, 127, b"", f"cannot run docker: {error}".encode())
        finally:
            if handle is not None:
                handle.close()
        return CommandResult(argv, completed.returncode, completed.stdout, completed.stderr)

    def run_to_file(
        self,
        args: Sequence[str],
        *,
        path: Path,
        timeout: float,
        env: Mapping[str, str] | None = None,
    ) -> StreamResult:
        """Stream stdout straight to disk: a dump is never held in memory."""
        argv = (self.binary, *args)
        digest = hashlib.sha256()
        size = 0
        with tempfile.TemporaryFile() as errors:
            try:
                process = subprocess.Popen(
                    argv,
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.PIPE,
                    stderr=errors,
                    env=_child_env(env),
                )
            except OSError as error:
                return StreamResult(argv, 127, 0, "", f"cannot run docker: {error}".encode())
            watchdog = threading.Timer(timeout, process.kill)
            watchdog.start()
            try:
                stdout = process.stdout
                if stdout is None:  # pragma: no cover - Popen always opens the pipe
                    raise BackupError("docker produced no output stream")
                with os.fdopen(_create_private_file(path), "wb") as sink:
                    while chunk := stdout.read(_CHUNK_BYTES):
                        digest.update(chunk)
                        size += len(chunk)
                        sink.write(chunk)
                    sink.flush()
                    os.fsync(sink.fileno())
                exit_code = process.wait()
            finally:
                _close_stream_process(process, watchdog)
            errors.seek(0, os.SEEK_END)
            errors.seek(max(0, errors.tell() - _STDERR_TAIL_BYTES))
            stderr = errors.read(_STDERR_TAIL_BYTES)
        return StreamResult(argv, exit_code, size, digest.hexdigest(), stderr)

    def run_acknowledged_to_file(
        self,
        args: Sequence[str],
        *,
        path: Path,
        timeout: float,
        nonce: str,
    ) -> StreamResult:
        """Cold tar only: verify/fsync its source SHA frame before acknowledging it."""
        if re.fullmatch(r"[0-9a-f]{32}", nonce) is None or timeout <= 0:
            raise BackupError("invalid cold stream nonce or deadline")
        argv = (self.binary, *args)
        digest, size = hashlib.sha256(), 0
        prefix = f"YLEUM-COLD-1 {nonce} ".encode()
        frame_size = len(prefix) + 65
        deadline = time.monotonic() + timeout
        expired = threading.Event()
        with tempfile.TemporaryFile() as errors:
            try:
                process = subprocess.Popen(
                    argv,
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=errors,
                    env=_child_env(None),
                )
            except OSError as error:
                return StreamResult(argv, 127, 0, "", f"cannot run docker: {error}".encode())

            def terminate() -> None:
                expired.set()
                try:
                    process.kill()
                except ProcessLookupError:
                    pass

            watchdog = threading.Timer(timeout, terminate)
            watchdog.start()
            failure = b""
            try:
                if process.stdout is None or process.stdin is None:  # pragma: no cover
                    raise BackupError("cold stream pipes unavailable")
                tail = b""
                with os.fdopen(_create_private_file(path), "wb") as sink:
                    while True:
                        # A buffered read(CHUNK) can wait for EOF while the producer
                        # waits for our ACK. os.read returns an available short read.
                        chunk = os.read(process.stdout.fileno(), _CHUNK_BYTES)
                        if not chunk:
                            raise BackupError("cold stream ended before source SHA frame")
                        pending = tail + chunk
                        body, tail = pending[:-frame_size], pending[-frame_size:]
                        if body:
                            sink.write(body)
                            digest.update(body)
                            size += len(body)
                        if (
                            len(tail) == frame_size
                            and tail.startswith(prefix)
                            and re.fullmatch(rb"[0-9a-f]{64}\n", tail[len(prefix) :])
                        ):
                            if tail[len(prefix) : -1].decode() != digest.hexdigest():
                                raise BackupError("cold stream source SHA mismatch")
                            sink.flush()
                            os.fsync(sink.fileno())
                            if expired.is_set() or time.monotonic() >= deadline:
                                raise BackupError("cold stream deadline before ACK")
                            acknowledgment = (
                                f"YLEUM-COLD-ACK-1 {nonce} {digest.hexdigest()}\n".encode()
                            )
                            if process.stdin.write(acknowledgment) != len(acknowledgment):
                                raise BackupError("cold stream ACK short write")
                            process.stdin.flush()
                            process.stdin.close()
                            break
                extra = False
                while chunk := os.read(process.stdout.fileno(), _CHUNK_BYTES):
                    extra = True
                exit_code = process.wait()
                if extra:
                    raise BackupError("cold stream emitted bytes after source SHA frame")
                if expired.is_set() or time.monotonic() >= deadline:
                    raise BackupError("cold stream deadline exceeded")
            except (BackupError, OSError) as error:
                failure = str(error).encode()
                exit_code = 124 if expired.is_set() else 1
            finally:
                _close_stream_process(process, watchdog)
            errors.seek(0)
            stderr = (failure + b"\n" + errors.read(_STDERR_TAIL_BYTES))[:_STDERR_TAIL_BYTES]
        return StreamResult(argv, exit_code, size, digest.hexdigest(), stderr)

    def pipe(
        self,
        producer: Sequence[str],
        consumer: Sequence[str],
        *,
        timeout: float,
    ) -> tuple[CommandResult, CommandResult]:
        """Stream one container's stdout into another's stdin, nothing buffered."""
        first = (self.binary, *producer)
        second = (self.binary, *consumer)
        with tempfile.TemporaryFile() as errors_a, tempfile.TemporaryFile() as errors_b:
            try:
                source = subprocess.Popen(
                    first,
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.PIPE,
                    stderr=errors_a,
                    env=_child_env(None),
                )
            except OSError as error:
                broken = f"cannot run docker: {error}".encode()
                return (
                    CommandResult(first, 127, b"", broken),
                    CommandResult(second, 127, b"", b""),
                )
            try:
                sink = subprocess.Popen(
                    second,
                    stdin=source.stdout,
                    stdout=subprocess.DEVNULL,
                    stderr=errors_b,
                    env=_child_env(None),
                )
            except OSError as error:
                source.kill()
                source.wait()
                return (
                    CommandResult(first, 127, b"", b""),
                    CommandResult(second, 127, b"", f"cannot run docker: {error}".encode()),
                )
            finally:
                if source.stdout is not None:
                    source.stdout.close()
            watchdogs = [threading.Timer(timeout, source.kill), threading.Timer(timeout, sink.kill)]
            for watchdog in watchdogs:
                watchdog.start()
            try:
                sink_code = sink.wait()
                source_code = source.wait()
            finally:
                for watchdog in watchdogs:
                    watchdog.cancel()
                for process in (source, sink):
                    if process.poll() is None:  # pragma: no cover - defensive
                        process.kill()
                        process.wait()
            errors_a.seek(0)
            errors_b.seek(0)
            return (
                CommandResult(first, source_code, b"", errors_a.read(_STDERR_TAIL_BYTES)),
                CommandResult(second, sink_code, b"", errors_b.read(_STDERR_TAIL_BYTES)),
            )


class SecretRedactor:
    """Last line of defence: no credential reaches stdout or the manifest."""

    def __init__(self) -> None:
        self._secrets: set[str] = set()

    def add(self, secret: str | None) -> None:
        if secret:
            self._secrets.add(secret)

    def scrub(self, text: str) -> str:
        for secret in self._secrets:
            text = text.replace(secret, "***")
        return text


class ScratchPool:
    """Every resource this tool creates, by exact name, removed in ``finally``."""

    def __init__(self, runner: DockerRunner) -> None:
        self.runner = runner
        self.containers: list[str] = []
        self.volumes: list[str] = []

    def container_name(self) -> str:
        name = _SCRATCH_PREFIX + secrets.token_hex(16)
        self.containers.append(name)
        return name

    def volume_name(self) -> str:
        name = _SCRATCH_PREFIX + secrets.token_hex(16)
        self.volumes.append(name)
        return name

    def release(self) -> None:
        for name in reversed(self.containers):
            _require_scratch(name)
            self.runner.run(["stop", "--time", "5", name], timeout=_STOP_TIMEOUT)
            self.runner.run(["rm", "--force", "--volumes", name], timeout=_STOP_TIMEOUT)
        for name in reversed(self.volumes):
            _require_scratch(name)
            self.runner.run(["volume", "rm", "--force", name], timeout=_STOP_TIMEOUT)
        # Synchronous --rm helpers can already be absent when cleanup starts.
        # A failed rm alone proves neither a leak nor successful removal.
        containers = self.runner.run(
            ["container", "ls", "--all", "--format", "{{.Names}}"], timeout=_QUERY_TIMEOUT
        )
        if not containers.ok or set(containers.text().splitlines()).intersection(self.containers):
            raise BackupError("owned scratch cleanup is unconfirmed")
        if set(list_volumes(self.runner)).intersection(self.volumes):
            raise BackupError("owned scratch cleanup is unconfirmed")
        self.containers.clear()
        self.volumes.clear()


# --------------------------------------------------------------------------- #
# validation helpers
# --------------------------------------------------------------------------- #


def _require_name(value: str, pattern: re.Pattern[str], label: str) -> str:
    if pattern.fullmatch(value) is None:
        raise BackupError(f"unsafe {label}: {value!r}")
    return value


def _require_scratch(name: str) -> str:
    """Nothing outside our own prefix may ever be stopped or removed."""
    if not name.startswith(_SCRATCH_PREFIX):
        raise BackupError(f"refusing to touch a resource we did not create: {name!r}")
    return _require_name(name, _VOLUME_NAME_RE, "scratch name")


def _require_match(value: str, pattern: re.Pattern[str], label: str) -> re.Match[str]:
    match = pattern.fullmatch(value)
    if match is None:
        raise BackupError(f"unsafe {label}: {value!r}")
    return match


def _child_env(extra: Mapping[str, str] | None) -> dict[str, str]:
    env = dict(os.environ)
    if extra:
        env.update(extra)
    return env


def _create_private_file(path: Path) -> int:
    """0600 from birth, never through a symlink, never over an existing file."""
    flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY
    for name in ("O_NOFOLLOW", "O_CLOEXEC"):
        flags |= getattr(os, name, 0)
    try:
        return os.open(path, flags, _FILE_MODE)
    except OSError as error:
        raise BackupError(f"cannot create {path.name}: {type(error).__name__}") from error


def _write_private_json(path: Path, payload: Mapping[str, Any]) -> None:
    with os.fdopen(_create_private_file(path), "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, sort_keys=True)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())


def _read_json(path: Path) -> dict[str, Any] | None:
    if path.is_symlink() or not path.is_file():
        return None
    try:
        with path.open(encoding="utf-8") as handle:
            payload = json.load(handle, object_pairs_hook=_unique_project_metadata)
    except (OSError, ValueError):
        return None
    return payload if isinstance(payload, dict) else None


def _iso_now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _detail(redactor: SecretRedactor, reason: str, extra: bytes = b"") -> str:
    tail = extra.decode("utf-8", "replace").strip().splitlines()
    suffix = f": {tail[-1]}" if tail else ""
    return redactor.scrub(f"{reason}{suffix}")[:_DETAIL_LIMIT]


def _file_digest(path: Path) -> tuple[str, int]:
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as handle:
        while chunk := handle.read(_CHUNK_BYTES):
            digest.update(chunk)
            size += len(chunk)
    return digest.hexdigest(), size


def _log(message: str) -> None:
    print(f"[backup-cells] {message}", flush=True)


# --------------------------------------------------------------------------- #
# enumeration
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class DatabaseTarget:
    kind: Literal["core", "project"]
    volume: str
    container: str
    # Directory inside the read-only helper mount that holds PGDATA's content.
    source_dir: str
    password: str | None
    project_role_protocol: Literal[0, 1] = 0


@dataclass(frozen=True, slots=True)
class WorkspacePlan:
    workspace_id: str
    role: Literal["editor", "production"]
    project_id: str | None
    deferred: bool
    databases: tuple[DatabaseTarget, ...]
    data_volumes: tuple[str, ...]


@dataclass
class Inventory:
    plans: list[WorkspacePlan] = field(default_factory=list)
    orphans: list[dict[str, str]] = field(default_factory=list)
    retired: list[dict[str, Any]] = field(default_factory=list)
    superseded_leases: list[dict[str, Any]] = field(default_factory=list)


def _json_digest(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _uuid(value: Any) -> bool:
    return isinstance(value, str) and _UUID_RE.fullmatch(value) is not None


def parse_platform_census(encoded: str, *, now: datetime | None = None) -> dict[str, Any]:
    """Protected caller supplies a full, single-statement platform DB snapshot.

    A checksum binds the receipt, not its authentication: only the canonical
    root collector may supply this environment value, including to peer roots.
    """
    try:
        payload = json.loads(encoded, object_pairs_hook=_unique_project_metadata)
        if (
            not isinstance(payload, dict)
            or set(payload)
            != {
                "version",
                "scope",
                "observed_at",
                "projects",
                "workspaces",
            }
            or type(payload["version"]) is not int
            or payload["version"] != 1
            or payload["scope"] != _CENSUS_SCOPE
        ):
            raise ValueError
        stamp = payload["observed_at"]
        if not isinstance(stamp, str) or not stamp.endswith("Z"):
            raise ValueError
        observed = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
        age = ((now or datetime.now(UTC)) - observed).total_seconds()
        if not -60 <= age <= _CENSUS_MAX_AGE_SECONDS:
            raise ValueError
        projects, workspaces = payload["projects"], payload["workspaces"]
        if (
            not isinstance(projects, list)
            or any(not _uuid(item) for item in projects)
            or len(set(projects)) != len(projects)
            or not isinstance(workspaces, list)
        ):
            raise ValueError
        seen: set[str] = set()
        for row in workspaces:
            if (
                not isinstance(row, dict)
                or set(row) != {"id", "project_id"}
                or not _uuid(row["id"])
                or row["id"] in seen
                or row["project_id"] not in projects
            ):
                raise ValueError
            seen.add(row["id"])
        return payload
    except (ValueError, TypeError, KeyError, OverflowError) as error:
        raise BackupError("platform project/workspace census is invalid or stale") from error


def _terminal_destroy(workspace: Mapping[str, Any]) -> dict[str, Any] | None:
    """Only an exact current terminal receipt supersedes earlier compute leases."""
    epoch, operations = workspace.get("fencing_epoch"), workspace.get("operations")
    if (
        workspace.get("phase") != "completed"
        or workspace.get("bundle_state") != "retained"
        or not _uuid(workspace.get("project_id"))
        or not _uuid(workspace.get("owner_id"))
        or type(epoch) is not int
        or epoch <= 0
        or not isinstance(operations, list)
        or not _uuid(workspace.get("last_operation_id"))
    ):
        return None
    wid = workspace.get("workspace_id")
    if not _uuid(wid):
        return None
    names = workspace.get("resource_names")
    stem = f"omnia-cell-{UUID(wid).hex}"
    expected = {
        "workspace_id": wid,
        "namespace": "prod",
        **{
            field: f"{stem}-{suffix}"
            for field, suffix in {
                "internal_network": "internal",
                "egress_network": "egress",
                "workspace_volume": "workspace",
                "agent_home_volume": "agent-home",
                "postgres_volume": "postgres",
                "redis_volume": "redis",
                "checkpoint_volume": "checkpoints",
                "postgres_container": "postgres",
                "redis_container": "redis",
            }.items()
        },
    }
    if names != expected:
        return None
    seen: set[str] = set()
    latest = None
    for operation in operations:
        if (
            not isinstance(operation, dict)
            or not _uuid(operation.get("operation_id"))
            or operation["operation_id"] in seen
            or type(operation.get("fencing_epoch")) is not int
            or not 0 < operation["fencing_epoch"] <= epoch
        ):
            return None
        seen.add(operation["operation_id"])
        if operation["operation_id"] == workspace["last_operation_id"]:
            latest = operation
        elif operation["fencing_epoch"] == epoch:
            return None
    if (
        latest is None
        or latest.get("kind") != "destroy"
        or latest.get("status") != "completed"
        or latest.get("phase") != "completed"
        or latest["fencing_epoch"] != epoch
        or "generation_run_id" not in latest
        or latest["generation_run_id"] is not None
    ):
        return None
    return latest


def _matched_superseded_lease(workspace: Mapping[str, Any]) -> bool:
    latest = _terminal_destroy(workspace)
    epoch, run = (
        workspace.get("active_generation_fencing_epoch"),
        workspace.get("active_generation_run_id"),
    )
    return (
        latest is not None
        and _uuid(run)
        and type(epoch) is int
        and 0 < epoch < latest["fencing_epoch"]
        and any(
            item.get("fencing_epoch") == epoch
            and item.get("generation_run_id") == run
            and item.get("kind") in {"ensure", "reconcile"}
            and item.get("status") == "completed"
            and item.get("phase") == "completed"
            for item in workspace["operations"]
        )
    )


def _docker_json_lines(runner: DockerRunner, args: list[str]) -> list[dict[str, Any]]:
    result = runner.run(args, timeout=_QUERY_TIMEOUT)
    if not result.ok:
        raise BackupError("complete Docker inventory command failed")
    try:
        rows = [
            json.loads(line, object_pairs_hook=_unique_project_metadata)
            for line in result.text().splitlines()
            if line.strip()
        ]
        if any(not isinstance(row, dict) for row in rows):
            raise ValueError
        return rows
    except ValueError as error:
        raise BackupError("complete Docker inventory is unreadable") from error


def _docker_labels(value: Any) -> dict[str, str]:
    if value is None:
        return {}
    if not isinstance(value, dict) or any(
        not isinstance(key, str) or not isinstance(item, str) for key, item in value.items()
    ):
        raise BackupError("Docker inventory labels are unconfirmed")
    # Never archive arbitrary labels (which may contain customer secrets).
    return {
        key: item
        for key, item in value.items()
        if key
        in {
            "omnia.workspace_id",
            "omnia.project_id",
            "omnia.owner_id",
            "omnia.resource_kind",
            "omnia.workspace-id",
            "omnia.project-id",
            "omnia.owner-id",
        }
    }


def _resolve_docker_source(source: Any, *, with_sudo: bool) -> str:
    if (
        not isinstance(source, str)
        or not Path(source).is_absolute()
        or any(character in source for character in ("\n", "\r", "\0"))
    ):
        raise BackupError("Docker source path is unconfirmed")
    if not with_sudo:
        try:
            return str(Path(source).resolve(strict=True))
        except (OSError, RuntimeError) as error:
            raise BackupError("Docker source path is inaccessible") from error
    # The controller must retain its UID for credential authority. Canonical
    # root callers explicitly allow only this fixed, metadata-only resolver.
    try:
        result = subprocess.run(
            ["/usr/bin/sudo", "-n", "--", "/usr/bin/realpath", "-e", "--", source],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            timeout=_QUERY_TIMEOUT,
            env={"PATH": "/usr/bin:/bin", "LC_ALL": "C"},
            check=False,
        )
        resolved = result.stdout.decode("utf-8")
        if result.returncode != 0 or not resolved.endswith("\n") or len(resolved.splitlines()) != 1:
            raise ValueError
        resolved = resolved.removesuffix("\n")
        if not Path(resolved).is_absolute() or any(
            character in resolved for character in ("\r", "\0")
        ):
            raise ValueError
        return resolved
    except (OSError, ValueError, subprocess.SubprocessError):
        # Never include argv, stderr, or returned path contents in diagnostics.
        raise BackupError("privileged Docker source resolution is unconfirmed") from None


def observe_docker_inventory(
    runner: DockerRunner,
    *,
    resolve_sources_with_sudo: bool = False,
) -> dict[str, Any]:
    """All volumes and all container states, without reading container environments."""
    names = list_volumes(runner)
    listed = runner.run(["ps", "--all", "--quiet", "--no-trunc"], timeout=_QUERY_TIMEOUT)
    if not listed.ok:
        raise BackupError("complete Docker container census failed")
    ids = listed.text().split()
    if (
        any(_VOLUME_NAME_RE.fullmatch(name) is None for name in names)
        or len(set(names)) != len(names)
        or len(set(ids)) != len(ids)
        or any(_CONTAINER_ID_RE.fullmatch(item) is None for item in ids)
    ):
        raise BackupError("complete Docker inventory identity is invalid")
    volumes, containers = [], []
    for start in range(0, len(names), 64):
        batch = names[start : start + 64]
        rows = _docker_json_lines(
            runner,
            [
                "volume",
                "inspect",
                "--format",
                '{"name":{{json .Name}},"labels":{{json .Labels}},'
                '"mountpoint":{{json .Mountpoint}}}',
                *batch,
            ],
        )
        if (
            len(rows) != len(batch)
            or any(not isinstance(row.get("name"), str) for row in rows)
            or {row["name"] for row in rows} != set(batch)
        ):
            raise BackupError("Docker volume census and inspect disagree")
        for row in rows:
            if set(row) != {"name", "labels", "mountpoint"}:
                raise BackupError("Docker volume inventory fields are unconfirmed")
            row["labels"] = _docker_labels(row.get("labels"))
            row["mountpoint"] = _resolve_docker_source(
                row["mountpoint"],
                with_sudo=resolve_sources_with_sudo,
            )
            volumes.append(row)
    for start in range(0, len(ids), 64):
        batch = ids[start : start + 64]
        rows = _docker_json_lines(
            runner,
            [
                "inspect",
                "--type",
                "container",
                "--format",
                '{"id":{{json .Id}},"name":{{json .Name}},"labels":{{json .Config.Labels}},'
                '"state":{{json .State.Status}},"mounts":{{json .Mounts}}}',
                *batch,
            ],
        )
        if (
            len(rows) != len(batch)
            or any(not isinstance(row.get("id"), str) for row in rows)
            or {row["id"] for row in rows} != set(batch)
        ):
            raise BackupError("Docker container census and inspect disagree")
        for row in rows:
            if (
                set(row) != {"id", "name", "labels", "state", "mounts"}
                or not isinstance(row.get("name"), str)
                or not isinstance(row.get("mounts"), list)
                or row.get("state")
                not in {"created", "running", "paused", "restarting", "removing", "exited", "dead"}
            ):
                raise BackupError("Docker container identity/state is unconfirmed")
            row["name"] = _require_name(
                row["name"].removeprefix("/"), _VOLUME_NAME_RE, "container name"
            )
            row["labels"] = _docker_labels(row.get("labels"))
            mounts = []
            for mount in row["mounts"]:
                if not isinstance(mount, dict) or mount.get("Type") not in {
                    "volume",
                    "bind",
                    "tmpfs",
                }:
                    raise BackupError("Docker container mount is unconfirmed")
                kind, source = mount["Type"], mount.get("Source", "")
                if kind == "tmpfs":
                    mounts.append({"type": kind})
                    continue
                source = _resolve_docker_source(source, with_sudo=resolve_sources_with_sudo)
                name = mount.get("Name", "")
                if kind == "volume" and (not isinstance(name, str) or name not in names):
                    raise BackupError("Docker named mount is absent from volume census")
                mounts.append({"type": kind, "name": name, "source": source})
            row["mounts"] = sorted(mounts, key=lambda item: json.dumps(item, sort_keys=True))
            containers.append(row)
    return {
        "scope": _DOCKER_OBSERVATION_SCOPE,
        "volumes": sorted(volumes, key=lambda row: row["name"]),
        "containers": sorted(containers, key=lambda row: row["id"]),
    }


def _owned_resource(row: Mapping[str, Any], workspace: Mapping[str, Any]) -> bool:
    wid, pid = workspace["workspace_id"], workspace["project_id"]
    stem = UUID(wid).hex
    name = row["name"]
    labels = row["labels"]
    return (
        name.startswith(
            (
                f"omnia-cell-{stem}-",
                f"omnia-cell-test-{stem}-",
                f"omnia-machine-{stem}-",
                f"omnia-machine-test-{stem}-",
            )
        )
        or any(labels.get(key) == wid for key in ("omnia.workspace_id", "omnia.workspace-id"))
        or any(labels.get(key) == pid for key in ("omnia.project_id", "omnia.project-id"))
    )


def _overlap(left: str, right: str) -> bool:
    a, b = Path(left), Path(right)
    return a == b or a in b.parents or b in a.parents


def scoped_docker_observation(
    observation: Mapping[str, Any], workspace: Mapping[str, Any]
) -> dict[str, Any]:
    """Foreign containers mounting this workspace are writers too, including bind aliases."""
    if observation.get("scope") != _DOCKER_OBSERVATION_SCOPE:
        raise BackupError("Docker inventory scope is unconfirmed")
    volumes = [row for row in observation["volumes"] if _owned_resource(row, workspace)]
    names = {row["name"] for row in volumes}
    mounts = [row["mountpoint"] for row in volumes]
    containers = [
        row
        for row in observation["containers"]
        if _owned_resource(row, workspace)
        or any(
            mount.get("name") in names
            or (
                mount.get("source") is not None
                and any(_overlap(mount["source"], path) for path in mounts)
            )
            for mount in row["mounts"]
        )
    ]
    return {"scope": _DOCKER_OBSERVATION_SCOPE, "volumes": volumes, "containers": containers}


def _terminal_receipt(
    state_root: Path, workspace: Mapping[str, Any], observation: Mapping[str, Any]
) -> dict[str, Any]:
    operation = _terminal_destroy(workspace)
    assert operation is not None
    wid = workspace["workspace_id"]
    return {
        "workspace_id": wid,
        "project_id": workspace["project_id"],
        "operation_id": operation["operation_id"],
        "fencing_epoch": operation["fencing_epoch"],
        "journal_sha256": _file_digest(state_root / "project-cells" / f"{wid}.json")[0],
        "docker_observation": observation,
        "docker_observation_sha256": _json_digest(observation),
    }


def _publication_obligation(state_root: Path, workspace: Mapping[str, Any]) -> bool:
    for project, payload in _publication_records(state_root):
        if payload.get("disabled") is True and payload.get("deletion_completed") is True:
            continue
        if project == workspace["project_id"] or workspace["workspace_id"] in json.dumps(payload):
            return True
    return False


def _terminal_metadata_valid(state_root: Path, workspace_id: str) -> bool:
    path = state_root / "project-machines" / workspace_id / "docker.json"
    if not path.exists() and not path.is_symlink():
        return True
    return _terminal_metadata_payload_valid(_read_json(path), workspace_id)


def _terminal_metadata_payload_valid(metadata: Any, workspace_id: str) -> bool:
    if not isinstance(metadata, dict):
        return False
    protocol = metadata.get("project_database_role_protocol", 0)
    if type(protocol) is not int or protocol not in (0, 1):
        return False
    stem = f"omnia-machine-{UUID(workspace_id).hex}-"
    selected = [
        metadata[key] for key in ("active_code_volume", "active_database_volume") if key in metadata
    ]
    reference = metadata.get("environment_ref")
    if reference is not None:
        if (
            not isinstance(reference, dict)
            or reference.get("workspace_id") != workspace_id
            or not isinstance(reference.get("volumes"), list)
        ):
            return False
        for row in reference["volumes"]:
            if not isinstance(row, dict) or not isinstance(row.get("name"), str):
                return False
            selected.append(row["name"])
    return all(
        isinstance(name, str)
        and (name.startswith(stem) or name == f"omnia-cell-{UUID(workspace_id).hex}-workspace")
        and _VOLUME_NAME_RE.fullmatch(name) is not None
        for name in selected
    )


def list_volumes(runner: DockerRunner) -> list[str]:
    result = runner.run(["volume", "ls", "--format", "{{.Name}}"], timeout=_QUERY_TIMEOUT)
    if not result.ok:
        raise BackupError("docker volume ls failed")
    return [line.strip() for line in result.text().splitlines() if line.strip()]


def _publication_records(state_root: Path) -> list[tuple[str, dict[str, Any]]]:
    """Separate canonical service artifacts from strict UUID journal directories."""
    root = state_root / "cell-publications"
    if not root.exists() and not root.is_symlink():
        return []
    if not root.is_dir() or root.is_symlink():
        raise BackupError("publication inventory root is invalid")
    records = []
    for entry in sorted(root.iterdir()):
        info = entry.lstat()
        if entry.name in {"locks", "identities"}:
            if not stat.S_ISDIR(info.st_mode):
                raise BackupError("publication service directory is invalid")
            continue
        if entry.name == "fingerprint.key":
            if (not stat.S_ISREG(info.st_mode) or info.st_size != 32
                    or stat.S_IMODE(info.st_mode) != 0o600):
                raise BackupError("publication fingerprint key is invalid")
            continue
        if _UUID_RE.fullmatch(entry.name) is None or not stat.S_ISDIR(info.st_mode):
            raise BackupError("publication inventory contains an invalid journal path")
        payload = _read_json(entry / "publication.json")
        if payload is None:
            raise BackupError("publication inventory contains an unreadable journal")
        records.append((entry.name, payload))
    return records


def production_workspaces(state_root: Path) -> dict[str, str]:
    """Production workspace id -> project id, from the publication journals."""
    found: dict[str, str] = {}
    for project, payload in _publication_records(state_root):
        if payload.get("disabled") is True and payload.get("deletion_completed") is True:
            continue
        workspace_id = payload.get("production_workspace_id")
        project_id = payload.get("project_id")
        if isinstance(workspace_id, str) and _UUID_RE.fullmatch(workspace_id):
            found[workspace_id] = project_id if isinstance(project_id, str) else project
    return found


def external_publications(state_root: Path) -> list[dict[str, str]]:
    """Named Kubernetes obligations for the full multi-host backup collector."""
    found = []
    for journal_project, payload in _publication_records(state_root):
        if payload.get("disabled") is True and payload.get("deletion_completed") is True:
            continue
        release = payload.get("active_release") or payload.get("prepared_release") or {}
        placement = release.get("placement") if isinstance(release, dict) else None
        if not isinstance(placement, dict) or placement.get("backend") != "kubernetes":
            continue
        project = payload.get("project_id")
        workspace = payload.get("production_workspace_id")
        if (not isinstance(project, str) or project != journal_project
                or not isinstance(workspace, str) or not _UUID_RE.fullmatch(workspace)
                or placement.get("namespace") != f"app-{project}"):
            raise BackupError("published runtime identity is unconfirmed")
        found.append({"project_id": project, "workspace_id": workspace,
                      "namespace": placement["namespace"]})
    return found


def _state_records(state_root: Path) -> dict[str, dict[str, Any]]:
    records: dict[str, dict[str, Any]] = {}
    root = state_root / "project-cells"
    if not root.is_dir():
        return records
    for entry in sorted(root.iterdir()):
        if entry.suffix != ".json":
            continue
        payload = _read_json(entry)
        if payload is None:
            raise BackupError("workspace inventory contains an unreadable record")
        workspace = payload.get("workspace")
        if (not isinstance(workspace, dict) or type(payload.get("version")) is not int
                or payload["version"] != 1):
            raise BackupError("workspace inventory contains an unreadable record")
        workspace_id = workspace.get("workspace_id")
        if isinstance(workspace_id, str) and _UUID_RE.fullmatch(workspace_id):
            if workspace_id in records or entry.stem != workspace_id:
                raise BackupError("workspace inventory identity is duplicated or misplaced")
            records[workspace_id] = workspace
        else:
            raise BackupError("workspace inventory contains an invalid identity")
    return records


def _active_database_volume(state_root: Path, workspace_id: str, default: str) -> str:
    """Restoration activates a ``-db-<hex>`` volume; the controller records it."""
    path = state_root / "project-machines" / workspace_id / "docker.json"
    payload = _read_json(path)
    if (path.exists() or path.is_symlink()) and payload is None:
        raise BackupError("active database registry is unreadable")
    active = payload.get("active_database_volume") if payload else None
    if (
        payload
        and "active_database_volume" in payload
        and active is not None
        and not isinstance(active, str)
    ):
        raise BackupError("active database registry is invalid")
    if not isinstance(active, str) or active == default:
        return default
    match = _require_match(active, _PROJECT_DB_VOLUME_RE, "active database volume")
    if match.group(1) != UUID(workspace_id).hex:
        raise BackupError(f"active database volume belongs to another workspace: {workspace_id}")
    return active


def _credential(path: Path) -> str | None:
    payload = _read_json(path)
    password = payload.get("postgres_password") if payload else None
    return password if isinstance(password, str) and password else None


def _unique_project_metadata(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    payload: dict[str, Any] = {}
    for key, value in pairs:
        if key in payload:
            raise ValueError("duplicate metadata key")
        payload[key] = value
    return payload


def _project_database_authority(
    state_root: Path, workspace_id: str
) -> tuple[Literal[0, 1], str | None]:
    """Use only the existing authority selected by the installed role protocol."""
    machine_root = state_root / "project-machines"
    metadata = machine_root / workspace_id / "docker.json"
    protocol = 0
    try:
        if metadata.exists() or metadata.is_symlink():
            if metadata.is_symlink() or not metadata.is_file():
                raise ValueError("invalid metadata path")
            with metadata.open(encoding="utf-8") as handle:
                payload = json.load(handle, object_pairs_hook=_unique_project_metadata)
            if not isinstance(payload, dict):
                raise ValueError("invalid metadata structure")
            protocol = payload.get("project_database_role_protocol", 0)
            if type(protocol) is not int or protocol not in (0, 1):
                raise ValueError("unsupported database role protocol")
        if protocol == 1:
            # The documented caller uses the orchestrator venv. Keep legacy
            # backup/verify usable without package imports; never allocate here.
            from yleum_orchestrator.services.project_database_credentials import (
                ProjectDatabaseCredentialStore,
            )

            credentials = ProjectDatabaseCredentialStore(
                machine_root / "project-db-credentials"
            ).load(UUID(workspace_id))
            return 1, credentials.admin_password
    except (ImportError, OSError, RuntimeError, ValueError, UnicodeError):
        # JSON parse exceptions can retain their input; suppress chaining and
        # expose neither credential paths nor any record/password contents.
        raise BackupError("project database authority unavailable or invalid") from None
    return 0, _credential(machine_root / "project-postgres-secrets" / f"{workspace_id}.json")


def _project_id(workspace: Mapping[str, Any], published: str | None) -> str | None:
    value = workspace.get("project_id")
    if isinstance(value, str) and _UUID_RE.fullmatch(value):
        return value
    return published


def _declared_core_volume(workspace: Mapping[str, Any], workspace_id: str) -> str | None:
    names = workspace.get("resource_names")
    if names is None:
        return None
    expected = f"omnia-cell-{UUID(workspace_id).hex}-postgres"
    if not isinstance(names, dict) or names.get("postgres_volume") != expected:
        raise BackupError("declared core database volume identity is invalid")
    return expected


def build_inventory(
    state_root: Path,
    volumes: Sequence[str],
    redactor: SecretRedactor,
    *,
    platform_census: Mapping[str, Any] | None = None,
    docker_observation: Mapping[str, Any] | None = None,
) -> Inventory:
    """Union of the state journal and the live volume list; neither alone is complete."""
    core: dict[str, str] = {}
    project: dict[str, set[str]] = {}
    data: dict[str, list[str]] = {}
    for name in volumes:
        if (core_match := _CORE_VOLUME_RE.fullmatch(name)) is not None:
            core[core_match.group(1)] = name
        elif (project_match := _PROJECT_VOLUME_RE.fullmatch(name)) is not None:
            project.setdefault(project_match.group(1), set()).add(name)
        elif (db_match := _PROJECT_DB_VOLUME_RE.fullmatch(name)) is not None:
            project.setdefault(db_match.group(1), set()).add(name)
        elif (data_match := _DATA_VOLUME_RE.fullmatch(name)) is not None:
            if _CACHE_DATA_RE.fullmatch(data_match.group(2)) is None:
                data.setdefault(data_match.group(1), []).append(name)

    inventory = Inventory()
    published = production_workspaces(state_root)
    records = _state_records(state_root)
    external = {item["workspace_id"] for item in external_publications(state_root)}
    for missing in sorted(published.keys() - records.keys() - external):
        inventory.orphans.append(
            {"workspace_id": missing, "reason": "published_workspace_without_state"}
        )
    for workspace_id, workspace in sorted(records.items()):
        stem = UUID(workspace_id).hex
        terminal = _terminal_destroy(workspace)
        scoped = (
            scoped_docker_observation(docker_observation, workspace)
            if terminal is not None and docker_observation is not None
            else None
        )
        if (
            terminal is not None
            and scoped is not None
            and platform_census is not None
            and workspace["project_id"] not in platform_census["projects"]
            and all(row["id"] != workspace_id for row in platform_census["workspaces"])
            and workspace_id not in published
            and workspace_id not in external
            and not _publication_obligation(state_root, workspace)
            and _terminal_metadata_valid(state_root, workspace_id)
            and not scoped["volumes"]
            and not scoped["containers"]
        ):
            inventory.retired.append(
                {
                    **_terminal_receipt(state_root, workspace, scoped),
                    "reason": "retired_no_local_storage",
                    "platform_census_sha256": _json_digest(platform_census),
                }
            )
            continue
        targets: list[DatabaseTarget] = []
        core_volume = core.pop(stem, None)
        declared_core = _declared_core_volume(workspace, workspace_id)
        if declared_core is not None and core_volume is None:
            inventory.orphans.append(
                {
                    "workspace_id": workspace_id,
                    "volume": declared_core,
                    "reason": "declared_core_volume_missing",
                }
            )
        if core_volume is not None:
            password = _credential(
                state_root / "project-cells-credentials" / f"{workspace_id}.json"
            )
            redactor.add(password)
            targets.append(
                DatabaseTarget(
                    kind="core",
                    volume=core_volume,
                    container=f"omnia-cell-{stem}-postgres",
                    source_dir=_CELL_SOURCE_DIR,
                    password=password,
                )
            )
        owned = project.pop(stem, set())
        metadata_path = state_root / "project-machines" / workspace_id / "docker.json"
        active = (
            _active_database_volume(
                state_root, workspace_id, f"omnia-machine-{stem}-app-postgres-data"
            )
            if owned or metadata_path.exists() or metadata_path.is_symlink()
            else None
        )
        metadata = _read_json(metadata_path)
        declared_active = metadata.get("active_database_volume") if metadata else None
        # Core-only cells are valid before the portable machine is materialized.
        # These metadata fields are persisted only after project PG creation (or
        # explicitly select a restored database); their absence is not a receipt
        # proving that a previously selected/materialized database may disappear.
        materialized = metadata is not None and (
            metadata.get("project_database_role_protocol") == 1 or "manifest" in metadata
        )
        if (
            active is not None
            and active not in owned
            and (owned or declared_active or materialized)
        ):
            inventory.orphans.append(
                {
                    "workspace_id": workspace_id,
                    "volume": active,
                    "reason": "active_database_volume_missing",
                }
            )
        if owned:
            if active in owned:
                protocol, password = _project_database_authority(state_root, workspace_id)
                redactor.add(password)
                targets.append(
                    DatabaseTarget(
                        kind="project",
                        volume=active,
                        container=f"omnia-machine-{stem}-project-postgres",
                        source_dir=_PROJECT_SOURCE_DIR,
                        password=password,
                        project_role_protocol=protocol,
                    )
                )
            for spare in sorted(owned - {active}):
                # A superseded restoration copy: real data, deliberately not dumped.
                inventory.orphans.append({"volume": spare, "reason": "inactive_database_volume"})
        owned_data = sorted(data.pop(stem, []))
        if not targets and not owned_data:
            inventory.orphans.append(
                {"workspace_id": workspace_id, "reason": "state_without_volume"}
            )
            continue
        superseded = (
            scoped is not None
            and _matched_superseded_lease(workspace)
            and not any(
                row["state"] not in {"created", "exited", "dead"} for row in scoped["containers"]
            )
        )
        if superseded:
            assert scoped is not None
            inventory.superseded_leases.append(_terminal_receipt(state_root, workspace, scoped))
        inventory.plans.append(
            WorkspacePlan(
                workspace_id=workspace_id,
                role="production" if workspace_id in published else "editor",
                project_id=_project_id(workspace, published.get(workspace_id)),
                deferred=workspace.get("active_generation_run_id") is not None and not superseded,
                databases=tuple(targets),
                data_volumes=tuple(owned_data),
            )
        )
    leftover = [*core.values(), *(name for names in project.values() for name in names)]
    leftover += [name for names in data.values() for name in names]
    for name in sorted(leftover):
        inventory.orphans.append({"volume": name, "reason": "volume_without_state"})
    inventory.orphans.sort(key=lambda item: sorted(item.items()))
    return inventory


# --------------------------------------------------------------------------- #
# dumping
# --------------------------------------------------------------------------- #


@dataclass
class DumpOutcome:
    status: Status
    method: str | None = None
    # The restore tool follows the FORMAT, not the file name: the exec path writes a
    # custom-format pg_dump, the scratch path a plain pg_dumpall script, and both land
    # in a `-core.dump` file. Picking pg_restore by name failed every halted cell.
    dump_format: DumpFormat | None = None
    file: str | None = None
    bytes: int | None = None
    sha256: str | None = None
    tables: int | None = None
    detail: str | None = None


class CellBackup:
    def __init__(
        self,
        *,
        runner: DockerRunner,
        out_dir: Path,
        postgres_image: str,
        helper_image: str,
        redactor: SecretRedactor,
    ) -> None:
        self.runner = runner
        self.out_dir = out_dir
        self.postgres_image = _require_name(postgres_image, _IMAGE_RE, "postgres image")
        self.helper_image = _require_name(helper_image, _IMAGE_RE, "helper image")
        self.redactor = redactor

    # -- entry points ------------------------------------------------------- #

    def dump_database(self, workspace_id: str, target: DatabaseTarget) -> DumpOutcome:
        try:
            container, occupied = self.running_postgres(target)
            if container is not None:
                return self._dump_running(workspace_id, target, container)
            if occupied:
                return DumpOutcome(status="busy", detail="volume held by a foreign container")
            return self._dump_stopped(workspace_id, target)
        except BackupError as error:
            return DumpOutcome(status="failed", detail=self.redactor.scrub(str(error)))

    def dump_data_volume(self, workspace_id: str, volume: str) -> DumpOutcome:
        """A business data volume has no server: a read-only tar.gz is its backup."""
        try:
            name = _require_name(volume, _VOLUME_NAME_RE, "volume")
            suffix = _require_match(name, _DATA_VOLUME_RE, "data volume").group(2)
            path = self.out_dir / f"{workspace_id}-data-{suffix}.tar.gz"
            pool = ScratchPool(self.runner)
            try:
                result = self.runner.run_to_file(
                    [
                        "run",
                        *self.helper_flags(pool.container_name(), read_caps=True),
                        "--volume",
                        f"{name}:{_SOURCE_MOUNT}:ro",
                        self.helper_image,
                        "tar",
                        "-czf",
                        "-",
                        "-C",
                        _SOURCE_MOUNT,
                        ".",
                    ],
                    path=path,
                    timeout=_COPY_TIMEOUT,
                )
            finally:
                pool.release()
            if not result.ok:
                return DumpOutcome(
                    status="failed",
                    method="helper_tar",
                    detail=_detail(self.redactor, "volume archive failed", result.stderr),
                )
            return DumpOutcome(
                status="ok",
                method="helper_tar",
                file=path.name,
                bytes=result.size_bytes,
                sha256=result.sha256,
            )
        except BackupError as error:
            return DumpOutcome(status="failed", detail=self.redactor.scrub(str(error)))

    # -- discovery ---------------------------------------------------------- #

    def running_postgres(self, target: DatabaseTarget) -> tuple[str | None, bool]:
        """(id of the expected running postgres, whether anything holds the volume)."""
        volume = _require_name(target.volume, _VOLUME_NAME_RE, "volume")
        result = self.runner.run(
            ["ps", "--filter", f"volume={volume}", "--format", "{{.ID}} {{.Names}}"],
            timeout=_QUERY_TIMEOUT,
        )
        if not result.ok:
            raise BackupError(f"docker ps failed for {volume}")
        holders = [line.split() for line in result.text().splitlines() if line.strip()]
        for parts in holders:
            if len(parts) >= 2 and parts[1] == target.container:
                return _require_name(parts[0], _CONTAINER_ID_RE, "container id"), True
        return None, bool(holders)

    def volume_is_held(self, volume: str) -> bool:
        result = self.runner.run(
            ["ps", "--filter", f"volume={volume}", "--format", "{{.ID}}"],
            timeout=_QUERY_TIMEOUT,
        )
        if not result.ok:
            raise BackupError(f"docker ps failed for {volume}")
        return any(line.strip() for line in result.text().splitlines())

    # -- exec path ---------------------------------------------------------- #

    def _dump_running(
        self,
        workspace_id: str,
        target: DatabaseTarget,
        container: str,
    ) -> DumpOutcome:
        if target.password is None:
            return DumpOutcome(
                status="failed", method="exec", detail="credential file missing or unreadable"
            )
        # `-e PGPASSWORD` without a value: docker copies it out of THIS process's
        # environment, so the secret never enters argv, /proc or any log.
        env = {"PGPASSWORD": target.password}
        prefix = ["exec", "-e", "PGPASSWORD", container]
        if target.project_role_protocol == 1:
            if target.kind != "project":
                raise BackupError("invalid project database transport")
            connection, env = self.project_admin_connection()
            prefix = ["exec", "--user", "postgres", "-e", "PGOPTIONS", container]
        if target.kind == "core":
            path = self.out_dir / f"{workspace_id}-core.dump"
            command = [
                "pg_dump",
                "-Fc",
                "-U",
                "postgres",
                "-d",
                "postgres",
                f"--lock-wait-timeout={_LOCK_WAIT}",
            ]
            connection = ["-U", "postgres", "-d", "postgres"]
        else:
            # A cluster dump preserves project roles and grants. Protocol 1
            # permits administrator access only through the postgres peer socket.
            path = self.out_dir / f"{workspace_id}-project.sql"
            command = [
                "pg_dumpall",
                "-h",
                "/tmp" if target.project_role_protocol == 1 else "127.0.0.1",
                "-U",
                "postgres",
                f"--lock-wait-timeout={_LOCK_WAIT}",
            ]
            if target.project_role_protocol == 0:
                connection = ["-h", "127.0.0.1", "-U", "postgres", "-d", "postgres"]
        result = self.runner.run_to_file(
            [*prefix, *command],
            path=path,
            timeout=_DUMP_TIMEOUT,
            env=env,
        )
        if not result.ok or result.size_bytes == 0:
            return DumpOutcome(
                status="failed",
                method="exec",
                file=path.name,
                bytes=result.size_bytes,
                detail=_detail(self.redactor, "dump command failed", result.stderr),
            )
        tables = self.count_tables(prefix, connection, env=env)
        if tables is None:
            return DumpOutcome(
                status="failed", method="exec", file=path.name, detail="table count unavailable"
            )
        return DumpOutcome(
            status="ok",
            method="exec",
            dump_format="custom" if target.kind == "core" else "plain",
            file=path.name,
            bytes=result.size_bytes,
            sha256=result.sha256,
            tables=tables,
        )

    # -- scratch path ------------------------------------------------------- #

    def _dump_stopped(self, workspace_id: str, target: DatabaseTarget) -> DumpOutcome:
        suffix = "core.dump" if target.kind == "core" else "project.sql"
        destination = self.out_dir / f"{workspace_id}-{suffix}"
        for attempt in (0, 1):
            pool = ScratchPool(self.runner)
            try:
                outcome = self._scratch_attempt(target, destination, pool)
            finally:
                pool.release()
            if outcome is not None:
                return outcome
            if destination.exists():
                # A retry must never append to, or reuse, the discarded stream.
                destination.unlink()
            if attempt == 1:
                return DumpOutcome(
                    status="busy",
                    method="scratch_copy",
                    detail="a container claimed the volume while copying",
                )
        raise BackupError("unreachable retry branch")  # pragma: no cover

    def _scratch_attempt(
        self,
        target: DatabaseTarget,
        destination: Path,
        pool: ScratchPool,
    ) -> DumpOutcome | None:
        """None means 'the volume became busy, discard this copy and retry'."""
        volume = _require_name(target.volume, _VOLUME_NAME_RE, "volume")
        if self.volume_is_held(volume):
            return None
        scratch_volume = pool.volume_name()
        created = self.runner.run(
            ["volume", "create", "--label", _SCRATCH_LABEL, scratch_volume],
            timeout=_QUERY_TIMEOUT,
        )
        if not created.ok:
            return DumpOutcome(
                status="failed",
                method="scratch_copy",
                detail=_detail(self.redactor, "scratch volume creation failed", created.stderr),
            )
        export, extract = self.runner.pipe(
            [
                "run",
                *self.helper_flags(pool.container_name(), read_caps=True),
                "--volume",
                f"{volume}:{_SOURCE_MOUNT}:ro",
                "--entrypoint",
                "sh",
                self.helper_image,
                "-ec",
                _EXPORT_SCRIPT,
                "_",
                target.source_dir,
            ],
            [
                "run",
                "--interactive",
                *self.helper_flags(pool.container_name(), write_caps=True),
                "--volume",
                f"{scratch_volume}:{_TARGET_MOUNT}",
                self.helper_image,
                "tar",
                "-xf",
                "-",
                "-C",
                _TARGET_MOUNT,
            ],
            timeout=_COPY_TIMEOUT,
        )
        if self.volume_is_held(volume):
            return None
        if export.exit_code == 3:
            return DumpOutcome(
                status="failed", method="scratch_copy", detail="volume holds no initialized PGDATA"
            )
        if not export.ok or not extract.ok:
            return DumpOutcome(
                status="failed",
                method="scratch_copy",
                detail=_detail(
                    self.redactor,
                    "read-only volume copy failed",
                    export.stderr or extract.stderr,
                ),
            )
        prepared = self.prepare_scratch_volume(pool, scratch_volume)
        if prepared is not None:
            return prepared
        server = self.start_scratch_server(pool, scratch_volume)
        if isinstance(server, DumpOutcome):
            return server
        prefix = ["exec", server]
        connection = self.scratch_connection()
        env = None
        if target.project_role_protocol == 1:
            connection, env = self.project_admin_connection()
            prefix = ["exec", "--user", "postgres", "-e", "PGOPTIONS", server]
        result = self.runner.run_to_file(
            [
                *prefix,
                "pg_dumpall",
                "-U",
                "postgres",
                "-h",
                _SCRATCH_SOCKET,
                f"--lock-wait-timeout={_LOCK_WAIT}",
            ],
            path=destination,
            timeout=_DUMP_TIMEOUT,
            env=env,
        )
        if not result.ok or result.size_bytes == 0:
            return DumpOutcome(
                status="failed",
                method="scratch_copy",
                file=destination.name,
                bytes=result.size_bytes,
                detail=_detail(self.redactor, "scratch dump failed", result.stderr),
            )
        tables = self.count_tables(prefix, connection, env=env)
        if tables is None:
            return DumpOutcome(
                status="failed",
                method="scratch_copy",
                file=destination.name,
                detail="table count unavailable",
            )
        return DumpOutcome(
            status="ok",
            method="scratch_copy",
            dump_format="plain",
            file=destination.name,
            bytes=result.size_bytes,
            sha256=result.sha256,
            tables=tables,
        )

    # -- scratch primitives ------------------------------------------------- #

    def prepare_scratch_volume(self, pool: ScratchPool, scratch_volume: str) -> DumpOutcome | None:
        """Give the copied (or empty) scratch PGDATA the ownership postgres demands."""
        ownership = self.runner.run(
            [
                "run",
                *self.helper_flags(pool.container_name(), write_caps=True),
                "--volume",
                f"{scratch_volume}:{_TARGET_MOUNT}",
                "--entrypoint",
                "sh",
                self.postgres_image,
                "-ec",
                _OWNERSHIP_SCRIPT,
                "_",
                _TARGET_MOUNT,
            ],
            timeout=_START_TIMEOUT,
        )
        if ownership.ok:
            return None
        return DumpOutcome(
            status="failed",
            method="scratch_copy",
            detail=_detail(self.redactor, "scratch ownership setup failed", ownership.stderr),
        )

    def start_scratch_server(
        self, pool: ScratchPool, scratch_volume: str, *, bootstrap_user: str = "postgres",
    ) -> str | DumpOutcome:
        if re.fullmatch(r"[a-z_][a-z0-9_]{0,62}", bootstrap_user) is None:
            raise BackupError("invalid scratch bootstrap role")
        script = _SCRATCH_SERVER_SCRIPT.replace(
            "--username=postgres", f"--username={bootstrap_user}"
        )
        name = pool.container_name()
        started = self.runner.run(
            [
                "run",
                "--detach",
                "--name",
                _require_scratch(name),
                "--label",
                _SCRATCH_LABEL,
                "--network",
                "none",
                "--user",
                "postgres",
                "--read-only",
                "--cap-drop",
                "ALL",
                "--security-opt",
                "no-new-privileges:true",
                "--memory",
                "512m",
                "--memory-swap",
                "512m",
                "--pids-limit",
                "128",
                "--cpus",
                "0.5",
                "--tmpfs",
                "/tmp:rw,nosuid,nodev,size=64m",
                "--tmpfs",
                "/run:rw,nosuid,nodev,size=16m",
                "--env",
                f"PGDATA={_SCRATCH_PGDATA}",
                "--volume",
                f"{scratch_volume}:{_SCRATCH_PGDATA}",
                "--entrypoint",
                "sh",
                self.postgres_image,
                "-ec",
                script,
            ],
            timeout=_START_TIMEOUT,
        )
        if not started.ok:
            return DumpOutcome(
                status="failed",
                method="scratch_copy",
                detail=_detail(self.redactor, "scratch server did not start", started.stderr),
            )
        deadline = time.monotonic() + _READY_TIMEOUT
        while True:
            ready = self.runner.run(
                ["exec", name, "pg_isready", "-U", bootstrap_user, "-h", _SCRATCH_SOCKET],
                timeout=_QUERY_TIMEOUT,
            )
            if ready.ok:
                return name
            if time.monotonic() >= deadline:
                return DumpOutcome(
                    status="failed",
                    method="scratch_copy",
                    detail=_detail(self.redactor, "scratch server never got ready", ready.stderr),
                )
            time.sleep(_READY_POLL_SECONDS)

    @staticmethod
    def project_admin_connection() -> tuple[list[str], dict[str, str]]:
        try:
            from yleum_orchestrator.services.restoration_database import admin_args

            connection, environment = admin_args(None)
            return list(connection), dict(environment)
        except (ImportError, OSError, RuntimeError, ValueError):
            raise BackupError("project database admin transport unavailable") from None

    @staticmethod
    def scratch_connection(*, user: str = "postgres", database: str = "postgres") -> list[str]:
        return ["-U", user, "-h", _SCRATCH_SOCKET, "-d", database]

    def create_scratch_restore_admin(self, server: str) -> str:
        """Keep postgres at bootstrap OID 10, independent of restored login flags."""
        admin = "omnia_backup_" + secrets.token_hex(16)
        result = self.runner.run(
            ["exec", _require_scratch(server), "psql", "-X", "-v", "ON_ERROR_STOP=1",
             *self.scratch_connection(), "-c", f"CREATE ROLE {admin} LOGIN SUPERUSER"],
            timeout=_QUERY_TIMEOUT,
        )
        if not result.ok:
            raise BackupError("scratch restore administrator creation failed")
        return admin

    def count_tables(
        self,
        prefix: Sequence[str],
        connection: Sequence[str],
        *,
        env: Mapping[str, str] | None = None,
    ) -> int | None:
        result = self.runner.run(
            [*prefix, "psql", "-X", "-qAt", *connection, "-c", _TABLE_COUNT_SQL],
            timeout=_QUERY_TIMEOUT,
            env=env,
        )
        if not result.ok:
            return None
        try:
            return int(result.text().strip())
        except ValueError:
            return None

    def helper_flags(
        self,
        name: str,
        *,
        read_caps: bool = False,
        write_caps: bool = False,
    ) -> list[str]:
        # PGDATA is mode 0700 owned by postgres: even root needs DAC_OVERRIDE
        # after cap_drop=ALL, and restoring ownership needs CHOWN/FOWNER.
        capabilities: list[str] = []
        if read_caps or write_caps:
            capabilities += ["--cap-add", "DAC_OVERRIDE"]
        if write_caps:
            capabilities += ["--cap-add", "CHOWN", "--cap-add", "FOWNER"]
        return [
            "--rm",
            "--name",
            _require_scratch(name),
            "--label",
            _SCRATCH_LABEL,
            "--network",
            "none",
            "--user",
            "0:0",
            "--read-only",
            "--cap-drop",
            "ALL",
            *capabilities,
            "--security-opt",
            "no-new-privileges:true",
            "--memory",
            "256m",
            "--memory-swap",
            "256m",
            "--pids-limit",
            "64",
            "--tmpfs",
            "/tmp:rw,nosuid,nodev,size=16m",
        ]


# --------------------------------------------------------------------------- #
# state archive
# --------------------------------------------------------------------------- #


def archive_state(state_root: Path, destination: Path) -> dict[str, Any]:
    """tar.gz of the state journal without the 126 GB artifact tree or locks."""
    entries = 0

    def _filter(info: tarfile.TarInfo) -> tarfile.TarInfo | None:
        nonlocal entries
        relative = info.name.removeprefix("state").lstrip("/")
        if relative and any(
            relative == item or relative.startswith(f"{item}/") for item in _STATE_EXCLUDES
        ):
            return None
        if not (info.isfile() or info.isdir() or info.issym()):
            return None
        entries += 1
        return info

    with os.fdopen(_create_private_file(destination), "wb") as raw:
        with tarfile.open(fileobj=raw, mode="w:gz") as archive:
            archive.add(str(state_root), arcname="state", recursive=True, filter=_filter)
        raw.flush()
        os.fsync(raw.fileno())
    digest, size = _file_digest(destination)
    return {
        "file": destination.name,
        "bytes": size,
        "sha256": digest,
        "entries": entries,
        "excluded": list(_STATE_EXCLUDES),
    }


# --------------------------------------------------------------------------- #
# backup command
# --------------------------------------------------------------------------- #


def _outcome_payload(outcome: DumpOutcome) -> dict[str, Any]:
    return {
        "status": outcome.status,
        "method": outcome.method,
        "format": outcome.dump_format,
        "file": outcome.file,
        "bytes": outcome.bytes,
        "sha256": outcome.sha256,
        "tables": outcome.tables,
        "detail": outcome.detail,
    }


def _inventory_signature(root: Path) -> str:
    """Bind the selected database authorities and journals across the export."""
    signature = hashlib.sha256()
    patterns = ("project-cells/*.json", "project-cells-credentials/*.json",
                "project-machines/*/docker.json", "project-machines/project-db-credentials/*.json",
                "cell-publications/*/publication.json")
    for path in sorted({path for pattern in patterns for path in root.glob(pattern)}):
        if path.is_symlink() or not path.is_file():
            raise BackupError("inventory authority is not a regular file")
        signature.update(str(path.relative_to(root)).encode())
        signature.update(b"\0")
        signature.update(path.read_bytes())
        signature.update(b"\0")
    return signature.hexdigest()


def run_backup(
    *,
    state_root: Path,
    out_dir: Path,
    postgres_image: str,
    helper_image: str,
    runner: DockerRunner,
    local_scope: bool = False,
    platform_census_json: str | None = None,
    resolve_docker_sources_with_sudo: bool = False,
) -> tuple[dict[str, Any], int]:
    redactor = SecretRedactor()
    started_at = _iso_now()
    census = (
        parse_platform_census(platform_census_json) if platform_census_json is not None else None
    )
    before = _inventory_signature(state_root)
    observe_terminal = any(
        _terminal_destroy(row) is not None for row in _state_records(state_root).values()
    )
    observation = (
        observe_docker_inventory(runner, resolve_sources_with_sudo=resolve_docker_sources_with_sudo)
        if observe_terminal
        else None
    )
    volumes = (
        [row["name"] for row in observation["volumes"]]
        if observation is not None
        else list_volumes(runner)
    )
    inventory = build_inventory(
        state_root, volumes, redactor, platform_census=census, docker_observation=observation
    )
    backup = CellBackup(
        runner=runner,
        out_dir=out_dir,
        postgres_image=postgres_image,
        helper_image=helper_image,
        redactor=redactor,
    )
    deferred = DumpOutcome(status="deferred_active_generation", detail="generation in progress")
    workspaces: list[dict[str, Any]] = []
    counts = {"ok": 0, "busy": 0, "deferred_active_generation": 0, "failed": 0}
    for plan in inventory.plans:
        databases: list[dict[str, Any]] = []
        data_volumes: list[dict[str, Any]] = []
        for target in plan.databases:
            outcome = deferred if plan.deferred else backup.dump_database(plan.workspace_id, target)
            counts[outcome.status] += 1
            databases.append(
                {"kind": target.kind, "volume": target.volume, **_outcome_payload(outcome)}
            )
            _log(f"{plan.workspace_id} {target.kind}: {outcome.status} ({outcome.method or '-'})")
        for volume in plan.data_volumes:
            outcome = (
                deferred if plan.deferred else backup.dump_data_volume(plan.workspace_id, volume)
            )
            counts[outcome.status] += 1
            data_volumes.append({"volume": volume, **_outcome_payload(outcome)})
        workspaces.append(
            {
                "workspace_id": plan.workspace_id,
                "role": plan.role,
                "project_id": plan.project_id,
                "databases": databases,
                "data_volumes": data_volumes,
            }
        )
    state_archive = archive_state(state_root, out_dir / "state.tar.gz")
    _log(
        f"state archive: {state_archive['entries']} entries, "
        f"{int(state_archive['bytes']) / 1024 / 1024:.1f} MiB"
    )
    external = external_publications(state_root)
    issues = [
        item for item in inventory.orphans if item.get("reason") != "inactive_database_volume"
    ]
    after_observation = (
        observe_docker_inventory(runner, resolve_sources_with_sudo=resolve_docker_sources_with_sudo)
        if observe_terminal
        else None
    )
    after_volumes = (
        [row["name"] for row in after_observation["volumes"]]
        if after_observation is not None
        else list_volumes(runner)
    )
    if (
        _inventory_signature(state_root) != before
        or build_inventory(
            state_root,
            after_volumes,
            redactor,
            platform_census=census,
            docker_observation=after_observation,
        )
        != inventory
    ):
        issues.append({"reason": "inventory_changed_during_backup"})
    coverage = {
        "version": 1,
        "scope": "local-docker" if local_scope else "all-runtimes",
        "expected": _artifact_identities(workspaces),
        "issues": issues,
        "inventory_sha256": before,
        "external_publications": external,
        "started_at": started_at,
        "platform_census": census,
        "retired_no_local_storage": inventory.retired,
        "superseded_generation_leases": inventory.superseded_leases,
        "docker_inventory": observation,
    }
    manifest: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "created_at": _iso_now(),
        "postgres_image": postgres_image,
        "helper_image": helper_image,
        "state_root": str(state_root),
        "state_archive": state_archive,
        "workspaces": workspaces,
        "orphans": inventory.orphans,
        "coverage": coverage,
        "totals": {
            "workspaces": len(workspaces),
            "databases": sum(len(item["databases"]) for item in workspaces),
            "data_volumes": sum(len(item["data_volumes"]) for item in workspaces),
            "orphans": len(inventory.orphans),
            "bytes": sum(
                int(entry.get("bytes") or 0)
                for item in workspaces
                for entry in (*item["databases"], *item["data_volumes"])
            )
            + int(state_archive["bytes"]),
            **counts,
        },
    }
    code = _exit_code(counts)
    return manifest, code or (2 if issues or (external and not local_scope) else 0)


def _exit_code(counts: Mapping[str, int]) -> int:
    if counts["failed"]:
        return 1
    if counts["busy"] or counts["deferred_active_generation"]:
        return 2
    return 0


# --------------------------------------------------------------------------- #
# verify command
# --------------------------------------------------------------------------- #


def run_verify(
    *,
    backup_dir: Path,
    postgres_image: str,
    runner: DockerRunner,
) -> tuple[dict[str, Any], int]:
    manifest = _read_json(backup_dir / "MANIFEST.json")
    if manifest is None or manifest.get("schema_version") != SCHEMA_VERSION:
        raise BackupError(f"unreadable or unsupported manifest in {backup_dir}")
    _validate_manifest(manifest)
    _validate_terminal_receipts(backup_dir, manifest)
    backup = CellBackup(
        runner=runner,
        out_dir=backup_dir,
        postgres_image=postgres_image,
        helper_image=postgres_image,
        redactor=SecretRedactor(),
    )
    results: list[dict[str, Any]] = []
    counts = {"ok": 0, "skipped": 0, "failed": 0}
    for entry in _verifiable_entries(manifest):
        result = _verify_entry(backup, backup_dir, entry)
        counts[str(result["status"])] += 1
        results.append(result)
        _log(f"verify {result['file']}: {result['status']} {result.get('detail') or ''}".rstrip())
    report: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "verified_at": _iso_now(),
        "backup": str(backup_dir),
        "created_at": manifest.get("created_at"),
        "results": results,
        "coverage": manifest["coverage"],
        "totals": {"entries": len(results), **counts},
    }
    if counts["failed"]:
        return report, 1
    coverage = manifest["coverage"]
    unconfirmed = coverage["issues"] or (
        coverage["external_publications"] and coverage["scope"] != "local-docker"
    )
    return report, 2 if counts["skipped"] or unconfirmed else 0


def _artifact_identities(workspaces: Sequence[Mapping[str, Any]]) -> list[dict[str, str]]:
    identities = []
    for workspace in workspaces:
        for entry in (*workspace["databases"], *workspace["data_volumes"]):
            identities.append({"workspace_id": workspace["workspace_id"],
                               "kind": entry.get("kind", "data"), "volume": entry["volume"]})
    return sorted(identities, key=lambda value: json.dumps(value, sort_keys=True))


def _artifact_name(entry: Mapping[str, Any]) -> str:
    """Bind an artifact to its manifest owner and kind, never just a safe path."""
    kind = entry.get("kind") or "data"
    if kind == "state":
        expected = "state.tar.gz"
    else:
        workspace_id = entry.get("workspace_id")
        if not isinstance(workspace_id, str) or _UUID_RE.fullmatch(workspace_id) is None:
            raise BackupError("artifact workspace identity is invalid")
        if kind in {"core", "project"}:
            expected = workspace_id + ("-core.dump" if kind == "core" else "-project.sql")
        elif kind == "data":
            volume = entry.get("volume")
            match = _DATA_VOLUME_RE.fullmatch(volume) if isinstance(volume, str) else None
            if match is None or match.group(1) != UUID(workspace_id).hex:
                raise BackupError("artifact data volume identity is invalid")
            expected = f"{workspace_id}-data-{match.group(2)}.tar.gz"
        else:
            raise BackupError("unsupported artifact kind")
    if entry.get("file") != expected or _ARTIFACT_RE.fullmatch(expected) is None:
        raise BackupError("artifact filename does not match its manifest identity")
    return expected


def _validate_manifest(manifest: Mapping[str, Any]) -> None:
    coverage, workspaces = manifest.get("coverage"), manifest.get("workspaces")
    if (
        not isinstance(coverage, dict)
        or type(coverage.get("version")) is not int
        or coverage["version"] != 1
        or coverage.get("scope") not in {"local-docker", "all-runtimes"}
        or not isinstance(coverage.get("issues"), list)
        or not isinstance(coverage.get("external_publications"), list)
        or not isinstance(workspaces, list)
        or not isinstance(manifest.get("state_archive"), dict)
    ):
        raise BackupError("backup coverage or state archive is unconfirmed")
    orphans = manifest.get("orphans")
    expected_issues = (
        [item for item in orphans if item.get("reason") != "inactive_database_volume"]
        if isinstance(orphans, list) and all(isinstance(item, dict) for item in orphans)
        else []
    )
    if (
        not isinstance(orphans, list)
        or any(not isinstance(item, dict) for item in orphans)
        or coverage["issues"]
        not in (expected_issues, [*expected_issues, {"reason": "inventory_changed_during_backup"}])
    ):
        raise BackupError("backup unresolved inventory does not match coverage")
    seen = set()
    for workspace in workspaces:
        identity = workspace.get("workspace_id")
        if (not isinstance(identity, str) or not _UUID_RE.fullmatch(identity)
                or identity in seen or not isinstance(workspace.get("databases"), list)
                or not isinstance(workspace.get("data_volumes"), list)):
            raise BackupError("backup workspace inventory is invalid")
        seen.add(identity)
        for entry in workspace["databases"]:
            if not isinstance(entry, dict) or entry.get("kind") not in {"core", "project"}:
                raise BackupError("backup database inventory is invalid")
        for entry in workspace["data_volumes"]:
            if not isinstance(entry, dict) or entry.get("kind", "data") != "data":
                raise BackupError("backup data volume inventory is invalid")
        for entry in (*workspace["databases"], *workspace["data_volumes"]):
            if (
                not isinstance(entry, dict)
                or not isinstance(entry.get("volume"), str)
                or not _VOLUME_NAME_RE.fullmatch(entry["volume"])
                or entry.get("status") not in {"ok", "busy", "deferred_active_generation", "failed"}
            ):
                raise BackupError("backup artifact inventory is invalid")
            if entry["status"] == "ok":
                _artifact_name({**entry, "workspace_id": identity})
    _artifact_name({**manifest["state_archive"], "kind": "state"})
    identities = _artifact_identities(workspaces)
    if coverage.get("expected") != identities or len(
        {json.dumps(item, sort_keys=True) for item in identities}
    ) != len(identities):
        raise BackupError("backup artifact coverage does not match its inventory")


def _validate_recorded_docker(observation: Any) -> dict[str, Any]:
    if (
        not isinstance(observation, dict)
        or set(observation) != {"scope", "volumes", "containers"}
        or observation["scope"] != _DOCKER_OBSERVATION_SCOPE
        or not isinstance(observation["volumes"], list)
        or not isinstance(observation["containers"], list)
    ):
        raise BackupError("terminal Docker inventory receipt is unconfirmed")
    for row in observation["volumes"]:
        if (
            not isinstance(row, dict)
            or set(row) != {"name", "labels", "mountpoint"}
            or not isinstance(row["name"], str)
            or not _VOLUME_NAME_RE.fullmatch(row["name"])
            or not isinstance(row["mountpoint"], str)
            or row["labels"] != _docker_labels(row["labels"])
        ):
            raise BackupError("terminal Docker volume receipt is unconfirmed")
    for row in observation["containers"]:
        if (
            not isinstance(row, dict)
            or set(row) != {"id", "name", "labels", "state", "mounts"}
            or not isinstance(row["id"], str)
            or not _CONTAINER_ID_RE.fullmatch(row["id"])
            or not isinstance(row["name"], str)
            or not isinstance(row["state"], str)
            or row["labels"] != _docker_labels(row["labels"])
            or not isinstance(row["mounts"], list)
        ):
            raise BackupError("terminal Docker container receipt is unconfirmed")
        for mount in row["mounts"]:
            if not isinstance(mount, dict) or (
                mount != {"type": "tmpfs"}
                and (
                    set(mount) != {"type", "name", "source"}
                    or mount["type"] not in {"volume", "bind"}
                    or not isinstance(mount["name"], str)
                    or not isinstance(mount["source"], str)
                )
            ):
                raise BackupError("terminal Docker mount receipt is unconfirmed")
    if len({row["name"] for row in observation["volumes"]}) != len(observation["volumes"]) or len(
        {row["id"] for row in observation["containers"]}
    ) != len(observation["containers"]):
        raise BackupError("terminal Docker inventory identity is duplicated")
    return observation


def _validate_terminal_receipts(backup_dir: Path, manifest: Mapping[str, Any]) -> None:
    coverage = manifest["coverage"]
    retired = coverage.get("retired_no_local_storage", [])
    leases = coverage.get("superseded_generation_leases", [])
    if not isinstance(retired, list) or not isinstance(leases, list):
        raise BackupError("terminal inventory receipts are invalid")
    if not retired and not leases:
        return
    try:
        started = datetime.fromisoformat(coverage["started_at"].replace("Z", "+00:00"))
        census = (
            parse_platform_census(json.dumps(coverage["platform_census"]), now=started)
            if retired
            else None
        )
        observation = _validate_recorded_docker(coverage.get("docker_inventory"))
        state = manifest["state_archive"]
        path = backup_dir / "state.tar.gz"
        if path.is_symlink() or _file_digest(path) != (state["sha256"], state["bytes"]):
            raise BackupError("terminal receipt state archive checksum mismatch")
        planned = {row["workspace_id"] for row in manifest["workspaces"]}
        seen: set[str] = set()
        with tarfile.open(path, "r:gz") as archive:
            members = archive.getmembers()
            for receipt in [*retired, *leases]:
                if not isinstance(receipt, dict) or not _uuid(receipt.get("workspace_id")):
                    raise ValueError
                wid = receipt["workspace_id"]
                if wid in seen:
                    raise ValueError
                seen.add(wid)
                matched = [row for row in members if row.name == f"state/project-cells/{wid}.json"]
                if (
                    len(matched) != 1
                    or not matched[0].isfile()
                    or matched[0].size > 4 * 1024 * 1024
                ):
                    raise ValueError
                stream = archive.extractfile(matched[0])
                if stream is None:
                    raise ValueError
                with stream:
                    raw = stream.read()
                payload = json.loads(raw, object_pairs_hook=_unique_project_metadata)
                if type(payload.get("version")) is not int or payload["version"] != 1:
                    raise ValueError
                workspace = payload["workspace"]
                if not isinstance(workspace, dict):
                    raise ValueError
                terminal = _terminal_destroy(workspace)
                scoped = scoped_docker_observation(observation, workspace)
                if (
                    workspace["workspace_id"] != wid
                    or terminal is None
                    or receipt.get("project_id") != workspace["project_id"]
                    or receipt.get("operation_id") != terminal["operation_id"]
                    or type(receipt.get("fencing_epoch")) is not int
                    or receipt["fencing_epoch"] != terminal["fencing_epoch"]
                    or receipt.get("journal_sha256") != hashlib.sha256(raw).hexdigest()
                    or receipt.get("docker_observation") != scoped
                    or receipt.get("docker_observation_sha256") != _json_digest(scoped)
                ):
                    raise ValueError
                if receipt in retired:
                    if (
                        census is None
                        or wid in planned
                        or scoped["volumes"]
                        or scoped["containers"]
                        or receipt.get("reason") != "retired_no_local_storage"
                        or receipt.get("platform_census_sha256") != _json_digest(census)
                        or workspace["project_id"] in census["projects"]
                        or any(row["id"] == wid for row in census["workspaces"])
                    ):
                        raise ValueError
                    metadata_members = [
                        row
                        for row in members
                        if row.name == f"state/project-machines/{wid}/docker.json"
                    ]
                    if len(metadata_members) > 1:
                        raise ValueError
                    if metadata_members:
                        member = metadata_members[0]
                        if not member.isfile() or member.size > 4 * 1024 * 1024:
                            raise ValueError
                        metadata_stream = archive.extractfile(member)
                        if metadata_stream is None:
                            raise ValueError
                        with metadata_stream:
                            metadata = json.load(
                                metadata_stream, object_pairs_hook=_unique_project_metadata
                            )
                        if not _terminal_metadata_payload_valid(metadata, wid):
                            raise ValueError
                    # Even historical publication evidence is retained until its
                    # own canonical deletion marker confirms retirement.
                    for member in members:
                        if not member.name.startswith(
                            "state/cell-publications/"
                        ) or not member.name.endswith("/publication.json"):
                            continue
                        if not member.isfile() or member.size > 4 * 1024 * 1024:
                            raise ValueError
                        public_stream = archive.extractfile(member)
                        if public_stream is None:
                            raise ValueError
                        with public_stream:
                            public = json.load(
                                public_stream, object_pairs_hook=_unique_project_metadata
                            )
                        if (
                            public.get("disabled") is True
                            and public.get("deletion_completed") is True
                        ):
                            continue
                        if member.name.split("/")[-2] == workspace[
                            "project_id"
                        ] or wid in json.dumps(public):
                            raise ValueError
                elif (
                    wid not in planned
                    or not _matched_superseded_lease(workspace)
                    or any(
                        row["state"] not in {"created", "exited", "dead"}
                        for row in scoped["containers"]
                    )
                ):
                    raise ValueError
    except (OSError, ValueError, TypeError, KeyError, tarfile.TarError) as error:
        raise BackupError(
            "terminal inventory receipt does not match its archived authorities"
        ) from error


def _verifiable_entries(manifest: Mapping[str, Any]) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    state_archive = manifest.get("state_archive")
    if isinstance(state_archive, dict):
        entries.append({**state_archive, "kind": "state", "status": "ok"})
    workspaces = manifest.get("workspaces")
    for workspace in workspaces if isinstance(workspaces, list) else []:
        for entry in (*workspace.get("databases", ()), *workspace.get("data_volumes", ())):
            entries.append({**entry, "workspace_id": workspace.get("workspace_id")})
    return entries


def _verify_entry(backup: CellBackup, backup_dir: Path, entry: Mapping[str, Any]) -> dict[str, Any]:
    kind = str(entry.get("kind") or "data")
    name = entry.get("file")
    base: dict[str, Any] = {
        "file": name,
        "kind": kind,
        "workspace_id": entry.get("workspace_id"),
    }
    if kind not in {"core", "project", "data", "state"}:
        return {**base, "status": "failed", "detail": "unsupported artifact kind"}
    if entry.get("status") == "failed":
        return {**base, "status": "failed", "detail": "source backup failed"}
    if entry.get("status") != "ok" or not isinstance(name, str):
        return {**base, "status": "skipped", "detail": f"backup status {entry.get('status')}"}
    try:
        _artifact_name(entry)
    except BackupError:
        return {**base, "status": "failed", "detail": "artifact identity mismatch"}
    path = backup_dir / name
    if path.is_symlink() or not path.is_file():
        return {**base, "status": "failed", "detail": "artifact missing"}
    digest, size = _file_digest(path)
    if digest != entry.get("sha256") or size != entry.get("bytes"):
        return {**base, "status": "failed", "detail": "checksum or size mismatch"}
    if kind not in {"core", "project"}:
        return {**base, "status": "ok", "detail": "checksum only"}
    if type(entry.get("tables")) is not int or entry["tables"] < 0:
        return {**base, "status": "failed", "detail": "expected table count is unconfirmed"}
    try:
        return {**base, **_restore_entry(backup, path, kind, entry)}
    except BackupError as error:
        return {**base, "status": "failed", "detail": backup.redactor.scrub(str(error))}


_SCRATCH_CORE_ROLE_SQL = (
    "CREATE ROLE omnia_core_runtime NOLOGIN NOSUPERUSER NOBYPASSRLS "
    "NOCREATEDB NOCREATEROLE NOREPLICATION NOINHERIT"
)


def _restore_entry(
    backup: CellBackup,
    path: Path,
    kind: str,
    entry: Mapping[str, Any],
) -> dict[str, Any]:
    pool = ScratchPool(backup.runner)
    try:
        scratch_volume = pool.volume_name()
        created = backup.runner.run(
            ["volume", "create", "--label", _SCRATCH_LABEL, scratch_volume],
            timeout=_QUERY_TIMEOUT,
        )
        if not created.ok:
            return {"status": "failed", "detail": "scratch volume creation failed"}
        prepared = backup.prepare_scratch_volume(pool, scratch_volume)
        if prepared is not None:
            return {"status": "failed", "detail": prepared.detail}
        dump_format = _dump_format(path, entry)
        # Explicit GRANTED BY postgres requires the original bootstrap OID 10,
        # not a later superuser of the same name. Keep initdb's standard DBs too.
        server = backup.start_scratch_server(pool, scratch_volume)
        if isinstance(server, DumpOutcome):
            return {"status": "failed", "detail": server.detail}
        admin = (
            backup.create_scratch_restore_admin(server) if dump_format == "plain" else "postgres"
        )
        connection = backup.scratch_connection(
            user=admin, database="template1" if dump_format == "plain" else "postgres",
        )
        if kind == "core" and dump_format == "custom":
            # pg_dump preserves ACL references but not cluster roles. This is
            # only a fresh isolated verifier, not a login/bootstrap assertion.
            role = backup.runner.run(
                ["exec", server, "psql", "-X", "-v", "ON_ERROR_STOP=1",
                 *backup.scratch_connection(), "-c", _SCRATCH_CORE_ROLE_SQL],
                timeout=_QUERY_TIMEOUT,
            )
            if not role.ok:
                return {"status": "failed", "detail": "scratch core ACL role creation failed"}
        source = (
            plain_restore_source(path, backup.out_dir)
            if dump_format == "plain" else nullcontext(path)
        )
        with source as restore_path:
            restored = backup.runner.run(
                ["exec", "--interactive", server, *_restore_command(dump_format, connection)],
                timeout=_DUMP_TIMEOUT,
                stdin_path=restore_path,
            )
        if not restored.ok:
            return {
                "status": "failed",
                "detail": _detail(backup.redactor, "restore failed", restored.stderr),
            }
        tables = backup.count_tables(["exec", server], backup.scratch_connection(user=admin))
        expected = entry.get("tables")
        if tables is None:
            return {"status": "failed", "detail": "table count unavailable"}
        if tables != expected:
            return {
                "status": "failed",
                "tables": tables,
                "expected_tables": expected,
                "detail": f"table count mismatch: restored {tables}, manifest {expected}",
            }
        return {"status": "ok", "tables": tables, "expected_tables": expected}
    finally:
        pool.release()


# A custom-format pg_dump starts with this magic; a plain script never does.
_CUSTOM_DUMP_MAGIC = b"PGDMP"
_SQL_WORD = re.compile(rb"[A-Za-z_][A-Za-z_0-9$]*")
_SQL_DOLLAR_QUOTE = re.compile(rb"\$(?:[A-Za-z_][A-Za-z_0-9]*)?\$")


def _bootstrap_role_span(prefix: bytes) -> tuple[int, int] | None:
    """Locate only a complete global-prefix CREATE ROLE postgres statement.

    This is a conservative lexer, not a SQL rewriter. Stop at database/COPY SQL
    or psql commands other than dump restrict markers. Ambiguous quoting leaves
    the input untouched, so psql's normal strict failure remains authoritative.
    """
    tokens: list[tuple[str, bytes]] = []
    start: int | None = None
    index = 0
    while index < len(prefix):
        byte = prefix[index:index + 1]
        if byte in b" \t\r\n\f":
            index += 1
            continue
        if prefix.startswith(b"--", index):
            end = prefix.find(b"\n", index)
            index = len(prefix) if end < 0 else end + 1
            continue
        if prefix.startswith(b"/*", index):
            depth = 1
            index += 2
            while index < len(prefix) and depth:
                if prefix.startswith(b"/*", index):
                    depth += 1
                    index += 2
                elif prefix.startswith(b"*/", index):
                    depth -= 1
                    index += 2
                else:
                    index += 1
            if depth:
                return None
            continue
        if byte == b"\\":
            end = prefix.find(b"\n", index)
            end = len(prefix) if end < 0 else end
            if tokens or re.fullmatch(rb"\\(?:un)?restrict[ \t]+[A-Za-z0-9]+[ \t\r]*",
                                      prefix[index:end]) is None:
                return None
            index = end
            continue
        if start is None:
            start = index
        if byte in {b"'", b'"'}:
            quote = byte
            value = bytearray()
            index += 1
            while index < len(prefix):
                current = prefix[index:index + 1]
                if current == b"\\" and quote == b"'":
                    return None  # Escape-string/settings ambiguity: never guess.
                if current == quote:
                    if prefix[index + 1:index + 2] == quote:
                        value.extend(quote)
                        index += 2
                        continue
                    index += 1
                    break
                value.extend(current)
                index += 1
            else:
                return None
            tokens.append(("quoted" if quote == b'"' else "opaque", bytes(value)))
            continue
        dollar = _SQL_DOLLAR_QUOTE.match(prefix, index)
        if dollar:
            delimiter = dollar.group()
            end = prefix.find(delimiter, index + len(delimiter))
            if end < 0:
                return None
            index = end + len(delimiter)
            tokens.append(("opaque", b""))
            continue
        word = _SQL_WORD.match(prefix, index)
        if word:
            tokens.append(("word", word.group().lower()))
            index += len(word.group())
            continue
        if byte == b";":
            if (len(tokens) == 3 and tokens[:2] == [("word", b"create"), ("word", b"role")]
                    and tokens[2] in {("word", b"postgres"), ("quoted", b"postgres")}):
                assert start is not None
                return start, index + 1
            global_statement = (
                bool(tokens) and tokens[0][0] == "word"
                and (tokens[0][1] in {
                    b"set", b"alter", b"grant", b"revoke", b"comment", b"security", b"select",
                }
                     or tokens[:2] == [("word", b"create"), ("word", b"role")])
            )
            if not global_statement:
                return None
            tokens.clear()
            start = None
        else:
            tokens.append(("other", byte))
        index += 1
    return None


@contextmanager
def plain_restore_source(path: Path, directory: Path) -> Iterator[Path]:
    """Skip one known bootstrap duplicate; preserve the archive and all SQL errors."""
    with path.open("rb") as source:
        prefix = source.read(4 * 1024 * 1024)
        span = _bootstrap_role_span(prefix)
        if span is None:
            yield path
            return
        start, end = span
        blank = bytes(byte if byte in b"\r\n" else 32 for byte in prefix[start:end])
        with tempfile.TemporaryDirectory(prefix="omnia-restore-", dir=directory) as folder:
            filtered = Path(folder) / "cluster.sql"
            with os.fdopen(_create_private_file(filtered), "wb") as target:
                target.write(prefix[:start] + blank + prefix[end:])
                shutil.copyfileobj(source, target, _CHUNK_BYTES)
            yield filtered


def _dump_format(path: Path, entry: Mapping[str, Any]) -> DumpFormat:
    """The recorded format wins; a backup written before it existed is sniffed."""
    recorded = entry.get("format")
    if recorded == "custom" or recorded == "plain":
        return recorded
    with path.open("rb") as handle:
        return "custom" if handle.read(len(_CUSTOM_DUMP_MAGIC)) == _CUSTOM_DUMP_MAGIC else "plain"


def _restore_command(
    dump_format: DumpFormat, connection: Sequence[str] | None = None,
) -> list[str]:
    connection = CellBackup.scratch_connection() if connection is None else connection
    if dump_format == "custom":
        return ["pg_restore", "--clean", "--if-exists", *connection]
    return ["psql", "-X", "-v", "ON_ERROR_STOP=1", "-q", *connection, "-f", "-"]


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #


def _image(explicit: str | None, variable: str, label: str) -> str:
    value = (explicit or os.environ.get(variable) or "").strip()
    if not value:
        raise BackupError(
            f"{label} is required: pass it explicitly or set {variable} "
            "(a digest-pinned reference, never a floating tag)"
        )
    return _require_name(value, _IMAGE_RE, label)


def _prepare_out_dir(path: Path) -> Path:
    path.mkdir(mode=_DIR_MODE, parents=True, exist_ok=True)
    os.chmod(path, _DIR_MODE)
    return path


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Project Cell logical backup")
    commands = parser.add_subparsers(dest="command", required=True)

    backup = commands.add_parser("backup", help="dump every Project Cell database")
    backup.add_argument("--state-root", required=True, type=Path)
    backup.add_argument("--out", required=True, type=Path)
    backup.add_argument("--postgres-image", default=None)
    backup.add_argument("--helper-image", default=None)
    backup.add_argument("--docker", default="docker")
    backup.add_argument(
        "--resolve-docker-sources-with-sudo", action="store_true",
        help="canonical controller-user caller: fixed sudo realpath metadata resolver",
    )
    backup.add_argument(
        "--local-scope",
        action="store_true",
        help="local Docker coverage only; full collector must close external obligations",
    )

    verify = commands.add_parser("verify", help="prove the dumps in a backup directory restore")
    verify.add_argument("--backup", required=True, type=Path)
    verify.add_argument("--postgres-image", default=None)
    verify.add_argument("--docker", default="docker")
    return parser


def main(argv: Sequence[str] | None = None, *, runner: DockerRunner | None = None) -> int:
    args = _build_parser().parse_args(argv)
    try:
        docker = runner if runner is not None else DockerRunner(args.docker)
        postgres_image = _image(args.postgres_image, "CELL_POSTGRES_IMAGE", "postgres image")
        if args.command == "backup":
            if not args.state_root.is_dir():
                raise BackupError(f"state root is not a directory: {args.state_root}")
            manifest, code = run_backup(
                state_root=args.state_root,
                out_dir=_prepare_out_dir(args.out),
                postgres_image=postgres_image,
                helper_image=_image(args.helper_image, "CELL_BACKUP_IMAGE", "helper image"),
                runner=docker,
                local_scope=args.local_scope,
                platform_census_json=os.environ.get("BACKUP_PLATFORM_CENSUS_JSON"),
                resolve_docker_sources_with_sudo=args.resolve_docker_sources_with_sudo,
            )
            _write_private_json(args.out / "MANIFEST.json", manifest)
            totals = manifest["totals"]
            _log(
                f"{totals['ok']} ok, {totals['busy']} busy, "
                f"{totals['deferred_active_generation']} deferred, {totals['failed']} failed; "
                f"{totals['bytes'] / 1024 / 1024:.1f} MiB in {args.out}"
            )
            return code
        report, code = run_verify(
            backup_dir=args.backup, postgres_image=postgres_image, runner=docker
        )
        totals = report["totals"]
        _log(
            f"verified {totals['ok']}/{totals['entries']} artifacts, "
            f"{totals['skipped']} skipped, {totals['failed']} failed"
        )
        return code
    except BackupError as error:
        _log(f"ERROR: {error}")
        return 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())

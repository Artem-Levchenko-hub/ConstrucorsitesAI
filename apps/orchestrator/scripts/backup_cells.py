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
import subprocess
import tarfile
import tempfile
import threading
import time
from collections.abc import Mapping, Sequence
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
                watchdog.cancel()
                if process.stdout is not None:
                    process.stdout.close()
                if process.poll() is None:  # pragma: no cover - defensive
                    process.kill()
                    process.wait()
            errors.seek(0)
            stderr = errors.read(_STDERR_TAIL_BYTES)
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
            payload = json.load(handle)
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


def list_volumes(runner: DockerRunner) -> list[str]:
    result = runner.run(["volume", "ls", "--format", "{{.Name}}"], timeout=_QUERY_TIMEOUT)
    if not result.ok:
        raise BackupError("docker volume ls failed")
    return [line.strip() for line in result.text().splitlines() if line.strip()]


def production_workspaces(state_root: Path) -> dict[str, str]:
    """Production workspace id -> project id, from the publication journals."""
    found: dict[str, str] = {}
    root = state_root / "cell-publications"
    if not root.is_dir():
        return found
    for entry in sorted(root.iterdir()):
        payload = _read_json(entry / "publication.json")
        if payload is None:
            continue
        workspace_id = payload.get("production_workspace_id")
        project_id = payload.get("project_id")
        if isinstance(workspace_id, str) and _UUID_RE.fullmatch(workspace_id):
            found[workspace_id] = project_id if isinstance(project_id, str) else entry.name
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
        workspace = payload.get("workspace") if payload else None
        if not isinstance(workspace, dict):
            continue
        workspace_id = workspace.get("workspace_id")
        if isinstance(workspace_id, str) and _UUID_RE.fullmatch(workspace_id):
            records[workspace_id] = workspace
    return records


def _active_database_volume(state_root: Path, workspace_id: str, default: str) -> str:
    """Restoration activates a ``-db-<hex>`` volume; the controller records it."""
    payload = _read_json(state_root / "project-machines" / workspace_id / "docker.json")
    active = payload.get("active_database_volume") if payload else None
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


def _project_id(workspace: Mapping[str, Any], published: str | None) -> str | None:
    value = workspace.get("project_id")
    if isinstance(value, str) and _UUID_RE.fullmatch(value):
        return value
    return published


def build_inventory(
    state_root: Path,
    volumes: Sequence[str],
    redactor: SecretRedactor,
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
    for workspace_id, workspace in sorted(_state_records(state_root).items()):
        stem = UUID(workspace_id).hex
        targets: list[DatabaseTarget] = []
        core_volume = core.pop(stem, None)
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
        if owned:
            active = _active_database_volume(
                state_root, workspace_id, f"omnia-machine-{stem}-app-postgres-data"
            )
            if active in owned:
                password = _credential(
                    state_root
                    / "project-machines"
                    / "project-postgres-secrets"
                    / f"{workspace_id}.json"
                )
                redactor.add(password)
                targets.append(
                    DatabaseTarget(
                        kind="project",
                        volume=active,
                        container=f"omnia-machine-{stem}-project-postgres",
                        source_dir=_PROJECT_SOURCE_DIR,
                        password=password,
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
        inventory.plans.append(
            WorkspacePlan(
                workspace_id=workspace_id,
                role="production" if workspace_id in published else "editor",
                project_id=_project_id(workspace, published.get(workspace_id)),
                deferred=workspace.get("active_generation_run_id") is not None,
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
            # Unix sockets are disabled on the project server and the app holds
            # full admin rights, so only a cluster-wide dump is complete.
            path = self.out_dir / f"{workspace_id}-project.sql"
            command = [
                "pg_dumpall",
                "-h",
                "127.0.0.1",
                "-U",
                "postgres",
                f"--lock-wait-timeout={_LOCK_WAIT}",
            ]
            connection = ["-h", "127.0.0.1", "-U", "postgres", "-d", "postgres"]
        result = self.runner.run_to_file(
            ["exec", "-e", "PGPASSWORD", container, *command],
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
        tables = self.count_tables(["exec", "-e", "PGPASSWORD", container], connection, env=env)
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
        result = self.runner.run_to_file(
            [
                "exec",
                server,
                "pg_dumpall",
                "-U",
                "postgres",
                "-h",
                _SCRATCH_SOCKET,
                f"--lock-wait-timeout={_LOCK_WAIT}",
            ],
            path=destination,
            timeout=_DUMP_TIMEOUT,
        )
        if not result.ok or result.size_bytes == 0:
            return DumpOutcome(
                status="failed",
                method="scratch_copy",
                file=destination.name,
                bytes=result.size_bytes,
                detail=_detail(self.redactor, "scratch dump failed", result.stderr),
            )
        tables = self.count_tables(["exec", server], self.scratch_connection())
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

    def start_scratch_server(self, pool: ScratchPool, scratch_volume: str) -> str | DumpOutcome:
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
                _SCRATCH_SERVER_SCRIPT,
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
                ["exec", name, "pg_isready", "-U", "postgres", "-h", _SCRATCH_SOCKET],
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
    def scratch_connection() -> list[str]:
        return ["-U", "postgres", "-h", _SCRATCH_SOCKET, "-d", "postgres"]

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


def run_backup(
    *,
    state_root: Path,
    out_dir: Path,
    postgres_image: str,
    helper_image: str,
    runner: DockerRunner,
) -> tuple[dict[str, Any], int]:
    redactor = SecretRedactor()
    inventory = build_inventory(state_root, list_volumes(runner), redactor)
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
    manifest: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "created_at": _iso_now(),
        "postgres_image": postgres_image,
        "helper_image": helper_image,
        "state_root": str(state_root),
        "state_archive": state_archive,
        "workspaces": workspaces,
        "orphans": inventory.orphans,
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
    return manifest, _exit_code(counts)


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
        "totals": {"entries": len(results), **counts},
    }
    if counts["failed"]:
        return report, 1
    return report, 2 if counts["skipped"] else 0


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
    if entry.get("status") != "ok" or not isinstance(name, str):
        return {**base, "status": "skipped", "detail": f"backup status {entry.get('status')}"}
    if _ARTIFACT_RE.fullmatch(name) is None:
        return {**base, "status": "failed", "detail": "unsafe artifact name"}
    path = backup_dir / name
    if path.is_symlink() or not path.is_file():
        return {**base, "status": "failed", "detail": "artifact missing"}
    digest, size = _file_digest(path)
    if digest != entry.get("sha256") or size != entry.get("bytes"):
        return {**base, "status": "failed", "detail": "checksum or size mismatch"}
    if kind not in {"core", "project"}:
        return {**base, "status": "ok", "detail": "checksum only"}
    try:
        return {**base, **_restore_entry(backup, path, kind, entry)}
    except BackupError as error:
        return {**base, "status": "failed", "detail": backup.redactor.scrub(str(error))}


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
        server = backup.start_scratch_server(pool, scratch_volume)
        if isinstance(server, DumpOutcome):
            return {"status": "failed", "detail": server.detail}
        restored = backup.runner.run(
            ["exec", "--interactive", server, *_restore_command(_dump_format(path, entry))],
            timeout=_DUMP_TIMEOUT,
            stdin_path=path,
        )
        if not restored.ok:
            return {
                "status": "failed",
                "detail": _detail(backup.redactor, "restore failed", restored.stderr),
            }
        tables = backup.count_tables(["exec", server], backup.scratch_connection())
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


def _dump_format(path: Path, entry: Mapping[str, Any]) -> DumpFormat:
    """The recorded format wins; a backup written before it existed is sniffed."""
    recorded = entry.get("format")
    if recorded == "custom" or recorded == "plain":
        return recorded
    with path.open("rb") as handle:
        return "custom" if handle.read(len(_CUSTOM_DUMP_MAGIC)) == _CUSTOM_DUMP_MAGIC else "plain"


def _restore_command(dump_format: DumpFormat) -> list[str]:
    if dump_format == "custom":
        return ["pg_restore", "--clean", "--if-exists", *CellBackup.scratch_connection()]
    # pg_dumpall recreates the bootstrap role, so a harmless "already exists" is
    # expected on restore: the table count, not psql's exit code, is the verdict.
    return ["psql", "-X", "-q", *CellBackup.scratch_connection(), "-f", "-"]


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

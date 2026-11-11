"""Prove source parity for one dormant failed fast_check and optionally record it."""

from __future__ import annotations

import argparse
import ctypes
import hashlib
import importlib.util
import json
import os
import re
import shutil
import signal
import stat
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from urllib.request import ProxyHandler, build_opener
from uuid import UUID, uuid4

_CHECKOUT = Path("/opt/omnia")
_DATA = "/var/lib/postgresql/data"
_DOCKER_VOLUMES = Path("/var/lib/docker/volumes")
_COPY_LIMIT = 128 * 1024**2
_CATALOG_LIMIT = 128 * 1024
_SOURCE_LIMIT = 2 * 1024**2
_RENAME_NOREPLACE = 1
_HEX = re.compile(r"[a-f0-9]{64}")
_VOLUME = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9_.-]{1,127}")


class RecoveryError(RuntimeError):
    """Only static reason codes may appear in operator output."""


def _need(condition, reason):
    if not condition:
        raise RecoveryError(reason)


def _digest(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    ).hexdigest()


def _decode(raw):
    def unique(pairs):
        result = dict(pairs)
        _need(len(result) == len(pairs), "duplicate_json_key")
        return result

    return json.loads(raw, object_pairs_hook=unique)


def _require_live_release(release_sha):
    checkout = _CHECKOUT.resolve(strict=True)
    script = Path(__file__).resolve(strict=True)
    _need(script == checkout / "infra/release" / script.name, "release_script_path")
    # The live environment is used only for application settings. Do not let it
    # select a different Git directory, work tree, index, or executable.
    git = ["/usr/bin/git", "-c", f"safe.directory={checkout}", "-C", str(checkout)]

    def command(*args):
        return subprocess.run(
            [*git, *args], env={"PATH": "/usr/bin:/bin", "GIT_CONFIG_NOSYSTEM": "1"},
            check=True, capture_output=True, timeout=10,
        ).stdout

    _need(command("rev-parse", "HEAD").decode().strip() == release_sha, "release_checkout")
    _need(command("show", f"HEAD:{script.relative_to(checkout)}") == script.read_bytes(),
          "release_script_bytes")
    command("diff", "--quiet", "--no-ext-diff", "--no-textconv", "HEAD", "--",
            "apps/orchestrator/src", "infra/release/generation-publication-drain.py")
    with build_opener(ProxyHandler({})).open("http://127.0.0.1:8003/health", timeout=5) as reply:
        health = _decode(reply.read(8193))
    _need(health.get("status") == "ok" and health.get("release_sha") == release_sha,
          "release_health")


def _release_gate():
    spec = importlib.util.spec_from_file_location(
        "source_guard_recovery_gate", Path(__file__).with_name("generation-publication-drain.py")
    )
    gate = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = gate
    spec.loader.exec_module(gate)
    return gate


@dataclass(frozen=True)
class _Snapshot:
    raw: tuple[bytes, ...]
    identity: dict[str, str]
    source: str
    database: str
    database_identity: str
    operation_sha256: str
    fencing_epoch: int


def _snapshot(gate, state_root, workspace_id, operation_id, uid):
    from yleum_orchestrator.services.cell_state import CellWorkspaceState
    from yleum_orchestrator.services.machine_startup import (
        source_guard_operation_sha256,
    )

    directory = state_root.parent / "project-machines" / str(workspace_id)
    paths = (state_root / f"{workspace_id}.json", directory / "machine.json",
             directory / "docker.json", directory / "migration-sources.json")
    raw = tuple(gate._secure_bytes(path, uid, 0o600) for path in paths)
    envelope, machine, metadata, archive = map(_decode, raw)
    _need(set(envelope) == {"version", "workspace"}
          and type(envelope["version"]) is int and envelope["version"] == 1, "cell_schema")
    cell = CellWorkspaceState.from_dict(envelope["workspace"])
    _need(cell.workspace_id == workspace_id and machine.get("workspace_id") == str(workspace_id)
          and cell.project_id is not None and cell.owner_id is not None
          and cell.resource_names is not None and cell.resource_names.namespace == "prod"
          and cell.phase == "completed" and cell.fencing_epoch > 0
          and cell.active_generation_run_id is None and cell.active_generation_fencing_epoch is None
          and all(row.status in {"completed", "failed", "cancelled"} for row in cell.operations),
          "cell_not_dormant")
    rows = machine["operations"]
    _need(type(rows) is dict and all(
        type(row) is dict and row.get("state") in {"completed", "failed", "cancelled", "timed_out"}
        for row in rows.values()
    ), "machine_not_dormant")
    operation_sha = source_guard_operation_sha256(operation_id, rows.get(str(operation_id)))
    _need("startup_recovery_barrier" not in metadata
          and metadata.get("restore_in_progress", False) is False
          and metadata.get("quiesce_state") in {None, "complete"}, "backend_not_dormant")
    source = metadata.get("active_code_volume") or cell.resource_names.workspace_volume
    database = (metadata.get("active_database_volume")
                or f"omnia-machine-{workspace_id.hex}-app-postgres-data")
    identity = {"omnia.workspace_id": str(workspace_id), "omnia.project_id": str(cell.project_id),
                "omnia.owner_id": str(cell.owner_id)}
    database_identity = archive["database_identity"]
    _need(type(source) is str and _VOLUME.fullmatch(source) is not None
          and type(database) is str and _VOLUME.fullmatch(database) is not None
          and source != database and archive.get("database_volume") == database
          and type(database_identity) is str and _HEX.fullmatch(database_identity) is not None,
          "archive_volume_identity")
    return _Snapshot(raw, identity, source, database, database_identity, operation_sha,
                     cell.fencing_epoch)


def _volume_identity(client, name, identity):
    attrs = client.volumes.get(name).attrs
    mountpoint = _DOCKER_VOLUMES / name / "_data"
    _need(attrs.get("Name") == name and attrs.get("Driver") == "local"
          and attrs.get("Scope") == "local" and not attrs.get("Options")
          and attrs.get("Mountpoint") == str(mountpoint) and attrs.get("CreatedAt")
          and all((attrs.get("Labels") or {}).get(key) == value for key, value in identity.items())
          and mountpoint.resolve(strict=True) == mountpoint, "volume_identity")
    # Include physical incarnation; Docker CreatedAt alone has second precision.
    info = mountpoint.lstat()
    birth = subprocess.run(
        ["/usr/bin/stat", "--printf=%w", "--", str(mountpoint)],
        check=True, capture_output=True, text=True, timeout=5,
    ).stdout
    _need(stat.S_ISDIR(info.st_mode) and birth and birth != "-", "volume_incarnation")
    return _digest({"docker": attrs, "mountpoint": [info.st_dev, info.st_ino, birth]})


def _detached_volumes(client, snapshot):
    measured = tuple(_volume_identity(client, name, snapshot.identity)
                     for name in (snapshot.source, snapshot.database))
    # Inspect matching holders, including bind mounts of a volume or its ancestors.
    def holds(mount, name):
        if mount.get("Name") == name:
            return True
        path = mount.get("Source")
        if mount.get("Type") != "bind" or not isinstance(path, str):
            return False
        original = _DOCKER_VOLUMES / name / "_data"
        bound = Path(path)
        return (bound.is_relative_to(original)
                or (original.is_relative_to(bound) and mount.get("RW") is not False))

    for container in client.containers.list(all=True, sparse=True):
        if not any(holds(mount, name) for mount in container.attrs["Mounts"]
                   for name in (snapshot.source, snapshot.database)):
            continue
        container.reload()
        state = container.attrs["State"]["Status"]
        restart = container.attrs["HostConfig"]["RestartPolicy"]["Name"]
        if state in {"created", "exited", "dead"} and restart == "no":
            continue
        for mount in container.attrs["Mounts"]:
            _need(not holds(mount, snapshot.database), "original_database_has_holder")
            _need(not holds(mount, snapshot.source) or mount.get("RW") is False,
                  "original_source_has_writer")
    return measured


def _local_image(client, image):
    _need(re.fullmatch(r"[^@\s]+@sha256:[a-f0-9]{64}", image) is not None, "postgres_image_pin")
    observed = client.images.get(image)
    _need(image in (observed.attrs.get("RepoDigests") or [])
          and re.fullmatch(r"sha256:[a-f0-9]{64}", observed.id) is not None
          and set(observed.attrs["Config"].get("Volumes") or {}) <= {_DATA}, "postgres_image_local")
    return observed.id


def _remove_scratch(client, stem, containers, volume):
    from docker.errors import NotFound

    labels = {"yleum.scratch": stem}
    failed = False
    for name in (*containers, volume):
        is_volume = name == volume
        get = client.volumes.get if is_volume else client.containers.get
        try:
            resource = get(name)
            actual = resource.attrs.get("Labels") if is_volume else resource.labels
            _need(actual == labels if is_volume else (actual or {}).get("yleum.scratch") == stem,
                  "scratch_authority")
            if is_volume:
                resource.remove()
            else:
                resource.remove(force=True, v=False)
            get(name)
        except NotFound:
            continue
        except Exception:
            failed = True
        else:
            failed = True
    if failed:
        # Opaque, script-generated scratch stem for exact manual cleanup only.
        print(json.dumps({"scratch_cleanup_pending": stem}), file=sys.stderr, flush=True)
        raise RecoveryError("scratch_removal_unconfirmed")


def _read_copy_catalog(client, image, original):
    from docker.types import LogConfig

    from yleum_orchestrator.services.applied_migration_sources import CATALOG_SQL
    from yleum_orchestrator.services.restoration_database import TRUSTED_ADMIN_PGOPTIONS

    mountpoint = _DOCKER_VOLUMES / original / "_data"
    measured = subprocess.run(
        ["/usr/bin/du", "-sb", "--", str(mountpoint)],
        check=True, capture_output=True, timeout=20,
    )
    _need(0 < int(measured.stdout.split()[0]) < _COPY_LIMIT
          and shutil.disk_usage(mountpoint).free > 2 * 1024**3, "database_copy_bound")
    stem = "yleum-source-recovery-" + uuid4().hex
    print(json.dumps({"scratch": stem}), file=sys.stderr, flush=True)
    scratch, copy_name, server_name = stem + "-db", stem + "-copy", stem + "-postgres"
    labels = {"yleum.scratch": stem}
    common = {
        "labels": labels, "network_mode": "none", "read_only": True, "privileged": False,
        "security_opt": ["no-new-privileges:true"], "cap_drop": ["ALL"],
        "mem_limit": "256m", "memswap_limit": "256m", "nano_cpus": 250_000_000,
        "pids_limit": 64, "tmpfs": {"/tmp": "size=32m,mode=1777"},
        "log_config": LogConfig(type="none"),
    }

    def create(name, shell, user, mounts, **kwargs):
        container = client.containers.create(
            image, ["-ec", shell], name=name, entrypoint=["/bin/sh"], user=user,
            volumes={volume: {"bind": path, "mode": "rw" if rw else "ro"}
                     for volume, path, rw in mounts}, **common, **kwargs,
        )
        container.reload()
        actual = {(item.get("Name"), item.get("Destination"), item.get("RW"))
                  for item in container.attrs["Mounts"] if item.get("Type") != "tmpfs"}
        host = container.attrs["HostConfig"]
        _need(actual == set(mounts) and host["NetworkMode"] == "none"
              and host["LogConfig"]["Type"] == "none" and host["ReadonlyRootfs"] is True
              and host["Privileged"] is False, "scratch_isolation")
        container.start()
        return container

    try:
        volume = client.volumes.create(name=scratch, driver="local", labels=labels)
        _need(volume.attrs.get("Labels") == labels, "scratch_authority")
        copier = create(
            copy_name,
            "test -s /source/PG_VERSION; "
            "test ! -e /source/standby.signal; test ! -e /source/recovery.signal; "
            f"timeout -k 5 25 cp -a /source/. {_DATA}/; rm -f {_DATA}/postgresql.auto.conf",
            "0:0", [(original, "/source", False), (scratch, _DATA, True)],
            cap_add=["CHOWN", "DAC_OVERRIDE", "FOWNER"],
        )
        _need(copier.wait(timeout=30)["StatusCode"] == 0, "database_copy_failed")
        server = create(
            server_name,
            "printf '%s\\n' 'local all all trust' > /tmp/recovery-hba.conf\n"
            f"exec timeout -k 5 90 postgres -D {_DATA} -c config_file=/dev/null "
            "-c listen_addresses='' "
            "-c unix_socket_directories=/tmp -c hba_file=/tmp/recovery-hba.conf",
            "postgres", [(scratch, _DATA, True)], environment={"PGDATA": _DATA},
        )
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            ready = server.exec_run(["pg_isready", "-h", "/tmp", "-d", "postgres"], user="postgres")
            if ready.exit_code == 0:
                break
            time.sleep(0.5)
        else:
            raise RecoveryError("database_copy_not_ready")
        # Low-level exec gives bounded streaming and a verifiable exit status.
        execution = client.api.exec_create(
            server.id, ["timeout", "10", "psql", "-X", "-w", "-qAt", "-v", "ON_ERROR_STOP=1",
                        "-h", "/tmp", "-U", "postgres", "-d", "postgres", "-c", CATALOG_SQL],
            user="postgres", environment={"PGOPTIONS": TRUSTED_ADMIN_PGOPTIONS},
        )["Id"]
        output = bytearray()
        stream = client.api.exec_start(execution, stream=True)
        try:
            for chunk in stream:
                _need(len(output) + len(chunk) <= _CATALOG_LIMIT, "database_catalog_bound")
                output.extend(chunk)
        finally:
            stream.close()
        _need(client.api.exec_inspect(execution)["ExitCode"] == 0, "database_catalog_failed")
        return _decode(output)
    finally:
        _remove_scratch(client, stem, (server_name, copy_name), scratch)


def _source_digest(root, migration):
    directory, filename = migration.split("/")
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
    root_fd = os.open(root, flags)
    try:
        dir_fd = os.open(directory, flags, dir_fd=root_fd)
        try:
            fd = os.open(filename, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC,
                         dir_fd=dir_fd)
            with os.fdopen(fd, "rb") as source:
                info = os.fstat(source.fileno())
                _need(stat.S_ISREG(info.st_mode) and info.st_nlink == 1
                      and info.st_size <= _SOURCE_LIMIT, "source_file_authority")
                content = source.read(_SOURCE_LIMIT + 1)
                _need(len(content) == info.st_size, "source_file_changed")
                return hashlib.sha256(content).hexdigest()
        finally:
            os.close(dir_fd)
    finally:
        os.close(root_fd)


def _receipt(snapshot, workspace_id, operation_id, measured, catalog):
    from yleum_orchestrator.services.machine_startup import SourceGuardRecoveryReceipt

    _need(type(catalog) is dict and set(catalog) == {
        "database_identity", "journal", "has_relations", "applied",
    } and catalog["database_identity"] == snapshot.database_identity
          and type(catalog["journal"]) is bool and type(catalog["has_relations"]) is bool
          and type(catalog["applied"]) is dict
          and (catalog["journal"] or (not catalog["has_relations"] and not catalog["applied"])),
          "database_catalog_identity")
    applied = catalog["applied"]
    _need(len(applied) <= 256, "source_file_count_bound")
    source_root = _DOCKER_VOLUMES / snapshot.source / "_data"
    for name, digest in applied.items():
        _need(re.fullmatch(r"drizzle/[^/\\\x00]+\.sql", name) is not None
              and type(digest) is str and _HEX.fullmatch(digest) is not None,
              "database_catalog_entry")
        _need(_source_digest(source_root, name) == digest, "applied_source_mismatch")
    receipt = SourceGuardRecoveryReceipt(
        version=1, workspace_id=workspace_id,
        project_id=UUID(snapshot.identity["omnia.project_id"]),
        owner_id=UUID(snapshot.identity["omnia.owner_id"]), operation_id=operation_id,
        operation_sha256=snapshot.operation_sha256, fencing_epoch=snapshot.fencing_epoch,
        source_volume=snapshot.source, database_volume=snapshot.database,
        database_identity=snapshot.database_identity, source_volume_identity=measured[0],
        database_volume_identity=measured[1], applied_sql_sha256=_digest(applied),
    )
    return receipt, len(applied)


def _validate_receipt(state_root, workspace_id, receipt, uid, gid):
    from yleum_orchestrator.services.machine_startup import (
        validate_source_guard_recovery,
    )

    pid = os.fork()
    if pid == 0:
        try:
            signal.signal(signal.SIGALRM, signal.SIG_DFL)
            signal.alarm(10)
            os.setgroups([])
            os.setgid(gid)
            os.setuid(uid)
            validate_source_guard_recovery(state_root, workspace_id, receipt)
        except BaseException:
            os._exit(1)
        os._exit(0)
    _, status = os.waitpid(pid, 0)
    _need(status == 0, "controller_history_not_clear")


def _publish_noreplace(directory_fd, temporary, destination):
    # One Linux rename publishes a single-link receipt. A crash cannot leave
    # the durable name sharing an inode with a staging hardlink.
    libc = ctypes.CDLL(None, use_errno=True)
    renameat2 = getattr(libc, "renameat2", None)
    _need(renameat2 is not None, "receipt_noreplace_unavailable")
    renameat2.argtypes = [
        ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint,
    ]
    renameat2.restype = ctypes.c_int
    if renameat2(directory_fd, os.fsencode(temporary), directory_fd, os.fsencode(destination),
                 _RENAME_NOREPLACE):
        error = ctypes.get_errno()
        raise OSError(error, os.strerror(error))


def _record_receipt(gate, path, receipt, uid, gid):
    from yleum_orchestrator.services.machine_startup import SourceGuardRecoveryReceipt

    def existing():
        try:
            data = gate._secure_bytes(path, uid, 0o600)
        except FileNotFoundError:
            return False
        previous = SourceGuardRecoveryReceipt.model_validate_json(data)
        _need(path.lstat().st_gid == gid
              and previous.fencing_epoch <= receipt.fencing_epoch
              and previous.model_copy(update={"fencing_epoch": receipt.fencing_epoch}) == receipt,
              "receipt_collision")
        return True

    directory_fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        info = os.fstat(directory_fd)
        _need(info.st_uid == uid and info.st_gid == gid and stat.S_IMODE(info.st_mode) == 0o700,
              "receipt_directory_authority")
        if existing():
            os.fsync(directory_fd)
            return "already_recorded"
        temporary = ".source-guard-" + uuid4().hex
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
                     0o600, dir_fd=directory_fd)
        renamed = False
        try:
            with os.fdopen(fd, "wb") as stream:
                os.fchown(stream.fileno(), uid, gid)
                os.fchmod(stream.fileno(), 0o600)
                stream.write(receipt.model_dump_json().encode())
                stream.flush()
                os.fsync(stream.fileno())
            try:
                _publish_noreplace(directory_fd, temporary, path.name)
                renamed = True
                result = "recorded"
            except FileExistsError:
                _need(existing(), "receipt_collision")
                result = "already_recorded"
        finally:
            if not renamed:
                os.unlink(temporary, dir_fd=directory_fd)
        os.fsync(directory_fd)
        return result
    finally:
        os.close(directory_fd)


def _receipt_location(state_root, workspace_id, operation_id, uid):
    from yleum_orchestrator.services.machine_startup import source_guard_recovery_path

    path = source_guard_recovery_path(state_root, workspace_id, operation_id)
    directory = path.parent.lstat()
    _need(stat.S_ISDIR(directory.st_mode) and directory.st_uid == uid
          and stat.S_IMODE(directory.st_mode) == 0o700, "receipt_directory_authority")
    return path, directory.st_gid


def _recover_locked(gate, client, state_root, workspace_id, operation_id, uid, release_sha, image,
                    *, record):
    path, gid = _receipt_location(state_root, workspace_id, operation_id, uid)
    snapshot = _snapshot(gate, state_root, workspace_id, operation_id, uid)
    measured = _detached_volumes(client, snapshot)
    catalog = _read_copy_catalog(client, image, snapshot.database)
    receipt, count = _receipt(snapshot, workspace_id, operation_id, measured, catalog)
    _require_live_release(release_sha)
    _need(_detached_volumes(client, snapshot) == measured, "physical_volumes_changed")
    _need(_snapshot(gate, state_root, workspace_id, operation_id, uid).raw == snapshot.raw,
          "controller_state_changed")
    _validate_receipt(state_root, workspace_id, receipt, uid, gid)
    if record:
        return _record_receipt(gate, path, receipt, uid, gid), count
    return "verified", count


def _uuid(text):
    try:
        result = UUID(text)
    except ValueError:
        result = None
    if result is None or str(result) != text:
        raise argparse.ArgumentTypeError("expected a canonical UUID")
    return result


def _interrupted(_signum, _frame):
    raise RecoveryError("interrupted")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace-id", required=True, type=_uuid)
    parser.add_argument("--operation-id", required=True, type=_uuid)
    parser.add_argument("--release-sha", required=True)
    parser.add_argument("--record", action="store_true", help="publish verified receipt")
    args = parser.parse_args()
    sys.dont_write_bytecode = True
    stage = "live_release"
    try:
        _need(sys.platform == "linux" and os.geteuid() == 0, "requires_linux_root")
        _need(re.fullmatch(r"[a-f0-9]{40}", args.release_sha) is not None, "release_sha_format")
        signal.signal(signal.SIGTERM, _interrupted)
        _require_live_release(args.release_sha)
        stage = "controller_configuration"
        gate = _release_gate()
        controller_dir = _CHECKOUT.resolve() / "apps/orchestrator"
        state_parent = gate.live_publication_root(expected_cwd=controller_dir).parent
        import docker

        from yleum_orchestrator.core.config import get_settings
        from yleum_orchestrator.services.cell_state import CellStateStore

        settings = get_settings()
        state_root = CellStateStore(settings.cell_state_path).root
        _need(Path.cwd() == controller_dir
              and state_root == state_parent / "project-cells"
              and settings.docker_host in {"unix:///var/run/docker.sock", "unix:///run/docker.sock"},
              "controller_host_authority")
        client = docker.DockerClient(base_url=settings.docker_host, timeout=10)
        try:
            _need(client.info().get("DockerRootDir") == str(_DOCKER_VOLUMES.parent),
                  "docker_root_authority")
            image = _local_image(client, settings.cell_postgres_image)
            stage = "workspace_lock"
            with gate._quiescence_lock(state_parent, str(args.workspace_id)) as uid:
                stage = "source_guard_proof"
                result, count = _recover_locked(gate, client, state_root, args.workspace_id,
                                                args.operation_id, uid, args.release_sha, image,
                                                record=args.record)
        finally:
            client.close()
    except (Exception, KeyboardInterrupt) as exc:
        reason = str(exc) if isinstance(exc, RecoveryError) else type(exc).__name__
        print(json.dumps({"source_guard_recovery": "refused", "stage": stage, "reason": reason}))
        return 1
    print(json.dumps({"source_guard_recovery": result, "applied_migrations": count}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Accept an already decrypted bundle in an explicitly disposable Docker engine.

No decryption, collector, SSH, live configuration discovery, or application boot.
All extracted data stays in the new private work directory for operator review.
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import importlib.util
import json
import os
import re
import shutil
import tarfile
import time
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path, PurePosixPath
from types import SimpleNamespace
from uuid import uuid4

REQUIRED = {
    "platform-omnia.sql.gz", "projects-omnia_users.sql.gz", "projects-src.tgz",
    "minio-data.tgz", "runtime-config.tgz", "cells.tgz", "peers.tgz",
    "platform-globals.sql.gz", "projects-globals.sql.gz",
}
LABEL = "yleum.offline-restore"


class AcceptanceError(RuntimeError):
    """Fixed nonsecret failure code; never include SQL or Docker stderr."""


def need(condition, reason):
    if not condition:
        raise AcceptanceError(reason)


def relative_name(name):
    need(bool(name) and "\\" not in name and ":" not in name, "unsafe_archive_path")
    path = PurePosixPath(name)
    need(not path.is_absolute() and ".." not in path.parts, "unsafe_archive_path")
    return path


def safe_extract(source, target):
    """Reject links altogether; manually create regular files exclusively."""
    target.mkdir(mode=0o700)
    seen = set()
    with tarfile.open(source, "r:*") as archive:
        for member in archive:
            path = relative_name(member.name)
            need(member.isdir() or member.isfile(), "unsupported_archive_entry")
            if path == PurePosixPath("."):
                need(member.isdir(), "unsafe_archive_root")
                continue
            need(path not in seen, "duplicate_archive_entry")
            seen.add(path)
            destination = target.joinpath(*path.parts)
            destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            if member.isdir():
                destination.mkdir(mode=0o700, exist_ok=True)
            else:
                stream = archive.extractfile(member)
                need(stream is not None, "archive_stream_missing")
                with stream, private_file(destination) as sink:
                    shutil.copyfileobj(stream, sink, 1024 * 1024)


def private_file(path):
    flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY | getattr(os, "O_NOFOLLOW", 0)
    return os.fdopen(os.open(path, flags, 0o600), "wb")


def sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify_checksums(directory, required=REQUIRED):
    checksums = directory / "SHA256SUMS"
    need(checksums.is_file() and not checksums.is_symlink(), "checksums_missing")
    listed = set()
    for line in checksums.read_text(encoding="utf-8").splitlines():
        match = re.fullmatch(r"([0-9a-fA-F]{64}) [ *](.+)", line)
        need(match is not None, "invalid_checksum_line")
        path = relative_name(match[2])
        need(path != PurePosixPath(".") and str(path) not in listed, "duplicate_checksum")
        listed.add(str(path))
        payload = directory.joinpath(*path.parts)
        need(payload.is_file() and not payload.is_symlink(), "checksum_payload_missing")
        need(sha256(payload) == match[1].lower(), "checksum_mismatch")
    need(set(required) <= listed, "required_checksum_missing")
    # Every executable restore input, including required globals, is bound.
    for payload in directory.iterdir():
        if payload.is_file() and payload.name.endswith((".gz", ".tgz", ".sql")):
            need(payload.name in listed, "unlisted_payload")
    return len(listed)


def require_sandbox(acknowledged, postgres_image, minio_image):
    need(acknowledged, "disposable_sandbox_ack_required")
    host = os.environ.get("DOCKER_HOST", "")
    need(host.startswith(("unix:///", "npipe://")), "explicit_local_docker_host_required")
    need(not os.environ.get("DOCKER_CONTEXT"), "docker_context_must_be_unset")
    for image in (postgres_image, minio_image):
        if image is not None:
            need(re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:/-]*@sha256:[a-f0-9]{64}", image),
                 "digest_pinned_image_required")


class OwnedDocker:
    """Add a run UUID label and guard destructive operations by exact ownership."""
    def __init__(self, backend, run_id):
        self.backend, self.run_id = backend, run_id
        self.containers, self.volumes = set(), set()

    def owned(self, kind, name):
        names = self.volumes if kind == "volume" else self.containers
        need(name in names, "foreign_scratch_resource")
        result = self.backend.run(
            [kind, "inspect", "--format", '{{json .Labels}}' if kind == "volume"
             else '{{json .Config.Labels}}', name], timeout=30)
        if not result.ok:
            listed = self.backend.run(
                [kind, "ls", *(["--all"] if kind == "container" else []),
                 "--format", "{{.Name}}" if kind == "volume" else "{{.Names}}"], timeout=30)
            need(listed.ok and name not in listed.text().splitlines(), "ownership_unconfirmed")
            return False
        labels = json.loads(result.stdout)
        need(isinstance(labels, dict) and labels.get(LABEL) == self.run_id,
             "ownership_label_mismatch")
        return True

    def prepare(self, arguments):
        args = list(arguments)
        need(bool(args), "docker_command_required")
        if args[0] == "run" or args[:2] == ["volume", "create"]:
            container = args[0] == "run"
            if container:
                need("--network" in args and args[args.index("--network") + 1] == "none",
                     "network_isolation_required")
                need("--name" in args, "scratch_name_required")
                name = args[args.index("--name") + 1]
                for index, value in enumerate(args):
                    if value == "--volume":
                        need(args[index + 1].split(":")[0] in self.volumes,
                             "foreign_volume_mount")
            else:
                name = args[-1]
            need(re.fullmatch(r"omnia-backup-scratch-[a-f0-9]{32}", name),
                 "scratch_name_required")
            kind = "container" if container else "volume"
            listed = self.backend.run(
                [kind, "ls", *(["--all"] if container else []),
                 "--format", "{{.Names}}" if container else "{{.Name}}"], timeout=30)
            need(listed.ok and name not in listed.text().splitlines(), "scratch_name_collision")
            (self.containers if container else self.volumes).add(name)
            offset = 1 if container else 2
            args[offset:offset] = ["--label", f"{LABEL}={self.run_id}"]
            if container:
                args[1:1] = ["--pull", "never"]
        elif args[0] == "exec":
            self.owned("container", self.exec_target(args))
        elif args[0] in {"stop", "rm", "restart"}:
            self.owned("container", args[-1])
        elif args[:2] == ["volume", "rm"]:
            self.owned("volume", args[-1])
        elif args[0] not in {"inspect", "ps"} and args[:2] not in (
                ["container", "ls"], ["volume", "ls"], ["image", "inspect"]):
            raise AcceptanceError("unsupported_docker_operation")
        return args

    @staticmethod
    def exec_target(args):
        """Parse only exec switches used by the existing offline restore helpers."""
        values = {"--user": "postgres", "-u": "postgres",
                  "-e": "PGOPTIONS", "--env": "PGOPTIONS"}
        index = 1
        while index < len(args) and args[index].startswith("-"):
            option = args[index]
            if option in {"--interactive", "-i"}:
                index += 1
                continue
            need(option in values, "unsupported_exec_option")
            need(index + 1 < len(args) and args[index + 1] == values[option],
                 "invalid_exec_option_value")
            index += 2
        need(index + 1 < len(args) and bool(args[index + 1])
             and not args[index + 1].startswith("-"), "exec_target_and_command_required")
        return args[index]

    def run(self, args, **kwargs):
        return self.backend.run(self.prepare(args), **kwargs)

    def run_to_file(self, args, **kwargs):
        return self.backend.run_to_file(self.prepare(args), **kwargs)

    def pipe(self, first, second, **kwargs):
        return self.backend.pipe(self.prepare(first), self.prepare(second), **kwargs)


def restore_sql(cells, runner, directory, image, name, dump, globals_dump):
    need(globals_dump is not None and globals_dump.is_file() and not globals_dump.is_symlink(),
         "sql_globals_required")
    pool = cells.ScratchPool(runner)
    backup = cells.CellBackup(runner=runner, out_dir=directory, postgres_image=image,
                              helper_image=image, redactor=cells.SecretRedactor())
    admin = "offline_" + uuid4().hex
    try:
        volume = pool.volume_name()
        need(runner.run(["volume", "create", "--label", cells._SCRATCH_LABEL, volume],
                        timeout=60).ok, "sql_volume_failed")
        need(backup.prepare_scratch_volume(pool, volume) is None, "sql_prepare_failed")
        server = backup.start_scratch_server(pool, volume, bootstrap_user=admin)
        need(isinstance(server, str), "sql_start_failed")

        def sql(command=None, path=None, database="postgres"):
            args = ["exec", *(["--interactive"] if path else []), server,
                    "psql", "-X", "-qAt", "-v", "ON_ERROR_STOP=1",
                    *backup.scratch_connection(user=admin, database=database)]
            args += ["-f", "-"] if path else ["-c", command]
            result = runner.run(args, timeout=7200, stdin_path=path)
            need(result.ok, "sql_restore_or_query_failed")
            return result.text().strip()

        for source, target_db in ((globals_dump, "postgres"), (dump, name)):
            if target_db == name:
                sql('CREATE DATABASE "' + name + '"')
            expanded = directory / (source.name + ".expanded.sql")
            with gzip.open(source, "rb") as stream, private_file(expanded) as sink:
                shutil.copyfileobj(stream, sink, 1024 * 1024)
            sql(path=expanded, database=target_db)
        counts_file = directory / (name + "-counts.sql")
        counts_sql = """CREATE TEMP TABLE offline_counts(n bigint);
SELECT format('INSERT INTO offline_counts SELECT count(*) FROM %I.%I',
              n.nspname, c.relname)
FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
WHERE c.relkind IN ('r','p') AND n.nspname NOT IN ('pg_catalog','information_schema')
  AND n.nspname !~ '^pg_' AND NOT c.relispartition
\\gexec
SELECT json_build_object('tables',count(*),'rows',coalesce(sum(n),0)) FROM offline_counts;
"""
        with private_file(counts_file) as sink:
            sink.write(counts_sql.encode())
        counts = json.loads(sql(path=counts_file, database=name))
        need(set(counts) == {"tables", "rows"} and all(type(v) is int and v >= 0
             for v in counts.values()), "sql_counts_invalid")
        probe = "offline_probe_" + uuid4().hex
        sql(f'CREATE SCHEMA "{probe}"; CREATE TABLE "{probe}".proof(n int); '
            f'INSERT INTO "{probe}".proof VALUES (1)', database=name)
        need(runner.run(["restart", server], timeout=120).ok, "sql_restart_failed")
        for _ in range(60):
            if runner.run(["exec", server, "pg_isready", "-U", admin, "-h",
                           cells._SCRATCH_SOCKET], timeout=10).ok:
                break
            time.sleep(1)
        else:
            raise AcceptanceError("sql_restart_not_ready")
        need(sql(f'SELECT sum(n) FROM "{probe}".proof', database=name) == "1",
             "sql_restart_persistence_failed")
        sql(f'INSERT INTO "{probe}".proof VALUES (2)', database=name)
        need(sql(f'SELECT sum(n) FROM "{probe}".proof', database=name) == "3",
             "sql_post_restart_write_failed")
        return {"status": "passed", **counts, "write_restart": True,
                "globals_restored": True}
    finally:
        pool.release()


def receipt():
    return {"version": 1, "status": "failed", "full_dr_verified": False,
            "ciphertext_restore_verified": False, "stages": {},
            "unverified": ["ciphertext_decryption", "minio_object_get",
                           "source_witness_comparison", "application_boot",
                           "external_integrations", "archive_environment_activation"]}


def load_runtime():
    spec = importlib.util.spec_from_file_location(
        "offline_runtime_backup", Path(__file__).with_name("runtime-backup.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--work-dir", type=Path, required=True)
    parser.add_argument("--cells-script", type=Path, required=True)
    parser.add_argument("--postgres-image", required=True)
    parser.add_argument("--minio-image", help="Reserved; MinIO GET remains a separate acceptance stage")
    parser.add_argument("--receipt", type=Path, required=True)
    parser.add_argument("--disposable-sandbox", action="store_true")
    args = parser.parse_args(argv)
    result = receipt()
    stage = "sandbox"
    try:
        require_sandbox(args.disposable_sandbox, args.postgres_image, args.minio_image)
        need(not args.receipt.exists() and not args.receipt.is_symlink(), "receipt_exists")
        need(args.receipt.parent.is_dir(), "receipt_parent_missing")
        stage = "extract"
        safe_extract(args.bundle, args.work_dir)
        result["stages"][stage] = {"status": "passed"}
        stage = "checksums"
        result["stages"][stage] = {"status": "passed",
                                   "files": verify_checksums(args.work_dir)}
        stage = "runtime_peers"
        safe_extract(args.work_dir / "cells.tgz", args.work_dir / "cells-unpacked")
        safe_extract(args.work_dir / "peers.tgz", args.work_dir / "peers-unpacked")
        for sums in args.work_dir.rglob("SHA256SUMS"):
            if sums.parent != args.work_dir:
                verify_checksums(sums.parent, required=())
        runtime = load_runtime()
        cells = runtime.load_cells(args.cells_script)
        runner = OwnedDocker(cells.DockerRunner(), uuid4().hex)
        cells.DockerRunner = lambda: runner
        need(runner.run(["image", "inspect", args.postgres_image], timeout=30).ok,
             "pinned_postgres_image_not_local")
        with open(os.devnull, "w") as silent, redirect_stdout(silent), redirect_stderr(silent):
            runtime.verify(cells, SimpleNamespace(
                core_cells=args.work_dir / "cells-unpacked/cells",
                peers=args.work_dir / "peers-unpacked", postgres_image=args.postgres_image))
        result["stages"][stage] = {"status": "passed"}
        for stage, name, filename in (
            ("platform_sql", "omnia", "platform-omnia.sql.gz"),
            ("projects_sql", "omnia_users", "projects-omnia_users.sql.gz"),
        ):
            globals_dump = args.work_dir / (stage.split("_")[0] + "-globals.sql.gz")
            result["stages"][stage] = restore_sql(
                cells, runner, args.work_dir, args.postgres_image, name,
                args.work_dir / filename, globals_dump)
        result["status"] = "passed_checked_scope"
    except Exception as error:  # noqa: BLE001 -- fail closed without leaking SQL or secrets
        result["stages"][stage] = {
            "status": "failed", "error": str(error) if isinstance(error, AcceptanceError)
            else type(error).__name__}
    try:
        with private_file(args.receipt) as sink:
            sink.write((json.dumps(result, indent=2, sort_keys=True) + "\n").encode())
    except OSError:
        print(json.dumps({"status": "failed", "stage": "receipt_write"}))
        return 1
    print(json.dumps({"status": result["status"], "receipt": str(args.receipt)}))
    return 0 if result["status"] == "passed_checked_scope" else 1


if __name__ == "__main__":
    raise SystemExit(main())

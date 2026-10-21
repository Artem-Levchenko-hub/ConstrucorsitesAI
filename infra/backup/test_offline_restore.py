"""Offline acceptance guards: no Docker daemon or production access required."""
import gzip
import hashlib
import importlib.util
import io
import json
import os
import sys
import tarfile
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

SCRIPT = Path(__file__).with_name("offline-restore.py")
spec = importlib.util.spec_from_file_location("offline_restore", SCRIPT)
module = importlib.util.module_from_spec(spec) if SCRIPT.exists() else None
if module:
    spec.loader.exec_module(module)


class OfflineTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(module, "offline acceptance runner is missing")
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def archive(self, name="bundle.tar", entries=None):
        path = self.root / name
        with tarfile.open(path, "w") as archive:
            for member, data in entries or []:
                archive.addfile(member, io.BytesIO(data))
        return path

    def regular(self, name, data=b"payload"):
        member = tarfile.TarInfo(name)
        member.size = len(data)
        return member, data

    def test_traversal_links_devices_and_duplicate_files_never_escape(self):
        for index, name in enumerate(("../escape", "/escape", "C:/escape", "a\\escape")):
            with self.subTest(name=name):
                path = self.archive(str(index), [self.regular(name)])
                with self.assertRaises(module.AcceptanceError):
                    module.safe_extract(path, self.root / ("out" + str(index)))
        for index, kind in enumerate((tarfile.SYMTYPE, tarfile.LNKTYPE, tarfile.CHRTYPE)):
            member = tarfile.TarInfo("link")
            member.type, member.linkname = kind, "../escape"
            path = self.archive("kind" + str(index), [(member, b"")])
            with self.assertRaises(module.AcceptanceError):
                module.safe_extract(path, self.root / ("kindout" + str(index)))
        path = self.archive(entries=[self.regular("same"), self.regular("same")])
        with self.assertRaises(module.AcceptanceError):
            module.safe_extract(path, self.root / "duplicate")
        self.assertFalse((self.root / "escape").exists())

    def test_existing_destination_is_never_overwritten(self):
        target = self.root / "out"
        target.mkdir()
        (target / "same").write_bytes(b"old")
        with self.assertRaises((module.AcceptanceError, FileExistsError)):
            module.safe_extract(self.archive(entries=[self.regular("same")]), target)
        self.assertEqual((target / "same").read_bytes(), b"old")

    def test_safe_nested_files_and_dot_directory(self):
        dot = tarfile.TarInfo(".")
        dot.type = tarfile.DIRTYPE
        path = self.archive(entries=[(dot, b""), self.regular("./cells/data", b"abc")])
        module.safe_extract(path, self.root / "out")
        self.assertEqual((self.root / "out/cells/data").read_bytes(), b"abc")

    def payload(self):
        names = (
            "platform-omnia.sql.gz", "projects-omnia_users.sql.gz", "projects-src.tgz",
            "minio-data.tgz", "runtime-config.tgz", "cells.tgz", "peers.tgz",
            "platform-globals.sql.gz", "projects-globals.sql.gz",
        )
        for name in names:
            (self.root / name).write_bytes(b"ok")
        digest = hashlib.sha256(b"ok").hexdigest()
        (self.root / "SHA256SUMS").write_text(
            "".join(f"{digest}  {name}\n" for name in names))

    def test_checksum_corruption_missing_component_and_unlisted_payload_fail(self):
        self.payload()
        self.assertEqual(module.verify_checksums(self.root), 9)
        (self.root / "platform-omnia.sql.gz").write_bytes(b"bad")
        with self.assertRaises(module.AcceptanceError):
            module.verify_checksums(self.root)
        (self.root / "platform-omnia.sql.gz").unlink()
        with self.assertRaises(module.AcceptanceError):
            module.verify_checksums(self.root)
        self.payload()
        (self.root / "extra-globals.sql.gz").write_bytes(b"unchecked")
        with self.assertRaises(module.AcceptanceError):
            module.verify_checksums(self.root)

    def test_missing_required_checksum_cannot_pass(self):
        self.payload()
        path = self.root / "SHA256SUMS"
        path.write_text("\n".join(path.read_text().splitlines()[1:]))
        with self.assertRaises(module.AcceptanceError):
            module.verify_checksums(self.root)

    def test_each_globals_payload_and_checksum_is_required(self):
        for name in ("platform-globals.sql.gz", "projects-globals.sql.gz"):
            with self.subTest(name=name):
                self.payload()
                sums = self.root / "SHA256SUMS"
                sums.write_text("\n".join(line for line in sums.read_text().splitlines()
                                          if not line.endswith(name)))
                (self.root / name).unlink()
                with self.assertRaisesRegex(module.AcceptanceError, "required_checksum_missing"):
                    module.verify_checksums(self.root)

    def test_sql_restore_requires_globals_before_creating_resources(self):
        for globals_path in (None, self.root / "missing-globals.sql.gz"):
            with self.subTest(globals_path=globals_path), self.assertRaisesRegex(
                    module.AcceptanceError, "sql_globals_required"):
                module.restore_sql(None, None, self.root, "unused", "omnia",
                                   self.root / "dump.sql.gz", globals_path)

    def test_actual_sql_restore_preserves_bootstrap_and_strict_globals(self):
        cells_path = SCRIPT.parents[2] / "apps/orchestrator/scripts/backup_cells.py"
        cells_spec = importlib.util.spec_from_file_location("offline_cells_fixture", cells_path)
        cells = importlib.util.module_from_spec(cells_spec)
        with patch.dict(sys.modules, {cells_spec.name: cells}):
            cells_spec.loader.exec_module(cells)

        class Runner:
            def __init__(self, invalid):
                self.invalid, self.calls, self.payloads, self.sum_queries = invalid, [], [], 0
                self.containers, self.volumes = set(), set()

            def run(self, args, *, timeout, stdin_path=None, **kwargs):
                self.calls.append(args)
                if args[:2] == ["volume", "create"]:
                    self.volumes.add(args[-1])
                if args[:2] == ["volume", "rm"]:
                    self.volumes.discard(args[-1])
                if args[0] == "run" and "--name" in args:
                    self.containers.add(args[args.index("--name") + 1])
                if args[0] == "rm":
                    self.containers.discard(args[-1])
                output = b""
                if stdin_path is not None:
                    payload = stdin_path.read_bytes()
                    self.payloads.append(payload)
                    self_outer.assertIn("ON_ERROR_STOP=1", args)
                    self_outer.assertTrue(args[args.index("-U") + 1].startswith("omnia_backup_"))
                    self_outer.assertNotIn(b"CREATE ROLE postgres;", payload)
                    if b"INVALID SQL;" in payload:
                        return cells.CommandResult(tuple(args), 1, b"", b"unrelated SQL error")
                    if stdin_path.name.endswith("-counts.sql"):
                        output = b'{"tables":1,"rows":2}'
                if "-c" in args and args[-1].startswith("SELECT sum(n)"):
                    self.sum_queries += 1
                    output = str(1 if self.sum_queries == 1 else 3).encode()
                return cells.CommandResult(tuple(args), 0, output, b"")

        self_outer = self
        for invalid in (False, True):
            with self.subTest(invalid=invalid):
                directory = self.root / str(invalid)
                directory.mkdir()
                globals_path, dump_path = directory / "globals.sql.gz", directory / "data.sql.gz"
                sql = (b"\\restrict Abc123\nCREATE ROLE postgres;\n"
                       b"ALTER ROLE postgres NOSUPERUSER NOLOGIN;\n"
                       b"CREATE ROLE reader;\nCREATE ROLE member;\n"
                       b"GRANT reader TO member GRANTED BY postgres;\n\\unrestrict Abc123\n")
                if invalid:
                    sql += b"INVALID SQL;\n"
                globals_path.write_bytes(gzip.compress(sql))
                dump_path.write_bytes(gzip.compress(b"CREATE TABLE t(n int); INSERT INTO t VALUES(1),(2);"))
                original = globals_path.read_bytes()
                runner = Runner(invalid)
                if invalid:
                    with self.assertRaisesRegex(module.AcceptanceError, "sql_restore_or_query_failed"):
                        module.restore_sql(cells, runner, directory, "unused", "omnia", dump_path, globals_path)
                    self.assertEqual(len(runner.payloads), 1)
                else:
                    result = module.restore_sql(cells, runner, directory, "unused", "omnia", dump_path, globals_path)
                    self.assertEqual(result, {"status": "passed", "tables": 1, "rows": 2,
                                              "write_restart": True, "globals_restored": True})
                self.assertIn(b"GRANTED BY postgres", runner.payloads[0])
                start = next(call for call in runner.calls if call[0] == "run" and "--detach" in call)
                self.assertIn("initdb --username=postgres ", start[-1])
                self.assertEqual(globals_path.read_bytes(), original)
                self.assertFalse(list(directory.glob("omnia-restore-*")))
                self.assertFalse(runner.containers or runner.volumes)

    def test_sandbox_and_local_explicit_host_and_pinned_image_required(self):
        image = "postgres@sha256:" + "a" * 64
        for ack, host, ref in ((False, "unix:///isolated.sock", image),
                               (True, "", image), (True, "ssh://production", image),
                               (True, "unix:///isolated.sock", "postgres:16")):
            with (self.subTest(ack=ack, host=host, ref=ref),
                  patch.dict(os.environ, {"DOCKER_HOST": host}),
                  self.assertRaises(module.AcceptanceError)):
                module.require_sandbox(ack, ref, None)

    def test_successful_plaintext_stages_do_not_claim_full_disaster_recovery(self):
        result = module.receipt()
        self.assertFalse(result["full_dr_verified"])
        self.assertFalse(result["ciphertext_restore_verified"])
        self.assertIn("minio_object_get", result["unverified"])

    def test_normal_digest_resolution_does_not_load_archive_verifier_or_fall_back(self):
        class Runner:
            def __init__(self, available):
                self.available, self.calls = available, []
            def run(self, args, **kwargs):
                self.calls.append(args)
                return SimpleNamespace(ok=self.available)
        for available in (True, False):
            runner = Runner(available)
            requested = "postgres@sha256:" + "a" * 64
            with patch.object(module, "load_image_provenance",
                              side_effect=AssertionError("fallback")):
                if available:
                    self.assertEqual(module.resolve_postgres_image(runner, requested, None),
                                     (requested, None))
                else:
                    with self.assertRaisesRegex(module.AcceptanceError,
                                                "pinned_postgres_image_not_local"):
                        module.resolve_postgres_image(runner, requested, None)
            self.assertEqual(runner.calls, [["image", "inspect", requested]])

    def test_explicit_archive_mapping_reaches_runtime_and_sql_and_retains_requested_pin(self):
        requested = "postgres@sha256:" + "a" * 64
        execution = "sha256:" + "b" * 64
        proof = {"status": "verified_explicit_archive_content_mapping",
                 "requested_image": requested, "execution_image": execution}
        backend = SimpleNamespace(run=lambda *_args, **_kwargs: SimpleNamespace(ok=True))
        cells = SimpleNamespace(DockerRunner=lambda: backend)
        observed = []
        runtime = SimpleNamespace(load_cells=lambda _path: cells,
                                  verify=lambda _cells, args: observed.append(args.postgres_image))
        verifier = SimpleNamespace(verify_local_image=lambda *_args: proof)
        report = self.root / "mapped-result.json"
        with (
            patch.dict(os.environ, {"DOCKER_HOST": "unix:///isolated.sock", "DOCKER_CONTEXT": ""}),
            patch.object(module, "safe_extract"),
            patch.object(module, "verify_checksums", return_value=9),
            patch.object(module, "load_runtime", return_value=runtime),
            patch.object(module, "load_image_provenance", return_value=verifier),
            patch.object(module, "restore_sql", return_value={"status": "passed"}) as restored,
            redirect_stdout(io.StringIO()),
        ):
            code = module.main(["--bundle", str(self.root / "unused.tgz"),
                                "--work-dir", str(self.root / "new"),
                                "--cells-script", str(self.root / "unused.py"),
                                "--postgres-image", requested,
                                "--postgres-image-archive", str(self.root / "image.tar"),
                                "--receipt", str(report), "--disposable-sandbox"])
        self.assertEqual(code, 0)
        self.assertEqual(observed, [execution])
        self.assertEqual([call.args[3] for call in restored.call_args_list], [execution, execution])
        result = json.loads(report.read_text())
        self.assertEqual(result["stages"]["postgres_image_provenance"], proof)
        self.assertFalse(result["full_dr_verified"])

    def test_guard_refuses_foreign_cleanup_and_network(self):
        class Backend:
            def __init__(self):
                self.calls = []
            def run(self, args, **kwargs):
                self.calls.append(args)
                raise AssertionError("unsafe operation reached Docker")
        backend = Backend()
        guarded = module.OwnedDocker(backend, "a" * 32)
        for args in (["rm", "--force", "live-db"],
                     ["run", "--name", "x", "--network", "bridge", "image"],
                     ["volume", "rm", "--force", "live-data"],
                     ["exec", "live-db", "psql"]):
            with self.assertRaises(module.AcceptanceError):
                guarded.run(args, timeout=1)
        self.assertEqual(backend.calls, [])

    def test_protocol_one_exec_preserves_pgoptions_and_reaches_owned_container(self):
        target = "omnia-backup-scratch-" + "c" * 32
        run_id = "a" * 32
        calls = []
        class Backend:
            def run(self, args, **kwargs):
                if args[:2] == ["container", "inspect"]:
                    return type("Result", (), {
                        "ok": True, "stdout": json.dumps({module.LABEL: run_id}).encode()})()
                calls.append((args, kwargs))
                return type("Result", (), {"ok": True, "stdout": b"2\n"})()
            def run_to_file(self, args, **kwargs):
                calls.append((args, kwargs))
                kwargs["path"].write_bytes(b"restored logical dump")
                return type("Result", (), {"ok": True})()
        runner = module.OwnedDocker(Backend(), run_id)
        runner.containers.add(target)
        # Exact cold-retained protocol=1 prefix used by CellBackup._scratch_attempt.
        prefix = ["exec", "--user", "postgres", "-e", "PGOPTIONS", target]
        dump_command = [*prefix, "pg_dumpall", "-U", "postgres", "-h", "/tmp",
                        "--lock-wait-timeout=30s"]
        destination = self.root / "dump.sql"
        environment = {"PGOPTIONS": "-c default_transaction_read_only=on"}
        runner.run_to_file(dump_command, path=destination, timeout=10, env=environment)
        self.assertEqual(destination.read_bytes(), b"restored logical dump")
        self.assertEqual(calls[0][0], dump_command)
        self.assertEqual(calls[0][1]["env"], environment)
        count_command = [*prefix, "psql", "-X", "-qAt", "-c", "SELECT 2"]
        self.assertEqual(runner.run(count_command, timeout=10, env=environment).stdout, b"2\n")
        self.assertEqual(calls[1][0], count_command)

    def test_exec_rejects_missing_unknown_options_and_foreign_target_without_docker(self):
        class Backend:
            def run(self, args, **kwargs):
                raise AssertionError("invalid exec reached Docker")
        runner = module.OwnedDocker(Backend(), "a" * 32)
        target = "omnia-backup-scratch-" + "c" * 32
        runner.containers.add(target)
        invalid = (
            ["exec"], ["exec", "--user"], ["exec", "--user", "-e", "PGOPTIONS", target],
            ["exec", "-e"], ["exec", "--interactive"], ["exec", target],
            ["exec", "--privileged", target, "psql"],
            ["exec", "--user", "root", target, "psql"],
            ["exec", "-e", "UNEXPECTED", target, "psql"],
            ["exec", "--user", "postgres", "-e", "PGOPTIONS", "live-db", "psql"],
        )
        for args in invalid:
            with self.subTest(args=args), self.assertRaises(module.AcceptanceError):
                runner.run(args, timeout=1)

    def test_nonbootstrap_role_collision_fails_and_owned_resources_are_removed(self):
        cells = module.load_runtime().load_cells(
            Path(__file__).resolve().parents[2] / "apps/orchestrator/scripts/backup_cells.py")
        class Backend:
            def __init__(self):
                self.resources = {"container": {}, "volume": {}}
                self.failed_sql = False
            def run(self, args, **kwargs):
                output, code = b"", 0
                if args[:2] in (["container", "ls"], ["volume", "ls"]):
                    output = "\n".join(self.resources[args[0]]).encode()
                elif len(args) > 1 and args[1] == "inspect":
                    labels = self.resources[args[0]].get(args[-1])
                    code = 0 if labels else 1
                    output = json.dumps(labels).encode()
                elif args[0] == "run" or args[:2] == ["volume", "create"]:
                    container = args[0] == "run"
                    name = args[args.index("--name") + 1] if container else args[-1]
                    labels = dict(args[i + 1].split("=", 1) for i, arg in enumerate(args)
                                  if arg == "--label")
                    self.resources["container" if container else "volume"][name] = labels
                    if container:
                        assert args[args.index("--network") + 1] == "none"
                        assert args[args.index("--pull") + 1] == "never"
                        if "--rm" in args:
                            del self.resources["container"][name]
                elif args[0] == "rm" or args[:2] == ["volume", "rm"]:
                    self.resources["container" if args[0] == "rm" else "volume"].pop(args[-1], None)
                elif "psql" in args and kwargs.get("stdin_path"):
                    assert "ON_ERROR_STOP=1" in args
                    payload = kwargs["stdin_path"].read_bytes()
                    assert b"CREATE ROLE postgres;" not in payload
                    assert payload.count(b"CREATE ROLE duplicate;") == 2
                    self.failed_sql = True
                    code = 1
                return cells.CommandResult(tuple(args), code, output,
                                           b"secret SQL role collision" if code else b"")
        backend = Backend()
        runner = module.OwnedDocker(backend, "b" * 32)
        dump = self.root / "database.sql.gz"
        roles = self.root / "globals.sql.gz"
        with gzip.open(roles, "wb") as sink:
            sink.write(b"CREATE ROLE postgres;\nCREATE ROLE duplicate;\nCREATE ROLE duplicate;\n")
        with gzip.open(dump, "wb") as sink:
            sink.write(b"CREATE TABLE t(n int);")
        with self.assertRaisesRegex(module.AcceptanceError, "^sql_restore_or_query_failed$"):
            module.restore_sql(cells, runner, self.root, "postgres@sha256:" + "a" * 64,
                               "omnia", dump, roles)
        self.assertTrue(backend.failed_sql)
        self.assertEqual(backend.resources, {"container": {}, "volume": {}})

    def test_guard_refuses_label_change_before_cleanup(self):
        class Backend:
            def run(self, args, **kwargs):
                return type("Result", (), {
                    "ok": True, "stdout": b'{"yleum.offline-restore":"foreign"}'})()
        guarded = module.OwnedDocker(Backend(), "a" * 32)
        guarded.containers.add("omnia-backup-scratch-" + "b" * 32)
        with self.assertRaisesRegex(module.AcceptanceError, "ownership_label_mismatch"):
            guarded.run(["rm", "--force", "omnia-backup-scratch-" + "b" * 32], timeout=1)

    def test_corrupt_bundle_receipt_is_failed_and_does_not_expose_payload(self):
        self.payload()
        (self.root / "platform-omnia.sql.gz").write_bytes(b"secret-private-sql")
        source = self.archive(entries=[self.regular(p.name, p.read_bytes())
                                      for p in self.root.iterdir() if p.is_file()])
        report = self.root / "result.json"
        stdout = io.StringIO()
        with patch.dict(os.environ, {"DOCKER_HOST": "unix:///isolated.sock",
                                    "DOCKER_CONTEXT": ""}), redirect_stdout(stdout):
            code = module.main(["--bundle", str(source),
                                "--work-dir", str(self.root / "fresh"),
                                "--cells-script", str(self.root / "unused.py"),
                                "--postgres-image", "postgres@sha256:" + "a" * 64,
                                "--receipt", str(report), "--disposable-sandbox"])
        self.assertEqual(code, 1)
        receipt = json.loads(report.read_text())
        self.assertEqual(receipt["status"], "failed")
        self.assertEqual(receipt["stages"]["checksums"]["error"], "checksum_mismatch")
        self.assertFalse(receipt["ciphertext_restore_verified"])
        self.assertFalse(receipt["full_dr_verified"])
        self.assertNotIn("secret-private-sql", report.read_text() + stdout.getvalue())


if __name__ == "__main__":
    unittest.main()

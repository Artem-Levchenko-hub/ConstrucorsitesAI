"""Run generated host backup payloads only, against private synthetic fixtures."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import threading
import unittest
from contextlib import closing
from pathlib import Path
from render_host_backup import render_file, render_payload

BASE = Path(__file__).resolve().parent
BASH = None
if os.name == "nt":
    candidate = Path.home() / ".cache/codex-runtimes/codex-primary-runtime/dependencies/native/git/usr/bin/sh.exe"
    if candidate.is_file():
        BASH = str(candidate)
if not BASH:
    BASH = shutil.which("bash") or shutil.which("sh")


def generated(name: str, target: str) -> str:
    if (name, target) == ("40-postgres.sh", "/usr/local/bin/max-backup.sh"):
        return render_file(BASE / "remote" / name)
    source = (BASE / "remote" / name).read_text(encoding="utf-8")
    match = re.search(r"cat > " + re.escape(target) + r" <<('?)(EOF)\1\n(.*?)\nEOF", source, re.S)
    if match is None:
        raise AssertionError("generated payload not found")
    if match[1]:
        return match[3] + "\n"
    rendered = subprocess.run(
        [BASH, "-s"], input="TARGET=commerce\nTARGET_WG=10.10.0.3\ncat <<EOF\n" + match[3] + "\nEOF\n",
        text=True, encoding="utf-8", capture_output=True, check=True,
        env={**os.environ, "PATH": str(Path(BASH).parent) + os.pathsep + os.environ["PATH"]},
    )
    return rendered.stdout


@unittest.skipUnless(BASH, "real Bash is required for generated payload contracts")
class HostBackupPipelineTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.folder = Path(self.temporary.name)
        self.root = self.folder / "backups"
        self.root.mkdir(mode=0o700)
        self.bin = self.folder / "bin"
        self.bin.mkdir()
        self.k3s = self.folder / "k3s-db"
        self.k3s.mkdir()
        with closing(sqlite3.connect(self.k3s / "state.db")) as database:
            database.execute("create table committed(value text)")
            database.execute("insert into committed values ('base')")
            database.commit()
        self.trace = self.folder / "trace"
        self.script = self.folder / "payload.sh"
        self.env = {**os.environ, "PATH": str(self.bin) + os.pathsep + str(Path(BASH).parent) + os.pathsep + os.environ["PATH"],
                    "TRACE": self.shell_path(self.trace), "PYTHONUTF8": "1"}
        self.stub("date", 'if [[ "$*" == *+%F* ]]; then echo 2026-10-21; else echo 2026-10-21T10:00:00Z; fi')
        self.stub("stat", 'echo "${FIXTURE_MODE:-700}"')
        self.stub("python3", "exec " + self.quote(self.shell_path(Path(sys.executable))) + ' "$@"')
        self.stub("install", 'mkdir -p -- "${@: -1}"')
        self.stub("realpath", 'readlink -f -- "${@: -1}"')
        self.stub("gzip", "python3 -c 'import gzip,sys;sys.stdout.buffer.write(gzip.compress(sys.stdin.buffer.read()))'")
        self.stub("du", 'echo "0 synthetic-backup"')
        self.stub("sudo", r'''printf '%s\0' "$@" >> "$TRACE.sudo"
case " $* " in
  *pg_dumpall*) [[ ${FAULT:-} != globals ]] || exit 7; echo synthetic-globals ;;
  *psql*)
    if [[ " $* " == *" -0 "* ]]; then
      printf 'fixturedb\0'
      [[ ${EDGE_DATABASES:-} != 1 ]] || printf 'space name\0odd/name\0name=host\0'
    else echo fixturedb; fi ;;
  *pg_dump*) echo synthetic-database; [[ ${FAULT:-} != dump ]] || exit 8 ;;
  *) exit 90 ;;
esac''')
        self.stub("tar", r'''printf '%s\0' "$@" >> "$TRACE.tar"
output=
staging=$2
while [[ $# -gt 0 ]]; do
  if [[ "$1" == -czf ]]; then output=$2; break; fi
  shift
done
[[ -n "$output" ]] || exit 91
printf synthetic-archive > "$output"
if [[ ${CAPTURE_K3S:-} != '' && "$output" == *k3s-state.tgz ]]; then
  python3 -c 'import shutil,sys;shutil.copyfile(sys.argv[1]+"/var/lib/rancher/k3s/server/db/state.db",sys.argv[2])' "$staging" "$CAPTURE_K3S"
fi
[[ ! ( ${FAULT:-} == k3s && "$output" == *k3s-state.tgz ) ]] || exit 9
[[ ! ( ${FAULT:-} == config && "$output" == *etc-max-studio.tgz ) ]] || exit 10''')
        self.stub("rsync", r'''printf '%s\0' "$@" > "$TRACE.rsync"; exit "${RSYNC_EXIT:-0}"''')

    @staticmethod
    def quote(value):
        return "'" + value.replace("'", "'\\''") + "'"

    @staticmethod
    def shell_path(path):
        if os.name != "nt":
            return str(path)
        value = path.absolute().as_posix()
        return "/" + value[0].lower() + value[2:]

    def stub(self, name, body):
        path = self.bin / name
        path.write_text("#!" + self.shell_path(Path(BASH)) + "\nset -euo pipefail\n" + body + "\n",
                        encoding="utf-8", newline="\n")
        path.chmod(0o700)

    def backup(self, invocation="1" * 32, **environment):
        body = generated("40-postgres.sh", "/usr/local/bin/max-backup.sh")
        body = body.replace("/var/backups/max-studio", self.shell_path(self.root))
        body = body.replace("/var/lib/rancher/k3s/server/db", self.shell_path(self.k3s))
        if os.name == "nt":
            # Windows Python cannot exec a POSIX stub; preserve the same argv
            # through Bash so this harness exercises its instrumented tar.
            body = body.replace("subprocess.run(['tar',", "subprocess.run([" + repr(BASH) +
                                ",'-c','exec tar \"$@\"','fixture-tar',")
        return self.run_payload(body, INVOCATION_ID=invocation, **environment)

    def run_payload(self, body, **environment):
        self.script.write_text(body, encoding="utf-8", newline="\n")
        return subprocess.run([BASH, self.shell_path(self.script)], env={**self.env, **environment},
                              capture_output=True, text=True, encoding="utf-8", timeout=30)

    def receipt(self, invocation):
        path = self.root / "2026-10-21" / invocation / "HOST-SNAPSHOT.json"
        self.assertTrue(path.is_file(), "actual generated job must retain an explicit scoped receipt")
        return json.loads(path.read_bytes())

    def test_same_day_runs_preserve_old_copy_and_have_separate_truthful_receipts(self):
        old = self.root / "2020-01-01"
        old.mkdir()
        marker = old / "keep.dump"
        marker.write_bytes(b"previous-copy")
        os.utime(old, (1, 1))
        for invocation in ("1" * 32, "2" * 32):
            result = self.backup(invocation)
            self.assertEqual(result.returncode, 0, result.stderr)
            receipt = self.receipt(invocation)
            self.assertEqual(receipt["status"], "complete")
            self.assertEqual(receipt["scope"], "host-artifact-snapshot")
            self.assertEqual(receipt["run_id"], invocation)
            self.assertFalse(receipt["restore_verified"])
            self.assertFalse(receipt["full_application_backup"])
            self.assertFalse(receipt["k3s_consistency_verified"])
        self.assertEqual(marker.read_bytes(), b"previous-copy")

    def test_no_systemd_invocation_uses_two_fresh_run_ids(self):
        for _ in range(2):
            result = self.backup("")
            self.assertEqual(result.returncode, 0, result.stderr)
        runs = list((self.root / "2026-10-21").iterdir())
        self.assertEqual(len(runs), 2)
        self.assertTrue(all(re.fullmatch(r"[0-9a-f]{32}", path.name) for path in runs))

    def test_replayed_or_malformed_invocation_never_overwrites_or_starts_postgres(self):
        self.assertEqual(self.backup().returncode, 0)
        self.receipt("1" * 32)
        original = (self.root / "2026-10-21" / ("1" * 32) / "HOST-SNAPSHOT.json").read_bytes()
        trace = self.trace.with_suffix(".sudo").read_bytes()
        for invocation in ("1" * 32, "../escape", "unknown", "F" * 32):
            with self.subTest(invocation=invocation):
                self.assertNotEqual(self.backup(invocation).returncode, 0)
                self.assertEqual(self.trace.with_suffix(".sudo").read_bytes(), trace)
        self.assertEqual((self.root / "2026-10-21" / ("1" * 32) / "HOST-SNAPSHOT.json").read_bytes(), original)

    def test_all_database_names_and_host_scope_are_preserved_without_path_interpretation(self):
        result = self.backup(EDGE_DATABASES="1")
        self.assertEqual(result.returncode, 0, result.stderr)
        receipt = self.receipt("1" * 32)
        self.assertEqual([row["name"] for row in receipt["databases"]],
                         ["fixturedb", "space name", "odd/name", "name=host"])
        self.assertEqual(len({row["file"] for row in receipt["databases"]}), 4)
        for row in receipt["databases"]:
            self.assertRegex(row["file"], r"^database-[0-9]{6}\.dump$")
            self.assertIn(row["file"], receipt["artifacts"])
        sudo = self.trace.with_suffix(".sudo").read_bytes().decode()
        self.assertIn("datistemplate", sudo)
        self.assertIn("datname<>'postgres'", sudo)
        self.assertIn("postgresql:///space%20name?host=/var/run/postgresql&user=postgres", sudo)
        self.assertIn("postgresql:///odd%2Fname?host=/var/run/postgresql&user=postgres", sudo)
        members = self.trace.with_suffix(".tar").read_bytes().split(b"\0")
        for member in (b"etc/max-studio", b"etc/wireguard", b"etc/ssh/sshd_config.d",
                       b"etc/ssh/authorized_keys.d", b"var/lib/rancher/k3s/server/token", b"etc/rancher/k3s"):
            self.assertIn(member, members)
        self.assertEqual(receipt["k3s_state"], "included")
        self.assertIn("globals.sql.gz", receipt["artifacts"])
        self.assertIn("k3s-state.tgz", receipt["artifacts"])
        self.assertIn("etc-max-studio.tgz", receipt["artifacts"])
        directory = self.root / "2026-10-21" / ("1" * 32)
        for name, mark in receipt["artifacts"].items():
            actual = (directory / name).read_bytes()
            self.assertEqual(mark, {"sha256": hashlib.sha256(actual).hexdigest(), "bytes": len(actual)})

    def test_dump_or_archive_failure_retains_evidence_and_never_reports_complete(self):
        for index, fault in enumerate(("globals", "dump", "k3s", "config"), start=3):
            invocation = str(index) * 32
            with self.subTest(fault=fault):
                result = self.backup(invocation, FAULT=fault)
                self.assertNotEqual(result.returncode, 0)
                receipt = self.receipt(invocation)
                self.assertEqual(receipt["status"], "failed")
                self.assertNotEqual(receipt["exit_code"], 0)
                self.assertNotIn(": complete", result.stdout)
                self.assertTrue(receipt["artifacts"])

    def test_online_k3s_snapshot_includes_committed_wal_without_checkpointing_live_database(self):
        saved = self.folder / "saved-k3s.db"
        live = sqlite3.connect(self.k3s / "state.db")
        self.addCleanup(live.close)
        live.execute("PRAGMA journal_mode=WAL")
        live.execute("PRAGMA wal_autocheckpoint=0")
        live.execute("insert into committed values ('only-in-wal')")
        live.commit()
        wal = self.k3s / "state.db-wal"
        before = wal.read_bytes()
        result = self.backup(CAPTURE_K3S=self.shell_path(saved))
        self.assertEqual(result.returncode, 0, result.stderr)
        with closing(sqlite3.connect(saved)) as restored:
            self.assertEqual(restored.execute("select value from committed order by rowid").fetchall(),
                             [("base",), ("only-in-wal",)])
            self.assertEqual(restored.execute("PRAGMA quick_check").fetchall(), [("ok",)])
        self.assertEqual(wal.read_bytes(), before, "backup must not checkpoint or rewrite live WAL")
        self.assertFalse(list((self.root / "2026-10-21").glob(".k3s-snapshot-*")))

    def test_online_k3s_snapshot_is_transactionally_consistent_with_concurrent_wal_writer(self):
        saved = self.folder / "concurrent-saved-k3s.db"
        stop, ready, backup_running = threading.Event(), threading.Event(), threading.Event()
        state_lock = threading.Lock()
        commits = {"during_backup": 0}
        errors = []

        def append_pair(connection):
            with connection:
                sequence = connection.execute("select value from sequence_state").fetchone()[0] + 1
                connection.executemany("insert into paired values (?, ?, ?)",
                                       [(sequence, half, f"commit-{sequence}") for half in (0, 1)])
                connection.execute("update sequence_state set value = ?", (sequence,))
            return sequence

        def write_live():
            try:
                with closing(sqlite3.connect(self.k3s / "state.db", timeout=1)) as writer:
                    writer.execute("PRAGMA wal_autocheckpoint=0")
                    while not stop.is_set():
                        sequence = append_pair(writer)
                        with state_lock:
                            if backup_running.is_set():
                                commits["during_backup"] += 1
                        if sequence >= 4:
                            ready.set()
                        stop.wait(0.002)
            except BaseException as error:
                errors.append(error)
            finally:
                ready.set()

        with closing(sqlite3.connect(self.k3s / "state.db", timeout=1)) as live:
            self.assertEqual(live.execute("PRAGMA journal_mode=WAL").fetchone(), ("wal",))
            live.execute("PRAGMA wal_autocheckpoint=0")
            live.execute("create table sequence_state(value integer not null)")
            live.execute("insert into sequence_state values (0)")
            live.execute("create table paired(sequence integer, half integer check(half in (0, 1)), "
                         "value text not null, primary key(sequence, half))")
            live.commit()
            worker = threading.Thread(target=write_live, name="synthetic-k3s-wal-writer", daemon=True)
            worker.start()
            try:
                self.assertTrue(ready.wait(5), "synthetic writer must commit before backup starts")
                self.assertFalse(errors, repr(errors))
                before = live.execute("select value from sequence_state").fetchone()[0]
                self.assertGreaterEqual(before, 4)
                backup_running.set()
                try:
                    result = self.backup(CAPTURE_K3S=self.shell_path(saved))
                finally:
                    backup_running.clear()
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertTrue(worker.is_alive(), "writer must remain active throughout backup")
                with state_lock:
                    self.assertGreater(commits["during_backup"], 0, "writer must commit while generated job runs")
                self.assertTrue(saved.is_file())
                self.assertFalse(Path(str(saved) + "-wal").exists())
                self.assertFalse(Path(str(saved) + "-shm").exists())
                with closing(sqlite3.connect(saved.as_uri() + "?mode=ro", uri=True)) as restored:
                    self.assertEqual(restored.execute("PRAGMA quick_check").fetchall(), [("ok",)])
                    captured = restored.execute("select value from sequence_state").fetchone()[0]
                    pairs = restored.execute("select sequence, half, value from paired order by sequence, half").fetchall()
                    self.assertEqual(pairs, [(sequence, half, f"commit-{sequence}")
                                             for sequence in range(1, captured + 1) for half in (0, 1)])
                self.assertGreaterEqual(captured, before, "snapshot must include already committed WAL rows")
                self.assertGreaterEqual(live.execute("select value from sequence_state").fetchone()[0], captured)
            finally:
                stop.set()
                worker.join(timeout=5)
                self.assertFalse(worker.is_alive(), "own writer must stop before fixture cleanup")
            self.assertFalse(errors, repr(errors))
            after = append_pair(live)
            self.assertGreater(after, captured, "source must accept another transaction after backup")
            self.assertEqual(live.execute("select count(*) from paired").fetchone()[0], after * 2)
        self.assertFalse(list((self.root / "2026-10-21").glob(".k3s-snapshot-*")))

    def test_renderer_rejects_missing_or_ambiguous_payloads(self):
        source = (BASE / "remote/40-postgres.sh").read_text(encoding="utf-8")
        self.assertTrue(render_payload(source).startswith("#!/usr/bin/env bash\n"))
        for invalid in ("", source + "\n" + source):
            with self.assertRaises(ValueError):
                render_payload(invalid)

    def test_unknown_or_corrupt_k3s_datastore_fails_instead_of_dropping_data(self):
        unexpected = self.k3s / "etcd"
        unexpected.mkdir()
        result = self.backup()
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.receipt("1" * 32)["status"], "failed")
        self.assertIn("unsupported K3s datastore", result.stderr)
        unexpected.rmdir()
        (self.k3s / "state.db").write_bytes(b"corrupt sqlite fixture")
        result = self.backup("2" * 32)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.receipt("2" * 32)["status"], "failed")
        self.assertFalse(list((self.root / "2026-10-21").glob(".k3s-snapshot-*")))

    def test_absent_k3s_is_explicit_and_unsafe_output_mode_stops_before_work(self):
        (self.k3s / "state.db").unlink()
        self.k3s.rmdir()
        result = self.backup()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.receipt("1" * 32)["k3s_state"], "not_present")
        self.assertNotIn("k3s-state.tgz", self.receipt("1" * 32)["artifacts"])
        trace = self.trace.with_suffix(".sudo").read_bytes()
        self.assertNotEqual(self.backup("2" * 32, FIXTURE_MODE="777").returncode, 0)
        self.assertEqual(self.trace.with_suffix(".sudo").read_bytes(), trace)

    def test_replica_uses_only_nondeleting_nonreplacing_transfer_and_known_host_keys(self):
        result = self.run_payload(generated("50-backup-sync.sh", "/usr/local/bin/max-backup-push.sh"))
        self.assertEqual(result.returncode, 0, result.stderr)
        arguments = self.trace.with_suffix(".rsync").read_bytes().decode().split("\0")[:-1]
        self.assertIn("--ignore-existing", arguments)
        self.assertFalse(any(arg.startswith("--delete") or arg in ("--remove-source-files", "--remove-sent-files") for arg in arguments))
        ssh = arguments[arguments.index("-e") + 1]
        for required in ("StrictHostKeyChecking=yes", "BatchMode=yes", "IdentitiesOnly=yes", "UpdateHostKeys=no",
                         "UserKnownHostsFile=/root/.ssh/known_hosts"):
            self.assertIn(required, ssh)
        self.assertNotIn("accept-new", ssh)
        self.assertIn("backupsync@10.10.0.3:/", arguments)

    def test_failed_replica_does_not_print_success(self):
        result = self.run_payload(generated("50-backup-sync.sh", "/usr/local/bin/max-backup-push.sh"), RSYNC_EXIT="23")
        self.assertEqual(result.returncode, 23)
        self.assertNotIn("ok", result.stdout)

    @unittest.skipIf(os.name == "nt", "real POSIX permission semantics require Linux")
    def test_real_private_directory_mode_is_enforced_before_postgres(self):
        (self.bin / "stat").unlink()
        self.root.chmod(0o777)
        result = self.backup()
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(self.trace.with_suffix(".sudo").exists())


if __name__ == "__main__":
    unittest.main()

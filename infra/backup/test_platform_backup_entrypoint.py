"""Private UTC day creation contract; no backup, service, database or model calls."""

from __future__ import annotations

import ast
import hashlib
import os
import shutil
import stat
import subprocess
import tempfile
import types
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
ENTRYPOINT = HERE / "platform-backup-entrypoint.sh"
ROOT_POSIX = os.name == "posix" and getattr(os, "geteuid", lambda: -1)() == 0


def inline_source():
    text = ENTRYPOINT.read_text(encoding="utf-8")
    prefix = "<<'PY_PRIVATE_DAY'\n"
    if text.count(prefix) != 1:
        raise AssertionError("one canonical private day helper required")
    return text.split(prefix, 1)[1].split("\nPY_PRIVATE_DAY\n", 1)[0]


def helper():
    module = types.ModuleType("private_day_contract")
    exec(compile(inline_source(), "<canonical-private-day>", "exec"), module.__dict__)
    return module


def stable_metadata(path):
    info = path.stat()
    return tuple(
        getattr(info, key)
        for key in (
            "st_dev",
            "st_ino",
            "st_uid",
            "st_gid",
            "st_mode",
            "st_nlink",
            "st_size",
            "st_mtime_ns",
            "st_ctime_ns",
        )
    )


class EntrypointContract(unittest.TestCase):
    def test_helper_precedes_all_expensive_collection_and_UTC_day_is_reused(self):
        text = ENTRYPOINT.read_text(encoding="utf-8")
        self.assertLess(
            text.index("backup_day=$(/usr/bin/python3"),
            text.index("export MINIO_BACKUP_IMAGE="),
        )
        self.assertLess(
            text.index("\n)\n"), text.index("/opt/omnia/infra/backup/backup-omnia.sh")
        )
        self.assertIn('dest="$backup_day/platform-encrypted/$name"', text)
        self.assertNotIn("$(date", text)
        self.assertIn("set -euo pipefail", text)

    def test_day_is_UTC_even_when_local_calendar_date_differs(self):
        at = datetime(2026, 10, 22, 2, 30, tzinfo=timezone(timedelta(hours=7)))
        self.assertEqual(helper().utc_day(at), "2026-10-21")

    def test_noncanonical_path_day_and_non_directory_identity_refuse(self):
        module = helper()
        for root, day in (
            ("relative", "2026-10-22"),
            ("/safe/../foreign", "2026-10-22"),
            ("/safe", "../foreign"),
            ("/safe", "2026-10-22/child"),
        ):
            with self.subTest(root=root, day=day), self.assertRaises(ValueError):
                module.prepare_day(root, day)
        for kind, uid, gid in (
            (stat.S_IFLNK, 0, 0),
            (stat.S_IFREG, 0, 0),
            (stat.S_IFDIR, 1000, 0),
            (stat.S_IFDIR, 0, 1000),
        ):
            row = types.SimpleNamespace(st_mode=kind | 0o755, st_uid=uid, st_gid=gid)
            with (
                self.subTest(kind=kind, uid=uid, gid=gid),
                self.assertRaises(RuntimeError),
            ):
                module.directory_identity(row)
        tree = ast.parse(inline_source())
        self.assertNotIn(
            "unlink",
            {node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)},
        )


@unittest.skipUnless(
    ROOT_POSIX, "requires isolated root-owned POSIX/GNU fixture; no production paths"
)
class PhysicalPrivateDay(unittest.TestCase):
    def setUp(self):
        # In CI/root validation this directory must be copied into a physical,
        # root-owned tree without group/world-writable ancestors (not /tmp).
        self.fixture = tempfile.TemporaryDirectory(prefix="private-day-", dir=HERE)
        self.addCleanup(self.fixture.cleanup)
        self.root = Path(self.fixture.name)
        os.chmod(self.root, 0o700)
        self.module = helper()
        self.day = "2026-10-22"

    def test_GNU_install_leading_directory_0755_RED_then_private_day_GREEN(self):
        installer = shutil.which("install")
        self.assertIsNotNone(installer)
        version = subprocess.run(
            [installer, "--version"], check=True, capture_output=True
        ).stdout
        self.assertIn(b"GNU coreutils", version)
        dest = self.root / self.day / "platform-encrypted" / "20261022-000500"
        subprocess.run(
            [
                "bash",
                "-c",
                'umask 077; "$1" -d -m 0700 "$2"',
                "fixture",
                installer,
                str(dest),
            ],
            check=True,
        )
        day = self.root / self.day
        self.assertEqual(stat.S_IMODE(day.stat().st_mode), 0o755)
        marker = dest / "ciphertext.cms"
        marker.write_bytes(b"disposable pre-existing ciphertext fixture")
        before = stable_metadata(marker)
        digest = hashlib.sha256(marker.read_bytes()).hexdigest()
        self.assertEqual(self.module.prepare_day(self.root, self.day), str(day))
        self.assertEqual(stat.S_IMODE(day.stat().st_mode), 0o700)
        self.assertEqual(hashlib.sha256(marker.read_bytes()).hexdigest(), digest)
        self.assertEqual(stable_metadata(marker), before)

    def test_fresh_day_is_0700_and_existing_0700_is_idempotent(self):
        day = self.root / self.day
        self.assertFalse(day.exists())
        self.module.prepare_day(self.root, self.day)
        original = day.stat()
        self.assertEqual(stat.S_IMODE(original.st_mode), 0o700)
        self.module.prepare_day(self.root, self.day)
        self.assertEqual(day.stat(), original)
        self.assertEqual(list(day.iterdir()), [])

    def test_symlink_day_never_changes_target_or_contents(self):
        target = self.root / "target"
        target.mkdir(mode=0o755)
        os.chmod(target, 0o755)
        marker = target / "keep"
        marker.write_bytes(b"unchanged")
        before = (stable_metadata(target), stable_metadata(marker), marker.read_bytes())
        (self.root / self.day).symlink_to(target, target_is_directory=True)
        with self.assertRaises((RuntimeError, OSError)):
            self.module.prepare_day(self.root, self.day)
        self.assertEqual(
            (stable_metadata(target), stable_metadata(marker), marker.read_bytes()),
            before,
        )
        self.assertTrue((self.root / self.day).is_symlink())

    def test_foreign_day_owner_is_denied_without_chmod(self):
        day = self.root / self.day
        day.mkdir(mode=0o755)
        os.chmod(day, 0o755)
        os.chown(day, 65534, 65534)
        before = day.stat()
        try:
            with self.assertRaises(RuntimeError):
                self.module.prepare_day(self.root, self.day)
            self.assertEqual(day.stat(), before)
        finally:
            os.chown(day, 0, 0)  # Disposable test fixture only.

    def test_unsafe_parent_or_day_mode_never_gets_repaired(self):
        for target in (self.root, self.root / self.day):
            if target != self.root:
                target.mkdir(mode=0o700)
            for mode in (0o750, 0o777):
                os.chmod(target, mode)
                with (
                    self.subTest(target=target.name, mode=mode),
                    self.assertRaises(RuntimeError),
                ):
                    self.module.prepare_day(self.root, self.day)
                self.assertEqual(stat.S_IMODE(target.stat().st_mode), mode)
            os.chmod(target, 0o700)

    def test_symlink_or_foreign_parent_is_denied_before_day_creation(self):
        alias = self.root / "alias"
        alias.symlink_to(self.root, target_is_directory=True)
        with self.assertRaises((RuntimeError, OSError)):
            self.module.prepare_day(alias, self.day)
        self.assertFalse((self.root / self.day).exists())
        alias.unlink()  # Disposable test fixture only.
        os.chown(self.root, 65534, 65534)
        try:
            with self.assertRaises(RuntimeError):
                self.module.prepare_day(self.root, self.day)
            self.assertFalse((self.root / self.day).exists())
        finally:
            os.chown(self.root, 0, 0)


if __name__ == "__main__":
    unittest.main()

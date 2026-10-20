"""Retention policy must preserve every old copy during recipient rotation."""

from __future__ import annotations

import os
import subprocess
import tempfile
import time
import unittest
from pathlib import Path

SCRIPT = Path(__file__).with_name("retention.sh")
BASH = os.environ.get(
    "BACKUP_TEST_BASH", "C:/Program Files/Git/bin/bash.exe" if os.name == "nt" else "bash"
)


class RetentionTests(unittest.TestCase):
    def run_shell(self, body: str, *args: str) -> subprocess.CompletedProcess[str]:
        env = os.environ.copy()
        env.pop("BASH_ENV", None)
        return subprocess.run(
            [BASH, "-c", 'source "$1"; shift; ' + body, "retention-test", SCRIPT.as_posix(), *args],
            capture_output=True,
            text=True,
            env=env,
            check=False,
        )

    def test_rotation_keeps_old_local_backup_bytes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            old = root / "20000101-000000"
            old.mkdir()
            payload = old / "old.cms"
            payload.write_bytes(b"irreplaceable-old-ciphertext")
            os.utime(old, (time.time() - 40 * 86400,) * 2)
            result = self.run_shell('backup_prune_local "$1" 14 false', root.as_posix())
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(payload.read_bytes(), b"irreplaceable-old-ciphertext")

    def test_enabled_policy_only_removes_expired_timestamp_directories(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            old = root / "20000101-000000"
            fresh = root / "29990101-000000"
            preservation = root / "20261020-hook-preservation"
            for path in (old, fresh, preservation):
                path.mkdir()
            for path in (old, preservation):
                os.utime(path, (time.time() - 40 * 86400,) * 2)
            result = self.run_shell('backup_prune_local "$1" 14 true', root.as_posix())
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertFalse(old.exists())
            self.assertTrue(fresh.exists())
            self.assertTrue(preservation.exists())

    def test_rotation_never_calls_remote_delete_or_listing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            calls = Path(tmp) / "calls"
            body = 'call_file="$1"; minio_mc() { printf "%s\\n" "$*" >> "$call_file"; }; backup_prune_minio backups 14 false'
            result = self.run_shell(body, calls.as_posix())
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertFalse(calls.exists())

    def test_enabled_remote_policy_only_deletes_expired_backup_prefix(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            calls = Path(tmp) / "calls"
            body = '''call_file="$1"; minio_mc() {
              printf '%s\\n' "$*" >> "$call_file"
              if [ "$1" = ls ]; then
                printf '%s\\n' 'date time 0B 20000101-000000/' 'date time 0B 29990101-000000/' 'date time 0B 20261020-hook-preservation/'
              fi
            }; backup_prune_minio backups 14 true'''
            result = self.run_shell(body, calls.as_posix())
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(
                calls.read_text().splitlines(),
                ["ls l/backups/", "rm --recursive --force l/backups/20000101-000000/"],
            )

    def test_invalid_policy_fails_closed(self) -> None:
        result = self.run_shell('backup_retention_validate typo 14')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("BACKUP_PRUNE_ENABLED", result.stderr)


if __name__ == "__main__":
    unittest.main()

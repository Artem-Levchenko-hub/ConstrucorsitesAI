"""Exercise the real shell decisions using only fresh local fixture archives."""

import json
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
BASH = "C:/Program Files/Git/bin/bash.exe" if os.name == "nt" else shutil.which("bash")


@unittest.skipUnless(BASH and Path(BASH).exists(), "local Bash required")
class BackupCompletenessTests(unittest.TestCase):
    def execute(self, directory, program):
        python = shlex.quote(sys.executable.replace("\\", "/"))
        prefix = "set -euo pipefail\npython3() { " + python + ' "$@"; }\n'
        return subprocess.run(
            [BASH, "-c", prefix + program],
            cwd=directory,
            capture_output=True,
            timeout=15,
            env={**os.environ, "MSYS_NO_PATHCONV": "1", "MSYS2_ARG_CONV_EXCL": "*"},
        )

    def test_partial_missing_or_failed_peer_never_promotes_cells(self):
        source = (ROOT / "infra/backup/backup-omnia.sh").read_text(encoding="utf-8")
        block = source.split("# 5b.", 1)[1].split("# 6.", 1)[0]
        block = "# 5b." + block
        cases = (
            (2, 0, False, 0),
            (0, 2, False, 0),
            (0, 0, True, 0),
            (0, 0, False, 1),
            (0, 0, False, 0),
        )
        for cell_code, peer_code, missing, query_code in cases:
            with (
                self.subTest(cell=cell_code, peer=peer_code, missing=missing, query=query_code),
                tempfile.TemporaryDirectory() as directory,
            ):
                root = Path(directory)
                (root / "state").mkdir()
                if not missing:
                    (root / "cells.py").write_text("fixture")
                (root / "runtime.py").write_text("fixture")
                (root / "topology.json").write_text("{}")
                (root / "backup").mkdir()
                census = {
                    "version": 1,
                    "scope": "complete_platform_project_workspace_census",
                    "observed_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
                    "projects": [],
                    "workspaces": [],
                }
                script = f"CELL_CODE={cell_code}\nPEER_CODE={peer_code}\nQUERY_CODE={query_code}\n"
                script += "CENSUS_FIXTURE=" + shlex.quote(json.dumps(census)) + "\n"
                script += """dir=backup
CELLS_SCRIPT=cells.py
STATE_ROOT=state
CELLS_PYTHON=cells_python
BACKUP_PYTHON=peer_python
RUNTIME_BACKUP_SCRIPT=runtime.py
BACKUP_TOPOLOGY=topology.json
BACKUP_PEER_TEMP_ROOT=unused
CELL_POSTGRES_IMAGE=fixture
CELL_BACKUP_IMAGE=fixture
log(){ printf '%s\\n' "$*"; }
fail(){ printf '%s\\n' "$*"; exit 1; }
cells_python(){
  printf cell-called > cell-called
  mkdir -p backup/cells
  printf '{}' > backup/cells/MANIFEST.json
  return "$CELL_CODE"
}
peer_python(){
  printf peer-called > peer-called
  mkdir -p backup/peers
  printf '{}' > backup/peers/COVERAGE.json
  return "$PEER_CODE"
}
platform_psql(){ printf '%s' "$CENSUS_FIXTURE"; return "$QUERY_CODE"; }
"""
                result = self.execute(root, script + block + "\nprintf promoted\n")
                failure = bool(cell_code or peer_code or missing or query_code)
                if failure:
                    self.assertNotEqual(result.returncode, 0)
                    self.assertNotIn(b"promoted", result.stdout)
                else:
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertIn(b"promoted", result.stdout)
                    self.assertTrue((root / "backup/cells.tgz").exists())
                    self.assertTrue((root / "backup/peers.tgz").exists())
                if cell_code or missing or query_code:
                    self.assertFalse((root / "peer-called").exists())
                if query_code:
                    self.assertFalse((root / "cell-called").exists())

    def test_verdict_binds_exact_ciphertext_and_backup_timestamp(self):
        source = (ROOT / "infra/backup/restore-test-omnia.sh").read_text(encoding="utf-8")
        function = (
            "write_verdict(){"
            + source.split("write_verdict(){", 1)[1].split("\n}\n", 1)[0]
            + "\n}\n"
        )
        for success in (False, True):
            with self.subTest(success=success), tempfile.TemporaryDirectory() as directory:
                script = "BACKUP_ROOT=.\nlatest=20261020-001234\nbackup_sha256=" + "a" * 64 + "\n"
                script += (
                    "verdict="
                    + ("true" if success else "false")
                    + "\n"
                    + function
                    + "\nwrite_verdict\n"
                )
                result = self.execute(directory, script)
                self.assertEqual(result.returncode, 0, result.stderr)
                receipt = json.loads((Path(directory) / "RESTORE_TEST.json").read_text())
                self.assertEqual(receipt["backup_sha256"], "a" * 64)
                self.assertEqual(receipt["backup_timestamp"], "20261020-001234")
                self.assertEqual(receipt["coverage_complete"], success)
                self.assertEqual(receipt["ok"], success)
                self.assertEqual(len(list(Path(directory).iterdir())), 1)

    def test_complete_verdict_follows_strict_peer_verifier_and_missing_paths_are_fatal(self):
        source = (ROOT / "infra/backup/restore-test-omnia.sh").read_text(encoding="utf-8")
        self.assertLess(
            source.index('"$RUNTIME_BACKUP_SCRIPT" verify'), source.index("\nverdict=true")
        )
        self.assertIn('"${latest}/peers.tgz"', source)
        self.assertNotIn("2) cells_note=", source)
        self.assertNotIn('cells_note="archive present, verifier missing"', source)
        self.assertIn("platform_scratch_created=1", source)
        self.assertIn('if [ "$platform_scratch_created" = 1 ]', source)


if __name__ == "__main__":
    unittest.main()

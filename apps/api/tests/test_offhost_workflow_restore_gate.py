"""Run the actual CLI gate; unproven copies remain preserved but never green."""

import json
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
SCRIPT = ROOT / "infra/backup/verify-offhost-status.py"


@pytest.mark.parametrize(
    "change,expected",
    [
        ({}, 0),
        ({"restore_test_ok": None}, 1),
        ({"restore_test_ok": False}, 1),
        ({"restore_test_ok": "true"}, 1),
        ({"restore_tested_at": None}, 1),
        ({"restore_tested_at": "malformed"}, 1),
        ({"restore_tested_at": "2026-01-01T00:00:00Z"}, 1),
        ({"restore_tested_at": "2099-01-01T00:00:00Z"}, 1),
        ({"restore_tested_at": "2026-10-20T00:00:00"}, 1),
    ],
)
def test_real_offhost_restore_gate(tmp_path, change, expected):
    now = datetime.now(UTC)
    status = {
        "created_at": (now - timedelta(hours=1)).isoformat(),
        "restore_tested_at": now.isoformat(),
        "restore_test_ok": True,
    }
    status.update(change)
    path = tmp_path / "status.json"
    path.write_text(json.dumps(status))
    result = subprocess.run([sys.executable, str(SCRIPT), str(path)], capture_output=True)
    assert result.returncode == expected


def test_workflow_preserves_ciphertext_before_requiring_restore_proof():
    source = (ROOT / ".github/workflows/offhost-backup.yml").read_text()
    assert source.index("actions/upload-artifact@v4") < source.index(
        "python3 infra/backup/verify-offhost-status.py status.json"
    ) < source.index("Raise the alarm")


def test_restore_before_backup_creation_cannot_verify_that_copy(tmp_path):
    now = datetime.now(UTC)
    path = tmp_path / "status.json"
    path.write_text(json.dumps({
        "created_at": (now - timedelta(hours=1)).isoformat(),
        "restore_tested_at": (now - timedelta(hours=2)).isoformat(),
        "restore_test_ok": True,
    }))
    result = subprocess.run([sys.executable, str(SCRIPT), str(path)], capture_output=True)
    assert result.returncode == 1

import os
import subprocess
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/reconcile_provider_ledger.py"


def test_invalid_arguments_return_safe_error_without_echoing_private_values(tmp_path):
    environment = os.environ.copy()
    environment["PYTHONPATH"] = str(SCRIPT.parents[1] / "src")
    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--expected-organization-id",
            "qa-private-organization",
            "--report-file",
            str(tmp_path / "never-created.json"),
            "--unexpected",
            "qa-private-request",
        ],
        capture_output=True,
        text=True,
        env=environment,
        check=False,
    )
    assert result.returncode == 3
    assert "qa-private" not in result.stdout + result.stderr
    assert '"error": "invalid_arguments"' in result.stdout
    assert not (tmp_path / "never-created.json").exists()


def test_wrong_source_is_rejected_before_database_configuration(tmp_path):
    source = tmp_path / "source.bin"
    source.write_bytes(b"synthetic source")
    document = tmp_path / "normalized.json"
    document.write_text(
        '{"schema_version":1,"organization_id":"qa-org",'
        '"source_kind":"balance_ledger_export","source_sha256":"wrong",'
        '"operations":[]}'
    )
    environment = os.environ.copy()
    environment["PYTHONPATH"] = str(SCRIPT.parents[1] / "src")
    environment["DATABASE_URL"] = "not-a-database-url"
    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--expected-organization-id",
            "qa-org",
            "--normalized-statement",
            str(document),
            "--source",
            str(source),
            "--report-file",
            str(tmp_path / "report.json"),
        ],
        capture_output=True,
        text=True,
        env=environment,
        check=False,
    )
    assert result.returncode == 3
    assert '"error": "source_hash_mismatch"' in result.stdout
    assert "database" not in result.stdout.lower() + result.stderr.lower()

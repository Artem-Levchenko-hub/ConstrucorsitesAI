import json
import stat

import pytest

from yleum_api.services.provider_ledger_output import write_restricted_report


def test_ids_are_written_only_to_private_file(tmp_path):
    path = tmp_path / "restricted.json"
    report = {"operations": [{"ref_id": "qa-provider-request-private"}]}
    write_restricted_report(path, report)
    assert json.loads(path.read_text()) == report
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_existing_file_is_not_overwritten(tmp_path):
    path = tmp_path / "existing.json"
    path.write_text("old confirmation")
    with pytest.raises(FileExistsError):
        write_restricted_report(path, {"operations": []})
    assert path.read_text() == "old confirmation"


def test_symlink_does_not_overwrite_target(tmp_path):
    target = tmp_path / "original.json"
    target.write_text("unchanged")
    path = tmp_path / "link.json"
    path.symlink_to(target)
    with pytest.raises(OSError):
        write_restricted_report(path, {"operations": []})
    assert target.read_text() == "unchanged"

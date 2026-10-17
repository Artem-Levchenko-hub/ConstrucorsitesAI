import ctypes
import json
import stat
import struct
import sys
from types import SimpleNamespace

import pytest

from yleum_orchestrator.core.cell_resources import CellIndeterminateOperation
from yleum_orchestrator.services.source_volume_identity import (
    VOLUME_STATX_SCRIPT,
    volume_incarnation,
)


def evidence():
    return {
        "daemon_id": "trusted-daemon",
        "name": "owned-volume",
        "metadata": {
            "Name": "owned-volume",
            "CreatedAt": "2026-10-17T01:00:00Z",
            "Driver": "local",
            "Mountpoint": "/owned/data",
            "Scope": "local",
            "Options": None,
            "Labels": {"owner": "owner"},
        },
        "labels": {"owner": "owner"},
        "stat_output": b"[123,456,1792198800,123456789]",
    }


def test_same_second_name_inode_reuse_still_has_different_incarnation():
    first = evidence()
    # Docker's name, CreatedAt (seconds), mountpoint, daemon and reused inode are unchanged.
    recreated = {**first, "stat_output": b"[123,456,1792198800,123456790]"}
    assert volume_incarnation(**first) != volume_incarnation(**recreated)
    assert volume_incarnation(**first) == volume_incarnation(**first)


@pytest.mark.parametrize(
    "case",
    [
        "birth_missing",
        "birth_boolean",
        "foreign_labels",
        "daemon",
        "remote_driver",
        "options",
        "name",
        "unknown_scope",
    ],
)
def test_unavailable_or_foreign_evidence_never_falls_back_to_name(case):
    value = evidence()
    if case == "birth_missing":
        value["stat_output"] = b"[123,456,0,0]"
    elif case == "birth_boolean":
        value["stat_output"] = b"[123,456,true,0]"
    elif case == "foreign_labels":
        value["labels"] = {"owner": "foreign"}
    elif case == "daemon":
        value["daemon_id"] = ""
    else:
        key, bad = {
            "remote_driver": ("Driver", "remote"),
            "options": ("Options", {"x": "x"}),
            "name": ("Name", "foreign"),
            "unknown_scope": ("Scope", None),
        }[case]
        value["metadata"][key] = bad
    with pytest.raises(CellIndeterminateOperation):
        volume_incarnation(**value)


@pytest.mark.parametrize("birth_available", [True, False])
def test_statx_linux_abi_and_missing_birth_mask_fail_closed(monkeypatch, capsys, birth_available):
    def statx(fd, path, flags, mask, buffer):
        assert (fd, path, flags, mask) == (-100, b"/volume", 0x100, 0x901)
        assert ctypes.sizeof(buffer) == 256
        struct.pack_into("=I", buffer, 0, 0x901 if birth_available else 0x101)
        struct.pack_into("=H", buffer, 28, stat.S_IFDIR | 0o755)
        struct.pack_into("=Q", buffer, 32, 456)
        struct.pack_into("=qI", buffer, 80, 1792198800, 123456789)
        struct.pack_into("=II", buffer, 136, 8, 1)
        return 0

    monkeypatch.setattr(ctypes, "CDLL", lambda *a, **kw: SimpleNamespace(statx=statx))
    monkeypatch.setattr(sys, "argv", ["trusted", "/volume"])
    if birth_available:
        exec(VOLUME_STATX_SCRIPT, {})
        assert json.loads(capsys.readouterr().out)[1:] == [456, 1792198800, 123456789]
    else:
        with pytest.raises(SystemExit) as failure:
            exec(VOLUME_STATX_SCRIPT, {})
        assert failure.value.code == 1
        assert capsys.readouterr().out == ""

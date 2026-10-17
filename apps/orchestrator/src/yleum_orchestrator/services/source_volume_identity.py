"""Server-only local-volume incarnation evidence. No source content is executed/read."""

import hashlib
import json
from typing import Any

from yleum_orchestrator.core.cell_resources import CellIndeterminateOperation

# Linux UAPI /usr/include/linux/stat.h: fixed 256-byte statx ABI; offsets 0x00,
# 0x1c, 0x20, 0x50, 0x88. Birth time is stable across ordinary directory edits;
# ctime/mtime intentionally are NOT identity. Missing STATX_BTIME fails closed.
VOLUME_STATX_SCRIPT = r"""
import ctypes, json, os, stat, struct, sys
try:
    libc = ctypes.CDLL(None, use_errno=True)
    call = libc.statx
    call.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int,
                     ctypes.c_uint, ctypes.c_void_p]
    call.restype = ctypes.c_int
    data = ctypes.create_string_buffer(256)
    required = 0x901
    assert call(-100, os.fsencode(sys.argv[1]), 0x100, required, data) == 0
    mask = struct.unpack_from('=I', data, 0)[0]
    mode = struct.unpack_from('=H', data, 28)[0]
    inode = struct.unpack_from('=Q', data, 32)[0]
    sec, ns = struct.unpack_from('=qI', data, 80)
    major, minor = struct.unpack_from('=II', data, 136)
    assert mask & required == required and stat.S_ISDIR(mode)
    assert inode > 0 and sec > 0 and 0 <= ns < 1000000000
    print(json.dumps([os.makedev(major, minor), inode, sec, ns]))
except BaseException:
    sys.exit(1)
"""


def volume_incarnation(
    *,
    daemon_id: object,
    name: str,
    metadata: dict[str, Any],
    labels: dict[str, str],
    stat_output: bytes,
) -> str:
    fields = {key: metadata.get(key) for key in ("Name", "CreatedAt", "Driver", "Mountpoint")}
    if (
        not isinstance(daemon_id, str)
        or not daemon_id
        or any(not isinstance(value, str) or not value for value in fields.values())
        or fields["Name"] != name
        or fields["Driver"] != "local"
        or metadata.get("Options")
        or metadata.get("Scope") != "local"
        or metadata.get("Labels") != labels
    ):
        raise CellIndeterminateOperation("source volume metadata unavailable or changed")
    try:
        if len(stat_output) > 256:
            raise ValueError("oversized stat")
        stats = json.loads(stat_output)
        if (
            not isinstance(stats, list)
            or len(stats) != 4
            or any(type(value) is not int for value in stats)
            or stats[0] < 0
            or stats[1] <= 0
            or stats[2] <= 0
            or not 0 <= stats[3] < 1_000_000_000
        ):
            raise ValueError("invalid stat")
    except (ValueError, TypeError) as exc:
        raise CellIndeterminateOperation("source volume birth identity unavailable") from exc
    evidence = {
        "version": 1,
        "daemon": daemon_id,
        "volume": fields,
        "labels": labels,
        "root": stats,
    }
    return hashlib.sha256(
        json.dumps(
            evidence,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode()
    ).hexdigest()

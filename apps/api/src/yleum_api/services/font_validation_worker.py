"""Credential-free subprocess entry point. Never import application settings."""

from __future__ import annotations

import base64
import io
import json
import os
import re
import shutil
import struct
import subprocess
import sys
import tempfile
from pathlib import Path

MAX_INPUT = 8 * 1024 * 1024
MAX_FACE = 1024 * 1024


def unicode_ranges(points: list[int]) -> str:
    spans: list[tuple[int, int]] = []
    for point in sorted(points):
        if spans and spans[-1][1] + 1 == point:
            spans[-1] = (spans[-1][0], point)
        else:
            spans.append((point, point))
    return ",".join(f"U+{a:X}" + (f"-{b:X}" if a != b else "") for a, b in spans)


def normalize(raw: bytes, *, sanitize: bool = True) -> dict[str, object]:
    from fontTools.ttLib import TTFont

    if not 0 < len(raw) <= MAX_INPUT or raw[:4] not in (b"wOF2", b"wOFF", b"OTTO", b"\0\1\0\0"):
        raise ValueError("font format or size")
    if raw[:4] in (b"wOF2", b"wOFF") and (
        len(raw) < 48 or struct.unpack_from(">I", raw, 16)[0] > 16 * 1024 * 1024
    ):
        raise ValueError("expanded font budget")
    if sanitize:
        executable = shutil.which("ots-sanitize")
        if not executable:
            raise RuntimeError("font sanitizer unavailable")
        with tempfile.TemporaryDirectory(prefix="yleum-font-") as directory:
            source, target = Path(directory) / "input.font", Path(directory) / "clean.ttf"
            source.write_bytes(raw)
            result = subprocess.run(
                [executable, str(source), str(target)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=8,
                check=False,
            )
            if (
                result.returncode
                or not target.is_file()
                or target.stat().st_size > 16 * 1024 * 1024
            ):
                raise ValueError("font sanitizer rejected file")
            raw = target.read_bytes()
    font = TTFont(
        io.BytesIO(raw),
        lazy=False,
        checkChecksums=2,
        ignoreDecompileErrors=False,
        recalcBBoxes=False,
        recalcTimestamp=False,
    )
    if (
        len(font.reader.tables) > 100
        or sum(row.length for row in font.reader.tables.values()) > 16 * 1024 * 1024
    ):
        raise ValueError("font tables budget")
    cmap = font.getBestCmap() or {}
    if not cmap or len(cmap) > 100000:
        raise ValueError("font has no supported unicode cmap")
    family = font["name"].getDebugName(16) or font["name"].getDebugName(1)
    if not family or len(family) > 100 or any(ord(char) < 32 for char in family):
        raise ValueError("font family metadata")
    static_weight = int(font["OS/2"].usWeightClass)
    if not 1 <= static_weight <= 1000:
        raise ValueError("font weight")
    weight: int | str = static_weight
    if "fvar" in font:
        axis = next((axis for axis in font["fvar"].axes if axis.axisTag == "wght"), None)
        if axis:
            if not 1 <= axis.minValue <= axis.maxValue <= 1000:
                raise ValueError("font variable weight")
            weight = f"{axis.minValue:g} {axis.maxValue:g}"
    style = "italic" if font["OS/2"].fsSelection & 1 else "normal"
    license_text = "\n".join(
        filter(None, [font["name"].getDebugName(13), font["name"].getDebugName(14)])
    )[:30000]
    font.flavor = "woff2"
    output = io.BytesIO()
    font.save(output, reorderTables=False)
    converted = output.getvalue()
    font.close()
    if len(converted) > MAX_FACE:
        raise ValueError("normalized font budget")
    ranges = unicode_ranges(list(cmap))
    if len(ranges) > 30000 or not re.fullmatch(r"[U+0-9A-F,\-]+", ranges):
        raise ValueError("font coverage budget")
    return {
        "family": family,
        "data": base64.b64encode(converted).decode("ascii"),
        "weight": weight,
        "style": style,
        "unicode_range": ranges,
        "license_text": license_text,
        "has_cyrillic": all(ord(c) in cmap for c in "АяЁё"),
        "has_ruble": 0x20BD in cmap,
    }


def main() -> None:
    if os.name == "posix":
        import resource

        resource.setrlimit(resource.RLIMIT_AS, (512 * 1024 * 1024, 512 * 1024 * 1024))
        resource.setrlimit(resource.RLIMIT_CPU, (12, 12))
        resource.setrlimit(resource.RLIMIT_FSIZE, (16 * 1024 * 1024, 16 * 1024 * 1024))
        resource.setrlimit(resource.RLIMIT_NOFILE, (32, 32))
    try:
        raw = sys.stdin.buffer.read(MAX_INPUT + 1)
        print(json.dumps(normalize(raw), ensure_ascii=True))
    except Exception:
        # Parser/provider details are not public diagnostics.
        sys.exit(1)


if __name__ == "__main__":
    main()

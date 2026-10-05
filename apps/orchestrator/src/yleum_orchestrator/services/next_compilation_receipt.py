"""Platform fixed-data reader. Never imports or evaluates generated project code.

Run only in a networkless pinned helper with one exact owned volume mounted RO.
Metadata and assets are untrusted input; unsupported output yields no witness.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
from typing import Any, cast

COLLECTOR_VERSION = "next-fixed-data-v1"
MAX_ASSET_BYTES = 8 * 1024**2
MAX_TOTAL_BYTES = 64 * 1024**2
MAX_ASSETS = 128


class CompilationUnavailable(ValueError):
    def __init__(self) -> None:
        super().__init__("COMPILATION_UNAVAILABLE")


def _need(value: object) -> None:
    if not value:
        raise CompilationUnavailable()


def _pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        _need(key not in result)
        result[key] = value
    return result


def asset_path(path: object, build_id: str) -> bool:
    if type(path) is not str or ".." in path:
        return False
    return bool(
        re.fullmatch(
            r"static/(?:chunks/[A-Za-z0-9_/-]+\.js|css/[A-Za-z0-9_/-]+\.css|"
            r"media/[A-Za-z0-9_.-]+\.(?:woff2?|ttf|otf|png|jpg|jpeg|webp|avif|gif|ico)|"
            + re.escape(build_id)
            + r"/_(?:buildManifest|ssgManifest)\.js)",
            path,
        )
    )


def _stamp(value: os.stat_result) -> tuple[int, int, int, int, int]:
    return value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns, value.st_ctime_ns


class _Reader:
    def __init__(self, root: str):
        self.root = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        self.stamps: dict[str, tuple[int, int, int, int, int]] = {}

    def _open(self, path: str) -> int:
        parts = path.split("/")
        _need(all(p and p not in {".", ".."} for p in parts))
        current = os.dup(self.root)
        try:
            for part in parts[:-1]:
                child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=current)
                os.close(current)
                current = child
            return os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=current)
        finally:
            os.close(current)

    def read(self, path: str, limit: int) -> bytes:
        fd = self._open(path)
        try:
            before = os.fstat(fd)
            if path in self.stamps:
                _need(_stamp(before) == self.stamps[path])
            _need(stat.S_ISREG(before.st_mode) and 0 < before.st_size <= limit)
            chunks: list[bytes] = []
            remaining = before.st_size
            while remaining:
                chunk = os.read(fd, min(65536, remaining))
                _need(chunk)
                chunks.append(chunk)
                remaining -= len(chunk)
            _need(not os.read(fd, 1) and _stamp(before) == _stamp(os.fstat(fd)))
            self.stamps[path] = _stamp(before)
            return b"".join(chunks)
        finally:
            os.close(fd)

    def json(self, path: str) -> dict[str, Any]:
        value = json.loads(self.read(path, 1024**2), object_pairs_hook=_pairs)
        _need(type(value) is dict)
        return cast(dict[str, Any], value)

    def verify(self) -> None:
        for path, expected in self.stamps.items():
            fd = self._open(path)
            try:
                _need(_stamp(os.fstat(fd)) == expected)
            finally:
                os.close(fd)

    def media(self) -> list[str]:
        # Only fixed nonrecursive static/media, no filesystem-supplied paths.
        try:
            fd = self._open(".next/static/media")
        except FileNotFoundError:
            return []
        try:
            _need(stat.S_ISDIR(os.fstat(fd).st_mode))
            entries = os.listdir(fd)
            _need(len(entries) <= MAX_ASSETS)
            return ["static/media/" + name for name in sorted(entries)]
        finally:
            os.close(fd)


def collect_next_compilation(root: str = "/proof") -> dict[str, Any]:
    reader: _Reader | None = None
    try:
        reader = _Reader(root)
        package = reader.json("package.json")
        _need(package.get("scripts", {}).get("build") == "next build")
        _need(package.get("dependencies", {}).get("next") == "15.5.24")
        build_bytes = reader.read(".next/BUILD_ID", 128)
        build_id = build_bytes.decode("ascii").strip()
        _need(re.fullmatch(r"[A-Za-z0-9_-]{1,128}", build_id))
        build = reader.json(".next/build-manifest.json")
        app = reader.json(".next/app-build-manifest.json")
        _need(type(app.get("pages")) is dict and "/page" in app["pages"])
        lists = [build.get(name) for name in ("rootMainFiles", "polyfillFiles", "lowPriorityFiles")]
        _need(type(build.get("pages")) is dict)
        lists.extend(build["pages"].values())
        lists.extend(app["pages"].values())
        _need(all(type(value) is list and len(value) <= MAX_ASSETS for value in lists))
        paths: set[str] = set(reader.media())
        for values in lists:
            for path in cast(list[Any], values):
                _need(asset_path(path, build_id))
                paths.add(path)
        _need(1 <= len(paths) <= MAX_ASSETS)
        # There must be actual root app JS in the compiler's root page entry.
        _need(
            any(
                p.startswith("static/chunks/app/") and p.endswith(".js")
                for p in app["pages"]["/page"]
            )
        )
        assets: list[dict[str, Any]] = []
        total = 0
        for path in sorted(paths):
            _need(asset_path(path, build_id))
            data = reader.read(".next/" + path, MAX_ASSET_BYTES)
            total += len(data)
            _need(total <= MAX_TOTAL_BYTES)
            assets.append(
                {
                    "path": "/_next/" + path,
                    "sha256": hashlib.sha256(data).hexdigest(),
                    "bytes": len(data),
                }
            )
        reader.verify()
        metadata = {
            name: hashlib.sha256(reader.read(name, 1024**2)).hexdigest()
            for name in (
                "package.json",
                ".next/build-manifest.json",
                ".next/app-build-manifest.json",
            )
        }
        reader.verify()
        return {
            "collector_version": COLLECTOR_VERSION,
            "declared_next_version": "15.5.24",
            "build_id_sha256": hashlib.sha256(build_bytes).hexdigest(),
            "metadata_sha256": metadata,
            "assets": assets,
        }
    except (OSError, ValueError, TypeError, AttributeError, KeyError, UnicodeError):
        raise CompilationUnavailable() from None
    finally:
        if reader is not None:
            os.close(reader.root)

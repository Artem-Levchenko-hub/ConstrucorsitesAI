"""Verify the source archive, allowing a proven empty legacy directory."""

from __future__ import annotations

import gzip
import sys
import tarfile
from pathlib import Path


def verify(archive: Path, source: Path) -> str:
    if archive.is_symlink() or source.is_symlink() or not source.is_dir():
        raise ValueError("project sources archive or source directory is invalid")
    # Tar accepts EOF after a member without its two end records. Check those
    # records explicitly, then consume gzip through EOF to validate its trailer.
    # Never include archive contents in diagnostics.
    with gzip.open(archive, "rb") as stream:
        with tarfile.open(fileobj=stream, mode="r:") as tar:
            members = tar.getmembers()
        end = max(
            (member.offset_data + ((member.size + 511) // 512) * 512 for member in members),
            default=0,
        )
        stream.seek(end)
        if stream.read(1024) != bytes(1024):
            raise ValueError("project sources tar archive is incomplete")
        while stream.read(1024 * 1024):
            pass
    roots = [member for member in members if member.name.rstrip("/") == source.name]
    if len(roots) != 1 or not roots[0].isdir():
        raise ValueError("project sources archive has no expected source directory")
    if archive.stat().st_size >= 200:
        return "project sources archive verified"
    if any(source.iterdir()) or len(members) != 1:
        raise ValueError("project sources archive is suspiciously small")
    return "project sources archive verified: empty source directory"


def main() -> int:
    try:
        if len(sys.argv) != 3:
            raise ValueError("project sources verification requires archive and source directory")
        print(verify(Path(sys.argv[1]), Path(sys.argv[2])))
    except (OSError, EOFError, tarfile.TarError, ValueError):
        print("project sources archive verification failed", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

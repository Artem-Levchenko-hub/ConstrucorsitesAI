"""Verify the source archive, allowing a proven empty legacy directory."""

from __future__ import annotations

import gzip
import sys
import tarfile
from pathlib import Path


def verify(archive: Path, source: Path) -> str:
    if archive.is_symlink() or source.is_symlink() or not source.is_dir():
        raise ValueError("project sources archive or source directory is invalid")
    # Consume gzip through EOF: tar can stop at its end marker before a corrupt
    # gzip trailer is read. Never include archive contents in diagnostics.
    with gzip.open(archive, "rb") as stream:
        while stream.read(1024 * 1024):
            pass
    with tarfile.open(archive, "r:gz") as stream:
        members = stream.getmembers()
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

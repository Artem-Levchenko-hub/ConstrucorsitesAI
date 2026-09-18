"""Orphan machine-environment archives: dry run by default, ``--apply`` deletes.

Run ON the production host with sudo. Until publication artifact retention (plan
P18) lands, checkpoint archives under ``project-machines/artifacts`` are never
collected: on 2026-09-18 they held 88.9 GiB of which 8.6 GiB were referenced.

Safety rules: an archive is kept when its name appears ANYWHERE in the controller
state JSON (not only in the machine that owns the directory), when it is younger
than two hours, or when any publication is active. Docker volumes, other
projects and symlinks are never touched.
"""

from __future__ import annotations

import json
import os
import re
import sys
import time

STATE = "/opt/omnia-runtime/state"
ARTIFACTS = os.path.join(STATE, "project-machines", "artifacts")
ARCHIVE = re.compile(r"[0-9a-f]{32}\.tar")
ACTIVE_PHASES = {"queued", "building", "swapping"}
MIN_AGE_SECONDS = 2 * 3600
MAX_STATE_FILE_BYTES = 64 * 1024 * 1024
GIB = 1024**3


def referenced_archives() -> set[str]:
    """Every archive name mentioned in any controller state JSON file."""
    names: set[str] = set()
    for base, dirs, files in os.walk(STATE, followlinks=False):
        if base.startswith(ARTIFACTS):
            dirs[:] = []
            continue
        for name in files:
            path = os.path.join(base, name)
            if os.path.islink(path) or not name.endswith(".json"):
                continue
            try:
                if os.path.getsize(path) > MAX_STATE_FILE_BYTES:
                    continue
                with open(path, errors="ignore") as handle:
                    names.update(ARCHIVE.findall(handle.read()))
            except OSError:
                continue
    return names


def active_publications() -> list[str]:
    active = []
    for base, _dirs, files in os.walk(os.path.join(STATE, "cell-publications")):
        if "publication.json" not in files:
            continue
        try:
            with open(os.path.join(base, "publication.json")) as handle:
                history = json.load(handle).get("history") or []
        except (OSError, ValueError):
            continue
        if history and history[-1].get("response", {}).get("phase") in ACTIVE_PHASES:
            active.append(base)
    return active


def orphan_archives(referenced: set[str]) -> tuple[list[tuple[str, int]], int]:
    now = time.time()
    orphans: list[tuple[str, int]] = []
    kept = 0
    for workspace in os.listdir(ARTIFACTS):
        directory = os.path.join(ARTIFACTS, workspace)
        if os.path.islink(directory) or not os.path.isdir(directory):
            continue
        for name in os.listdir(directory):
            path = os.path.join(directory, name)
            if os.path.islink(path) or not ARCHIVE.fullmatch(name):
                continue
            stat = os.stat(path)
            if name in referenced or now - stat.st_mtime < MIN_AGE_SECONDS:
                kept += stat.st_size
            else:
                orphans.append((path, stat.st_size))
    return orphans, kept


def main() -> int:
    apply = "--apply" in sys.argv
    referenced = referenced_archives()
    active = active_publications()
    orphans, kept = orphan_archives(referenced)
    total = sum(size for _, size in orphans)
    print(
        f"archive names referenced in state: {len(referenced)}; active publications: {len(active)}"
    )
    print(
        f"orphan archives (>2 h, unreferenced): {len(orphans)} files, {total / GIB:.1f} GiB; "
        f"kept: {kept / GIB:.1f} GiB"
    )
    if not apply:
        print("dry run: pass --apply to delete")
        return 0
    if active:
        print("ABORT: a publication is active")
        return 1
    freed = 0
    for path, size in orphans:
        try:
            os.unlink(path)
        except OSError as error:
            print("skip", os.path.basename(path), type(error).__name__)
            continue
        freed += size
        try:
            os.unlink(path + ".ok")
        except OSError:
            pass
    print(f"deleted {freed / GIB:.1f} GiB of orphan archives")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

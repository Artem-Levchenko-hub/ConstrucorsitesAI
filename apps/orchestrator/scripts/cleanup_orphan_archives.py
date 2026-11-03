"""Collect old, unreferenced machine archives; dry run unless ``--apply``.

Run with the orchestrator's Python environment and state-directory owner. State
must be completely readable and valid. Apply shares the controller workspace
lock, rescans all state under that lock, and checks archive identity before
unlinking. Nothing outside canonical archive files is removed.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import shutil
import stat
import sys
import time
from collections import defaultdict
from pathlib import Path
from typing import Any
from uuid import UUID

# Also support direct execution from a checkout on the production host.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from yleum_orchestrator.core.cell_resources import (
    WorkspaceLockTimeout,
    WorkspaceLockUnavailable,
)
from yleum_orchestrator.services import build_retention_inventory as inventory
from yleum_orchestrator.services.build_retention_inventory import (
    ARCHIVE,
    MIN_AGE_SECONDS,
    UnsafeStateError,
    _directory,
    _identity,
    checkpoint_references,
    scan_state,
)
from yleum_orchestrator.services.cell_lock import WorkspaceOperationLock

STATE = "/opt/omnia-runtime/state"
ARTIFACTS = os.path.join(STATE, "project-machines", "artifacts")


def _all_references(root: Path, report: dict[str, Any]) -> tuple[set[str], int]:
    selections: dict[str, inventory.CheckpointSelection] = {}
    busy_workspaces: set[str] = set()
    referenced, active, declared, optional = scan_state(
        root, selections=selections, busy_workspaces=busy_workspaces,
    )
    checkpoints, volume_count, file_count, missing_planned = checkpoint_references(
        declared, optional, selections=selections, root=root,
    )
    referenced.update(checkpoints)
    report.update(
        active_operations=active,
        unresolved_workspaces=len(busy_workspaces),
        missing_planned_volumes=missing_planned,
        checkpoint_volumes=volume_count,
        checkpoint_files=file_count,
        checkpoint_referenced_names=len(checkpoints),
    )
    return referenced, active


def candidates(
    root: Path,
    referenced: set[str],
    min_age: int,
) -> tuple[dict[UUID, list[tuple[Path, os.stat_result]]], int]:
    archives = root / "project-machines" / "artifacts"
    grouped: dict[UUID, list[tuple[Path, os.stat_result]]] = defaultdict(list)
    kept = 0
    if not archives.exists():
        return grouped, kept
    _directory(archives)
    for workspace in archives.iterdir():
        info = workspace.lstat()
        if not stat.S_ISDIR(info.st_mode):
            continue
        try:
            workspace_id = UUID(workspace.name)
        except ValueError:
            continue
        if str(workspace_id) != workspace.name:
            continue
        for path in workspace.iterdir():
            info = path.lstat()
            if not ARCHIVE.fullmatch(path.name) or not stat.S_ISREG(info.st_mode):
                continue
            if path.name in referenced or time.time() - info.st_mtime < min_age:
                kept += info.st_size
            else:
                grouped[workspace_id].append((path, info))
    return grouped, kept


def _remove_seal(
    marker: Path,
    original: os.stat_result,
    min_age: int,
    report: dict[str, Any],
) -> None:
    if not stat.S_ISREG(original.st_mode) or time.time() - original.st_mtime < min_age:
        return
    _directory(marker.parent)
    try:
        current = marker.lstat()
    except FileNotFoundError:
        return
    if not stat.S_ISREG(current.st_mode) or _identity(current) != _identity(original):
        return
    marker.unlink()
    report["deleted_seals"] += 1
    report["deleted_seal_bytes"] += current.st_size


async def _apply(
    root: Path,
    grouped: dict[UUID, list[tuple[Path, os.stat_result]]],
    min_age: int,
    report: dict[str, Any],
) -> None:
    lock = WorkspaceOperationLock(root, acquire_timeout_seconds=0.1, retry_interval_seconds=0.025)
    for workspace_id, archives in grouped.items():
        try:
            async with lock.hold(workspace_id):
                referenced, active = _all_references(root, report)
                if active:
                    raise UnsafeStateError("active_operation")
                for path, original in archives:
                    _directory(path.parent)
                    try:
                        current = path.lstat()
                    except FileNotFoundError:
                        report["skipped_files"] += 1
                        continue
                    if (
                        not stat.S_ISREG(current.st_mode)
                        or _identity(current) != _identity(original)
                        or path.name in referenced
                        or time.time() - current.st_mtime < min_age
                    ):
                        report["skipped_files"] += 1
                        continue
                    marker = path.with_name(path.name + ".ok")
                    try:
                        seal_before = marker.lstat()
                    except FileNotFoundError:
                        seal_before = None
                    # Inspecting the seal must not widen the archive identity window.
                    if _identity(path.lstat()) != _identity(current):
                        report["skipped_files"] += 1
                        continue
                    path.unlink()
                    report["deleted_files"] += 1
                    report["deleted_bytes"] += current.st_size
                    if seal_before is not None:
                        _remove_seal(marker, seal_before, min_age, report)
        except WorkspaceLockTimeout:
            report["busy_workspaces"] += 1
            report["skipped_files"] += len(archives)


def collect_archives(
    root: Path, *, min_age_seconds: int = MIN_AGE_SECONDS, apply: bool = False,
) -> dict[str, Any]:
    if min_age_seconds < MIN_AGE_SECONDS:
        raise ValueError("min_age_seconds must be at least 7200")
    report: dict[str, Any] = {
        "mode": "apply" if apply else "dry_run",
        "status": "ok",
        "deleted_files": 0,
        "deleted_bytes": 0,
        "deleted_seals": 0,
        "deleted_seal_bytes": 0,
        "disk_free_bytes_before": None,
        "disk_free_bytes_after": None,
        "busy_workspaces": 0,
        "skipped_files": 0,
    }
    try:
        # absolute(), unlike resolve(), does not silently follow a symlink root.
        root = root.absolute()
        _directory(root)
        report["disk_free_bytes_before"] = shutil.disk_usage(root).free
        referenced, active = _all_references(root, report)
        grouped, kept = candidates(root, referenced, min_age_seconds)
        report.update(
            referenced_names=len(referenced),
            candidate_files=sum(len(items) for items in grouped.values()),
            candidate_bytes=sum(info.st_size for items in grouped.values() for _, info in items),
            kept_bytes=kept,
        )
        if apply:
            if active:
                raise UnsafeStateError("active_operation")
            asyncio.run(_apply(root, grouped, min_age_seconds, report))
    except (
        UnsafeStateError,
        OSError,
        ValueError,
        RecursionError,
        WorkspaceLockUnavailable,
    ) as error:
        report.update(
            status="aborted",
            error=(str(error) if isinstance(error, UnsafeStateError) else type(error).__name__),
        )
    finally:
        if report["disk_free_bytes_before"] is not None:
            try:
                _directory(root)
                report["disk_free_bytes_after"] = shutil.disk_usage(root).free
            except (UnsafeStateError, OSError):
                report["status"] = "aborted"
                report.setdefault("error", "disk_usage_unavailable")
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state-root", type=Path, default=Path(STATE))
    parser.add_argument("--min-age-seconds", type=int, default=MIN_AGE_SECONDS)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    if args.min_age_seconds < MIN_AGE_SECONDS:
        parser.error("--min-age-seconds must be at least 7200")
    report = collect_archives(
        args.state_root, min_age_seconds=args.min_age_seconds, apply=args.apply,
    )
    print(json.dumps(report, sort_keys=True))
    return int(report["status"] != "ok")


if __name__ == "__main__":
    raise SystemExit(main())

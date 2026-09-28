"""Collect old, unreferenced machine archives; dry run unless ``--apply``.

Run with the orchestrator's Python environment and state-directory owner. State
must be completely readable and valid. Apply shares the controller workspace
lock, rescans all state under that lock, and checks archive identity before
unlinking. Nothing outside canonical archive files is removed.
"""

from __future__ import annotations

import argparse
import asyncio
import io
import json
import os
import re
import shutil
import stat
import sys
import tarfile
import time
from collections import defaultdict
from pathlib import Path, PurePosixPath
from typing import Any
from uuid import UUID, uuid4

# Also support direct execution from a checkout on the production host.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from yleum_orchestrator.core.cell_resources import (
    WorkspaceLockTimeout,
    WorkspaceLockUnavailable,
)
from yleum_orchestrator.services.cell_lock import WorkspaceOperationLock

STATE = "/opt/omnia-runtime/state"
ARTIFACTS = os.path.join(STATE, "project-machines", "artifacts")
ARCHIVE = re.compile(r"[0-9a-f]{32}\.tar")
MIN_AGE_SECONDS = 2 * 3600
MAX_STATE_FILE_BYTES = 64 * 1024 * 1024
# DeployPhase and CellPublicationService: every other phase is unsafe,
# including future nonterminal phases this collector does not yet understand.
TERMINAL_PHASES = {"done", "failed", "cancelled"}
CHECKPOINT_KINDS = {"checkpoint", "checkpoints", "checkpoint-volume", "checkpoint_volume"}
VOLUME_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,254}")
_FIND_MACHINE_FILES = (
    "set -eu; "
    'links=$(find /checkpoints -type l -print); [ -z "$links" ]; '
    'bad=$(find /checkpoints -name machine.json ! -type f -print); [ -z "$bad" ]; '
    "find /checkpoints -name machine.json -type f -print0"
)


class UnsafeStateError(Exception):
    """The state cannot establish that deletion is safe (no payload in errors)."""


def _directory(path: Path) -> None:
    """Reject symlinks in every component, including ancestors of state root."""
    for component in (*reversed(path.parents), path):
        if not stat.S_ISDIR(component.lstat().st_mode):
            raise UnsafeStateError("unsafe_directory")


def _identity(info: os.stat_result) -> tuple[int, int, int, int]:
    return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise UnsafeStateError("ambiguous_json")
        result[key] = value
    return result


def _invalid_constant(_value: str) -> None:
    raise UnsafeStateError("invalid_json")


def _read_json(path: Path) -> Any:
    before = path.lstat()
    if not stat.S_ISREG(before.st_mode) or before.st_size > MAX_STATE_FILE_BYTES:
        raise UnsafeStateError("unsafe_state_file")
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    with os.fdopen(os.open(path, flags), "rb") as handle:
        if _identity(os.fstat(handle.fileno())) != _identity(before):
            raise UnsafeStateError("state_changed_during_scan")
        raw = handle.read(MAX_STATE_FILE_BYTES + 1)
        if len(raw) > MAX_STATE_FILE_BYTES:
            raise UnsafeStateError("oversized_state_file")
        if _identity(os.fstat(handle.fileno())) != _identity(before):
            raise UnsafeStateError("state_changed_during_scan")
    if _identity(path.lstat()) != _identity(before):
        raise UnsafeStateError("state_changed_during_scan")
    return json.loads(raw, object_pairs_hook=_unique_object, parse_constant=_invalid_constant)


def _walk_error(_error: OSError) -> None:
    raise UnsafeStateError("incomplete_state_scan")


def scan_state(root: Path) -> tuple[set[str], int, set[str], set[str]]:
    """All JSON, across workspaces and retained histories; no silent skips."""
    _directory(root)
    names: set[str] = set()
    active = 0
    declared: set[str] = set()
    optional: set[str] = set()
    required: set[str] = set()
    for base, dirs, files in os.walk(root, followlinks=False, onerror=_walk_error):
        directory = Path(base)
        _directory(directory)
        for name in dirs:
            _directory(directory / name)
        for name in files:
            if not name.endswith(".json"):
                continue
            path = directory / name
            data = _read_json(path)
            declarations = _declared_checkpoints(data)
            planned = _planned_checkpoint(path, root, data)
            declared.update(declarations)
            optional.update(planned)
            required.update(declarations - planned)
            # Parse first: escaped JSON strings can contain archive references too.
            names.update(ARCHIVE.findall(json.dumps(data, ensure_ascii=False)))
            if name != "publication.json" or path.relative_to(root).parts[0] != "cell-publications":
                continue
            if not isinstance(data, dict) or not isinstance(data.get("history", []), list):
                raise UnsafeStateError("invalid_publication_state")
            history = data.get("history", [])
            if history:
                latest = history[-1]
                response = latest.get("response") if isinstance(latest, dict) else None
                if not isinstance(response, dict) or not isinstance(response.get("phase"), str):
                    raise UnsafeStateError("invalid_publication_state")
                active += response["phase"] not in TERMINAL_PHASES
    return names, active, declared, optional - required


def _declared_checkpoints(data: Any) -> set[str]:
    result: set[str] = set()
    pending = [data]
    while pending:
        value = pending.pop()
        if isinstance(value, dict):
            if "checkpoint_volume" in value:
                name = value["checkpoint_volume"]
                if not isinstance(name, str) or not VOLUME_NAME.fullmatch(name):
                    raise UnsafeStateError("invalid_declared_checkpoint_volume")
                result.add(name)
            pending.extend(value.values())
        elif isinstance(value, list):
            pending.extend(value)
    return result


def _planned_checkpoint(path: Path, root: Path, data: Any) -> set[str]:
    """Only a complete controller journal can prove a name was merely planned.

    begin() records deterministic resource names before provisioning. Its full
    operations journal is retained, so a missing volume with no checkpoint or
    restore intent can be optional. Every other declaration remains required.
    """
    from yleum_orchestrator.services.cell_state import CellWorkspaceState

    if (
        path.parent != root / "project-cells"
        or not isinstance(data, dict)
        or set(data) != {"version", "workspace"}
        or type(data["version"]) is not int
        or data["version"] != 1
        or not isinstance(data["workspace"], dict)
    ):
        return set()
    try:
        state = CellWorkspaceState.from_dict(data["workspace"])
    except (RuntimeError, ValueError, TypeError, KeyError):
        return set()
    if path.name != f"{state.workspace_id}.json" or state.resource_names is None:
        return set()
    if any("checkpoint" in item.kind or "restor" in item.kind for item in state.operations):
        return set()
    pending = [data]
    while pending:
        value = pending.pop()
        if isinstance(value, dict):
            if value.get("checkpoint_ref") is not None:
                return set()
            pending.extend(value.values())
        elif isinstance(value, list):
            pending.extend(value)
    return {state.resource_names.checkpoint_volume}


def _checkpoint_volumes(
    client: Any,
    declared: set[str],
    optional: set[str],
) -> tuple[list[Any], int]:
    found = set()
    selected = []
    for volume in client.volumes.list():
        name = volume.name
        labels = volume.attrs.get("Labels") or {}
        kinds = {
            labels[key] for key in ("omnia.resource_kind", "yleum.resource_kind") if key in labels
        }
        if not (
            name in declared
            or name.endswith(("-checkpoint", "-checkpoints"))
            or any("checkpoint" in kind for kind in kinds)
        ):
            continue
        if not VOLUME_NAME.fullmatch(name) or not kinds or not kinds <= CHECKPOINT_KINDS:
            raise UnsafeStateError("unknown_checkpoint_identity")
        identities = {
            labels[key] for key in ("omnia.workspace_id", "yleum.workspace_id") if key in labels
        }
        if len(identities) != 1:
            raise UnsafeStateError("unknown_checkpoint_identity")
        identity = identities.pop()
        if str(UUID(identity)) != identity:
            raise UnsafeStateError("unknown_checkpoint_identity")
        match = re.fullmatch(r"(?:omnia|yleum)-cell(?:-test)?-([0-9a-f]{32})-checkpoints?", name)
        if match and UUID(match[1]) != UUID(identity):
            raise UnsafeStateError("conflicting_checkpoint_identity")
        mount = PurePosixPath(volume.attrs.get("Mountpoint", ""))
        if (
            volume.attrs.get("Driver") != "local"
            or not mount.is_absolute()
            or ".." in mount.parts
            or mount.parts[-3:] != ("volumes", name, "_data")
        ):
            raise UnsafeStateError("unsafe_checkpoint_mount")
        selected.append(volume)
        found.add(name)
    if not declared - optional <= found:
        raise UnsafeStateError("missing_declared_checkpoint_volume")
    return selected, len((declared & optional) - found)


def _checkpoint_file(container: Any, path: str) -> set[str]:
    location = PurePosixPath(path)
    if (
        not location.is_relative_to("/checkpoints")
        or ".." in location.parts
        or location.name != "machine.json"
    ):
        raise UnsafeStateError("unsafe_checkpoint_path")
    stream, metadata = container.get_archive(path)
    try:
        if (
            metadata.get("linkTarget")
            or not isinstance(metadata.get("size"), int)
            or metadata["size"] > MAX_STATE_FILE_BYTES
        ):
            raise UnsafeStateError("unsafe_checkpoint_file")
        payload = bytearray()
        for chunk in stream:
            payload.extend(chunk)
            if len(payload) > MAX_STATE_FILE_BYTES + 65536:
                raise UnsafeStateError("oversized_checkpoint_file")
    finally:
        close = getattr(stream, "close", None)
        if close:
            close()
    with tarfile.open(fileobj=io.BytesIO(payload), mode="r:") as archive:
        members = archive.getmembers()
        if (
            len(members) != 1
            or not members[0].isfile()
            or members[0].name != "machine.json"
            or members[0].size != metadata["size"]
        ):
            raise UnsafeStateError("unsafe_checkpoint_file")
        handle = archive.extractfile(members[0])
        if handle is None:
            raise UnsafeStateError("unreadable_checkpoint_file")
        with handle:
            data = json.loads(
                handle.read(MAX_STATE_FILE_BYTES + 1),
                object_pairs_hook=_unique_object,
                parse_constant=_invalid_constant,
            )
    if not isinstance(data, dict) or not isinstance(data.get("reference"), dict):
        raise UnsafeStateError("invalid_checkpoint_machine")
    return set(ARCHIVE.findall(json.dumps(data, ensure_ascii=False)))


def checkpoint_references(
    declared: set[str],
    optional: set[str],
) -> tuple[set[str], int, int, int]:
    """Inventory retained checkpoint metadata, never workspace/database payloads.

    Resolve an already-installed helper image to its immutable ID; create() does
    not pull. Bind existing inspected mountpoints (not named mounts which could
    silently create a missing volume). All helper data access is read-only.
    """
    import docker  # type: ignore[import-untyped]
    from docker.types import LogConfig, Mount  # type: ignore[import-untyped]

    client = None
    try:
        client = docker.from_env(timeout=15)
        volumes, missing_planned = _checkpoint_volumes(client, declared, optional)
        if not volumes:
            return set(), 0, 0, missing_planned
        image_id = client.images.get("alpine:latest").id
        if not re.fullmatch(r"sha256:[0-9a-f]{64}", image_id):
            raise UnsafeStateError("invalid_local_helper_image")
        names: set[str] = set()
        files = 0
        for volume in volumes:
            helper = None
            try:
                helper = client.containers.create(
                    image_id,
                    ["/bin/sh", "-c", "sleep 90"],
                    name=f"omnia-archive-gc-{uuid4().hex}",
                    network_mode="none",
                    read_only=True,
                    cap_drop=["ALL"],
                    security_opt=["no-new-privileges:true"],
                    mem_limit="96m",
                    pids_limit=16,
                    mounts=[
                        Mount(
                            target="/checkpoints",
                            source=volume.attrs["Mountpoint"],
                            type="bind",
                            read_only=True,
                        )
                    ],
                    log_config=LogConfig(type="none"),
                )
                helper.start()
                result = helper.exec_run(["/bin/sh", "-c", _FIND_MACHINE_FILES])
                if result.exit_code != 0 or len(result.output) > 1024 * 1024:
                    raise UnsafeStateError("incomplete_checkpoint_inventory")
                if result.output and not result.output.endswith(b"\0"):
                    raise UnsafeStateError("incomplete_checkpoint_inventory")
                for path in result.output.split(b"\0"):
                    if path:
                        names.update(_checkpoint_file(helper, path.decode("utf-8")))
                        files += 1
            finally:
                if helper is not None:
                    helper.remove(force=True, v=False)
        return names, len(volumes), files, missing_planned
    except Exception as error:
        # Docker exceptions can contain credentials or private daemon details.
        raise UnsafeStateError("checkpoint_inventory_failed") from error
    finally:
        if client is not None:
            client.close()


def _all_references(root: Path, report: dict[str, Any]) -> tuple[set[str], int]:
    referenced, active, declared, optional = scan_state(root)
    checkpoints, volume_count, file_count, missing_planned = checkpoint_references(
        declared, optional
    )
    referenced.update(checkpoints)
    report.update(
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
                    raise UnsafeStateError("active_publication")
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


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state-root", type=Path, default=Path(STATE))
    parser.add_argument("--min-age-seconds", type=int, default=MIN_AGE_SECONDS)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    if args.min_age_seconds < MIN_AGE_SECONDS:
        parser.error("--min-age-seconds must be at least 7200")
    report: dict[str, Any] = {
        "mode": "apply" if args.apply else "dry_run",
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
        root = args.state_root.absolute()
        _directory(root)
        report["disk_free_bytes_before"] = shutil.disk_usage(root).free
        referenced, active = _all_references(root, report)
        grouped, kept = candidates(root, referenced, args.min_age_seconds)
        report.update(
            referenced_names=len(referenced),
            active_publications=active,
            candidate_files=sum(len(items) for items in grouped.values()),
            candidate_bytes=sum(info.st_size for items in grouped.values() for _, info in items),
            kept_bytes=kept,
        )
        if args.apply:
            if active:
                raise UnsafeStateError("active_publication")
            asyncio.run(_apply(root, grouped, args.min_age_seconds, report))
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
    print(json.dumps(report, sort_keys=True))
    return int(report["status"] != "ok")


if __name__ == "__main__":
    raise SystemExit(main())

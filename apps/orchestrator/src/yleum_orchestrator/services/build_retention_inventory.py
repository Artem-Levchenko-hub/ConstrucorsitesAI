"""Read-only, fail-closed inventory shared by build cleanup and its internal API."""
from __future__ import annotations

import hashlib
import io
import json
import os
import re
import stat
import tarfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any
from uuid import UUID, uuid4

from yleum_orchestrator.services.build_retention import (
    LIFECYCLE_KINDS as _LIFECYCLE_KINDS,
)
from yleum_orchestrator.services.build_retention import (
    TERMINAL_LIFECYCLE_PHASES as _TERMINAL_LIFECYCLE_PHASES,
)
from yleum_orchestrator.services.build_retention import (
    CheckpointSelection as CheckpointSelection,
)
from yleum_orchestrator.services.build_retention import checkpoint_selection
from yleum_orchestrator.services.cell_state import CellWorkspaceState

ARCHIVE = re.compile(r"[0-9a-f]{32}\.tar")
IMAGE_ID = re.compile(r"(?<![A-Za-z0-9])sha256:[0-9a-f]{64}(?![A-Za-z0-9])")
_HOST_LABEL = r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?"
_UUID = r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}"
APPLICATION_IMAGE = re.compile(
    rf"{_HOST_LABEL}(?:\.{_HOST_LABEL})*(?::[0-9]{{1,5}})?/max-app/{_UUID}"
    r"(?::[A-Za-z0-9_][A-Za-z0-9_.-]{0,127})?(?:@sha256:[0-9a-f]{64})?"
)


MIN_AGE_SECONDS = 2 * 3600


MAX_STATE_FILE_BYTES = 64 * 1024 * 1024


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


@dataclass(frozen=True, slots=True)
class ProtectedBuildRoots:
    archive_names: frozenset[str]
    image_ids: frozenset[str]
    image_refs: frozenset[str]
    busy_workspaces: frozenset[str]
    active_operations: int
    observed_at: str
    generation: str


def _is_link(info: os.stat_result) -> bool:
    # Junctions are reparse points on Windows, even when reported as directories.
    return stat.S_ISLNK(info.st_mode) or bool(
        getattr(info, "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    )


def _directory(path: Path) -> None:
    """Reject symlinks in every component, including ancestors of state root."""
    for component in (*reversed(path.parents), path):
        info = component.lstat()
        if _is_link(info) or not stat.S_ISDIR(info.st_mode):
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
    if (
        _is_link(before) or not stat.S_ISREG(before.st_mode)
        or before.st_size > MAX_STATE_FILE_BYTES
    ):
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


def _metadata_census(root: Path) -> dict[str, tuple[int, int, int, int]]:
    """Fingerprint the complete tree, including newly created metadata subtrees.

    No file content is opened here. File and directory identities also detect
    replacement, removal, and symlinks outside the ordinary JSON suffix scan.
    """
    _directory(root)
    result = {".": _identity(root.lstat())}
    for base, dirs, files in os.walk(root, followlinks=False, onerror=_walk_error):
        directory = Path(base)
        _directory(directory)
        for name in (*dirs, *files):
            path = directory / name
            info = path.lstat()
            if _is_link(info) or not (stat.S_ISDIR(info.st_mode) or stat.S_ISREG(info.st_mode)):
                raise UnsafeStateError("unsafe_state_entry")
            result[path.relative_to(root).as_posix()] = _identity(info)
    return result


def _image_roots(data: Any, image_ids: set[str] | None, image_refs: set[str] | None) -> None:
    pending = [data]
    while pending:
        value = pending.pop()
        if isinstance(value, dict):
            pending.extend(value.keys())
            pending.extend(value.values())
        elif isinstance(value, list):
            pending.extend(value)
        elif isinstance(value, str):
            if image_ids is not None:
                image_ids.update(IMAGE_ID.findall(value))
            if image_refs is not None and APPLICATION_IMAGE.fullmatch(value):
                image_refs.add(value)


def _workspace_state(root: Path, path: Path, data: Any) -> CellWorkspaceState | None:
    if path.parent != root / "project-cells":
        return None
    if (
        not isinstance(data, dict) or set(data) != {"version", "workspace"}
        or type(data["version"]) is not int or data["version"] != 1
        or not isinstance(data["workspace"], dict)
    ):
        raise UnsafeStateError("invalid_workspace_state")
    try:
        state = CellWorkspaceState.from_dict(data["workspace"])
    except (RuntimeError, ValueError, TypeError, KeyError) as error:
        raise UnsafeStateError("invalid_workspace_state") from error
    if path.name != f"{state.workspace_id}.json":
        raise UnsafeStateError("workspace_identity_mismatch")
    operation_ids = [operation.operation_id for operation in state.operations]
    if len(set(operation_ids)) != len(operation_ids):
        raise UnsafeStateError("ambiguous_workspace_operations")
    return state


def _workspace_activity(state: CellWorkspaceState, busy_workspaces: set[str]) -> int:
    unresolved = sum(
        operation.status not in {"completed", "failed"} for operation in state.operations
    )
    current = state.operation(state.last_operation_id)
    busy = (
        state.active_generation_run_id is not None
        or state.active_generation_fencing_epoch is not None
        or unresolved > 0
        or state.project_id is None or state.owner_id is None or state.resource_names is None
        or any(operation.kind not in _LIFECYCLE_KINDS for operation in state.operations)
        or state.bundle_state not in {"resources_ready", "resources_paused", "retained"}
        or state.phase not in _TERMINAL_LIFECYCLE_PHASES
        or current is None
        or current.phase not in _TERMINAL_LIFECYCLE_PHASES
        or (current is not None and current.fencing_epoch != state.fencing_epoch)
    )
    if busy:
        busy_workspaces.add(str(state.workspace_id))
    return max(unresolved, int(busy))


def _publication_activity(data: Any, busy_workspaces: set[str] | None) -> int:
    if not isinstance(data, dict) or not isinstance(data.get("history", []), list):
        raise UnsafeStateError("invalid_publication_state")
    history = data.get("history", [])
    active = False
    if history:
        latest = history[-1]
        response = latest.get("response") if isinstance(latest, dict) else None
        if not isinstance(response, dict) or not isinstance(response.get("phase"), str):
            raise UnsafeStateError("invalid_publication_state")
        active = response["phase"] not in TERMINAL_PHASES
    active = active or bool(data.get("recovery_required") or data.get("activation_pending"))
    if active and busy_workspaces is not None:
        pending = [data]
        while pending:
            value = pending.pop()
            if isinstance(value, dict):
                for key in ("source_workspace_id", "production_workspace_id", "workspace_id"):
                    identity = value.get(key)
                    if identity is not None:
                        if not isinstance(identity, str) or str(UUID(identity)) != identity:
                            raise UnsafeStateError("invalid_publication_workspace")
                        busy_workspaces.add(identity)
                pending.extend(value.values())
            elif isinstance(value, list):
                pending.extend(value)
    return int(active)


def scan_state(
    root: Path, *, selections: dict[str, CheckpointSelection] | None = None,
    image_ids: set[str] | None = None, image_refs: set[str] | None = None,
    busy_workspaces: set[str] | None = None,
) -> tuple[set[str], int, set[str], set[str]]:
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
            state = _workspace_state(root, path, data)
            if state is not None and busy_workspaces is not None:
                active += _workspace_activity(state, busy_workspaces)
            declarations = _declared_checkpoints(data)
            planned = _planned_checkpoint(path, root, data)
            marker = root / "cell-deletions" / path.name
            deleted = None
            if path.parent == root / "project-cells" and (marker.exists() or marker.is_symlink()):
                deleted = _read_json(marker)
            selection = checkpoint_selection(root, path, data, deleted)
            if selection is not None:
                if selections is not None:
                    previous = selections.get(selection.volume)
                    if previous is not None and previous != selection:
                        raise UnsafeStateError("conflicting_checkpoint_selection")
                    selections[selection.volume] = selection
                if selection.optional_missing:
                    planned.add(selection.volume)
            declared.update(declarations)
            optional.update(planned)
            required.update(declarations - planned)
            # Parse first: escaped JSON strings can contain archive references too.
            names.update(ARCHIVE.findall(json.dumps(data, ensure_ascii=False)))
            _image_roots(data, image_ids, image_refs)
            if name != "publication.json" or path.relative_to(root).parts[0] != "cell-publications":
                continue
            active += _publication_activity(data, busy_workspaces)
    return names, active, declared, optional - required


def collect_roots(root: Path) -> ProtectedBuildRoots:
    """Publish roots only after a complete, stable, read-only state inventory."""
    try:
        root = root.absolute()
        before = _metadata_census(root)
        selections: dict[str, CheckpointSelection] = {}
        image_ids: set[str] = set()
        image_refs: set[str] = set()
        busy_workspaces: set[str] = set()
        names, active, declared, optional = scan_state(
            root, selections=selections, image_ids=image_ids, image_refs=image_refs,
            busy_workspaces=busy_workspaces,
        )
        checkpoints, _, _, _ = checkpoint_references(
            declared, optional, selections=selections, root=root,
            image_ids=image_ids, image_refs=image_refs,
        )
        if _metadata_census(root) != before:
            raise UnsafeStateError("state_changed_during_scan")
        names.update(checkpoints)
        generation = hashlib.sha256(
            json.dumps({
                "metadata": sorted(before.items()),
                "archive_names": sorted(names),
                "image_ids": sorted(image_ids),
                "image_refs": sorted(image_refs),
                "busy_workspaces": sorted(busy_workspaces),
                "active_operations": active,
            }, separators=(",", ":")).encode()
        ).hexdigest()
        return ProtectedBuildRoots(
            archive_names=frozenset(names), image_ids=frozenset(image_ids),
            image_refs=frozenset(image_refs), busy_workspaces=frozenset(busy_workspaces),
            active_operations=active, observed_at=datetime.now(UTC).isoformat(),
            generation=generation,
        )
    except Exception:
        # Raw JSON/Docker/filesystem exceptions can contain private paths or values.
        raise UnsafeStateError("incomplete_build_retention_inventory") from None


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


def _verified_current_environment(root: Path, data: dict[str, Any], workspace_id: str) -> None:
    from yleum_orchestrator.services.machine_environment import (
        MachineEnvironmentRef,
        VolumeEnvironmentRef,
    )

    reference = MachineEnvironmentRef.model_validate(data["reference"])
    if str(reference.workspace_id) != workspace_id:
        raise UnsafeStateError("checkpoint_workspace_mismatch")
    directory = root / "project-machines" / "artifacts" / workspace_id
    _directory(directory)
    items: tuple[MachineEnvironmentRef | VolumeEnvironmentRef, ...] = (
        reference, *reference.volumes,
    )
    for item in items:
        path = directory / item.artifact_ref
        before = path.lstat()
        if not stat.S_ISREG(before.st_mode) or before.st_size != item.size:
            raise UnsafeStateError("current_environment_unavailable")
        marker = path.with_name(path.name + ".ok")
        sealed = _read_json(marker) if marker.exists() or marker.is_symlink() else None
        verified = isinstance(sealed, dict) and all((
            sealed.get("sha256") == item.sha256,
            sealed.get("size") == item.size,
            sealed.get("inode") == before.st_ino,
            sealed.get("mtime_ns") == before.st_mtime_ns,
        ))
        if not verified:
            with os.fdopen(os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)), "rb") as f:
                if _identity(os.fstat(f.fileno())) != _identity(before):
                    raise UnsafeStateError("current_environment_changed")
                if hashlib.file_digest(f, "sha256").hexdigest() != item.sha256:
                    raise UnsafeStateError("current_environment_corrupt")
        if _identity(path.lstat()) != _identity(before):
            raise UnsafeStateError("current_environment_changed")


def _checkpoint_file(
    container: Any, path: str, *, root: Path | None = None, workspace_id: str | None = None,
    image_ids: set[str] | None = None, image_refs: set[str] | None = None,
) -> set[str]:
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
    if root is not None and workspace_id is not None:
        _verified_current_environment(root, data, workspace_id)
    _image_roots(data, image_ids, image_refs)
    return set(ARCHIVE.findall(json.dumps(data, ensure_ascii=False)))


def checkpoint_references(
    declared: set[str],
    optional: set[str],
    *,
    selections: dict[str, CheckpointSelection] | None = None,
    root: Path | None = None,
    image_ids: set[str] | None = None,
    image_refs: set[str] | None = None,
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
                paths = {path.decode("utf-8") for path in result.output.split(b"\0") if path}
                selection = (selections or {}).get(volume.name)
                current_only = selection is not None and selection.refs is not None
                if current_only:
                    assert selection is not None and selection.refs is not None
                    labels = volume.attrs.get("Labels") or {}
                    actual = labels.get("omnia.workspace_id") or labels.get("yleum.workspace_id")
                    if actual != selection.workspace_id:
                        raise UnsafeStateError("checkpoint_workspace_mismatch")
                    wanted = {f"/checkpoints/{ref}/machine.json" for ref in selection.refs}
                    if not wanted <= paths:
                        raise UnsafeStateError("current_checkpoint_missing")
                    paths = wanted
                for path in sorted(paths):
                    names.update(_checkpoint_file(
                        helper, path, root=root if current_only else None,
                        workspace_id=selection.workspace_id if current_only and selection else None,
                        image_ids=image_ids, image_refs=image_refs,
                    ))
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

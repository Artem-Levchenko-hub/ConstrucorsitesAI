from __future__ import annotations

import json
import os
from dataclasses import FrozenInstanceError
from datetime import datetime
from pathlib import Path
from uuid import UUID

import pytest

from tests.test_cleanup_orphan_archives import (
    WORKSPACE,
    archive,
    machine_envelope,
    paused_cell,
)
from tests.test_cleanup_orphan_archives import (
    fake_docker as fake_docker,
)
from yleum_orchestrator.services import build_retention_inventory as inventory

IMAGE_ID = "sha256:" + "1" * 64
OTHER_IMAGE_ID = "sha256:" + "2" * 64
APP = f"registry.yleum.ru/max-app/{WORKSPACE}"
PRODUCTION = "22222222-2222-4222-8222-222222222222"


@pytest.fixture
def without_checkpoints(monkeypatch):
    monkeypatch.setattr(
        inventory, "checkpoint_references", lambda *args, **kwargs: (set(), 0, 0, 0)
    )


def collect(root):
    assert callable(getattr(inventory, "collect_roots", None)), "protected inventory is missing"
    return inventory.collect_roots(root)


def write_json(path: Path, value: object) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")
    return path


def edit_cell(root: Path, **changes):
    paused_cell(root)
    path = root / "project-cells" / f"{WORKSPACE}.json"
    value = json.loads(path.read_text())
    value["workspace"].update(changes)
    return write_json(path, value)


def test_roots_normalize_strings_preserve_host_references_and_are_immutable(
    tmp_path, without_checkpoints,
):
    write_json(tmp_path / "unknown-history.json", {
        "archive": "a" * 32 + ".tar",
        "nested": [IMAGE_ID, APP + ":v1@" + OTHER_IMAGE_ID],
        "generic_digest": "shared/base@sha256:" + "3" * 64,
        "secret": "private-canary-secret",
    })
    roots = collect(tmp_path)
    assert roots.archive_names == frozenset({"a" * 32 + ".tar"})
    assert roots.image_ids == frozenset({IMAGE_ID, OTHER_IMAGE_ID, "sha256:" + "3" * 64})
    assert roots.image_refs == frozenset({APP + ":v1@" + OTHER_IMAGE_ID})
    assert roots.busy_workspaces == frozenset()
    assert roots.active_operations == 0
    assert datetime.fromisoformat(roots.observed_at).utcoffset() is not None
    assert len(roots.generation) == 64
    assert "private-canary-secret" not in repr(roots)
    with pytest.raises(FrozenInstanceError):
        roots.active_operations = 2
    assert collect(tmp_path).generation == roots.generation


def test_escaped_image_reference_is_decoded_before_matching(tmp_path, without_checkpoints):
    (tmp_path / "state.json").write_text(
        '{"image": "registry.yleum.ru\\u002fmax-app\\u002f' + WORKSPACE + ':current"}'
    )
    assert collect(tmp_path).image_refs == frozenset({APP + ":current"})


@pytest.mark.parametrize("value", [
    "https://" + APP + ":v1",
    "user:secret@" + APP + ":v1",
    APP.upper() + ":v1",
    APP + "-extra:v1",
    APP.replace("max-app", "shared") + ":v1",
    APP + ":v1/extra",
    "prefix " + APP + ":v1",
])
def test_only_strict_application_image_references_are_exposed(
    tmp_path, without_checkpoints, value,
):
    write_json(tmp_path / "state.json", {"image": value})
    assert not collect(tmp_path).image_refs


@pytest.mark.parametrize("raw", ["{broken", '{"a":1,"a":2}', '{"a":NaN}'])
def test_corrupt_or_ambiguous_json_aborts_without_leaking_payload(
    tmp_path, without_checkpoints, raw,
):
    (tmp_path / "state.json").write_text(raw + "private-canary-secret")
    with pytest.raises(inventory.UnsafeStateError) as error:
        collect(tmp_path)
    assert "private-canary-secret" not in str(error.value)


def test_unreadable_metadata_aborts_inventory(tmp_path, monkeypatch, without_checkpoints):
    path = write_json(tmp_path / "state.json", {})
    original = os.open

    def denied(location, *args, **kwargs):
        if Path(location) == path:
            raise PermissionError("private-canary-secret")
        return original(location, *args, **kwargs)

    monkeypatch.setattr(os, "open", denied)
    with pytest.raises(inventory.UnsafeStateError) as error:
        collect(tmp_path)
    assert "private-canary-secret" not in str(error.value)


@pytest.mark.parametrize("change", ["existing", "new", "removed"])
def test_complete_metadata_generation_change_during_checkpoint_scan_aborts(
    tmp_path, monkeypatch, change,
):
    path = write_json(tmp_path / "state.json", {"image": IMAGE_ID})

    def changed(*args, **kwargs):
        if change == "existing":
            write_json(path, {"image": OTHER_IMAGE_ID})
        elif change == "new":
            write_json(tmp_path / "new-subtree" / "new.json", {"image": OTHER_IMAGE_ID})
        else:
            path.unlink()
        return set(), 0, 0, 0

    monkeypatch.setattr(inventory, "checkpoint_references", changed)
    with pytest.raises(inventory.UnsafeStateError):
        collect(tmp_path)


def test_metadata_replaced_with_same_size_and_mtime_changes_generation(
    tmp_path, monkeypatch,
):
    path = write_json(tmp_path / "state.json", {"image": IMAGE_ID})
    before = path.stat()

    def replaced(*args, **kwargs):
        temporary = write_json(tmp_path / "replacement", {"image": OTHER_IMAGE_ID})
        os.utime(temporary, ns=(before.st_atime_ns, before.st_mtime_ns))
        os.replace(temporary, path)
        return set(), 0, 0, 0

    monkeypatch.setattr(inventory, "checkpoint_references", replaced)
    with pytest.raises(inventory.UnsafeStateError):
        collect(tmp_path)


@pytest.mark.parametrize("directory", [False, True])
def test_link_in_state_tree_aborts_inventory(tmp_path, without_checkpoints, directory):
    target = tmp_path / "target"
    if directory:
        target.mkdir()
    else:
        write_json(target, {})
    link = tmp_path / ("linked-directory" if directory else "linked.json")
    try:
        link.symlink_to(target, target_is_directory=directory)
    except OSError as error:
        pytest.skip(f"symlink creation unavailable: {type(error).__name__}")
    with pytest.raises(inventory.UnsafeStateError):
        collect(tmp_path)


@pytest.mark.parametrize("change", ["version", "workspace", "filename", "names"])
def test_malformed_known_workspace_identity_aborts(tmp_path, without_checkpoints, change):
    path = edit_cell(tmp_path)
    value = json.loads(path.read_text())
    if change == "version":
        value["version"] = 2
    elif change == "workspace":
        value["workspace"].pop("operations")
    elif change == "names":
        value["workspace"]["resource_names"]["workspace_id"] = PRODUCTION
    else:
        path.unlink()
        path = path.with_name(f"{PRODUCTION}.json")
    write_json(path, value)
    with pytest.raises(inventory.UnsafeStateError):
        collect(tmp_path)


@pytest.mark.parametrize("changes", [
    {"active_generation_run_id": str(UUID(int=777))},
    {"active_generation_fencing_epoch": 3},
    {"bundle_state": "degraded"},
    {"bundle_state": "indeterminate"},
    {"bundle_state": "future-state"},
    {"phase": "future-phase"},
])
def test_unresolved_workspace_lifecycle_is_busy(tmp_path, without_checkpoints, changes):
    edit_cell(tmp_path, **changes)
    roots = collect(tmp_path)
    assert roots.busy_workspaces == frozenset({WORKSPACE})
    assert roots.active_operations >= 1


@pytest.mark.parametrize("status", ["running", "indeterminate", "future-status"])
def test_nonterminal_operation_is_busy(tmp_path, without_checkpoints, status):
    path = edit_cell(tmp_path)
    value = json.loads(path.read_text())
    value["workspace"]["operations"][-1]["status"] = status
    write_json(path, value)
    roots = collect(tmp_path)
    assert roots.busy_workspaces == frozenset({WORKSPACE})
    assert roots.active_operations == 1


def test_healthy_running_application_is_not_an_active_operation(tmp_path, without_checkpoints):
    edit_cell(tmp_path, bundle_state="resources_ready")
    roots = collect(tmp_path)
    assert not roots.busy_workspaces
    assert roots.active_operations == 0


@pytest.mark.parametrize("change", ["kind", "phase", "no-current", "no-owner", "no-names"])
def test_unproven_lifecycle_identity_or_operation_is_busy(
    tmp_path, without_checkpoints, change,
):
    path = edit_cell(tmp_path)
    value = json.loads(path.read_text())
    workspace = value["workspace"]
    if change in {"kind", "phase"}:
        workspace["operations"][-1][change] = "future-operation"
    elif change == "no-current":
        workspace["last_operation_id"] = None
    elif change == "no-owner":
        workspace["owner_id"] = None
    else:
        workspace["resource_names"] = None
    write_json(path, value)
    assert collect(tmp_path).busy_workspaces == frozenset({WORKSPACE})


@pytest.mark.parametrize("phase", ["queued", "pending", "building", "future-phase"])
def test_pending_publication_marks_source_and_production_busy(
    tmp_path, without_checkpoints, phase,
):
    write_json(tmp_path / "cell-publications" / str(UUID(int=3)) / "publication.json", {
        "history": [{"response": {
            "phase": phase, "source_workspace_id": WORKSPACE,
            "production_workspace_id": PRODUCTION,
        }}],
    })
    roots = collect(tmp_path)
    assert roots.active_operations == 1
    assert roots.busy_workspaces == frozenset({WORKSPACE, PRODUCTION})


@pytest.mark.parametrize("flag", ["recovery_required", "activation_pending"])
def test_unresolved_publication_after_terminal_phase_stays_busy(
    tmp_path, without_checkpoints, flag,
):
    write_json(tmp_path / "cell-publications" / str(UUID(int=3)) / "publication.json", {
        flag: True,
        "active_release": {"source_workspace_id": WORKSPACE,
                           "production_workspace_id": PRODUCTION},
        "history": [{"response": {"phase": "failed"}}],
    })
    roots = collect(tmp_path)
    assert roots.active_operations >= 1
    assert roots.busy_workspaces == frozenset({WORKSPACE, PRODUCTION})


def test_finished_publication_does_not_revive_older_pending_journal(
    tmp_path, without_checkpoints,
):
    write_json(tmp_path / "cell-publications" / str(UUID(int=3)) / "publication.json", {
        "history": [{"response": {"phase": "queued"}}, {"response": {"phase": "done"}}],
    })
    assert collect(tmp_path).active_operations == 0


def test_current_checkpoint_retains_image_roots_and_hash_validation(tmp_path, fake_docker):
    current = archive(tmp_path, "b")
    paused_cell(tmp_path)
    fake_docker.output = b"/checkpoints/c1/machine.json\0/checkpoints/c2/machine.json\0"
    envelope = json.loads(machine_envelope(current))
    envelope["image_ref"] = APP + ":current"
    fake_docker.raw_by_path = {
        "/checkpoints/c1/machine.json": json.dumps({"reference": {"image_id": IMAGE_ID}}).encode(),
        "/checkpoints/c2/machine.json": json.dumps(envelope).encode(),
    }
    roots = collect(tmp_path)
    assert roots.image_ids == frozenset({"sha256:" + "e" * 64})
    assert roots.image_refs == frozenset({APP + ":current"})
    assert roots.archive_names == frozenset({current.name})
    assert "/checkpoints/c1/machine.json" not in fake_docker.reads
    current.write_bytes(b"corrupt")
    with pytest.raises(inventory.UnsafeStateError):
        collect(tmp_path)


def test_legacy_checkpoint_keeps_all_image_roots_and_two_argument_interface(
    tmp_path, fake_docker,
):
    fake_docker.output = b"/checkpoints/c1/machine.json\0/checkpoints/c2/machine.json\0"
    fake_docker.raw_by_path = {
        "/checkpoints/c1/machine.json": json.dumps({"reference": {
            "archive": "a" * 32 + ".tar", "image_id": IMAGE_ID, "image": APP + ":old",
        }}).encode(),
        "/checkpoints/c2/machine.json": json.dumps({"reference": {
            "archive": "b" * 32 + ".tar", "image_id": OTHER_IMAGE_ID,
        }}).encode(),
    }
    roots = collect(tmp_path)
    assert roots.image_ids == frozenset({IMAGE_ID, OTHER_IMAGE_ID})
    assert roots.image_refs == frozenset({APP + ":old"})
    names, volumes, files, missing = inventory.checkpoint_references(set(), set())
    assert names == {"a" * 32 + ".tar", "b" * 32 + ".tar"}
    assert (volumes, files, missing) == (1, 2, 0)


@pytest.mark.parametrize("field,first,second", [
    ("image_id", IMAGE_ID, OTHER_IMAGE_ID),
    ("archive", "a" * 32 + ".tar", "b" * 32 + ".tar"),
    ("image", APP + ":first", APP + ":second"),
])
def test_generation_also_binds_checkpoint_roots_outside_host_metadata(
    tmp_path, fake_docker, field, first, second,
):
    fake_docker.raw = json.dumps({"reference": {field: first}}).encode()
    before = collect(tmp_path)
    fake_docker.raw = json.dumps({"reference": {field: second}}).encode()
    assert collect(tmp_path).generation != before.generation

from __future__ import annotations

import asyncio
import json
import os
from dataclasses import asdict
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast
from uuid import UUID, uuid4

import pytest

import omnia_orchestrator.services.cell_state as cell_state_module
from omnia_orchestrator.core.cell_resources import CellResourceNames, LifecycleMutation
from omnia_orchestrator.core.workspace_provider import WorkspaceSpec
from omnia_orchestrator.services.cell_state import CellCredentialStore, CellStateStore


def _spec(workspace_id: UUID) -> WorkspaceSpec:
    return WorkspaceSpec(
        workspace_id=workspace_id,
        project_id=UUID("00000000-0000-0000-0000-000000000002"),
        owner_id=UUID("00000000-0000-0000-0000-000000000003"),
        profile_version="docker-owner-cell-resources-v1",
        generation_run_id=UUID("00000000-0000-0000-0000-000000000005"),
    )


def _mutation(seed: str, fence: int) -> LifecycleMutation:
    return LifecycleMutation(uuid4(), fence, seed * 64)


def _operation_payload(operation_id: UUID | None = None) -> dict[str, Any]:
    return {
        "operation_id": str(operation_id or UUID("00000000-0000-0000-0000-000000000004")),
        "kind": "ensure",
        "status": "completed",
        "phase": "completed",
        "request_digest": "a" * 64,
        "fencing_epoch": 3,
        "generation_run_id": "00000000-0000-0000-0000-000000000005",
        "checkpoint_ref": None,
        "provider_ref": "docker-owner-canary:test",
        "bundle_state": "resources_ready",
        "detail": None,
        "expected_resources": {"postgres": "omnia-cell"},
        "observed_resources": {"postgres": "omnia-cell"},
    }


def _workspace_payload(workspace_id: UUID) -> dict[str, Any]:
    names = CellResourceNames.for_workspace(workspace_id, namespace="test")
    operation_id = UUID("00000000-0000-0000-0000-000000000004")
    return {
        "workspace_id": str(workspace_id),
        "project_id": "00000000-0000-0000-0000-000000000002",
        "owner_id": "00000000-0000-0000-0000-000000000003",
        "profile_version": "docker-owner-cell-resources-v1",
        "phase": "completed",
        "bundle_state": "resources_ready",
        "fencing_epoch": 3,
        "active_generation_run_id": "00000000-0000-0000-0000-000000000005",
        "active_generation_fencing_epoch": 3,
        "last_operation_id": str(operation_id),
        "provider_ref": "docker-owner-canary:test",
        "resource_names": {
            **asdict(names),
            "workspace_id": str(names.workspace_id),
        },
        "operations": [_operation_payload(operation_id)],
    }


def _write_workspace_state(
    store: CellStateStore,
    workspace_id: UUID,
    workspace_payload: dict[str, Any],
) -> None:
    path = store.workspace_path(workspace_id)
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"version": 1, "workspace": workspace_payload}),
        encoding="utf-8",
    )
    path.chmod(0o600)


def test_state_round_trip_is_per_workspace(tmp_path: Path) -> None:
    store = CellStateStore(tmp_path / "project-cells.json")
    workspace_id = UUID("00000000-0000-0000-0000-000000000001")
    spec = _spec(workspace_id)
    mutation = _mutation("a", 1)
    names = CellResourceNames.for_workspace(workspace_id, namespace="test")

    store.begin(spec, mutation, kind="ensure", phase="planned", resource_names=names)
    store.advance(workspace_id, mutation, phase="sidecars_started", bundle_state="running")
    store.complete(
        workspace_id,
        mutation,
        provider_ref="docker-owner-canary:test",
        bundle_state="resources_ready",
    )

    restored = store.load(workspace_id)

    assert restored is not None
    assert restored.resource_names == names
    assert restored.provider_ref == "docker-owner-canary:test"
    assert restored.bundle_state == "resources_ready"
    assert restored.active_generation_run_id == spec.generation_run_id
    assert restored.active_generation_fencing_epoch == mutation.fencing_epoch
    assert [item.operation_id for item in restored.operations] == [mutation.operation_id]
    assert restored.operations[0].generation_run_id == spec.generation_run_id


def test_release_generation_clears_only_matching_active_lease(tmp_path: Path) -> None:
    store = CellStateStore(tmp_path / "project-cells.json")
    workspace_id = uuid4()
    spec = _spec(workspace_id)
    ensure = _mutation("a", 1)
    names = CellResourceNames.for_workspace(workspace_id, namespace="test")
    store.begin(spec, ensure, kind="ensure", phase="planned", resource_names=names)
    store.complete(
        workspace_id,
        ensure,
        provider_ref="docker-owner-canary:test",
        bundle_state="resources_ready",
    )
    release = _mutation("b", 2)
    store.begin(spec, release, kind="release", phase="planned", resource_names=names)

    released = store.release_generation(
        workspace_id,
        release,
        generation_run_id=spec.generation_run_id,
    )

    assert released.active_generation_run_id is None
    assert released.active_generation_fencing_epoch is None
    assert released.bundle_state == "resources_ready"
    assert released.operation(release.operation_id).status == "completed"


def test_release_generation_rejects_wrong_run_without_state_change(tmp_path: Path) -> None:
    store = CellStateStore(tmp_path / "project-cells.json")
    workspace_id = uuid4()
    spec = _spec(workspace_id)
    ensure = _mutation("a", 1)
    names = CellResourceNames.for_workspace(workspace_id, namespace="test")
    store.begin(spec, ensure, kind="ensure", phase="planned", resource_names=names)
    store.complete(workspace_id, ensure, bundle_state="resources_ready")
    release = _mutation("b", 2)
    store.begin(spec, release, kind="release", phase="planned", resource_names=names)

    with pytest.raises(RuntimeError, match="generation lease mismatch"):
        store.release_generation(workspace_id, release, generation_run_id=uuid4())

    state = store.load(workspace_id)
    assert state is not None
    assert state.active_generation_run_id == spec.generation_run_id


def test_ready_reconcile_adopts_recoverable_generation_at_current_fence(
    tmp_path: Path,
) -> None:
    store = CellStateStore(tmp_path / "project-cells.json")
    workspace_id = uuid4()
    spec = _spec(workspace_id)
    ensure = _mutation("a", 1)
    names = CellResourceNames.for_workspace(workspace_id, namespace="test")
    store.begin(spec, ensure, kind="ensure", phase="planned", resource_names=names)
    store.mark_indeterminate(workspace_id, mutation=ensure, detail="response unknown")
    reconcile = _mutation("b", 2)
    store.begin(
        spec,
        reconcile,
        kind="reconcile",
        phase="planned",
        resource_names=names,
    )

    completed = store.complete(
        workspace_id,
        reconcile,
        bundle_state="resources_ready",
    )

    assert completed.active_generation_run_id == spec.generation_run_id
    assert completed.active_generation_fencing_epoch == reconcile.fencing_epoch
    assert completed.operation(reconcile.operation_id).generation_run_id == (spec.generation_run_id)


def test_reconcile_cannot_resurrect_generation_after_completed_release(tmp_path: Path) -> None:
    store = CellStateStore(tmp_path / "project-cells.json")
    workspace_id = uuid4()
    spec = _spec(workspace_id)
    names = CellResourceNames.for_workspace(workspace_id, namespace="test")
    ensure = _mutation("a", 1)
    store.begin(spec, ensure, kind="ensure", phase="planned", resource_names=names)
    store.complete(workspace_id, ensure, bundle_state="resources_ready")
    release = _mutation("b", 2)
    store.begin(spec, release, kind="release", phase="planned", resource_names=names)
    store.release_generation(
        workspace_id,
        release,
        generation_run_id=spec.generation_run_id,
    )
    before = store.load(workspace_id)

    with pytest.raises(RuntimeError, match="reconcile generation lease is not recoverable"):
        store.begin(
            spec,
            _mutation("c", 3),
            kind="reconcile",
            phase="planned",
            resource_names=names,
        )

    assert store.load(workspace_id) == before


def test_begin_rejects_immutable_identity_mismatch_without_mutating_state(tmp_path: Path) -> None:
    store = CellStateStore(tmp_path / "project-cells.json")
    workspace_id = uuid4()
    original_spec = _spec(workspace_id)
    names = CellResourceNames.for_workspace(workspace_id, namespace="test")
    original_mutation = _mutation("a", 1)
    store.begin(
        original_spec,
        original_mutation,
        kind="ensure",
        phase="planned",
        resource_names=names,
    )
    store.complete(
        workspace_id,
        original_mutation,
        provider_ref="docker-owner-canary:test",
        bundle_state="resources_ready",
    )
    mismatched_spec = WorkspaceSpec(
        workspace_id=workspace_id,
        project_id=UUID("00000000-0000-0000-0000-000000000099"),
        owner_id=original_spec.owner_id,
        profile_version=original_spec.profile_version,
    )

    with pytest.raises(RuntimeError, match="immutable identity mismatch"):
        store.begin(
            mismatched_spec,
            _mutation("b", 2),
            kind="ensure",
            phase="planned",
            resource_names=names,
        )

    restored = store.load(workspace_id)
    assert restored is not None
    assert restored.project_id == original_spec.project_id
    assert restored.owner_id == original_spec.owner_id
    assert restored.profile_version == original_spec.profile_version
    assert restored.phase == "completed"
    assert restored.bundle_state == "resources_ready"
    assert restored.last_operation_id == original_mutation.operation_id
    assert len(restored.operations) == 1


def test_corrupt_state_file_fails_closed(tmp_path: Path) -> None:
    store = CellStateStore(tmp_path / "project-cells.json")
    workspace_id = uuid4()
    path = store.workspace_path(workspace_id)
    path.parent.mkdir(mode=0o700, parents=True)
    path.write_text("{not json", encoding="utf-8")
    path.chmod(0o600)

    with pytest.raises(RuntimeError):
        store.load(workspace_id)


def test_state_payload_missing_required_field_fails_closed(tmp_path: Path) -> None:
    store = CellStateStore(tmp_path / "project-cells.json")
    workspace_id = uuid4()
    payload = _workspace_payload(workspace_id)
    del payload["phase"]
    _write_workspace_state(store, workspace_id, payload)

    with pytest.raises(RuntimeError, match="workspace state keys mismatch"):
        store.load(workspace_id)


@pytest.mark.parametrize(
    ("operations", "match"),
    [
        ({}, "operations must be list"),
        ([1], r"operations\[0\] must be object"),
    ],
)
def test_state_payload_requires_exact_operation_list_shape(
    tmp_path: Path,
    operations: object,
    match: str,
) -> None:
    store = CellStateStore(tmp_path / "project-cells.json")
    workspace_id = uuid4()
    payload = _workspace_payload(workspace_id)
    payload["operations"] = operations
    _write_workspace_state(store, workspace_id, payload)

    with pytest.raises(RuntimeError, match=match):
        store.load(workspace_id)


def test_state_payload_rejects_resource_names_from_other_workspace(tmp_path: Path) -> None:
    store = CellStateStore(tmp_path / "project-cells.json")
    workspace_id = uuid4()
    payload = _workspace_payload(workspace_id)
    other = CellResourceNames.for_workspace(uuid4(), namespace="test")
    payload["resource_names"] = {
        **asdict(other),
        "workspace_id": str(other.workspace_id),
    }
    _write_workspace_state(store, workspace_id, payload)

    with pytest.raises(RuntimeError, match=r"resource_names\.workspace_id mismatch"):
        store.load(workspace_id)


def test_state_payload_rejects_nondeterministic_resource_names(tmp_path: Path) -> None:
    store = CellStateStore(tmp_path / "project-cells.json")
    workspace_id = uuid4()
    payload = _workspace_payload(workspace_id)
    resource_names = dict(cast(dict[str, str], payload["resource_names"]))
    resource_names["postgres_container"] = f"{resource_names['postgres_container']}-shadow"
    payload["resource_names"] = resource_names
    _write_workspace_state(store, workspace_id, payload)

    with pytest.raises(RuntimeError, match="deterministic workspace names"):
        store.load(workspace_id)


def test_hardlinked_state_file_fails_closed(tmp_path: Path) -> None:
    store = CellStateStore(tmp_path / "project-cells.json")
    workspace_id = uuid4()
    store.begin(
        _spec(workspace_id),
        _mutation("a", 1),
        kind="ensure",
        phase="planned",
        resource_names=CellResourceNames.for_workspace(workspace_id, namespace="test"),
    )
    state_path = store.workspace_path(workspace_id)
    hardlink_path = state_path.with_suffix(".shadow")
    try:
        os.link(state_path, hardlink_path)
    except OSError:
        pytest.skip("hardlinks unsupported")

    with pytest.raises(RuntimeError, match="hardlink"):
        store.load(workspace_id)


def test_symlinked_state_root_is_rejected(tmp_path: Path) -> None:
    target = tmp_path / "real-state-root"
    target.mkdir(parents=True)
    linked = tmp_path / "linked-state-root"
    try:
        linked.symlink_to(target, target_is_directory=True)
    except (NotImplementedError, OSError):
        pytest.skip("symlinks unsupported")

    store = CellStateStore(linked / "project-cells.json")
    workspace_id = uuid4()

    with pytest.raises(RuntimeError, match="symlink"):
        store.begin(
            _spec(workspace_id),
            _mutation("a", 1),
            kind="ensure",
            phase="planned",
            resource_names=CellResourceNames.for_workspace(workspace_id, namespace="test"),
        )


def test_symlinked_state_leaf_is_rejected_not_treated_as_missing(tmp_path: Path) -> None:
    store = CellStateStore(tmp_path / "project-cells.json")
    workspace_id = uuid4()
    target = tmp_path / "target-state.json"
    target.write_text(
        json.dumps({"version": 1, "workspace": _workspace_payload(workspace_id)}),
        encoding="utf-8",
    )
    path = store.workspace_path(workspace_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        path.symlink_to(target)
    except (NotImplementedError, OSError):
        pytest.skip("symlinks unsupported")

    with pytest.raises(RuntimeError, match="symlink"):
        store.load(workspace_id)


def test_broken_symlink_state_leaf_is_rejected_not_treated_as_missing(tmp_path: Path) -> None:
    store = CellStateStore(tmp_path / "project-cells.json")
    workspace_id = uuid4()
    path = store.workspace_path(workspace_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        path.symlink_to(tmp_path / "missing-state.json")
    except (NotImplementedError, OSError):
        pytest.skip("symlinks unsupported")

    with pytest.raises(RuntimeError, match="symlink"):
        store.load(workspace_id)


def test_state_owner_and_mode_guards_fail_closed(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    module_os = cast(Any, cell_state_module).os
    monkeypatch.setattr(module_os, "name", "posix")
    monkeypatch.setattr(cast(Any, cell_state_module), "_current_uid", lambda: 1000)

    with pytest.raises(RuntimeError, match="unsafe directory owner"):
        cell_state_module._validate_dir_stat(
            cast(os.stat_result, SimpleNamespace(st_uid=1001, st_mode=0o040700)),
            tmp_path,
        )

    with pytest.raises(RuntimeError, match="unsafe directory mode"):
        cell_state_module._validate_dir_stat(
            cast(os.stat_result, SimpleNamespace(st_uid=1000, st_mode=0o040755)),
            tmp_path,
        )

    monkeypatch.setattr(
        module_os,
        "fstat",
        lambda _fd: cast(
            os.stat_result,
            SimpleNamespace(st_mode=0o100644, st_uid=1000, st_nlink=1),
        ),
    )
    with pytest.raises(RuntimeError, match="unsafe file mode"):
        cell_state_module._validate_regular_fd(1, expected_mode=0o600)


def test_credentials_round_trip_is_stable(tmp_path: Path) -> None:
    credentials = CellCredentialStore(tmp_path / "credentials")
    workspace_id = uuid4()

    first = credentials.load_or_create(workspace_id)
    second = credentials.load_or_create(workspace_id)

    assert first == second
    payload = json.loads((tmp_path / "credentials" / f"{workspace_id}.json").read_text("utf-8"))
    assert payload["postgres_password"] == first.postgres_password


def test_credential_broken_symlink_leaf_is_rejected(tmp_path: Path) -> None:
    credentials = CellCredentialStore(tmp_path / "credentials")
    workspace_id = uuid4()
    path = tmp_path / "credentials" / f"{workspace_id}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        path.symlink_to(tmp_path / "missing-credentials.json")
    except (NotImplementedError, OSError):
        pytest.skip("symlinks unsupported")

    with pytest.raises(RuntimeError, match="symlink"):
        credentials.load_or_create(workspace_id)


@pytest.mark.asyncio
async def test_different_workspace_updates_do_not_clobber_each_other(tmp_path: Path) -> None:
    store = CellStateStore(tmp_path / "project-cells.json")
    workspace_one = uuid4()
    workspace_two = uuid4()
    names_one = CellResourceNames.for_workspace(workspace_one, namespace="test")
    names_two = CellResourceNames.for_workspace(workspace_two, namespace="test")

    async def write_state(workspace_id: UUID, fence: int, names: CellResourceNames) -> None:
        spec = _spec(workspace_id)
        mutation = _mutation("a" if fence == 1 else "b", fence)
        await asyncio.to_thread(
            store.begin,
            spec,
            mutation,
            kind="ensure",
            phase="planned",
            resource_names=names,
        )
        await asyncio.to_thread(
            store.complete,
            workspace_id,
            mutation,
            bundle_state="resources_ready",
            provider_ref=f"docker-owner-canary:{workspace_id}",
        )

    await asyncio.gather(
        write_state(workspace_one, 1, names_one),
        write_state(workspace_two, 2, names_two),
    )

    assert store.load(workspace_one) is not None
    assert store.load(workspace_two) is not None
    assert len(store.all_states()) == 2


def test_operation_generation_run_id_round_trips_through_json(tmp_path: Path) -> None:
    store = CellStateStore(tmp_path / "project-cells.json")
    workspace_id = UUID("00000000-0000-0000-0000-000000000001")
    spec = _spec(workspace_id)
    mutation = _mutation("a", 1)

    store.begin(
        spec,
        mutation,
        kind="ensure",
        phase="planned",
        resource_names=CellResourceNames.for_workspace(workspace_id, namespace="test"),
    )
    store.complete(
        workspace_id,
        mutation,
        provider_ref="docker-owner-canary:test",
        bundle_state="resources_ready",
    )

    payload = json.loads(store.workspace_path(workspace_id).read_text(encoding="utf-8"))
    operation = payload["workspace"]["operations"][0]

    assert operation["generation_run_id"] == str(spec.generation_run_id)
    restored = store.load(workspace_id)
    assert restored is not None
    assert restored.operations[0].generation_run_id == spec.generation_run_id

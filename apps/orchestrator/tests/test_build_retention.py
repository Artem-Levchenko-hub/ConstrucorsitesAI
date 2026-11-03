from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from uuid import UUID

import pytest

from yleum_orchestrator.core.cell_resources import CellResourceNames, LifecycleMutation
from yleum_orchestrator.core.workspace_provider import WorkspaceSpec
from yleum_orchestrator.services.build_retention import checkpoint_selection
from yleum_orchestrator.services.cell_state import CellStateStore

WID = UUID("11111111-1111-4111-8111-111111111111")


def payload(root: Path) -> tuple[Path, dict[str, Any]]:
    store = CellStateStore(root / "project-cells")
    spec = WorkspaceSpec(WID, UUID(int=2), UUID(int=3), "docker-owner-cell-resources-v1")
    for epoch in (1, 2):
        mutation = LifecycleMutation(UUID(int=epoch + 10), epoch, str(epoch) * 64)
        store.begin(
            spec,
            mutation,
            kind="pause",
            phase="planned",
            checkpoint_ref=f"c{epoch}",
            resource_names=CellResourceNames.for_workspace(WID),
        )
        store.complete(
            WID, mutation, phase="completed", provider_ref=None, bundle_state="resources_paused"
        )
    path = store.workspace_path(WID)
    return path, json.loads(path.read_text(encoding="utf-8"))


def test_latest_successful_pause_is_the_current_resume_root(tmp_path: Path) -> None:
    path, data = payload(tmp_path)
    result = checkpoint_selection(tmp_path, path, data)
    assert result is not None and result.refs == frozenset({"c2"})


@pytest.mark.parametrize(
    "fault",
    [
        "future_kind",
        "future_kind_no_reference",
        "historical_future_kind",
        "future_phase",
        "reordered",
        "future_epoch",
    ],
)
def test_ambiguous_lifecycle_keeps_all_envelopes(tmp_path: Path, fault: str) -> None:
    path, data = payload(tmp_path)
    ops = data["workspace"]["operations"]
    if fault == "future_kind":
        ops[-1]["kind"] = "future_checkpoint_protocol"
    elif fault == "future_kind_no_reference":
        ops[-1].update(kind="future_protocol", checkpoint_ref=None)
    elif fault == "historical_future_kind":
        ops[0]["kind"] = "future_checkpoint_protocol"
    elif fault == "future_phase":
        data["workspace"]["phase"] = "future_phase"
    elif fault == "reordered":
        ops.reverse()
    else:
        ops[0]["fencing_epoch"] = 3
    result = checkpoint_selection(tmp_path, path, data)
    assert result is not None and result.refs is None


@pytest.mark.parametrize("status", ["running", "indeterminate", "future_status"])
def test_unresolved_operation_protects_all_envelopes(tmp_path: Path, status: str) -> None:
    path, data = payload(tmp_path)
    data["workspace"]["operations"][0]["status"] = status
    result = checkpoint_selection(tmp_path, path, data)
    assert result is not None and result.refs is None


@pytest.mark.parametrize(
    "field,value",
    [
        ("bundle_state", "degraded"),
        ("bundle_state", "future_bundle"),
        ("active_generation_run_id", str(UUID(int=99))),
    ],
)
def test_degraded_or_active_generation_protects_all(
    tmp_path: Path,
    field: str,
    value: str,
) -> None:
    path, data = payload(tmp_path)
    data["workspace"][field] = value
    result = checkpoint_selection(tmp_path, path, data)
    assert result is not None and result.refs is None


@pytest.mark.parametrize("fault", [None, "wrong_workspace", "wrong_operation", "future_epoch"])
def test_missing_deleted_checkpoint_requires_owned_completed_destroy(
    tmp_path: Path,
    fault: str | None,
) -> None:
    path, data = payload(tmp_path)
    state = data["workspace"]
    state["bundle_state"] = "retained"
    state["operations"][-1]["kind"] = "destroy"
    marker = {
        "workspace_id": str(WID),
        "operation_id": state["last_operation_id"],
        "fencing_epoch": state["fencing_epoch"],
    }
    if fault == "wrong_workspace":
        marker["workspace_id"] = str(UUID(int=77))
    elif fault == "wrong_operation":
        marker["operation_id"] = str(UUID(int=77))
    elif fault == "future_epoch":
        marker["fencing_epoch"] = 100
    result = checkpoint_selection(tmp_path, path, data, marker)
    assert result is not None and result.refs is None
    assert result.optional_missing is (fault is None)


def test_incomplete_destroy_cannot_relax_missing_checkpoint(tmp_path: Path) -> None:
    path, data = payload(tmp_path)
    state = data["workspace"]
    state["bundle_state"] = "retained"
    state["operations"][-1].update(kind="destroy", status="indeterminate")
    marker = {
        "workspace_id": str(WID),
        "operation_id": state["last_operation_id"],
        "fencing_epoch": state["fencing_epoch"],
    }
    result = checkpoint_selection(tmp_path, path, data, marker)
    assert result is not None and result.refs is None and not result.optional_missing


def test_unknown_schema_cannot_release_old_checkpoint(tmp_path: Path) -> None:
    path, data = payload(tmp_path)
    data["version"] = 2
    assert checkpoint_selection(tmp_path, path, data) is None


def test_completed_restore_target_is_the_current_root(tmp_path: Path) -> None:
    path, data = payload(tmp_path)
    data["workspace"]["operations"][-1].update(kind="restore", checkpoint_ref="c1")
    result = checkpoint_selection(tmp_path, path, data)
    assert result is not None and result.refs == frozenset({"c1"})

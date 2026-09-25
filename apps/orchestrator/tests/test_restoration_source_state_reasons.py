"""Проверка состояния источника отката называет конкретную причину отказа.

25.09.2026: три подготовки подряд отказали с CellIdentityConflict, а единственный
текст «restoration source identity, fence or activity changed» покрывал четыре разные
ситуации — по нему нельзя было понять, ушла ли эпоха, висит ли аренда генерации или
подменился владелец. Здесь закреплено: каждая причина — своё сообщение с числами.
"""

from __future__ import annotations

from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest

from yleum_orchestrator.core.cell_resources import CellIdentityConflict
from yleum_orchestrator.services.code_restoration_engine import (
    CodeRestorationEngine,
    PreparationNeedsChanges,
)


def _request() -> SimpleNamespace:
    return SimpleNamespace(workspace_id=uuid4(), project_id=uuid4(), owner_id=uuid4())


def _state(request: SimpleNamespace, **overrides: object) -> SimpleNamespace:
    fields: dict[str, object] = {
        "workspace_id": request.workspace_id,
        "project_id": request.project_id,
        "owner_id": request.owner_id,
        "fencing_epoch": 6,
        "active_generation_run_id": None,
    }
    fields.update(overrides)
    return SimpleNamespace(**fields)


def _manager(tmp_path, state: object, *, runtime: bool = True) -> SimpleNamespace:
    return SimpleNamespace(
        profile=SimpleNamespace(state_path=str(tmp_path / "project-cells.json")),
        state_store=SimpleNamespace(load=lambda _workspace_id: state),
        machine_runtime=SimpleNamespace(exists=lambda _workspace_id: True) if runtime else None,
    )


def test_a_healthy_source_passes(tmp_path) -> None:
    request = _request()
    state = _state(request)

    assert CodeRestorationEngine._state(_manager(tmp_path, state), request, epoch=6) is state


def test_a_missing_state_is_named(tmp_path) -> None:
    with pytest.raises(CellIdentityConflict, match="workspace state is missing"):
        CodeRestorationEngine._state(_manager(tmp_path, None), _request(), epoch=6)


@pytest.mark.parametrize("field", ["project_id", "owner_id"])
def test_a_foreign_project_or_owner_is_an_identity_change(tmp_path, field) -> None:
    request = _request()
    state = _state(request, **{field: uuid4()})

    with pytest.raises(CellIdentityConflict, match="identity changed"):
        CodeRestorationEngine._state(_manager(tmp_path, state), request, epoch=6)


def test_a_moved_fence_reports_both_epochs(tmp_path) -> None:
    request = _request()
    state = _state(request, fencing_epoch=5)

    with pytest.raises(CellIdentityConflict, match="expected epoch 6, workspace is at 5"):
        CodeRestorationEngine._state(_manager(tmp_path, state), request, epoch=6)


def test_an_active_generation_lease_names_the_run(tmp_path) -> None:
    request = _request()
    run_id = UUID("00000000-0000-0000-0000-00000000abcd")
    state = _state(request, active_generation_run_id=run_id)

    with pytest.raises(CellIdentityConflict, match=f"active generation {run_id}"):
        CodeRestorationEngine._state(_manager(tmp_path, state), request, epoch=6)


def test_a_missing_machine_runtime_asks_for_changes_not_a_conflict(tmp_path) -> None:
    request = _request()
    state = _state(request)

    with pytest.raises(PreparationNeedsChanges):
        CodeRestorationEngine._state(_manager(tmp_path, state, runtime=False), request, epoch=6)

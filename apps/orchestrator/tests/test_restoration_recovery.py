"""Восстановление после оборвавшегося отката не имеет права ошибиться томом.

21.09 проект остался расщеплённым: авторитетный снимок #7, редактируемое дерево от
#6, база отвязана и цела. Рядом лежит пустая устаревшая база — выбрать её «по
имени» значит потерять данные владельца, показав зелёный результат.

Эти тесты закрепляют два свойства до всякой реализации фаз: намерение нельзя
собрать из кусков разных проектов, и инспекция не смеет ничего запускать или
писать в оригинальный том.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from omnia_orchestrator.schemas.restoration_recovery import (
    MUTATING_PHASES,
    RecoveryFinding,
    RecoveryReport,
    RestorationRecoveryIntent,
)

_HEX32 = "891ed2449b00458babf49f23f194aaab"
_OTHER32 = "aaaaaaaabbbbccccddddeeeeffff0000"
_DIGEST_A = "a" * 64
_DIGEST_B = "b" * 64


def _intent(**overrides: object) -> RestorationRecoveryIntent:
    payload: dict[str, object] = {
        "project_id": "361b3326-97b4-4c73-94d5-c8d277a69dc6",
        "owner_id": "f5a028d8-0000-4000-8000-000000000001",
        "workspace_id": "891ed244-9b00-458b-abf4-9f23f194aaab",
        "current_snapshot_id": "4d1089a5-ef13-5dcd-b313-7f0cf4b88c04",
        "current_commit_sha": "6371b71b9b3ca83ba31119bbc5b4bfc996cc4c94",
        "expected_fencing_epoch": 17,
        "registry_binding_digest": _DIGEST_A,
        "active_code_volume": f"omnia-machine-{_HEX32}-code-4ecaa09ac717487dbe39553062a537e5",
        "active_database_volume": f"omnia-machine-{_HEX32}-db-4ecaa09ac717487dbe39553062a537e5",
        "expected_workspace_inventory_digest": _DIGEST_A,
        "target_inventory_digest": _DIGEST_B,
        "original_db_content_manifest_digest": "c" * 64,
    }
    payload.update(overrides)
    return RestorationRecoveryIntent(**payload)  # type: ignore[arg-type]


def test_a_complete_intent_is_accepted_and_frozen() -> None:
    intent = _intent()

    assert intent.expected_fencing_epoch == 17
    with pytest.raises(ValidationError):
        intent.expected_fencing_epoch = 18  # type: ignore[misc]


def test_recovery_binds_registry_active_database_not_legacy() -> None:
    # Рядом с активным томом лежит пустая устаревшая база приложения. Она проходит
    # по форме имени, но принадлежит другой рабочей области — значит, не она.
    with pytest.raises(ValidationError):
        _intent(active_database_volume=f"omnia-machine-{_OTHER32}-app-postgres-data")


def test_an_intent_cannot_name_the_same_volume_twice() -> None:
    volume = f"omnia-machine-{_HEX32}-db-4ecaa09ac717487dbe39553062a537e5"
    with pytest.raises(ValidationError):
        _intent(active_code_volume=volume, active_database_volume=volume)


def test_an_intent_that_reconciles_nothing_is_refused() -> None:
    # Совпадение описей означает, что чинить нечего; такое намерение — ошибка сборки.
    with pytest.raises(ValidationError):
        _intent(target_inventory_digest=_DIGEST_A)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("current_commit_sha", "not-a-sha"),
        ("expected_fencing_epoch", 0),
        ("registry_binding_digest", "short"),
        ("active_code_volume", "omnia-machine-../etc/passwd"),
        ("active_database_volume", "postgres"),
    ],
)
def test_a_malformed_identity_is_refused_not_normalised(field: str, value: object) -> None:
    with pytest.raises(ValidationError):
        _intent(**{field: value})


def test_no_extra_field_can_smuggle_a_docker_option() -> None:
    with pytest.raises(ValidationError):
        _intent(volumes=["/:/host"])


def test_a_failed_report_must_name_the_finding_that_failed() -> None:
    with pytest.raises(ValidationError):
        RecoveryReport(phase="inspect", ok=False, findings=())

    report = RecoveryReport(
        phase="inspect",
        ok=False,
        findings=(RecoveryFinding(check="fencing_epoch", ok=False, detail="17 != 18"),),
    )
    assert report.ok is False


def test_inspect_is_not_a_mutating_phase() -> None:
    # Инспекцию можно выполнять когда угодно именно потому, что она ничего не меняет.
    assert "inspect" not in MUTATING_PHASES
    assert MUTATING_PHASES[0] == "clone_witness"
    assert MUTATING_PHASES[-1] == "complete"

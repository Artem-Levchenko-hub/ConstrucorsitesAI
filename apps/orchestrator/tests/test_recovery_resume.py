"""Восстановление обязано переживать собственный перезапуск.

Живое восстановление идёт фазами, и между фазами оператор, процесс или сама
машина могут прерваться. Опасность не в том, что работу придётся начать заново,
а в том, что повтор сделает эффект ВТОРОЙ раз: ещё один запуск на той же базе,
ещё одна версия, ещё одно списание. Или наоборот — увидев уже сделанное, повтор
объявит расхождение и навсегда заблокирует восстановление, которое фактически
прошло.

Поэтому продвижение по фазам идёт через журнал: он помнит, что уже пройдено,
сверяет, что продолжают ТО ЖЕ намерение с той же оградой, и не даёт перескочить
фазу. Ниже закреплена матрица прерываний из плана: пауза после публикации копии,
до и после сверки-и-замены исходника, после запуска до проверки и после
завершения до ответа.
"""

from __future__ import annotations

import pytest

from yleum_orchestrator.schemas.restoration_recovery import RecoveryFinding, RecoveryReport
from yleum_orchestrator.services.restoration_recovery import (
    RecoveryConflict,
    advance_recovery,
    intent_digest,
)

from .test_restoration_recovery import _intent

_PHASES = ("clone_witness", "source_sync", "start_current", "verify", "complete")


class _Journal:
    """Журнал в памяти с той же формой, что и файловый: только дозапись."""

    def __init__(self) -> None:
        self.rows: list[object] = []

    def entries(self, project_id: str):
        return [row for row in self.rows if row.project_id == project_id]  # type: ignore[attr-defined]

    def append(self, entry: object) -> None:
        self.rows.append(entry)


def _ok(phase: str) -> RecoveryReport:
    return RecoveryReport(phase=phase, ok=True, findings=(RecoveryFinding(check=phase, ok=True),))  # type: ignore[arg-type]


def _run_all(journal: _Journal, intent, effects: list[str], *, upto: int = len(_PHASES)) -> None:
    for phase in _PHASES[:upto]:
        advance_recovery(
            intent,
            phase,  # type: ignore[arg-type]
            journal=journal,
            perform=lambda p=phase: (effects.append(p), _ok(p))[1],
        )


def test_each_gate_replays_after_a_restart_without_doing_it_twice() -> None:
    """Матрица прерываний: пауза после каждой фазы, затем полный повтор с нуля."""
    intent = _intent()
    for pause_after in range(1, len(_PHASES) + 1):
        journal, effects = _Journal(), []
        _run_all(journal, intent, effects, upto=pause_after)
        assert effects == list(_PHASES[:pause_after])

        # Процесс умер; новый запуск повторяет весь список фаз с начала.
        _run_all(journal, intent, effects)

        assert effects == list(_PHASES), (
            f"пауза после {pause_after}: фазы выполнены не по одному разу"
        )


def test_a_replayed_gate_returns_the_recorded_verdict_without_running_it() -> None:
    intent, journal = _intent(), _Journal()
    calls: list[str] = []

    first = advance_recovery(
        intent, "clone_witness", journal=journal,
        perform=lambda: (calls.append("x"), _ok("clone_witness"))[1],
    )
    second = advance_recovery(
        intent, "clone_witness", journal=journal,
        perform=lambda: pytest.fail("уже пройденная фаза не имеет права выполниться снова"),
    )

    assert calls == ["x"]
    assert first.ok and second.ok and second.phase == "clone_witness"


def test_an_effect_already_in_place_is_resumed_not_repeated() -> None:
    """Обрыв между эффектом и записью в журнал: эффект есть, записи нет.

    Это самый опасный случай. Повторять эффект нельзя — на фазе запуска это второй
    контейнер на той же базе. Объявить расхождение тоже нельзя — восстановление
    фактически прошло и застрянет навсегда.
    """
    intent, journal = _intent(), _Journal()
    advance_recovery(intent, "clone_witness", journal=journal, perform=lambda: _ok("clone_witness"))

    report = advance_recovery(
        intent,
        "source_sync",
        journal=journal,
        perform=lambda: pytest.fail("эффект уже на месте — выполнять нечего"),
        already_done=lambda: True,
    )

    assert report.ok is True
    assert any(f.check == "resumed" for f in report.findings)


def test_a_phase_whose_effect_is_absent_is_actually_performed() -> None:
    intent, journal = _intent(), _Journal()
    calls: list[str] = []

    advance_recovery(intent, "clone_witness", journal=journal, perform=lambda: _ok("clone_witness"))
    advance_recovery(
        intent, "source_sync", journal=journal,
        perform=lambda: (calls.append("did"), _ok("source_sync"))[1],
        already_done=lambda: False,
    )

    assert calls == ["did"]


def test_a_different_intent_for_the_same_project_is_a_conflict() -> None:
    """Другое намерение — не продолжение, а вторая попытка чинить то же самое."""
    journal = _Journal()
    advance_recovery(
        _intent(), "clone_witness", journal=journal, perform=lambda: _ok("clone_witness")
    )

    with pytest.raises(RecoveryConflict) as caught:
        advance_recovery(
            _intent(current_commit_sha="f" * 40),
            "source_sync",
            journal=journal,
            perform=lambda: pytest.fail("чужое намерение не должно ничего выполнить"),
        )

    assert caught.value.status_code == 409


def test_a_stale_fence_cannot_continue_someone_elses_recovery() -> None:
    journal = _Journal()
    advance_recovery(
        _intent(), "clone_witness", journal=journal, perform=lambda: _ok("clone_witness")
    )

    with pytest.raises(RecoveryConflict):
        advance_recovery(
            _intent(expected_fencing_epoch=18),
            "source_sync",
            journal=journal,
            perform=lambda: pytest.fail("ограда сменилась — это уже другой владелец операции"),
        )


def test_phases_cannot_be_skipped() -> None:
    intent, journal = _intent(), _Journal()

    with pytest.raises(RecoveryConflict):
        advance_recovery(
            intent, "start_current", journal=journal,
            perform=lambda: pytest.fail("запуск до свидетеля и сверки исходника запрещён"),
        )


def test_a_failed_gate_does_not_open_the_next_one() -> None:
    intent, journal = _intent(), _Journal()
    failed = RecoveryReport(
        phase="clone_witness", ok=False,
        findings=(RecoveryFinding(check="clone_sql_witness", ok=False, detail="no rows"),),
    )
    advance_recovery(intent, "clone_witness", journal=journal, perform=lambda: failed)

    with pytest.raises(RecoveryConflict):
        advance_recovery(
            intent, "source_sync", journal=journal,
            perform=lambda: pytest.fail("после провала фазы следующая не открывается"),
        )


def test_a_failed_gate_may_be_retried_in_place() -> None:
    intent, journal = _intent(), _Journal()
    failed = RecoveryReport(
        phase="clone_witness", ok=False,
        findings=(RecoveryFinding(check="clone_sql_witness", ok=False, detail="no rows"),),
    )
    advance_recovery(intent, "clone_witness", journal=journal, perform=lambda: failed)

    retried = advance_recovery(
        intent, "clone_witness", journal=journal, perform=lambda: _ok("clone_witness")
    )

    assert retried.ok is True


def test_inspection_is_not_a_phase_that_can_be_journalled() -> None:
    # Инспекция ничего не меняет, поэтому её нельзя «пройти» и зачесть.
    with pytest.raises(ValueError):
        advance_recovery(
            _intent(), "inspect", journal=_Journal(),  # type: ignore[arg-type]
            perform=lambda: _ok("inspect"),
        )


def test_the_digest_changes_with_every_field_of_the_intent() -> None:
    base = intent_digest(_intent())

    assert len(base) == 64
    assert intent_digest(_intent()) == base, "одно и то же намерение — один и тот же отпечаток"
    assert intent_digest(_intent(expected_fencing_epoch=18)) != base
    assert intent_digest(_intent(current_commit_sha="f" * 40)) != base

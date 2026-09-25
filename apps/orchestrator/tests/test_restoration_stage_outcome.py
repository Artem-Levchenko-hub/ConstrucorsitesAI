"""Причина отказа стадии восстановления доступна без файла на хосте.

24.09.2026: подготовка отката остановилась на «Проверка исторического кода не
прошла; нужна совместимая правка», а в артефактах операции лежали только
attempt.json со state=finished и producer.json. Вывод стадии существовал ровно
в одном месте — restoration-check.log в каталоге машины на core — и оператор
искал его вслепую. Здесь закреплено: стадия, код выхода, таймаут и хвост
вывода записываются в attempt.json вместе с исходом, а владелец в тексте
причины видит стадию, код выхода и последние строки своей же сборки.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest

from omnia_orchestrator.services.code_restoration_engine import (
    CodeRestorationEngine,
    PreparationNeedsChanges,
    owner_visible_tail,
    stage_failure_details,
    stage_output_tail,
)
from omnia_orchestrator.services.restoration_execution import RestorationExecutionJournal

_WORKSPACE = "93602ee5-d028-5747-ab7d-777a3fb1919e"
_BUILD_LOG = (
    b"> next build\n\n   Creating an optimized production build ...\n"
    b" \xe2\x9c\x93 Compiled successfully in 2.1min\n   Collecting build traces ...\n"
    b" ELIFECYCLE  Command failed.\n"
)


class _Result:
    def __init__(self, exit_code: int, output: bytes) -> None:
        self.exit_code = exit_code
        self.output = output


class _Container:
    def __init__(self, result: _Result) -> None:
        self._result = result

    def exec_run(self, argv: list[str], workdir: str) -> _Result:
        return self._result


def _refusal(tmp_path: Path, exit_code: int, output: bytes = _BUILD_LOG) -> PreparationNeedsChanges:
    (tmp_path / _WORKSPACE).mkdir(parents=True, exist_ok=True)
    backend = SimpleNamespace(root=str(tmp_path), workspace_id=_WORKSPACE)
    with pytest.raises(PreparationNeedsChanges) as refused:
        CodeRestorationEngine._command(
            backend, _Container(_Result(exit_code, output)), ["pnpm", "build"], 600, ".", "build:0"
        )
    return refused.value


def test_a_refusal_carries_the_stage_outcome(tmp_path: Path) -> None:
    error = _refusal(tmp_path, 1)

    assert error.details is not None
    assert error.details["result"] == "failed"
    assert error.details["stage"] == "build:0"
    assert error.details["argv"] == ["pnpm", "build"]
    assert error.details["exit_code"] == 1
    assert error.details["timeout_seconds"] == 600
    assert error.details["timed_out"] is False
    assert error.details["output_tail"].endswith("ELIFECYCLE  Command failed.")


def test_a_killed_stage_is_marked_timed_out(tmp_path: Path) -> None:
    error = _refusal(tmp_path, 124)

    assert error.details is not None
    assert error.details["timed_out"] is True
    assert error.details["exit_code"] == 124


def test_the_owner_sees_the_stage_the_exit_code_and_the_last_lines(tmp_path: Path) -> None:
    message = str(_refusal(tmp_path, 1))

    assert "Стадия build:0" in message
    assert "код выхода 1" in message
    assert "ELIFECYCLE  Command failed." in message
    assert "Compiled successfully" in message
    # Вердикт прежний — тесты соседней правки на него опираются.
    assert "совместимая правка" in message
    assert "ограничение платформы" not in message


def test_a_timeout_message_keeps_its_honest_verdict_and_adds_the_tail(tmp_path: Path) -> None:
    message = str(_refusal(tmp_path, 124))

    assert "ограничение платформы" in message
    assert "совместимая правка" not in message
    assert "Стадия build:0" in message
    assert "Collecting build traces" in message


def test_the_owner_tail_is_short_and_single_line() -> None:
    long_tail = "\n".join(f"line {index} " + "x" * 200 for index in range(10))
    visible = owner_visible_tail(long_tail)

    assert "\n" not in visible
    assert len(visible) <= 301  # 300 символов плюс многоточие
    assert visible.endswith("x" * 50)


def test_the_journal_tail_is_printable_and_bounded() -> None:
    noisy = b"\x1b[31merror\x1b[0m\x00 tail" + b"z" * 5000
    tail = stage_output_tail(noisy)

    assert "\x1b" not in tail and "\x00" not in tail
    assert len(tail) <= 1200


def test_details_are_json_serialisable() -> None:
    details = stage_failure_details(
        stage="install",
        argv=["pnpm", "install"],
        cwd=".",
        exit_code=2,
        timeout_seconds=360,
        output=b"boom",
    )

    assert json.loads(json.dumps(details)) == details


def test_finish_attempt_records_the_outcome(tmp_path: Path) -> None:
    journal = RestorationExecutionJournal(tmp_path)
    operation_id = uuid4()
    directory = journal._directory(operation_id)
    (directory / "attempt.json").write_text(
        json.dumps({"attempt_id": "a-1", "stage": "build:0", "state": "running"}), encoding="utf-8"
    )

    journal.finish_attempt(
        operation_id,
        "a-1",
        outcome={
            "result": "failed",
            "exit_code": 124,
            "timed_out": True,
            "output_tail": "ELIFECYCLE",
        },
    )

    saved = json.loads((directory / "attempt.json").read_text(encoding="utf-8"))
    assert saved["state"] == "finished"
    assert saved["outcome"]["exit_code"] == 124
    assert saved["outcome"]["timed_out"] is True


def test_finish_attempt_without_an_outcome_is_unchanged(tmp_path: Path) -> None:
    journal = RestorationExecutionJournal(tmp_path)
    operation_id = uuid4()
    directory = journal._directory(operation_id)
    (directory / "attempt.json").write_text(
        json.dumps({"attempt_id": "a-1", "stage": "launch", "state": "running"}), encoding="utf-8"
    )

    journal.finish_attempt(operation_id, "a-1", state="stopped")

    saved = json.loads((directory / "attempt.json").read_text(encoding="utf-8"))
    assert saved["state"] == "stopped"
    assert "outcome" not in saved

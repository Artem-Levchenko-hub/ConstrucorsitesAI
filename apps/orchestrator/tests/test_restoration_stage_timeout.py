"""Убитая по времени сборка — не «плохой код владельца».

23.09.2026, живой откат проекта 887c0523. Историческая версия собралась:
`next build` дошёл до «Compiled successfully in 2.1min», прошёл типы и линт,
сгенерировал все 15 страниц — и был убит на последнем шаге. В логе осталось
только «ELIFECYCLE Command failed» без единой ошибки, а владелец прочитал:
«Проверка исторического кода не прошла; нужна совместимая правка».

Правки не требовалось. Стадии давали 420 секунд при собственном лимите сборки
проекта в 600, да ещё в проверочной ячейке с вдвое меньшим числом ядер: сборка
обязана быть МЕДЛЕННЕЕ обычной, а времени ей давали МЕНЬШЕ. Откат при таком
правиле не мог завершиться ни у одного приложения, чья сборка близка к своему
бюджету.

Здесь закреплено и правило времени, и честность ответа: ограничение платформы
нельзя выдавать за ошибку в коде версии — по такому сообщению владелец пойдёт
править то, что исправно.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from omnia_orchestrator.services.code_restoration_engine import (
    CodeRestorationEngine,
    PreparationNeedsChanges,
    stage_timeout_seconds,
)


def test_a_stage_gets_at_least_what_the_project_allows_itself() -> None:
    """Главное правило: не меньше собственного лимита проекта."""
    assert stage_timeout_seconds(600) == 600
    assert stage_timeout_seconds(900) == 900


def test_the_old_ceiling_no_longer_starves_a_normal_build() -> None:
    # 420 — ровно то число, на котором умер живой откат.
    assert stage_timeout_seconds(600) > 420


def test_an_absurd_limit_is_still_bounded() -> None:
    """Кандидат не должен занимать проверочный слот бесконечно."""
    assert stage_timeout_seconds(10**9) == 1800
    assert stage_timeout_seconds(0) == 1


class _Result:
    def __init__(self, exit_code: int, output: bytes = b"build log") -> None:
        self.exit_code = exit_code
        self.output = output


class _Container:
    def __init__(self, result: _Result) -> None:
        self._result = result
        self.argv: list[str] = []

    def exec_run(self, argv: list[str], workdir: str) -> _Result:
        self.argv = argv
        return self._result


def _run(tmp_path: Path, exit_code: int, timeout: int = 600) -> None:
    workspace = "93602ee5-d028-5747-ab7d-777a3fb1919e"
    (tmp_path / workspace).mkdir(parents=True, exist_ok=True)
    backend = SimpleNamespace(root=str(tmp_path), workspace_id=workspace)
    CodeRestorationEngine._command(
        backend,
        _Container(_Result(exit_code)),
        ["pnpm", "build"],
        timeout,
    )


def test_a_build_killed_by_the_limit_says_so_and_blames_nobody(tmp_path: Path) -> None:
    with pytest.raises(PreparationNeedsChanges) as refused:
        _run(tmp_path, 124)

    message = str(refused.value)
    assert "не уложилась в 600 с" in message
    assert "ограничение платформы" in message
    assert "совместимая правка" not in message


def test_a_genuinely_broken_build_still_asks_for_a_compatible_edit(tmp_path: Path) -> None:
    # Обратная сторона: настоящую поломку нельзя списывать на время.
    with pytest.raises(PreparationNeedsChanges) as refused:
        _run(tmp_path, 1)

    assert "совместимая правка" in str(refused.value)
    assert "ограничение платформы" not in str(refused.value)


def test_a_successful_stage_raises_nothing(tmp_path: Path) -> None:
    _run(tmp_path, 0)


def test_the_log_carries_the_exit_code_for_the_operator(tmp_path: Path) -> None:
    """Без кода выхода оператор не отличит убитую сборку от сломанной."""
    workspace = "93602ee5-d028-5747-ab7d-777a3fb1919e"
    (tmp_path / workspace).mkdir(parents=True, exist_ok=True)

    with pytest.raises(PreparationNeedsChanges):
        _run(tmp_path, 124, timeout=600)

    log = (tmp_path / workspace / "restoration-check.log").read_bytes()
    assert b"exit_code=124" in log
    assert b"timeout=600s" in log
    assert b"build log" in log


def test_the_log_stays_private(tmp_path: Path) -> None:
    workspace = "93602ee5-d028-5747-ab7d-777a3fb1919e"
    (tmp_path / workspace).mkdir(parents=True, exist_ok=True)

    with pytest.raises(PreparationNeedsChanges):
        _run(tmp_path, 1)

    log = tmp_path / workspace / "restoration-check.log"
    assert log.stat().st_mode & 0o077 == 0

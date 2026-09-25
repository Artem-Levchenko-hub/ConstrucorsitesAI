"""Провал отката обязан называть причину, когда причина есть.

23.09.2026 откат проекта на проде встал в «провалено» с текстом «Не удалось
завершить проверку восстановления». Больше нигде не было ничего: в базе пусто,
в журнале одно имя класса исключения. Настоящая причина — нехватка ёмкости для
проверочной ячейки — не доехала ни до владельца, ни до оператора, и на поиск
ушёл час.

Здесь закреплено ровно две вещи, и вторая не менее важна первой:

  * ресурсный отказ доносится словами — владельцу есть что с этим сделать
    (подождать) или кого позвать (оператора, если дело в настройке);
  * любой другой сбой по-прежнему не рассказывает о себе ничего. Внутренние
    сообщения могут содержать что угодно, вплоть до кусков чужих данных, и
    выносить их владельцу нельзя даже ради удобства расследования.
"""

from __future__ import annotations

import json

import pytest

from tests.test_code_restorations import Engine, request, service, status
from yleum_orchestrator.core.cell_resources import (
    CellCapacityUnavailable,
    CellVerificationBudgetTooSmall,
)

_BLANKET = "Не удалось завершить проверку восстановления."


async def _failed_with(tmp_path, error: Exception) -> dict:
    engine = Engine()

    async def refusing(_body):
        raise error

    engine.prepare = refusing
    svc = service(tmp_path, engine)
    value = request()
    await svc.prepare(value)
    await svc.drain()
    return await status(svc, value)


async def test_a_capacity_shortage_tells_the_owner_to_wait(tmp_path) -> None:
    result = await _failed_with(tmp_path, CellCapacityUnavailable("insufficient_cpu"))

    assert result["state"] == "failed"
    assert "повторите позже" in result["error"]


async def test_a_misconfigured_budget_is_named_as_configuration_not_as_shortage(
    tmp_path,
) -> None:
    """Разные беды — разные указания: одну пережидают, другую чинят."""
    result = await _failed_with(
        tmp_path,
        CellVerificationBudgetTooSmall("бюджет проверок 1 ядра меньше одной ячейки (1.45 ядра)"),
    )

    assert "Ошибка настройки сервера" in result["error"]
    assert "повторите позже" not in result["error"]


@pytest.mark.parametrize(
    "reason",
    ["insufficient_memory", "insufficient_disk", "insufficient_verification_cpu"],
)
async def test_every_capacity_reason_reaches_the_owner_in_words(tmp_path, reason: str) -> None:
    result = await _failed_with(tmp_path, CellCapacityUnavailable(reason))

    assert result["error"] != _BLANKET, f"причина {reason} осталась безымянной"


async def test_an_unrelated_failure_still_says_nothing_about_itself(tmp_path) -> None:
    # Главная страховка правки: объяснять можно только ресурсные отказы.
    result = await _failed_with(tmp_path, RuntimeError("private password failure"))

    assert result["error"] == _BLANKET
    assert "private" not in json.dumps(result)
    assert "password" not in json.dumps(result)


async def test_the_reason_is_added_to_the_known_sentence_not_instead_of_it(tmp_path) -> None:
    # Прежний текст остаётся началом строки: на него уже смотрят и люди,
    # и разбор ответа на стороне платформы.
    result = await _failed_with(tmp_path, CellCapacityUnavailable("insufficient_cpu"))

    assert result["error"].startswith(_BLANKET)

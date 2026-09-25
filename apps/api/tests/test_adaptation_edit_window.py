"""Первый ход адаптации не помещается в срок обычной правки.

25.09.2026, прод, проект b5c4c26d, прогон dab6c832. Агент адаптации работал
ровно до секунды отсечки: шлюз моделей записал 19 оплаченных вызовов подряд с
06:54:03 по 07:17:13, а прогон был остановлен с «generation deadline exceeded;
stage=edit». До починки дело не дошло вовсе — её окно даже не открылось.

Это второй замер подряд, и они сходятся. Предыдущий прогон уложил первый ход в
1393 секунды — то есть впритык под тот же потолок 1500 — и упёрся уже в починку.
Работа у адаптации объёмнее обычной правки по существу: она читает описание
схемы, читает новые миграции, переписывает маршруты исторической версии под
текущую базу и ставит проверочную точку, доказывающую работу с данными.

Разгонять обычную правку заодно нельзя: убежавшая генерация жжёт деньги владельца
тем дольше, чем шире окно, а обычные правки в этот потолок укладываются. Поэтому
у адаптации свой срок на первый ход — как уже есть свои сроки на починку и на
передачу доказательства.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from yleum_api.core.config import Settings
from yleum_api.models.generation_run import GenerationRun
from yleum_api.services.generation_deadline import generation_deadline


@pytest.fixture(autouse=True)
def _settings_from_declared_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    """Считать сроки по объявленным значениям, без поднятой базы.

    Настройки целиком требуют подключения к базе, а здесь проверяется ровно
    развилка «обычная правка против адаптации», а не чтение окружения.
    """
    monkeypatch.setattr(
        "yleum_api.services.generation_deadline.get_settings",
        lambda: SimpleNamespace(
            max_generation_deadline_seconds=_default("max_generation_deadline_seconds"),
            restoration_adaptation_edit_seconds=_default(
                "restoration_adaptation_edit_seconds"
            ),
            restoration_adaptation_repair_seconds=_default(
                "restoration_adaptation_repair_seconds"
            ),
            restoration_adaptation_activation_seconds=_default(
                "restoration_adaptation_activation_seconds"
            ),
        ),
    )

_T0 = datetime(2026, 9, 25, 6, 51, tzinfo=UTC)
# Столько первый ход отработал, прежде чем его остановили на полном ходу.
_LIVE_FIRST_TURN_NEEDED = 1500


def _default(name: str) -> int:
    return int(Settings.model_fields[name].default)


def _run(*, adaptation: bool) -> GenerationRun:
    run_id = uuid.uuid4()
    state: dict[str, object] = {}
    if adaptation:
        state["restoration_adaptation"] = {
            "operation_id": str(uuid.uuid4()),
            "adaptation_run_id": str(run_id),
        }
    return GenerationRun(
        id=run_id,
        project_id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        status="running",
        created_at=_T0,
        started_at=_T0,
        agent_state=state,
    )


def test_the_adaptation_first_turn_outlives_what_the_live_run_ran_out_of() -> None:
    """Прежнего потолка не хватило ровно в край — значит он мал по определению."""
    assert _default("restoration_adaptation_edit_seconds") > _LIVE_FIRST_TURN_NEEDED


def test_an_ordinary_edit_keeps_its_own_shorter_limit() -> None:
    # Обычную правку расширять нельзя: проблема была не в ней, а цена ошибки —
    # деньги владельца, потраченные убежавшей генерацией.
    assert _default("max_generation_deadline_seconds") == 1500


def test_an_ordinary_run_is_measured_by_the_ordinary_limit() -> None:
    deadline = generation_deadline(_run(adaptation=False))

    assert deadline.stage == "edit"
    assert deadline.at == _T0 + timedelta(seconds=_default("max_generation_deadline_seconds"))


def test_an_adaptation_run_is_measured_by_its_own_limit() -> None:
    """Главное: тот же самый первый ход у адаптации получает больше времени."""
    deadline = generation_deadline(_run(adaptation=True))

    assert deadline.stage == "edit"
    assert deadline.at == _T0 + timedelta(
        seconds=_default("restoration_adaptation_edit_seconds")
    )


def test_the_adaptation_limit_is_never_shorter_than_the_ordinary_one() -> None:
    """Иначе настройка однажды сделает адаптации хуже, чем обычной правке."""
    assert _default("restoration_adaptation_edit_seconds") >= _default(
        "max_generation_deadline_seconds"
    )


def test_the_repair_window_is_left_alone() -> None:
    # Починка — отдельная беда с отдельным замером; её потолок здесь не трогаем.
    assert _default("restoration_adaptation_repair_seconds") == 3600


def test_the_production_compose_repeats_every_deadline_the_code_declares() -> None:
    """Прод берёт срок из compose, а не из кода, и молчаливое расхождение уже стоило прогона.

    25.09 окно починки было поднято в коде до 1800, а compose продолжал
    подставлять 900 — прод работал по старому сроку, и это заметили только со
    второго раза (5f86e9ff). Здесь закреплено, что ни один срок не разъедется.
    """
    from pathlib import Path

    compose = (
        Path(__file__).resolve().parents[2] / "llm-gateway/deploy/full/docker-compose.yml"
    ).read_text(encoding="utf-8")

    for field, variable in (
        ("max_generation_deadline_seconds", "MAX_GENERATION_DEADLINE_SECONDS"),
        ("restoration_adaptation_edit_seconds", "RESTORATION_ADAPTATION_EDIT_SECONDS"),
        ("restoration_adaptation_repair_seconds", "RESTORATION_ADAPTATION_REPAIR_SECONDS"),
        (
            "restoration_adaptation_activation_seconds",
            "RESTORATION_ADAPTATION_ACTIVATION_SECONDS",
        ),
    ):
        expected = f"{variable}: ${{{variable}:-{_default(field)}}}"
        # Срок нужен и апи, и воркеру генераций — иначе сторож и агент разойдутся.
        assert compose.count(expected) == 2, f"{variable}: compose расходится с кодом"

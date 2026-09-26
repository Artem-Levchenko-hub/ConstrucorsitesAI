"""Сколько сборок идут одновременно — это настройка, а не число в коде.

Владелец 26.09 спросил, сколько пользователей могут запустить генерацию разом.
Ответ упирался не в железо: мощности ячеек после правки учёта хватает на 14 на
площадку, а воркер пускал ровно 8 — и восьмёрка была вписана в код, то есть
менялась только пересборкой образа.

Здесь закреплено поведение предела и то, по чему его можно двигать осознанно:
воркер сообщает в сердцебиении, сколько сборок идёт и сколько разрешено.
"""

from __future__ import annotations

import json
from uuid import UUID, uuid4

import pytest

from yleum_api.core.config import Settings
from yleum_api.services.readiness import parse_worker_heartbeat
from yleum_api.workers.generation import (
    dispatch_heartbeat_payload,
    select_runs_to_start,
)


def _ids(count: int) -> list[UUID]:
    return [uuid4() for _ in range(count)]


def test_the_limit_is_a_setting_with_a_sane_range() -> None:
    assert Settings.model_fields["generation_worker_max_concurrent"].default == 8
    with pytest.raises(ValueError):
        Settings(generation_worker_max_concurrent=0)
    with pytest.raises(ValueError):
        Settings(generation_worker_max_concurrent=65)


def test_an_empty_worker_starts_exactly_the_allowed_number() -> None:
    candidates = _ids(20)

    starting = select_runs_to_start(candidates, {}, 8)

    assert len(starting) == 8
    assert starting == candidates[:8], "порядок очереди должен сохраняться"


def test_raising_the_setting_really_lets_more_through() -> None:
    """Смысл всей правки: число меняется настройкой, а не пересборкой."""
    candidates = _ids(20)

    assert len(select_runs_to_start(candidates, {}, 8)) == 8
    assert len(select_runs_to_start(candidates, {}, 12)) == 12


def test_a_busy_worker_starts_only_what_fits() -> None:
    candidates = _ids(10)
    active = {run_id: object() for run_id in candidates[:6]}

    starting = select_runs_to_start(candidates, active, 8)

    assert starting == candidates[6:8], "две свободные ячейки — две новые сборки"


def test_a_full_worker_starts_nothing_and_does_not_go_negative() -> None:
    candidates = _ids(5)
    active = {run_id: object() for run_id in _ids(9)}

    assert select_runs_to_start(candidates, active, 8) == []


def test_a_run_already_in_flight_is_never_started_twice() -> None:
    """Повторный запуск той же сборки — это двойные эффекты, а не ускорение."""
    candidates = _ids(4)
    active = {candidates[1]: object()}

    starting = select_runs_to_start(candidates, active, 8)

    assert candidates[1] not in starting
    assert starting == [candidates[0], candidates[2], candidates[3]]


def test_the_heartbeat_says_how_loaded_the_worker_is() -> None:
    payload = json.loads(dispatch_heartbeat_payload(active=8, limit=8))

    assert payload["active"] == 8
    assert payload["limit"] == 8, "равенство active и limit = предел упёрся"
    assert "release_sha" in payload and "at" in payload


def test_the_older_reader_still_understands_the_heartbeat() -> None:
    """Проба готовности платформы берёт отсюда только ревизию — новые поля ей не мешают."""
    alive, release = parse_worker_heartbeat(dispatch_heartbeat_payload(active=1, limit=8))

    assert alive is True
    assert release != ""


def test_the_limit_is_read_from_settings_on_every_scan(monkeypatch: pytest.MonkeyPatch) -> None:
    """Связь предела с настройкой — именно то, ради чего правка делалась.

    Читается на каждом заходе, поэтому новое значение подхватывается перезапуском
    воркера, без пересборки образа.
    """
    from types import SimpleNamespace

    from yleum_api.workers import generation

    monkeypatch.setattr(
        generation, "get_settings", lambda: SimpleNamespace(generation_worker_max_concurrent=12)
    )
    assert generation.current_dispatch_limit() == 12

    monkeypatch.setattr(
        generation, "get_settings", lambda: SimpleNamespace(generation_worker_max_concurrent=0)
    )
    assert generation.current_dispatch_limit() == 1, "ноль остановил бы все сборки"


def test_health_shows_the_load_so_the_limit_can_be_raised_on_evidence() -> None:
    """«3/8» в health отвечает на вопрос «предел мешает или нет» без доступа к Redis."""
    from yleum_api.services.readiness import parse_worker_load

    assert parse_worker_load(dispatch_heartbeat_payload(active=3, limit=8)) == "3/8"
    assert parse_worker_load(dispatch_heartbeat_payload(active=8, limit=8)) == "8/8"


@pytest.mark.parametrize(
    "raw",
    [
        None,
        b"",
        b"not json at all",
        b'{"release_sha":"abc"}',  # старое сердцебиение без полей загрузки
        '{"active":"три","limit":8}',  # строка вместо числа
    ],
)
def test_an_old_or_broken_heartbeat_does_not_break_health(raw: bytes | str | None) -> None:
    from yleum_api.services.readiness import parse_worker_load

    assert parse_worker_load(raw) == "unknown"

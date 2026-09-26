"""Шлюз, который не может записать списание, не должен отвечать «всё хорошо».

25–26.09.2026, прод. Во время окна переименования контейнеров у шлюза сменилась
подсеть, и при старте он не смог подключиться к базе. Падение было поймано и
записано предупреждением «mode=degraded», после чего шлюз продолжил работать —
и отвечать на health кодом 200.

Дальше десять часов каждый вызов модели проходил успешно (провайдер отвечал 200,
деньги тратились), а запись списания падала с «пул базы не инициализирован».
Наружу при этом уходило «провайдер недоступен» — то есть сообщение указывало на
чужую сторону. В журнале расхода за эти десять часов ноль записей.

Две беды сразу, и обе закрываются здесь:
  * здоровье врало — по нему ни человек, ни smoke не могли увидеть поломку;
  * соединение не восстанавливалось само: доступ к базе открыли через семь минут
    после падения, но шлюз об этом так и не узнал до ручного пересоздания.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from yleum_gateway.core import db
from yleum_gateway.main import app


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


def test_health_is_not_ok_without_a_place_to_record_charges(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Главное: без базы «всё хорошо» отвечать нельзя.

    Именно это молчание и стоило десяти часов оплаченных и выброшенных ответов.
    """
    monkeypatch.setattr(db, "_pool", None)
    monkeypatch.setattr(db, "try_init_pool", lambda: _refuse())

    response = client.get("/health")

    assert response.status_code == 503
    assert response.json()["status"] != "ok"


def test_health_names_what_is_broken(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Одного «плохо» мало: дежурный должен понять, куда смотреть."""
    monkeypatch.setattr(db, "_pool", None)
    monkeypatch.setattr(db, "try_init_pool", lambda: _refuse())

    body = client.get("/health").json()

    assert "database" in str(body).lower()


def test_health_recovers_by_itself_once_the_database_is_reachable(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Вторая половина беды: доступ открыли, а шлюз об этом не узнал.

    Правила доступа к базе добавили через семь минут после падения, но шлюз
    продолжал считать себя без базы до ручного пересоздания контейнера. Проверка
    здоровья теперь сама пробует поднять пул — и восстановление не требует
    человека.
    """
    monkeypatch.setattr(db, "_pool", None)
    attempts: list[int] = []

    async def try_init_pool() -> object | None:
        attempts.append(1)
        if len(attempts) < 2:
            return None
        db._pool = object()  # база снова доступна
        return db._pool

    monkeypatch.setattr(db, "try_init_pool", try_init_pool)

    first = client.get("/health")
    second = client.get("/health")

    assert first.status_code == 503
    assert second.status_code == 200
    assert len(attempts) == 2, "здоровье обязано пробовать подняться, а не просто мерить"


def test_a_working_gateway_still_answers_ok(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Защита от перестраховки: рабочий шлюз не должен вдруг начать краснеть."""
    monkeypatch.setattr(db, "_pool", object())

    response = client.get("/health")

    assert response.status_code == 200
    assert response.json()["status"] == "ok"


async def _refuse() -> None:
    return None

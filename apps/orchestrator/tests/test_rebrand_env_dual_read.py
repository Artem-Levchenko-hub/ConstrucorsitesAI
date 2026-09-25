"""Переменные окружения читаются под новым и старым именем сразу.

Файлы `.env` на серверах и выкатка кода не происходят одномоментно: между ними
всегда есть зазор. Если код умеет только одно имя, в этом зазоре сервис теряет
настройку — и чаще всего молча, подставив умолчание. Поэтому двойное чтение
закреплено тестом: без него оно однажды исчезнет при очередной уборке.
"""

from __future__ import annotations

import pytest

from yleum_orchestrator.core.env import rebrand_env


def test_new_name_is_read(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("YLEUM_RUNTIME_NETWORK", "новая-сеть")
    monkeypatch.delenv("OMNIA_RUNTIME_NETWORK", raising=False)
    assert rebrand_env("RUNTIME_NETWORK") == "новая-сеть"


def test_legacy_name_still_works(monkeypatch: pytest.MonkeyPatch) -> None:
    """Хост, чей .env ещё не переписан, обязан продолжать работать."""
    monkeypatch.delenv("YLEUM_RUNTIME_NETWORK", raising=False)
    monkeypatch.setenv("OMNIA_RUNTIME_NETWORK", "старая-сеть")
    assert rebrand_env("RUNTIME_NETWORK") == "старая-сеть"


def test_new_name_wins_over_legacy(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("YLEUM_RUNTIME_NETWORK", "новая-сеть")
    monkeypatch.setenv("OMNIA_RUNTIME_NETWORK", "старая-сеть")
    assert rebrand_env("RUNTIME_NETWORK") == "новая-сеть"


def test_default_when_neither_is_set(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("YLEUM_RUNTIME_NETWORK", raising=False)
    monkeypatch.delenv("OMNIA_RUNTIME_NETWORK", raising=False)
    assert rebrand_env("RUNTIME_NETWORK", "по-умолчанию") == "по-умолчанию"


def test_empty_new_value_is_not_resurrected_by_legacy(monkeypatch: pytest.MonkeyPatch) -> None:
    """Пустое новое значение задано намеренно и старое его не подменяет."""
    monkeypatch.setenv("YLEUM_WILDCARD_CERT_ROOT", "")
    monkeypatch.setenv("OMNIA_WILDCARD_CERT_ROOT", "/старый/путь")
    assert rebrand_env("WILDCARD_CERT_ROOT", "запасной") == ""

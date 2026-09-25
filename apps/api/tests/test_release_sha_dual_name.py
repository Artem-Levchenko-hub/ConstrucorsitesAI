"""Ревизия релиза читается и под YLEUM_RELEASE_SHA, и под OMNIA_RELEASE_SHA.

Переписать .env на всех хостах и выкатить код одномоментно нельзя, поэтому код
понимает оба имени: новое в приоритете, старое остаётся страховкой от отката на
прежний .env. На совпадении этой ревизии держится проверка целостности релиза,
так что молчаливая потеря настройки здесь дороже обычной.
"""

from __future__ import annotations

import pytest

from yleum_api.core.config import Settings


def _settings(**kwargs: object) -> Settings:
    return Settings(  # type: ignore[arg-type]
        _env_file=None,
        database_url="postgresql+asyncpg://x:x@localhost/x",
        jwt_secret="test-secret",
        **kwargs,
    )


def test_reads_the_new_name(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("YLEUM_RELEASE_SHA", "новая")
    monkeypatch.delenv("OMNIA_RELEASE_SHA", raising=False)
    assert _settings().omnia_release_sha == "новая"


def test_reads_the_legacy_name(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("YLEUM_RELEASE_SHA", raising=False)
    monkeypatch.setenv("OMNIA_RELEASE_SHA", "старая")
    assert _settings().omnia_release_sha == "старая"


def test_new_name_wins(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("YLEUM_RELEASE_SHA", "новая")
    monkeypatch.setenv("OMNIA_RELEASE_SHA", "старая")
    assert _settings().omnia_release_sha == "новая"


def test_field_name_still_assignable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("YLEUM_RELEASE_SHA", raising=False)
    monkeypatch.delenv("OMNIA_RELEASE_SHA", raising=False)
    assert _settings(omnia_release_sha="вручную").omnia_release_sha == "вручную"


def test_default_when_neither_is_set(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("YLEUM_RELEASE_SHA", raising=False)
    monkeypatch.delenv("OMNIA_RELEASE_SHA", raising=False)
    assert _settings().omnia_release_sha == "unknown"

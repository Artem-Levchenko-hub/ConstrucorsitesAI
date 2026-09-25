"""Ревизия релиза читается под обоими именами переменной.

Это самое чувствительное место двойного чтения: по совпадению ревизий двух
оркестраторов проверяется целостность релиза, и если один хост прочитает
настройку, а другой подставит умолчание, `/api/health` отдаст «mixed», а
проверка прода покраснеет без внятной причины.
"""

from __future__ import annotations

from typing import Any, cast

import pytest

from yleum_orchestrator.core.config import Settings


def _settings(**overrides: object) -> Settings:
    return Settings(
        _env_file=None,
        database_url="postgresql://test:test@127.0.0.1:5432/test",
        internal_token="test-internal-token-not-a-real-secret",
        **cast(dict[str, Any], overrides),
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
    """Псевдонимы не должны отнимать возможность задать поле по имени."""
    monkeypatch.delenv("YLEUM_RELEASE_SHA", raising=False)
    monkeypatch.delenv("OMNIA_RELEASE_SHA", raising=False)
    assert _settings(omnia_release_sha="вручную").omnia_release_sha == "вручную"

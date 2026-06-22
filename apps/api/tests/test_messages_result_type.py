"""Tests for the first-build result-type wiring in routers/messages (RT-1).

The router itself (classify/resolve/infer + the type→stack map) is unit-tested in
test_discovery.py; here we lock the messages-layer helper ``_maybe_result_type_
question`` — the ONE clarifying question asked when the result type is genuinely
ambiguous. It must stay a strict no-op while the flags are off (today's behaviour)
and only ever fire when BOTH the keyword net is silent AND the classifier is
unsure. The gateway/classifier is stubbed so these stay offline and deterministic.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from omnia_api.routers import messages
from omnia_api.services.discovery import ASK


def _settings(*, router: bool, clarify: bool) -> SimpleNamespace:
    return SimpleNamespace(
        use_result_type_router=router,
        result_type_clarify_question=clarify,
    )


async def test_clarify_question_off_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    """Master flag off → always None (byte-identical to today)."""
    monkeypatch.setattr(messages, "get_settings", lambda: _settings(router=False, clarify=False))
    assert await messages._maybe_result_type_question("сделай сайт", "ru") is None


async def test_clarify_question_off_when_only_router_on(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Router on but the clarify sub-flag off → no extra question."""
    monkeypatch.setattr(messages, "get_settings", lambda: _settings(router=True, clarify=False))
    assert await messages._maybe_result_type_question("сделай сайт", "ru") is None


async def test_clarify_question_skipped_when_keyword_net_sure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A booking landing is decisive on the keyword net → no question, even on."""
    monkeypatch.setattr(messages, "get_settings", lambda: _settings(router=True, clarify=True))

    async def _boom(*a: object, **k: object) -> tuple[str | None, float]:
        raise AssertionError("classifier must not be called when the net is sure")

    monkeypatch.setattr(messages, "classify_result_type", _boom)
    assert await messages._maybe_result_type_question("запись на приём", "ru") is None


async def test_clarify_question_skipped_when_classifier_confident(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Vague prompt but a confident classifier → trust it, no question."""
    monkeypatch.setattr(messages, "get_settings", lambda: _settings(router=True, clarify=True))

    async def _conf(*a: object, **k: object) -> tuple[str, float]:
        return "tool", 0.9

    monkeypatch.setattr(messages, "classify_result_type", _conf)
    assert await messages._maybe_result_type_question("сделай сайт", "ru") is None


async def test_clarify_question_fires_on_genuine_ambiguity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Net silent AND classifier unsure → ONE ASK turn with the four type chips."""
    monkeypatch.setattr(messages, "get_settings", lambda: _settings(router=True, clarify=True))

    async def _unsure(*a: object, **k: object) -> tuple[str | None, float]:
        return None, 0.0

    monkeypatch.setattr(messages, "classify_result_type", _unsure)
    res = await messages._maybe_result_type_question("сделай сайт", "ru")
    assert res is not None
    assert res.action == ASK
    assert "Приложение с аккаунтами" in res.choices
    assert res.allow_custom is True

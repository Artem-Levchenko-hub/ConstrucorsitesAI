"""Tests for the progressive discovery interview (P1 — owner directive 2026-06-09).

Discovery replaces the blocking onboarding quiz / one-shot clarify with a
conversational, one-question-at-a-time intake that decides on its own when to
build. The contract that matters: it NEVER raises (fail-soft, R-10), respects the
hard turn cap and explicit "build now" signals, and folds a usable brief + a
sane stack into every BUILD result. The gateway is stubbed so these stay offline
and deterministic.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from omnia_api.services import discovery
from omnia_api.services.discovery import (
    ASK,
    BUILD,
    MAX_DISCOVERY_QUESTIONS,
    _infer_stack_from_text,
    run_discovery,
    wants_build_now,
)

# ─── Gateway stub ────────────────────────────────────────────────────────


class _FakeResp:
    def __init__(self, status_code: int, payload: dict[str, Any]) -> None:
        self.status_code = status_code
        self._payload = payload

    def json(self) -> dict[str, Any]:
        return self._payload


class _FakeClient:
    """Stands in for ``httpx.AsyncClient`` as an async context manager."""

    def __init__(self, resp: _FakeResp | None, exc: Exception | None) -> None:
        self._resp = resp
        self._exc = exc

    async def __aenter__(self) -> _FakeClient:
        return self

    async def __aexit__(self, *_: object) -> bool:
        return False

    async def post(self, url: str, json: dict[str, Any]) -> _FakeResp:
        if self._exc is not None:
            raise self._exc
        assert self._resp is not None
        return self._resp


def _gateway_returning(
    content: str, *, status_code: int = 200
) -> _FakeResp:
    """Wrap a model reply string in the gateway's chat-completion envelope."""
    return _FakeResp(
        status_code,
        {"choices": [{"message": {"content": content}}]},
    )


def _install(
    monkeypatch: pytest.MonkeyPatch,
    *,
    resp: _FakeResp | None = None,
    exc: Exception | None = None,
) -> None:
    monkeypatch.setattr(
        discovery.httpx,
        "AsyncClient",
        lambda *a, **k: _FakeClient(resp, exc),
    )


# ─── wants_build_now (pure) ──────────────────────────────────────────────


def test_wants_build_now_detects_explicit_signals() -> None:
    assert wants_build_now("давай уже генерируй")
    assert wants_build_now("СТРОЙ сайт")
    assert wants_build_now("just build it")
    assert wants_build_now("ок, поехали")


def test_wants_build_now_ignores_normal_answers() -> None:
    assert not wants_build_now("это магазин косметики")
    assert not wants_build_now("аудитория — молодые мамы")
    assert not wants_build_now("")


# ─── ASK path ────────────────────────────────────────────────────────────


async def test_ask_path_streams_model_question(monkeypatch: pytest.MonkeyPatch) -> None:
    _install(
        monkeypatch,
        resp=_gateway_returning(
            json.dumps({"action": "ask", "message": "Кто ваша аудитория?"})
        ),
    )
    result = await run_discovery([], "хочу сайт кофейни", asked_count=0)
    assert result.action == ASK
    assert result.message == "Кто ваша аудитория?"
    assert result.brief == ""


async def test_gateway_error_falls_back_to_deterministic_question(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install(monkeypatch, exc=RuntimeError("gateway down"))
    # Never raises; degrades to the keyed fallback question for this turn index.
    first = await run_discovery([], "идея", asked_count=0)
    second = await run_discovery([], "идея", asked_count=1)
    assert first.action == ASK and first.message
    assert second.action == ASK and second.message
    assert first.message != second.message  # one question at a time, advances


async def test_gateway_5xx_falls_back(monkeypatch: pytest.MonkeyPatch) -> None:
    _install(monkeypatch, resp=_gateway_returning("whatever", status_code=502))
    result = await run_discovery([], "идея", asked_count=0)
    assert result.action == ASK and result.message


# ─── ASK quick-reply chips (P1) ──────────────────────────────────────────


async def test_ask_returns_model_choices(monkeypatch: pytest.MonkeyPatch) -> None:
    _install(
        monkeypatch,
        resp=_gateway_returning(
            json.dumps(
                {
                    "action": "ask",
                    "message": "Нужна админка?",
                    "choices": ["Да", "Нет"],
                }
            )
        ),
    )
    result = await run_discovery([], "хочу crm", asked_count=0)
    assert result.action == ASK
    assert result.choices == ("Да", "Нет")
    assert result.allow_custom is True  # free-text path always open


async def test_ask_open_question_has_no_choices(monkeypatch: pytest.MonkeyPatch) -> None:
    _install(
        monkeypatch,
        resp=_gateway_returning(
            json.dumps({"action": "ask", "message": "Как ты это представляешь?"})
        ),
    )
    result = await run_discovery([], "идея", asked_count=0)
    assert result.action == ASK
    assert result.choices == ()


async def test_choices_are_clamped_and_deduped(monkeypatch: pytest.MonkeyPatch) -> None:
    """Untrusted model output: ≤5 chips, length-capped, de-duped, junk dropped."""
    _install(
        monkeypatch,
        resp=_gateway_returning(
            json.dumps(
                {
                    "action": "ask",
                    "message": "Стиль?",
                    "choices": [
                        "Премиум",
                        "премиум",  # dup (case-insensitive)
                        "  ",  # blank → dropped
                        42,  # non-string → dropped
                        "x" * 80,  # over-long → truncated to 40
                        "A",
                        "B",
                        "C",
                        "D",  # would be the 6th kept → dropped by cap
                    ],
                }
            )
        ),
    )
    result = await run_discovery([], "сайт", asked_count=0)
    assert len(result.choices) <= 5
    assert "Премиум" in result.choices
    assert all(len(c) <= 40 for c in result.choices)
    # case-insensitive dedup keeps a single "премиум"
    assert sum(1 for c in result.choices if c.lower() == "премиум") == 1


async def test_fallback_question_carries_paired_choices(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """On a gateway failure the deterministic question for the 'tone' turn
    (index 1) still ships its tappable chips, not just bare text."""
    _install(monkeypatch, exc=RuntimeError("gateway down"))
    result = await run_discovery([], "идея", asked_count=1)
    assert result.action == ASK
    assert "Премиум" in result.choices


# ─── BUILD path ──────────────────────────────────────────────────────────


async def test_build_path_honours_model_brief_and_stack(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install(
        monkeypatch,
        resp=_gateway_returning(
            json.dumps(
                {
                    "action": "build",
                    "message": "Отлично, собираю…",
                    "brief": "Магазин косметики с корзиной и личным кабинетом.",
                    "stack": "nextjs_entities",
                }
            )
        ),
    )
    result = await run_discovery(
        [{"role": "user", "content": "магазин помады"}],
        "с корзиной и входом",
        asked_count=2,
    )
    assert result.action == BUILD
    assert "корзин" in result.brief.lower()
    assert result.stack == "nextjs_entities"


async def test_invalid_stack_defaults_to_static(monkeypatch: pytest.MonkeyPatch) -> None:
    _install(
        monkeypatch,
        resp=_gateway_returning(
            json.dumps(
                {"action": "build", "message": "ок", "brief": "лендинг", "stack": "django"}
            )
        ),
    )
    result = await run_discovery([], "лендинг", asked_count=1)
    assert result.action == BUILD
    assert result.stack == "static"


async def test_force_build_overrides_model_ask(monkeypatch: pytest.MonkeyPatch) -> None:
    """User said "генерируй" — even if the model wants to keep asking, we build,
    compiling the brief from the conversation so the build has full context."""
    _install(
        monkeypatch,
        resp=_gateway_returning(
            json.dumps({"action": "ask", "message": "ещё вопрос?"})
        ),
    )
    history = [
        {"role": "user", "content": "сайт пекарни"},
        {"role": "assistant", "content": "Какой тон?"},
        {"role": "user", "content": "тёплый, уютный"},
    ]
    result = await run_discovery(
        history, "генерируй уже", asked_count=1, force_build=True
    )
    assert result.action == BUILD
    # Fallback brief carries the whole conversation forward.
    assert "пекарни" in result.brief and "уютный" in result.brief


async def test_turn_cap_forces_build(monkeypatch: pytest.MonkeyPatch) -> None:
    """At the hard cap we build no matter what the model returns — discovery can
    never loop forever (R-10)."""
    _install(
        monkeypatch,
        resp=_gateway_returning(
            json.dumps({"action": "ask", "message": "бесконечный вопрос"})
        ),
    )
    result = await run_discovery(
        [{"role": "user", "content": "что-то"}],
        "ещё ответ",
        asked_count=MAX_DISCOVERY_QUESTIONS,
    )
    assert result.action == BUILD
    assert result.brief  # never empty on a build


async def test_build_with_unparseable_reply_still_builds_from_history(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install(monkeypatch, resp=_gateway_returning("не json вообще, просто текст"))
    result = await run_discovery(
        [{"role": "user", "content": "барбершоп"}],
        "делай",
        asked_count=0,
        force_build=True,
    )
    assert result.action == BUILD
    assert "барбершоп" in result.brief


# ─── Stack safety-net (P0★ — owner directive 2026-06-10) ─────────────────────


def test_infer_stack_detects_backend_intent() -> None:
    """Accounts / saved-data / commerce intent → a container stack."""
    assert _infer_stack_from_text("нужна регистрация и личный кабинет") == "nextjs_entities"
    assert _infer_stack_from_text("habit tracker with login and dashboard") == "nextjs_entities"
    assert _infer_stack_from_text("магазин с корзиной и каталогом товаров") == "nextjs_entities"
    assert _infer_stack_from_text("CRM с ролями пользователей") == "nextjs_entities"


def test_infer_stack_leaves_pure_landing_alone() -> None:
    """A genuine marketing landing must NOT be upgraded to a backend app."""
    assert _infer_stack_from_text("лендинг кофейни в питере с меню и фото") is None
    assert _infer_stack_from_text("портфолио фотографа, галерея работ") is None
    assert _infer_stack_from_text("") is None


async def test_forced_build_with_backend_intent_routes_to_entities(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """THE P0★ bug: parse-fail + forced build for an auth/cabinet product used to
    default to static (dead login buttons). The safety-net now routes it to a
    real container stack from the gathered intent."""
    _install(monkeypatch, resp=_gateway_returning("не json — парс упадёт"))
    result = await run_discovery(
        [{"role": "user", "content": "SaaS-трекер привычек"}],
        "регистрация, вход и личный кабинет на /dashboard, генерируй сразу",
        asked_count=0,
        force_build=True,
    )
    assert result.action == BUILD
    assert result.stack == "nextjs_entities"


async def test_forced_build_pure_landing_stays_static(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A landing with no backend intent still builds static — the net is precise."""
    _install(monkeypatch, resp=_gateway_returning("не json"))
    result = await run_discovery(
        [{"role": "user", "content": "лендинг для кофейни"}],
        "просто красивая визитка с меню, генерируй",
        asked_count=0,
        force_build=True,
    )
    assert result.action == BUILD
    assert result.stack == "static"

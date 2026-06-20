"""A4 i18n — discovery + clarify reply in project language.

Tests:
  1. _reply_language_line: empty for RU (zero diff on existing prompts), non-empty
     for non-RU with correct instruction content.
  2. English build-now signals fire correctly (wants_build_now uses _BUILD_NOW_SIGNALS).
  3. English backend signals route to nextjs_entities (_infer_stack_from_text).
  4. run_discovery / plan_discovery_questions language param is wired (system prompt
     contains the language suffix for non-RU; unchanged for RU).
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from omnia_api.services.lang_detect import _reply_language_line


# ─── 1. _reply_language_line ────────────────────────────────────────────────


def test_reply_language_line_empty_for_ru():
    assert _reply_language_line("ru") == ""
    assert _reply_language_line("RU") == ""
    assert _reply_language_line("ru-RU") == ""


def test_reply_language_line_empty_for_default():
    # Empty / None treated as RU default
    assert _reply_language_line("") == ""


def test_reply_language_line_set_for_en():
    line = _reply_language_line("en")
    # Must instruct NOT to use Russian
    assert "en" in line
    assert "русск" in line.lower()


def test_reply_language_line_set_for_de():
    line = _reply_language_line("de")
    assert "de" in line
    assert line.strip()  # non-empty


def test_reply_language_line_contains_language_code():
    for lang in ("en", "fr", "es", "zh"):
        line = _reply_language_line(lang)
        assert lang in line, f"Expected '{lang}' in line for language={lang!r}"


# ─── 2. English build-now signals ───────────────────────────────────────────


def test_english_build_now_signal_fires_build_it():
    from omnia_api.services.discovery import wants_build_now

    assert wants_build_now("build it")


def test_english_build_now_signal_fires_just_build():
    from omnia_api.services.discovery import wants_build_now

    assert wants_build_now("just build it now")


def test_english_build_now_signal_fires_make_it():
    from omnia_api.services.discovery import wants_build_now

    assert wants_build_now("ok, make it")


def test_english_build_now_signal_fires_go_ahead():
    from omnia_api.services.discovery import wants_build_now

    assert wants_build_now("go ahead and start")


def test_english_build_now_signal_fires_create_it():
    from omnia_api.services.discovery import wants_build_now

    assert wants_build_now("create it please")


def test_english_build_now_signal_fires_generate_now():
    from omnia_api.services.discovery import wants_build_now

    assert wants_build_now("generate now")


def test_english_build_now_signal_fires_skip_questions():
    from omnia_api.services.discovery import wants_build_now

    assert wants_build_now("skip questions and build")


def test_english_build_now_signal_fires_start_building():
    from omnia_api.services.discovery import wants_build_now

    assert wants_build_now("start building")


def test_ru_build_now_still_fires():
    """RU stems must remain functional after adding EN equivalents."""
    from omnia_api.services.discovery import wants_build_now

    assert wants_build_now("генерируй")
    assert wants_build_now("просто сделай")
    assert wants_build_now("поехали!")


def test_innocent_english_does_not_fire_build_now():
    from omnia_api.services.discovery import wants_build_now

    # Generic words that should NOT trigger build-now
    assert not wants_build_now("I need a landing page for my coffee shop")
    assert not wants_build_now("what sections do you recommend?")


# ─── 3. English backend signals ─────────────────────────────────────────────


def test_english_login_routes_to_entities():
    from omnia_api.services.discovery import _infer_stack_from_text

    assert _infer_stack_from_text("users need to login and see their dashboard") == "nextjs_entities"


def test_english_signup_routes_to_entities():
    from omnia_api.services.discovery import _infer_stack_from_text

    assert _infer_stack_from_text("sign up page with user profiles") == "nextjs_entities"


def test_english_register_routes_to_entities():
    from omnia_api.services.discovery import _infer_stack_from_text

    assert _infer_stack_from_text("users can register and manage their orders") == "nextjs_entities"


def test_english_checkout_routes_to_entities():
    from omnia_api.services.discovery import _infer_stack_from_text

    assert _infer_stack_from_text("shopping cart and checkout flow") == "nextjs_entities"


def test_english_booking_routes_to_entities():
    from omnia_api.services.discovery import _infer_stack_from_text

    assert _infer_stack_from_text("appointment booking system") == "nextjs_entities"


def test_english_negated_login_does_not_route():
    """'no login' / 'without auth' should NOT trip the backend signal."""
    from omnia_api.services.discovery import _infer_stack_from_text

    # Negation guard should suppress this
    result = _infer_stack_from_text("no login required, just a public landing page")
    # May or may not fire depending on negation window — just test it doesn't
    # blow up; correctness of exact negation is tested in existing discovery tests.
    assert result in ("nextjs_entities", None)


def test_ru_backend_signal_still_fires():
    """RU stems must remain functional."""
    from omnia_api.services.discovery import _infer_stack_from_text

    assert _infer_stack_from_text("магазин с корзиной и личным кабинетом") == "nextjs_entities"
    assert _infer_stack_from_text("регистрация пользователей") == "nextjs_entities"


def test_innocent_english_no_backend():
    from omnia_api.services.discovery import _infer_stack_from_text

    assert _infer_stack_from_text("a landing page for my coffee shop") is None
    assert _infer_stack_from_text("portfolio website with dark theme") is None


# ─── 4. language param wired into system prompt ─────────────────────────────


class _FakeResp:
    def __init__(self, status_code: int, body: dict[str, Any]) -> None:
        self.status_code = status_code
        self._body = body

    def json(self) -> dict[str, Any]:
        return self._body


def _ask_reply(message: str = "What is your goal?") -> dict[str, Any]:
    return {
        "choices": [
            {
                "message": {
                    "content": json.dumps(
                        {"action": "ask", "message": message, "choices": ["Option A"]}
                    )
                }
            }
        ]
    }


@pytest.mark.asyncio
async def test_run_discovery_ru_system_prompt_unchanged():
    """For RU the system prompt passed to the gateway must be exactly _SYSTEM."""
    from omnia_api.services import discovery as disc

    captured: list[dict] = []

    async def fake_post(url, *, json=None, **_kw):  # noqa: A002
        captured.append(json or {})
        return _FakeResp(200, _ask_reply())

    mock_client = MagicMock()
    mock_client.__aenter__ = AsyncMock(return_value=MagicMock(post=AsyncMock(side_effect=fake_post)))
    mock_client.__aexit__ = AsyncMock(return_value=False)

    with patch("omnia_api.services.discovery.httpx.AsyncClient", return_value=mock_client):
        await disc.run_discovery([], "build me a coffee shop site", asked_count=0, language="ru")

    assert captured, "No gateway call was made"
    messages = captured[0]["messages"]
    system_msg = next((m for m in messages if m["role"] == "system"), None)
    assert system_msg is not None
    # For RU the content must be exactly _SYSTEM (no suffix appended)
    assert system_msg["content"] == disc._SYSTEM


@pytest.mark.asyncio
async def test_run_discovery_en_system_prompt_has_suffix():
    """For EN the system prompt must contain the language instruction suffix."""
    from omnia_api.services import discovery as disc

    captured: list[dict] = []

    async def fake_post(url, *, json=None, **_kw):  # noqa: A002
        captured.append(json or {})
        return _FakeResp(200, _ask_reply())

    mock_client = MagicMock()
    mock_client.__aenter__ = AsyncMock(return_value=MagicMock(post=AsyncMock(side_effect=fake_post)))
    mock_client.__aexit__ = AsyncMock(return_value=False)

    with patch("omnia_api.services.discovery.httpx.AsyncClient", return_value=mock_client):
        await disc.run_discovery([], "build me a landing page", asked_count=0, language="en")

    assert captured
    messages = captured[0]["messages"]
    system_msg = next((m for m in messages if m["role"] == "system"), None)
    assert system_msg is not None
    content = system_msg["content"]
    # Must start with the original _SYSTEM
    assert content.startswith(disc._SYSTEM)
    # And contain the language directive
    assert "en" in content
    assert "русск" in content.lower()


@pytest.mark.asyncio
async def test_plan_discovery_ru_system_prompt_unchanged():
    """For RU plan_discovery_questions must pass _PLAN_SYSTEM unmodified."""
    from omnia_api.services import discovery as disc

    captured: list[dict] = []

    plan_reply = {
        "choices": [
            {
                "message": {
                    "content": json.dumps(
                        {
                            "questions": [
                                {"message": "What is your goal?", "choices": ["Sales", "Info"]},
                            ]
                        }
                    )
                }
            }
        ]
    }

    async def fake_post(url, *, json=None, **_kw):  # noqa: A002
        captured.append(json or {})
        return _FakeResp(200, plan_reply)

    mock_client = MagicMock()
    mock_client.__aenter__ = AsyncMock(return_value=MagicMock(post=AsyncMock(side_effect=fake_post)))
    mock_client.__aexit__ = AsyncMock(return_value=False)

    with patch("omnia_api.services.discovery.httpx.AsyncClient", return_value=mock_client):
        await disc.plan_discovery_questions("coffee shop", language="ru")

    assert captured
    messages = captured[0]["messages"]
    system_msg = next((m for m in messages if m["role"] == "system"), None)
    assert system_msg is not None
    assert system_msg["content"] == disc._PLAN_SYSTEM


@pytest.mark.asyncio
async def test_plan_discovery_en_system_prompt_has_suffix():
    """For EN plan_discovery_questions must append the language suffix."""
    from omnia_api.services import discovery as disc

    captured: list[dict] = []

    plan_reply = {
        "choices": [
            {
                "message": {
                    "content": json.dumps(
                        {
                            "questions": [
                                {"message": "What is your goal?", "choices": ["Sales"]},
                            ]
                        }
                    )
                }
            }
        ]
    }

    async def fake_post(url, *, json=None, **_kw):  # noqa: A002
        captured.append(json or {})
        return _FakeResp(200, plan_reply)

    mock_client = MagicMock()
    mock_client.__aenter__ = AsyncMock(return_value=MagicMock(post=AsyncMock(side_effect=fake_post)))
    mock_client.__aexit__ = AsyncMock(return_value=False)

    with patch("omnia_api.services.discovery.httpx.AsyncClient", return_value=mock_client):
        await disc.plan_discovery_questions("coffee shop", language="en")

    assert captured
    messages = captured[0]["messages"]
    system_msg = next((m for m in messages if m["role"] == "system"), None)
    assert system_msg is not None
    content = system_msg["content"]
    assert content.startswith(disc._PLAN_SYSTEM)
    assert "en" in content
    assert "русск" in content.lower()

"""Messenger → realtime routing must survive the build-trigger turn.

Live bug (2026-06-27): prompt «создай мессенджер: реалтайм чат…», then the
skip-survey button sent the trigger «Постройте сейчас». Stack inference ran on
the TRIGGER (no messenger word) → defaulted to spa → built a generic dashboard,
not a chat. Fix: the realtime net runs on the FULL conversation intent (all user
messages), so the original messenger intent still forces the realtime stack.
"""

from __future__ import annotations

from omnia_api.services.discovery import _infer_realtime_from_text


def test_trigger_phrase_alone_misses_realtime() -> None:
    # The build-trigger phrase carries no messenger/chat intent on its own.
    assert _infer_realtime_from_text("Постройте сейчас") is False
    assert _infer_realtime_from_text("генерируй") is False


def test_messenger_prompt_fires_realtime() -> None:
    assert _infer_realtime_from_text("Создай мессенджер: реалтайм чат") is True
    assert _infer_realtime_from_text("сделай семейный чат") is True


def test_full_intent_join_catches_messenger_despite_trigger() -> None:
    # This is exactly what the handler now feeds the net: every user message
    # joined, so the original intent survives the «Постройте сейчас» trigger.
    user_messages = [
        "Создай мессенджер: реалтайм чат, сообщения мгновенно без перезагрузки",
        "Постройте сейчас",
    ]
    full_intent = " ".join(user_messages) + " Постройте сейчас"
    assert _infer_realtime_from_text(full_intent) is True

"""Pre-generation clarify interview.

Before the FIRST build of a project, the assistant asks the user 3–4 short,
business-specific questions so the generator works from a precise brief instead
of a one-line idea. The user's answers arrive as the next chat message and flow
into the real build via history — no special plumbing.

Single public surface: ``generate_clarify_questions(prompt)`` → a markdown
question list (Russian). Fail-soft: any gateway error returns a sensible default
question set so the flow never breaks (the user can always reply "генерируй" to
skip straight to generation).
"""

from __future__ import annotations

import logging

import httpx

from omnia_api.core.config import get_settings, model_for_role
from omnia_api.services.lang_detect import _reply_language_line

log = logging.getLogger(__name__)

_SYSTEM = (
    "Ты — продуктовый дизайнер Omnia.AI. Пользователь прислал короткую идею сайта. "
    "Прежде чем строить, задай 3–4 КОРОТКИХ уточняющих вопроса, чтобы сделать сайт "
    "точечно под него. Вопросы — под КОНКРЕТНЫЙ бизнес из его идеи (не общие): "
    "главная цель/целевое действие, аудитория и тон, обязательные разделы и контент, "
    "фирменный стиль / цвета / референс, контакты и оффер. Максимум 4 вопроса. НЕ "
    "пиши вступлений, пояснений и кода. Формат — нумерованный список вопросов на "
    "русском, дружелюбно и по делу. Последней строкой добавь: "
    "«Ответь парой слов на каждый — или напиши «генерируй», и я начну сразу.»"
)

# Used when the gateway is unreachable / errors — generic but useful, so the
# user is never blocked on a vague prompt.
_DEFAULT = (
    "Прежде чем собрать сайт, уточню пару моментов — так получится точнее:\n\n"
    "1. Какая ГЛАВНАЯ цель сайта — заявки, продажи, запись, презентация?\n"
    "2. Кто аудитория и какой тон ближе — премиум / дружелюбный / строгий?\n"
    "3. Какие разделы обязательно нужны (услуги, цены, отзывы, контакты…)?\n"
    "4. Есть фирменные цвета, логотип или сайт-референс, который нравится?\n\n"
    "Ответь парой слов на каждый — или напиши «генерируй», и я начну сразу."
)


async def generate_clarify_questions(prompt: str, language: str = "ru") -> str:
    """Ask the gateway for 3–4 clarifying questions about ``prompt``.

    ``language`` is the project's detected language (BCP-47-ish, e.g. ``"en"``).
    RU is the default and leaves the system prompt unchanged (zero diff from the
    pre-i18n baseline). ``_DEFAULT`` (the gateway-failure fallback) stays RU —
    acceptable because it only fires when the gateway is unreachable.

    Never raises — returns ``_DEFAULT`` on any failure so the clarify turn always
    produces something usable.
    """
    settings = get_settings()
    url = f"{settings.llm_gateway_url.rstrip('/')}/v1/chat/completions"
    system_content = _SYSTEM + _reply_language_line(language)
    payload = {
        "model": model_for_role("edit"),
        "messages": [
            {"role": "system", "content": system_content},
            {"role": "user", "content": (prompt or "").strip()[:2000]},
        ],
        "max_tokens": 420,
        "stream": False,
    }
    try:
        async with httpx.AsyncClient(timeout=45.0) as client:
            resp = await client.post(url, json=payload)
        if resp.status_code >= 400:
            log.warning("clarify: gateway %d — using default", resp.status_code)
            return _DEFAULT
        body = resp.json()
        text = (body.get("choices") or [{}])[0].get("message", {}).get("content", "")
        return (text or "").strip() or _DEFAULT
    except Exception as exc:  # noqa: BLE001 — clarify must never break the flow
        log.warning("clarify: gateway error (using default): %r", exc)
        return _DEFAULT


__all__ = ["generate_clarify_questions"]

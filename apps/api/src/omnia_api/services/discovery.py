"""Progressive discovery interview (P1 — owner directive 2026-06-09).

Replaces BOTH the blocking onboarding quiz (removed client-side) AND the one-shot
3-4-question clarify with a CONVERSATIONAL discovery: on a brand-new project the
assistant asks ONE short, elementary question at a time, adapts to the answer, and
decides ON ITS OWN when it has enough to build. Then it compiles a compact brief
and recommends a stack.

Single public surface: ``run_discovery(...) -> DiscoveryResult`` (R-01 — the rule
set + JSON contract stay hidden behind a trivial call). Deterministic fail-soft:
any gateway / parse error degrades to a sensible next question, or — at the turn
cap or on an explicit "build now" — to BUILD from whatever was gathered, so the
onboarding never dead-ends (R-10).
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass

import httpx

from omnia_api.core.config import get_settings, model_for_role

log = logging.getLogger(__name__)

ASK = "ask"
BUILD = "build"

# Stacks the discovery may recommend. ``static`` builds immediately with no
# container; the container stacks (``fullstack`` / ``nextjs_entities``) are routed
# to the orchestrator by the provisioning step (P1 subtask 5). Until that lands,
# the recommendation is recorded in the brief so the build is at least aware of it.
_STACKS: frozenset[str] = frozenset({"static", "fullstack", "nextjs_entities"})
_DEFAULT_STACK = "static"

# Hard cap so discovery can never loop forever — after this many questions we
# build with whatever we have. The user can always force a build sooner.
MAX_DISCOVERY_QUESTIONS = 5

# Explicit "stop asking, build now" signals — substring match on the lowered
# prompt (Russian stems cover падежи). When present we skip straight to BUILD.
_BUILD_NOW_SIGNALS: frozenset[str] = frozenset(
    {
        "генерир", "сгенерир", "строй", "собери", "построй", "сделай уже",
        "давай уже", "поехали", "начинай", "хватит вопрос", "просто сделай",
        "build now", "just build", "go ahead",
    }
)


@dataclass(frozen=True)
class DiscoveryResult:
    """Outcome of one discovery turn.

    ``action`` is ASK (stream ``message`` as the next question, no build) or BUILD
    (run the generator with ``brief`` as the prompt). ``stack`` is the recommended
    stack id; ``message`` on a BUILD is a short friendly "собираю…" note.
    """

    action: str
    message: str
    brief: str
    stack: str


def wants_build_now(prompt: str) -> bool:
    """True when the user explicitly asked to skip questions and build."""
    text = (prompt or "").strip().lower()
    return any(sig in text for sig in _BUILD_NOW_SIGNALS)


_SYSTEM = (
    "Ты — продуктовый дизайнер Omnia.AI, который ведёт КОРОТКИЙ дружелюбный диалог-"
    "знакомство с пользователем перед сборкой его сайта/приложения. Твоя задача — "
    "по чуть-чуть, ОДНИМ простым вопросом за раз, понять, что человеку нужно, и "
    "когда станет достаточно — собрать бриф и выбрать тех-стек.\n\n"
    "ПРАВИЛА ДИАЛОГА:\n"
    "1. Задавай РОВНО ОДИН короткий, элементарный вопрос за ход (не списком). "
    "Подстраивайся под предыдущие ответы. Можно открытые вопросы вроде «как ты "
    "представляешь это приложение?».\n"
    "2. Двигайся от общего к деталям: суть/цель → аудитория и тон → ключевые "
    "разделы/возможности → стиль/цвета/референс.\n"
    "3. Если в сообщениях пользователя УЖЕ достаточно, чтобы собрать достойный "
    "продукт, ИЛИ пользователь просит начать — НЕ тяни, верни action=build.\n"
    "4. Обычно хватает 2–4 вопросов. Не превращай это в анкету.\n\n"
    "ВЫБОР СТЕКА (поле stack при build):\n"
    "- \"static\" — сайт/лендинг/портфолио/блог/визитка. НЕТ входа, личных "
    "кабинетов, корзины, базы данных, CRUD.\n"
    "- \"nextjs_entities\" — есть пользователи, каталог/товары, корзина, запись/"
    "бронирование, CRM, личный кабинет, любые сохраняемые данные. Полноценное "
    "приложение с БД.\n"
    "- \"fullstack\" — интерактивное веб-приложение/SPA с лёгким бэкендом, не "
    "подходящее под entities.\n\n"
    "ФОРМАТ ОТВЕТА — СТРОГО один JSON-объект на одной строке, без пояснений и кода.\n"
    "Если спрашиваешь:\n"
    '{"action":"ask","message":"<один короткий вопрос на русском>"}\n'
    "Если пора строить:\n"
    '{"action":"build","message":"<короткая фраза: «Отлично, собираю…»>",'
    '"brief":"<сжатый бриф для генератора на русском: тип продукта, цель, '
    'аудитория, обязательные разделы/возможности, тон, цвета/референс, важные '
    'детали>","stack":"static|fullstack|nextjs_entities"}'
)


def _fallback_question(asked_count: int) -> str:
    """Deterministic next question when the gateway/parse fails — one at a time,
    keyed by how many we've already asked (no randomness — keeps resumable)."""
    questions = [
        "Расскажите в двух словах — что за проект и какая у него главная цель?",
        "Кто ваша аудитория и какое настроение ближе — премиум, дружелюбное или строгое?",
        "Какие разделы или возможности обязательно нужны?",
        "Есть фирменные цвета, логотип или сайт-референс, который вам нравится?",
    ]
    idx = min(asked_count, len(questions) - 1)
    return questions[idx]


def _fallback_brief(history: list[dict[str, str]], latest_prompt: str) -> str:
    """Compile a build brief from the raw conversation when the model can't —
    so a forced/capped build still has the full context to work from."""
    parts: list[str] = []
    for m in history:
        content = (m.get("content") or "").strip()
        if not content:
            continue
        who = "Пользователь" if m.get("role") == "user" else "Ассистент"
        parts.append(f"{who}: {content}")
    latest = (latest_prompt or "").strip()
    if latest:
        parts.append(f"Пользователь: {latest}")
    convo = "\n".join(parts)
    return (
        "Собери продуманный, завершённый сайт по итогам этого диалога-знакомства.\n\n"
        f"{convo}"
    ).strip()


def _parse(raw: str) -> dict[str, object] | None:
    """Pull the JSON object out of a model reply (tolerant of ``` fences and
    surrounding prose). Returns the dict, or None if nothing parseable."""
    text = (raw or "").strip()
    if not text:
        return None
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return None
    try:
        obj = json.loads(text[start : end + 1])
    except (json.JSONDecodeError, ValueError):
        return None
    return obj if isinstance(obj, dict) else None


async def run_discovery(
    history: list[dict[str, str]],
    latest_prompt: str,
    *,
    asked_count: int,
    force_build: bool = False,
) -> DiscoveryResult:
    """Decide the next discovery turn: ask one more question, or build.

    ``history`` is the prior conversation (``[{"role","content"}]``), ``latest_prompt``
    the user's newest message (not yet in ``history``). ``asked_count`` is how many
    questions the assistant has already asked (drives the hard cap). ``force_build``
    short-circuits to BUILD (explicit user request).

    Never raises — degrades to a sensible question, or a from-history build at the
    cap / on force, so onboarding can never dead-end.
    """
    capped = asked_count >= MAX_DISCOVERY_QUESTIONS
    must_build = force_build or capped

    settings = get_settings()
    url = f"{settings.llm_gateway_url.rstrip('/')}/v1/chat/completions"
    convo: list[dict[str, str]] = [{"role": "system", "content": _SYSTEM}]
    for m in history[-12:]:
        content = (m.get("content") or "").strip()
        if not content:
            continue
        role = m.get("role")
        convo.append(
            {"role": role if role in ("user", "assistant") else "user", "content": content[:2000]}
        )
    user_turn = (latest_prompt or "").strip()[:4000]
    if must_build:
        # Nudge the model to wrap up — but we'll also build deterministically if
        # it refuses or errors (see below), so this is best-effort steering only.
        user_turn = (
            f"{user_turn}\n\n[СИСТЕМА: пора строить — верни action=build с брифом и "
            "stack по всему, что уже известно. Больше вопросов не задавай.]"
        )
    convo.append({"role": "user", "content": user_turn or "(пусто)"})

    payload = {
        "model": model_for_role("edit"),
        "messages": convo,
        "max_tokens": 700,
        "stream": False,
    }
    parsed: dict[str, object] | None = None
    try:
        async with httpx.AsyncClient(timeout=45.0) as client:
            resp = await client.post(url, json=payload)
        if resp.status_code < 400:
            body = resp.json()
            content = (body.get("choices") or [{}])[0].get("message", {}).get("content", "")
            parsed = _parse(content)
        else:
            log.warning("discovery: gateway %d — using fallback", resp.status_code)
    except Exception as exc:
        log.warning("discovery: gateway error (using fallback): %r", exc)

    action = str(parsed.get("action") or "").strip().lower() if parsed else ""
    stack = str(parsed.get("stack") or "").strip().lower() if parsed else ""
    if stack not in _STACKS:
        stack = _DEFAULT_STACK

    # BUILD path — taken when the model says so, or when we MUST build (forced /
    # capped) regardless of what the model returned.
    if action == BUILD or must_build:
        brief = ""
        message = ""
        if parsed:
            brief = str(parsed.get("brief") or "").strip()
            message = str(parsed.get("message") or "").strip()
        if not brief:
            brief = _fallback_brief(history, latest_prompt)
        if not message:
            message = "Отлично — собираю первый вариант. Это займёт минуту."
        return DiscoveryResult(action=BUILD, message=message, brief=brief, stack=stack)

    # ASK path — one more question.
    message = ""
    if parsed and action == ASK:
        message = str(parsed.get("message") or "").strip()
    if not message:
        message = _fallback_question(asked_count)
    return DiscoveryResult(action=ASK, message=message, brief="", stack=stack)


__all__ = [
    "ASK",
    "BUILD",
    "MAX_DISCOVERY_QUESTIONS",
    "DiscoveryResult",
    "run_discovery",
    "wants_build_now",
]

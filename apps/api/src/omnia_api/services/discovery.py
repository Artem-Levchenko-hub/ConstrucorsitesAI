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
import re
from dataclasses import dataclass

import httpx

from omnia_api.core.config import get_settings, model_for_role

log = logging.getLogger(__name__)

ASK = "ask"
BUILD = "build"

# Stacks the discovery may recommend. ``static`` builds immediately with no
# container; the container stacks (``fullstack`` / ``nextjs_entities`` / ``spa``)
# are routed to the orchestrator by the provisioning step (``stack_routing``).
# ``spa`` (Vite + React, no backend) is the no-ceiling escape hatch for an
# INTERACTIVE tool/app that needs real build tooling but no accounts/DB — see the
# stack-choice rules in ``_SYSTEM`` (Phase 7.2 multi-stack).
_STACKS: frozenset[str] = frozenset(
    {"static", "fullstack", "nextjs_entities", "spa"}
)
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


# High-precision backend-intent signals (lowered substrings, RU stems + EN).
# When the user's gathered intent contains ANY of these, the product needs real
# accounts / saved data / CRUD — a static landing with dead login buttons is the
# wrong build (owner directive 2026-06-10: «полноценное приложение с 1 генерации»).
# Kept precise so a genuine marketing landing (кофейня, портфолио) does NOT trip
# it: these are product-intent words, not generic nav labels.
_BACKEND_SIGNALS: frozenset[str] = frozenset(
    {
        # auth / accounts
        "регистрац", "зарегистр", "войти", "вход в", "логин", "авториз",
        "личный кабинет", "кабинет", "профиль пользоват", "аккаунт",
        "log in", "login", "sign in", "signin", "sign up", "signup", "auth",
        # private app surface / data ownership
        "dashboard", "per-user", "каждый пользователь", "пользователи видят",
        "роли пользоват", "админк", "admin panel",
        # data / CRUD / commerce
        "crm", "crud", "база данных", "сущност", "entities", "сохраня",
        "корзин", "оформить заказ", "заказы", "checkout",
        "бронирован", "запись на", "каталог товар", "товаров", "трекер",
    }
)


# A backend signal that is immediately NEGATED ("без регистрации", "без
# аккаунтов", "no login") means the OPPOSITE — a no-backend tool (the spa stack,
# Phase 7.2). Naive substring matching is blind to this: "регистрац" lives inside
# "без регистрации". We anchor a negator at the end of the short window right
# before the matched signal so only a genuine, non-negated mention fires.
_NEGATION_BEFORE = re.compile(
    r"(?:\bбез\b|\bне\s+нужн\w*|\bне\s+требу\w*|\bне\s+надо\b|\bwithout\b|\bno\b)\s*$",
    re.IGNORECASE,
)
_NEGATION_WINDOW = 24


def _signal_fires(haystack: str, sig: str) -> bool:
    """True when ``sig`` occurs in ``haystack`` at least once NOT negated."""
    start = 0
    while True:
        i = haystack.find(sig, start)
        if i == -1:
            return False
        prefix = haystack[max(0, i - _NEGATION_WINDOW) : i]
        if not _NEGATION_BEFORE.search(prefix):
            return True  # a non-negated occurrence — the signal genuinely fires
        start = i + 1  # this one was negated; keep looking for a clean mention


def _infer_stack_from_text(text: str) -> str | None:
    """Deterministic safety-net: pick a container stack from product intent.

    Returns ``"nextjs_entities"`` when the text carries clear backend signals
    (accounts, saved data, CRUD, commerce), else ``None`` (leave as static).
    Used only when the model didn't confidently pick a container stack — never
    downgrades a good model choice. Negated mentions ("без регистрации") are
    ignored so a no-backend tool keeps the model's ``spa`` pick (Phase 7.2)."""
    haystack = (text or "").lower()
    if any(_signal_fires(haystack, sig) for sig in _BACKEND_SIGNALS):
        return "nextjs_entities"
    return None


@dataclass(frozen=True)
class DiscoveryResult:
    """Outcome of one discovery turn.

    ``action`` is ASK (stream ``message`` as the next question, no build) or BUILD
    (run the generator with ``brief`` as the prompt). ``stack`` is the recommended
    stack id; ``message`` on a BUILD is a short friendly "собираю…" note.

    On an ASK, ``choices`` are 0–5 short quick-reply answers the UI renders as
    tappable chips beneath the question; ``allow_custom`` (always True) tells the
    UI to keep a free-text "Другое" path open so a chip never traps the user.
    Empty ``choices`` is fine — the question is just answered by typing.
    """

    action: str
    message: str
    brief: str
    stack: str
    choices: tuple[str, ...] = ()
    allow_custom: bool = True


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
    "- \"static\" — пассивный сайт-контент: лендинг/портфолио/блог/визитка. "
    "НЕТ входа, личных кабинетов, корзины, базы данных, CRUD И нет сложной "
    "клиентской логики (только текст, картинки, ссылки, простые формы).\n"
    "- \"nextjs_entities\" — есть пользователи, каталог/товары, корзина, запись/"
    "бронирование, CRM, личный кабинет, любые сохраняемые данные. Полноценное "
    "приложение с БД.\n"
    "- \"spa\" — ИНТЕРАКТИВНЫЙ инструмент/приложение БЕЗ бэкенда и БЕЗ "
    "регистрации: калькулятор, конвертер, визуализатор, генератор, игра, "
    "конфигуратор, интерактивный дашборд на демо-данных. Богатая клиентская "
    "логика, но НЕ нужны аккаунты или сохранение в БД между пользователями.\n"
    "- \"fullstack\" — интерактивное веб-приложение с лёгким собственным "
    "бэкендом, не подходящее под entities.\n\n"
    "ФОРМАТ ОТВЕТА — СТРОГО один JSON-объект на одной строке, без пояснений и кода.\n"
    "Если спрашиваешь:\n"
    '{"action":"ask","message":"<один короткий вопрос на русском>",'
    '"choices":["<2–5 коротких вариантов ответа, 1–3 слова каждый>"]}\n'
    "  — choices это подсказки-кнопки под вопросом (например для «Нужна "
    "админка?» → [\"Да\",\"Нет\"]; для стиля → [\"Премиум\",\"Дружелюбный\","
    "\"Строгий\"]). Давай их КОГДА ответ сводится к выбору. Если вопрос "
    "открытый (например «как ты это представляешь?») — верни choices:[] "
    "(пустой). Пользователь всегда может ответить и своим текстом.\n"
    "Если пора строить:\n"
    '{"action":"build","message":"<короткая фраза: «Отлично, собираю…»>",'
    '"brief":"<сжатый бриф для генератора на русском: тип продукта, цель, '
    'аудитория, обязательные разделы/возможности, тон, цвета/референс, важные '
    'детали>","stack":"static|spa|nextjs_entities|fullstack"}'
)


# Deterministic fallback questions + matching quick-reply chips, keyed by how
# many questions we've already asked (no randomness — keeps the turn resumable).
# Chips parallel ``_FALLBACK_QUESTIONS`` index-for-index; an empty tuple means
# "answer by typing" (open question).
_FALLBACK_QUESTIONS: tuple[str, ...] = (
    "Расскажите в двух словах — что за проект и какая у него главная цель?",
    "Кто ваша аудитория и какое настроение ближе — премиум, дружелюбное или строгое?",
    "Какие разделы или возможности обязательно нужны?",
    "Есть фирменные цвета, логотип или сайт-референс, который вам нравится?",
)
_FALLBACK_CHOICES: tuple[tuple[str, ...], ...] = (
    (),
    ("Премиум", "Дружелюбное", "Строгое"),
    (),
    (),
)


def _fallback_question(asked_count: int) -> str:
    """Deterministic next question when the gateway/parse fails — one at a time."""
    idx = min(asked_count, len(_FALLBACK_QUESTIONS) - 1)
    return _FALLBACK_QUESTIONS[idx]


def _fallback_choices(asked_count: int) -> tuple[str, ...]:
    """Quick-reply chips paired with the deterministic fallback question."""
    idx = min(asked_count, len(_FALLBACK_CHOICES) - 1)
    return _FALLBACK_CHOICES[idx]


# Quick-reply chips are untrusted model output headed into the UI: cap the count
# and per-chip length (R-10 fail-fast at the boundary) so a misbehaving model
# can't flood the chat with a wall of long "buttons".
_MAX_CHOICES = 5
_MAX_CHOICE_LEN = 40


def _clean_choices(raw: object) -> tuple[str, ...]:
    """Normalise the model's ``choices`` into ≤5 short, de-duped chip labels.
    Anything non-list / unparseable degrades to no chips (the question still
    stands on its own — typing always works)."""
    if not isinstance(raw, list):
        return ()
    out: list[str] = []
    seen: set[str] = set()
    for item in raw:
        if not isinstance(item, str):
            continue
        label = item.strip()[:_MAX_CHOICE_LEN].strip()
        if not label:
            continue
        key = label.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(label)
        if len(out) >= _MAX_CHOICES:
            break
    return tuple(out)


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
        # Discovery runs INSIDE the POST /prompt request, and the web client caps
        # that POST at 30s. Bound the gateway call well under that (R-10 fail
        # fast) so a slow/cold gateway degrades to a deterministic question/brief
        # within the window instead of blowing the client timeout with an error.
        async with httpx.AsyncClient(timeout=20.0) as client:
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
        # Stack safety-net: the model often defaults to / mis-classifies as
        # "static" (or its reply parse-failed → static default above), even when
        # the user clearly asked for accounts + saved data. Re-derive from the
        # full gathered intent so a real app gets a container stack instead of a
        # dead static landing (owner directive 2026-06-10). Only overrides when
        # the model didn't already pick a container stack.
        if stack not in ("fullstack", "nextjs_entities"):
            intent_text = "\n".join(
                [brief, *(m.get("content") or "" for m in history), latest_prompt or ""]
            )
            inferred = _infer_stack_from_text(intent_text)
            if inferred:
                log.info("discovery: stack '%s'→'%s' (backend intent signals)", stack, inferred)
                stack = inferred
        return DiscoveryResult(action=BUILD, message=message, brief=brief, stack=stack)

    # ASK path — one more question (+ optional quick-reply chips).
    message = ""
    choices: tuple[str, ...] = ()
    if parsed and action == ASK:
        message = str(parsed.get("message") or "").strip()
        choices = _clean_choices(parsed.get("choices"))
    if not message:
        # Gateway/parse failed → deterministic question AND its paired chips, so
        # the fallback turn still offers tappable answers, not just bare text.
        message = _fallback_question(asked_count)
        choices = _fallback_choices(asked_count)
    return DiscoveryResult(
        action=ASK, message=message, brief="", stack=stack, choices=choices
    )


__all__ = [
    "ASK",
    "BUILD",
    "MAX_DISCOVERY_QUESTIONS",
    "DiscoveryResult",
    "run_discovery",
    "wants_build_now",
]

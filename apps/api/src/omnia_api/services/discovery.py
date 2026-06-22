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

import asyncio
import json
import logging
import re
from dataclasses import dataclass
from typing import Any

import httpx

from omnia_api.core.config import get_settings, model_for_role
from omnia_api.services.chip_pixel_gate import (
    compile_build_spec,
    spec_confidence,
    spec_from_discovery,
)
from omnia_api.services.lang_detect import _reply_language_line

log = logging.getLogger(__name__)

ASK = "ask"
BUILD = "build"

# Zero-question floor (V2.12 / North Star pillar 2 — "the best onboarding is its
# absence when intent is clear"). When the user's VERY FIRST prompt already pins
# at least this many concrete design axes (theme / accent / sections / tone), the
# intent is unambiguous enough to build immediately — no popup, no gateway call.
# A conservative ≥2: a thin one-axis hint ("на тёмном фоне") still earns one
# question, only a genuinely steered prompt skips the interview.
_ZERO_QUESTION_MIN_AXES = 2

# Stacks the discovery may recommend. ``static`` builds immediately with no
# container; the container stacks (``fullstack`` / ``nextjs_entities`` / ``spa``)
# are routed to the orchestrator by the provisioning step (``stack_routing``).
# ``spa`` (Vite + React, no backend) is the no-ceiling escape hatch for an
# INTERACTIVE tool/app that needs real build tooling but no accounts/DB — see the
# stack-choice rules in ``_SYSTEM`` (Phase 7.2 multi-stack).
_STACKS: frozenset[str] = frozenset(
    {"static", "fullstack", "nextjs_entities", "spa", "code"}
)
_DEFAULT_STACK = "static"

# ── Result type (RT-1, 2026-06-22) ───────────────────────────────────────
# `result_type` is WHAT the user wants; `stack` is HOW we build it. The router
# decides the type first (semantically), then maps it to a build stack. The key
# split: a conversion word («запись/бронь/заказ») is a `landing` (public lead
# form, NO /signin), NOT a `web_app` — only an explicit account/cabinet ask is.
RESULT_TYPES: frozenset[str] = frozenset(
    {"landing", "web_app", "tool", "site", "code", "static"}
)
_RESULT_TYPE_TO_STACK: dict[str, str] = {
    "landing": "spa",              # public lead-form, NO /signin
    "web_app": "nextjs_entities",  # accounts / saved data
    "tool": "spa",
    "site": "spa",
    "code": "code",
    "static": "static",
}


def result_type_to_stack(rt: str) -> str | None:
    """Map a result_type to its build stack id, or None for an unknown type."""
    return _RESULT_TYPE_TO_STACK.get((rt or "").strip().lower())


# Hard cap so discovery can never loop forever — after this many questions we
# build with whatever we have. The user can always force a build sooner.
MAX_DISCOVERY_QUESTIONS = 5

# Explicit "stop asking, build now" signals — substring match on the lowered
# prompt (Russian stems cover падежи). When present we skip straight to BUILD.
# EN equivalents added (A4 i18n) so English users get the same skip-to-build
# shortcut without changing RU routing (RU input never contains these EN stems).
_BUILD_NOW_SIGNALS: frozenset[str] = frozenset(
    {
        # RU stems (unchanged)
        "генерир", "сгенерир", "строй", "собери", "построй", "сделай уже",
        "давай уже", "поехали", "начинай", "хватит вопрос", "просто сделай",
        # EN equivalents
        "build now", "just build", "go ahead", "build it", "make it",
        "create it", "generate it", "generate now", "just go", "go now",
        "skip questions", "start building", "start now",
    }
)


# High-precision backend-intent signals (lowered substrings, RU stems + EN).
# When the user's gathered intent contains ANY of these, the product needs real
# accounts / saved data / CRUD — a static landing with dead login buttons is the
# wrong build (owner directive 2026-06-10: «полноценное приложение с 1 генерации»).
# Kept precise so a genuine marketing landing (кофейня, портфолио) does NOT trip
# it: these are product-intent words, not generic nav labels.
# EN equivalents added (A4 i18n) so English prompts get the same routing; they
# cannot fire on RU input (different alphabet / words), so RU routing is stable.
_BACKEND_SIGNALS: frozenset[str] = frozenset(
    {
        # auth / accounts — RU stems
        "регистрац", "зарегистр", "войти", "вход в", "логин", "авториз",
        "личный кабинет", "кабинет", "профиль пользоват", "аккаунт",
        # auth / accounts — EN
        "log in", "login", "sign in", "signin", "sign up", "signup", "auth",
        "register", "account", "user account", "user profile",
        # private app surface / data ownership — RU
        "каждый пользователь", "пользователи видят",
        "роли пользоват", "админк",
        # private app surface / data ownership — EN
        "dashboard", "per-user", "admin panel", "admin dashboard",
        "user roles", "role-based",
        # data / CRUD / commerce — RU
        "crm", "crud", "база данных", "сущност", "сохраня",
        "корзин", "оформить заказ", "заказы",
        "бронирован", "запись на", "каталог товар", "товаров", "трекер",
        # data / CRUD / commerce — EN
        "entities", "database", "save data", "user data",
        "shopping cart", "checkout", "booking", "appointments",
        "payments", "payment", "orders", "product catalog",
        "inventory", "tracker",
    }
)


# Account / per-user-data signals — the SUBSET of backend intent that genuinely
# needs customer auth (a private cabinet), vs a public conversion (заявка/запись/
# бронь — covered by a landing lead form without accounts). RU stems + EN. This
# is the BS-7 narrowing: a conversion word alone is NOT proof of a web_app.
_ACCOUNT_SIGNALS: frozenset[str] = frozenset(
    {
        "регистрац", "зарегистр", "личный кабинет", "профиль пользоват",
        "аккаунт", "каждый пользователь", "пользователи видят",
        "роли пользоват", "админк", "сохраня",
        "crm", "crud", "база данных", "сущност", "каталог товар", "корзин",
        "sign up", "signup", "register", "account", "user account",
        "user profile", "dashboard", "per-user", "admin panel",
        "user roles", "role-based", "database", "save data", "user data",
        "shopping cart", "product catalog",
    }
)
# Conversion words a LANDING satisfies with a public lead form (POST /lead),
# NOT a customer account. Present-but-no-_ACCOUNT_SIGNALS ⇒ landing (BS-7).
_CONVERSION_SIGNALS: frozenset[str] = frozenset(
    {
        "запись на", "записаться", "бронирован", "забронир", "оформить заказ",
        "заявк", "обратн", "перезвон", "booking", "appointment", "appointments",
        "book a", "order now", "request a", "contact form", "lead",
    }
)


def _has_account_intent(text: str) -> bool:
    """True when a NON-negated account/per-user signal fires — the only thing
    that lifts a conversion landing into a web_app (BS-7 split)."""
    low = (text or "").lower()
    return any(_signal_fires(low, sig) for sig in _ACCOUNT_SIGNALS)


def _has_conversion_intent(text: str) -> bool:
    """True when the prompt asks for a lead/booking conversion (covered by a
    public landing form without accounts)."""
    low = (text or "").lower()
    return any(sig in low for sig in _CONVERSION_SIGNALS)


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


# App-ification framing (P-H1, owner 2026-06-21). On a FOLLOW-UP, an UNMISTAKABLE
# "turn this static page into a real app" ask. We require BOTH a non-negated
# backend signal (accounts / saved data / CRUD) AND one of these framing phrases,
# so a bare backend-noun edit ("добавь форму входа в hero", "сделай личный кабинет
# на тёмном фоне", "переименуй кнопку войти") stays a CHEAP surgical edit — only a
# clear app-ification escalates the stack. NB: "личный кабинет" is deliberately NOT
# a framing phrase (it is a plain noun that appears in cosmetic edits) — it counts
# only as a backend SIGNAL. Framing is whole-phrase substrings on lowered text.
_APPIFY_FRAMING: frozenset[str] = frozenset(
    {
        "полноценное приложение", "полноценное веб-приложение",
        "полноценным приложением", "полноценное web-приложение",
        "настоящее приложение", "настоящим приложением", "настоящее веб-приложение",
        "веб-приложение", "веб приложение", "web-приложение", "web app",
        "web application", "real app", "real application",
        "сделай приложение", "сделать приложением", "сделай из этого приложение",
        "преврати в приложение", "превратить в приложение", "превратить в полноценное",
        "чтобы пользователи могли", "чтобы клиенты могли", "чтобы клиенты записыв",
        "чтобы пользователи регистр", "чтобы пользователи могли регистр",
        "регистрироваться и сохран", "регистрировались и сохран",
    }
)


def detect_appification(text: str) -> bool:
    """True when a FOLLOW-UP unmistakably asks to turn a static page into a real
    (container) app (P-H1).

    Requires (a) a non-negated backend signal AND (b) explicit app-ification
    framing, and is vetoed by an explicit no-backend ask — so a cosmetic
    auth-element edit ("добавь форму входа", "сделай кнопку войти крупнее") and an
    explicit no-account tool ("лендинг без регистрации") never escalate. Reuses the
    negation-aware ``_signal_fires`` so "без регистрации" does not count as a
    signal. Deterministic, no LLM — safe on the hot path."""
    low = (text or "").lower()
    if _explicit_no_backend(low):
        return False
    if not any(_signal_fires(low, sig) for sig in _BACKEND_SIGNALS):
        return False
    return any(frame in low for frame in _APPIFY_FRAMING)


# Owner directive 2026-06-18: don't lock the builder to web output. When the user
# clearly asks for a standalone PROGRAM/SCRIPT (any language) — not a site — route
# to the `code` template (file-only, no container; the writer emits arbitrary
# source via the <file> contract). High precision so a normal site request never
# trips it: STRONG artifact words win outright; a bare "… на <язык>" only counts
# as code when no website word is present (so "сайт на Python" stays a web build).
_CODE_STRONG_SIGNALS: frozenset[str] = frozenset(
    {
        "скрипт", "script",
        "парсер", "парсинг", "scraper", "скрейпер", "распарс",
        "утилит", "консольн", "командной строк", "command line", "cli ",
        "напиши код", "написать код", "напиши программу", "код на ",
        "программу на", "программа на",
        "алгоритм", "automation script", "автоматизирующий скрипт",
    }
)
# Ambiguous "<вещь> на <язык>" — code only when the prompt isn't about a website.
_CODE_LANG_HINTS: frozenset[str] = frozenset(
    {
        " на python", " на питон", " на golang", " на go ", " на rust",
        " на расте", " на c++", " на си++", " на java", " на джава",
        " на kotlin", " на котлин", " на php", " на ruby", " на bash",
        " на c#", " на c шарп",
    }
)
# Website words that veto the WEAK language hint (a Django/SSR site mentions a
# language but still wants a web product, not a bare script).
_WEBSITE_SIGNALS: frozenset[str] = frozenset(
    {
        "сайт", "лендинг", "лендос", "landing", "магазин", "портфолио",
        "веб-страниц", "веб страниц", "website", "webpage", "дашборд",
        "интернет-магазин",
    }
)


def _infer_code_from_text(text: str) -> str | None:
    """Deterministic safety-net: pick the ``code`` stack for a program/script ask.

    Returns ``"code"`` when the text clearly asks for a standalone program in any
    language, else ``None`` (leave the model's / backend net's choice alone). Runs
    BEFORE the backend escalation so "напиши парсер на python" → code, not an
    auth-backed web app."""
    low = (text or "").lower()
    # An explicit "plain static HTML page" ask is never code, even though it often
    # says "без скриптов" (the "скрипт" substring would otherwise trip the strong
    # signal below). Owner 2026-06-18 escape hatch takes precedence.
    if _explicit_static(low):
        return None
    if any(sig in low for sig in _CODE_STRONG_SIGNALS):
        return "code"
    if any(sig in low for sig in _CODE_LANG_HINTS) and not any(
        w in low for w in _WEBSITE_SIGNALS
    ):
        return "code"
    return None


def infer_result_type_from_text(text: str) -> str | None:
    """Deterministic result-type from product intent (safety-net behind the LLM
    classifier). Priority mirrors the legacy stack net:
      code > explicit-static > web_app(account|framing) > landing(conversion) > None.
    BS-7 split: a conversion word WITHOUT an account/per-user signal is a
    `landing` (public lead form), never a `web_app` behind /signin. An
    app-ification framing phrase (`_APPIFY_FRAMING`) counts as a web_app signal
    too (bug 2 — raise framing into the first build). None → caller asks /
    defaults to site/spa."""
    low = (text or "").lower()
    if _infer_code_from_text(low):
        return "code"
    if _explicit_static(low):
        return "static"
    if _explicit_no_backend(low):
        return "landing" if _has_conversion_intent(low) else None
    if _has_account_intent(low) or any(f in low for f in _APPIFY_FRAMING):
        return "web_app"
    if _has_conversion_intent(low):
        return "landing"
    return None


# Static is opt-in (owner 2026-06-18). These are the ONLY phrases that keep a
# build on the flat-HTML `static` stack — an explicit ask for a plain static page.
# A normal site request (лендинг/портфолио/блог/кофейня) does NOT match and is
# routed to `spa` instead. Narrow on purpose: a false miss just means one more
# interactive React build, which is exactly the new default we want.
_EXPLICIT_STATIC_SIGNALS: frozenset[str] = frozenset(
    {
        "статичн", "статическ", "статикой", "статику", "статика",
        "просто html", "просто статич", "обычный html", "чистый html",
        "html-страниц", "html страниц", "голый html",
        "без интерактив", "без js", "без скрипт",
        "plain html", "static site", "static page", "just html", "just static",
    }
)


def _explicit_static(text: str) -> bool:
    """True when the user EXPLICITLY asked for a plain static HTML page.

    The only thing that keeps a build on the `static` stack now (owner 2026-06-18:
    «убрать статику, оставляем только если задача явно требует»). Whole-phrase
    substrings on lowered text; a generic site/landing request never matches."""
    low = (text or "").lower()
    return any(sig in low for sig in _EXPLICIT_STATIC_SIGNALS)


# Owner 2026-06-19 — a `code` project follow-up asking to RUN it as a web page
# ("сделай веб-вид", "в браузере", "запусти здесь"). Used ONLY on a `code` project
# to pivot it to a previewable `static` web build. PRECISE on purpose: a false
# positive flips the project's stack, so we match clear "run as a web page here"
# phrases, NOT bare "запусти" (which on a code project means "run the script") or
# bare "сайт"/"html" (a scraper of a site / parser of html is still code).
_WEB_PIVOT_SIGNALS: frozenset[str] = frozenset(
    {
        "веб-вид", "веб вид", "веб-верс", "веб верс", "веб-страниц", "веб страниц",
        "в браузере", "прям здесь", "прямо здесь", "запусти здесь",
        "запустить здесь", "открыть здесь", "поиграть здесь", "сыграть здесь",
        "preview", "превью", "в вебе", "онлайн-вид", "сделай онлайн",
        "playable", "run it here", "in the browser",
    }
)


def _infer_web_pivot(text: str) -> bool:
    """True when the user wants to RUN a code project as a web page (owner
    2026-06-19). Used ONLY on a `code` project follow-up → pivot to `static`."""
    low = (text or "").lower()
    return any(sig in low for sig in _WEB_PIVOT_SIGNALS)


# Owner 2026-06-19 — user wants to RUN / INSTALL the project locally on their own
# machine ("как запустить", "хочу запустить", "установщик", "дай поиграть"). On a
# follow-up this short-circuits to a one-click "download installer" card (the .zip
# already ships a run.bat launcher) instead of another generation. PRECISE: clear
# run/install phrases only — NOT bare "запусти" (= generate) or web-preview phrases
# (those are the web-pivot above).
_RUN_INTENT_SIGNALS: frozenset[str] = frozenset(
    {
        "установщик", "инсталлятор", "как установить", "установи и запусти",
        "как запустить", "как мне запустить", "хочу запустить", "запусти локально",
        "запустить локально", "запустить у себя", "запустить на компе",
        "запустить на компьютере", "скачать и запустить", "скачать и поиграть",
        "хочу поиграть", "дай поиграть", "как поиграть", "как мне поиграть",
        "сразу запустить", "запустить у меня", "как мне это запустить",
        "хочу установить",
        # «в один клик» phrasings — only when paired with a run word, so a UI
        # request like «заказ в один клик» (a build) never trips it.
        "запустилось в один клик", "запускалось в один клик",
        "запустить в один клик", "запуск в один клик", "в один клик запуст",
        "запустилось одним кликом", "запустить одним кликом",
        "одним кликом запуст", "запустить в 1 клик", "в 1 клик запуст",
    }
)


def _infer_run_intent(text: str) -> bool:
    """True when the user wants to RUN/INSTALL the project locally (owner
    2026-06-19) → offer a one-click installer download instead of a build."""
    low = (text or "").lower()
    return any(sig in low for sig in _RUN_INTENT_SIGNALS)


# WEAKER, AMBIGUOUS hints (owner 2026-06-19: «спрашивай, если сомневаешься»). These
# might mean "run/share it locally" OR something else (generate, publish a link,
# play in preview). Too uncertain to act on → we ASK "собрать установщик?" with
# yes/no chips instead of guessing. Kept distinct from the STRONG set above.
_RUN_MAYBE_SIGNALS: frozenset[str] = frozenset(
    {
        "запусти", "запустить", "поиграть", "поиграем",
        "поделиться", "поделись", "скинуть", "скинь друз", "отправить друз",
        "отправить друг", "как использовать", "как пользоваться",
        "хочу попробовать", "дай попробовать", "как открыть проект",
        "можно запустить", "можно ли запустить", "это запустить",
    }
)
# A "no, keep editing" reply to the installer question → don't build garbage from
# the bare "нет"; ask what to change instead.
_RUN_DECLINE_SIGNALS: frozenset[str] = frozenset(
    {
        "доработать проект", "доработать", "не надо установщик",
        "без установщика", "не нужен установщик", "продолжаем дорабат",
        "продолжим дорабат", "нет, правим", "не устанавливать",
    }
)


def _infer_run_intent_maybe(text: str) -> bool:
    """True when run/install intent is PLAUSIBLE but not certain → ask first.
    Excludes the strong case (handled directly) and an explicit decline."""
    low = (text or "").lower()
    if _infer_run_intent(low) or _is_run_decline(low):
        return False
    return any(sig in low for sig in _RUN_MAYBE_SIGNALS)


def _is_run_decline(text: str) -> bool:
    """True when the user declines the installer offer ("нет, доработать")."""
    low = (text or "").lower()
    if any(sig in low for sig in _RUN_DECLINE_SIGNALS):
        return True
    return low.strip().startswith("нет") and (
        "доработ" in low or "правит" in low or "продолж" in low or "устанавлив" in low
    )


# Explicit "no accounts / no login" phrases. Unlike _BACKEND_SIGNALS (bare stems
# whose NEGATED mentions are filtered out), these are whole negated phrases that
# carry a *positive* intent: the user actively refused auth + persistence. A tool
# the user said needs NO login must never be escalated to an auth-backed stack —
# nextjs_entities/fullstack scaffold a `(app)` route group behind /signin, which
# gates the tool behind a login wall the user explicitly rejected (dogfood run #2:
# «калькулятор ипотеки … без регистрации» → model picked nextjs_entities → the
# calculator landed in (app)/dashboard behind /signin, while the page copy itself
# promised «без регистрации»).
_NO_BACKEND_SIGNALS: frozenset[str] = frozenset(
    {
        "без регистрац", "без вход", "без аккаунт", "без авториз", "без логин",
        "не нужна регистрац", "не нужен вход", "не нужна авториз",
        "no login", "no sign up", "no signup", "no account", "no registration",
        "without login", "without sign", "without account",
    }
)


def _explicit_no_backend(text: str) -> bool:
    """True when the user EXPLICITLY refused accounts/login (whole-phrase match).

    Drives the negative stack safety-net (run_discovery BUILD path): a model that
    over-escalates such a tool to nextjs_entities/fullstack gets vetoed back to
    ``spa`` (a no-backend interactive React tool), so the tool isn't gated behind
    /signin against the user's stated wish."""
    haystack = (text or "").lower()
    return any(sig in haystack for sig in _NO_BACKEND_SIGNALS)


@dataclass(frozen=True)
class DiscoveryResult:
    """Outcome of one discovery turn.

    ``action`` is ASK (stream ``message`` as the next question, no build) or BUILD
    (run the generator with ``brief`` as the prompt). ``stack`` is the recommended
    stack id; ``message`` on a BUILD is a short friendly "собираю…" note.

    On an ASK, ``choices`` are 2–5 short quick-reply answers the UI renders as
    tappable chips beneath the question; ``allow_custom`` (always True) tells the
    UI to keep a free-text "Другое" path open so a chip never traps the user.
    Every ASK lands with chips — the model is steered to provide them and a
    deterministic stage-keyed floor fills any it omits (V2.1: чипы СРАЗУ на
    первый промпт, never bare text).

    ``multi_select`` is True when several chips can sensibly apply at once (e.g.
    "какие разделы нужны?") — the UI then renders the chips as toggles plus a
    «Готово» button so the user picks a SET in one go instead of one chip per
    turn (NORTH STAR pillar 2 — мультивыбор). The model may flag it; a
    deterministic keyword floor (:func:`_infer_multi_select`) catches the
    inherently multi-answer questions it omits, so it is model-independent.
    """

    action: str
    message: str
    brief: str
    stack: str
    choices: tuple[str, ...] = ()
    allow_custom: bool = True
    multi_select: bool = False
    # Onboarding-popup framing (NORTH STAR pillar 2): the 1-based position of this
    # question in the planned batch and the batch size, so the workspace can frame
    # discovery as a guided popup with a «Вопрос N из M» counter instead of a bare
    # chat row. 0/0 on a BUILD turn and on the legacy per-question path (no upfront
    # plan → unknown total).
    question_index: int = 0
    question_total: int = 0
    # Short human niche label inferred from the product idea (e.g. «школа /
    # образование») for the framing banner «Давайте разберёмся под вашу идею: …».
    # Empty when the idea matches no known niche (banner then shows no suffix).
    niche: str = ""
    # Onboarding LIVE-causality (NORTH STAR pillar 2 — «вас услышали»): short
    # labels of the answers gathered so far (chip taps / free text), newest last,
    # so the popup can echo «✓ …» recap chips back at the user and prove the loop
    # reacts to what they said. Empty on the first turn (nothing answered yet) and
    # on BUILD turns.
    recap: tuple[str, ...] = ()
    # Onboarding LIVE design-preview (NORTH STAR pillars 2×3 — «покажи ЧТО
    # построим»): the resolved design tokens (accent hex/family, theme, tone,
    # sections) the gathered answers steer toward, so the popup can paint a live
    # mini-hero that morphs on every answer instead of only echoing words. Shape
    # is :func:`chip_pixel_gate.spec_preview`'s payload (or None when nothing
    # design-relevant has been decided). None on the first turn and on BUILD turns.
    design_preview: dict[str, Any] | None = None


# Niche → short Russian banner label, matched by lowered-substring stems on the
# product idea. Model-independent (a fixed lookup, no gateway call) so the
# onboarding frame names the niche the same way every run. First match wins;
# order most-specific → general. Unrecognised idea → "" (generic banner). Used
# only for the framing banner, so a miss is cosmetic, never a dead-end.
_NICHE_LABELS: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "школа / образование",
        ("школ", "гимназ", "лице", "сош", "мбоу", "образоват", "ученик", "учебн", "обучен"),
    ),
    (
        "клиника / медицина",
        ("клиник", "медицин", "стоматолог", "больниц", "пациент", " врач", "врача"),
    ),
    ("салон красоты", ("салон красот", "парикмахер", "барбершоп", "маникюр", "косметолог")),
    ("фитнес / спорт", ("фитнес", "спортзал", "тренаж", " йог", "тренировк", "спорт-клуб")),
    ("кафе / ресторан", ("ресторан", "кафе", "пиццери", "кофейн", "доставка еды", "меню")),
    ("автосервис", ("автосервис", "автомастер", "шиномонтаж", "ремонт авто", "сто ")),
    ("недвижимость", ("недвижим", "квартир", "застройщик", "аренда жил")),
    ("туризм / путешествия", ("турагент", "путешеств", " тур ", "отел", "бронирован")),
    ("мероприятия / события", ("конференц", "мероприят", "событи", "билет", "афиш")),
    (
        "интернет-магазин",
        ("магазин", "shop", "e-comm", "ecommerce", "товар", "каталог", "маркетплейс"),
    ),
    ("CRM / управление", ("crm", "црм", "воронк", "сделк", "пайплайн", "лид")),
    ("портфолио", ("портфолио", "резюме", "мои работы")),
    ("блог / медиа", ("блог", "журнал", "новостн", "медиа", "стат")),
)


def infer_niche_label(text: str) -> str:
    """Map a product idea to a short niche label for the onboarding-frame banner.

    Deterministic and LLM-free — a fixed substring lookup over lowered text, so
    every run frames the same idea identically. Returns "" when nothing matches;
    the banner then shows its generic phrasing with no niche suffix (never a
    dead-end). Cosmetic only — does not steer the build."""
    low = (text or "").lower()
    for label, stems in _NICHE_LABELS:
        if any(stem in low for stem in stems):
            return label
    return ""


# ── Onboarding LIVE-causality (NORTH STAR pillar 2) ──────────────────────────
# The interview was inert: the niche badge was inferred once from the first
# prompt, the next question never reacted to prior answers, and nothing echoed
# back what the user said. These pure helpers make the loop visibly causal — a
# live niche badge (re-inferred on the CUMULATIVE answers) and an answer-recap
# strip — without any extra gateway call.

_MAX_RECAP_ITEMS = 3
_MAX_RECAP_LEN = 28


def _user_contents(history: list[dict[str, str]] | None) -> list[str]:
    """Every non-empty user-role turn, in order — the idea then each answer."""
    return [
        (m.get("content") or "").strip()
        for m in (history or [])
        if (m.get("role") or "") == "user" and (m.get("content") or "").strip()
    ]


def cumulative_idea(
    history: list[dict[str, str]] | None, latest_prompt: str
) -> str:
    """All user-supplied text so far (the idea + every answer), newline-joined.

    Basis for LIVE niche re-inference: :func:`infer_niche_label` is re-run on
    THIS (not just the first prompt), so the badge sharpens as the conversation
    reveals more — a vague «сайт для бизнеса» that later mentions «доставка еды»
    surfaces «кафе / ресторан». Deterministic; the latest prompt rides last."""
    parts = list(_user_contents(history))
    latest = (latest_prompt or "").strip()
    if latest:
        parts.append(latest)
    return "\n".join(parts)


def gather_answers(
    history: list[dict[str, str]] | None,
    latest_prompt: str,
    asked_count: int,
) -> tuple[str, ...]:
    """The user's answers to discovery questions so far (chip taps / free text),
    newest last. The FIRST user message is the product idea, not an answer, so it
    is excluded. On the very first turn (``asked_count == 0``) nothing has been
    answered yet → empty. The latest prompt is the answer to the previous
    question (not yet in ``history``), so it rides last once the interview is in
    flight. Pure — drives the answer-recap card."""
    answers = list(_user_contents(history)[1:])  # drop the idea
    if asked_count >= 1:
        latest = (latest_prompt or "").strip()
        if latest:
            answers.append(latest)
    return tuple(answers)


def recap_labels(answers: tuple[str, ...]) -> tuple[str, ...]:
    """Compact the gathered answers into ≤3 short recap chips (newest-last), each
    whitespace-collapsed and length-clipped, so the onboarding can echo «✓ …»
    back at the user without flooding the narrow chat panel."""
    out: list[str] = []
    for a in answers[-_MAX_RECAP_ITEMS:]:
        label = " ".join(a.split())
        if len(label) > _MAX_RECAP_LEN:
            label = label[: _MAX_RECAP_LEN - 1].rstrip() + "…"
        if label:
            out.append(label)
    return tuple(out)


# Confidence-skip floor (pillar 2 — «лучший онбординг — его отсутствие»). Once
# the gathered answers have pinned a RECOGNISED niche AND ≥ this many design axes,
# the interview knows enough — it builds instead of asking the remaining planned
# questions. Conservative: requires real engagement (≥2 answered) so a decisive
# user gets a shorter path while an undecided one keeps the full batch.
_CONFIDENCE_SKIP_MIN_ANSWERS = 2
_CONFIDENCE_SKIP_MIN_AXES = 2


def confident_enough_to_build(
    history: list[dict[str, str]] | None,
    latest_prompt: str,
    *,
    asked_count: int,
    niche: str,
) -> bool:
    """True when the gathered answers already pin a recognised niche + ≥2 design
    axes — the confident user has steered enough, so the popup should build now
    rather than ask the rest of the batch (NORTH STAR pillar 2 confidence-skip).

    Pure + deterministic + fail-soft: reuses the same :func:`spec_from_discovery`
    extractor the gauntlet uses (R-04 single source); an unclear interview returns
    False and the full batch continues. Gated to ``asked_count >= 2`` so it can
    never cut an interview the user has barely begun."""
    if asked_count < _CONFIDENCE_SKIP_MIN_ANSWERS or not niche:
        return False
    spec = spec_from_discovery(history, latest_prompt)
    return spec is not None and spec_confidence(spec) >= _CONFIDENCE_SKIP_MIN_AXES


def wants_build_now(prompt: str) -> bool:
    """True when the user explicitly asked to skip questions and build."""
    text = (prompt or "").strip().lower()
    return any(sig in text for sig in _BUILD_NOW_SIGNALS)


def zero_question_build(
    history: list[dict[str, str]], latest_prompt: str
) -> DiscoveryResult | None:
    """V2.12 zero-question floor: if the FIRST prompt already pins ≥
    ``_ZERO_QUESTION_MIN_AXES`` concrete design axes (theme / accent / sections /
    tone), build straight away — the popup never appears and we skip the gateway
    round-trip entirely. Returns the BUILD result, or None when the prompt is too
    thin and a question is genuinely needed. Deterministic and LLM-free.

    Shared by the per-question :func:`run_discovery` and the batch planner so
    both paths honour the same "best onboarding is its absence" floor (North Star
    pillar 2) — extracted so neither can drift from the other.
    """
    spec = compile_build_spec(latest_prompt or "")
    if spec_confidence(spec) < _ZERO_QUESTION_MIN_AXES:
        return None
    brief = _fallback_brief(history, latest_prompt)
    intent_text = "\n".join(
        [*(m.get("content") or "" for m in history), latest_prompt or ""]
    )
    # Same priority as run_discovery's BUILD net: code > backend(entities) > spa
    # default. Static is opt-in (owner 2026-06-18) — a richly-specified first prompt
    # is a real site/app, so it builds as `spa`, never flat static, unless the user
    # explicitly asked for a plain HTML page.
    stack = _infer_code_from_text(intent_text) or _infer_stack_from_text(intent_text)
    if stack is None:
        stack = "static" if _explicit_static(intent_text) else "spa"
    log.info(
        "discovery: zero-question build (%d intent axes pinned, stack=%s)",
        spec_confidence(spec),
        stack,
    )
    return DiscoveryResult(
        action=BUILD,
        message="Понял задумку — собираю первый вариант. Это займёт минуту.",
        brief=brief,
        stack=stack,
    )


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
    "- \"spa\" — ДЕФОЛТ для сайтов и интерактивных продуктов БЕЗ бэкенда/аккаунтов: "
    "лендинг, портфолио, блог, визитка, промо-страница, а также калькулятор, "
    "конвертер, визуализатор, генератор, игра, конфигуратор, дашборд на демо-"
    "данных. Интерактивное React-приложение. Если это сайт/страница и нет явной "
    "БД/аккаунтов — это \"spa\".\n"
    "- \"nextjs_entities\" — есть пользователи, каталог/товары, корзина, запись/"
    "бронирование, CRM, личный кабинет, любые сохраняемые данные. Полноценное "
    "приложение с БД.\n"
    "- \"static\" — ТОЛЬКО если пользователь ЯВНО просит простую СТАТИЧНУЮ "
    "HTML-страницу («просто html», «статичная страница», «без интерактива/без js»). "
    "Обычный лендинг/портфолио/блог сюда НЕ относится — это \"spa\". Не выбирай "
    "static по умолчанию.\n"
    "- \"fullstack\" — интерактивное веб-приложение с лёгким собственным "
    "бэкендом, не подходящее под entities.\n"
    "- \"code\" — НЕ сайт, а отдельная ПРОГРАММА/СКРИПТ на любом языке "
    "(Python, Go, Rust, JS, Bash, …): скрипт, парсер, утилита, CLI, бот для "
    "обработки данных, алгоритм. Мы храним код как файлы (как репозиторий), "
    "пользователь скачивает/пушит в GitHub. Если просят «скрипт/программу на "
    "<язык>» — это \"code\", НЕ static. (Сайт на Python/Django — это всё равно "
    "веб-стек, а не code.)\n\n"
    "ФОРМАТ ОТВЕТА — СТРОГО один JSON-объект на одной строке, без пояснений и кода.\n"
    "Если спрашиваешь:\n"
    '{"action":"ask","message":"<один короткий вопрос на русском>",'
    '"choices":["<2–5 коротких вариантов ответа, 1–3 слова каждый>"],'
    '"multiSelect":false}\n'
    "  — choices это подсказки-кнопки под вопросом (например для «Нужна "
    "админка?» → [\"Да\",\"Нет\"]; для стиля → [\"Премиум\",\"Дружелюбное\","
    "\"Строгое\"]). ВСЕГДА давай 2–5 таких вариантов — даже для открытого "
    "вопроса предложи типовые направления-примеры (например «как "
    "представляешь стиль?» → [\"Минимализм\",\"Премиум\",\"Ярко\",\"Строго\"]). "
    "Пользователь всегда может ответить и своим текстом (кнопка «Другое»).\n"
    "  — multiSelect:true СТАВЬ, когда уместно выбрать НЕСКОЛЬКО вариантов "
    "сразу (например «какие разделы нужны?» → можно отметить и каталог, и "
    "блог, и контакты). Для вопросов с одним ответом (тон, да/нет, тип "
    "продукта) ставь multiSelect:false или опусти поле.\n"
    "Если пора строить:\n"
    '{"action":"build","message":"<короткая фраза: «Отлично, собираю…»>",'
    '"brief":"<сжатый бриф для генератора на русском: тип продукта, цель, '
    'аудитория, обязательные разделы/возможности, тон, цвета/референс, важные '
    'детали>","stack":"static|spa|nextjs_entities|fullstack|code"}'
)


# Deterministic fallback questions + matching quick-reply chips, keyed by how
# many questions we've already asked (no randomness — keeps the turn resumable).
# Chips parallel ``_FALLBACK_QUESTIONS`` index-for-index. Every stage carries a
# non-empty set: this table is ALSO the chip FLOOR for a model ASK that omitted
# its own choices (see ``run_discovery``), so a discovery question never lands as
# bare text (V2.1). The stages mirror the model's general→detail progression, so
# the floor reads sensibly even under a model-authored question of the same stage.
_FALLBACK_QUESTIONS: tuple[str, ...] = (
    "Расскажите в двух словах — что за проект и какая у него главная цель?",
    "Кто ваша аудитория и какое настроение ближе — премиум, дружелюбное или строгое?",
    "Какие разделы или возможности обязательно нужны?",
    "Есть фирменные цвета, логотип или сайт-референс, который вам нравится?",
)
_FALLBACK_CHOICES: tuple[tuple[str, ...], ...] = (
    ("Лендинг", "Интернет-магазин", "Приложение с кабинетом", "Портфолио", "Блог"),
    ("Премиум", "Дружелюбное", "Строгое"),
    ("Каталог", "Корзина", "Запись/бронь", "Личный кабинет", "Блог"),
    ("Свои цвета", "На ваш вкус"),
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


# Questions that are inherently multi-answer — "which sections / features /
# pages do you need?" — naturally take a SET, not one chip per turn. We detect
# them on the QUESTION TEXT (lowered substrings, RU stems + EN) so the same
# floor fires for a model-authored question AND the deterministic fallback,
# model-independent. Tone / yes-no / single-choice questions stay single-select.
_MULTI_SELECT_HINTS: frozenset[str] = frozenset(
    {
        "раздел", "возможност", "функци", "секци", "страниц", "фич",
        "что должно быть", "что нужно на сайт", "какие блоки",
        "sections", "features", "pages",
    }
)


def _infer_multi_select(message: str) -> bool:
    """True when the question text reads as an inherently multi-answer question
    (which sections / features / pages) — the UI should offer toggle chips +
    «Готово» so the user picks several at once. Deterministic, model-independent."""
    haystack = (message or "").lower()
    return any(hint in haystack for hint in _MULTI_SELECT_HINTS)


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


# ── Batch discovery plan (NORTH STAR pillar 2 / owner rule 13 #1) ────────────
# The progressive interview costs ONE gateway round-trip PER question, so the
# user waits ~a minute BETWEEN questions and (on a discovery timeout) the first
# question degrades to a generic "какой тип сайта" instead of one about THEIR
# product. The batch path fixes both: ONE upfront pass reads the first prompt and
# plans the WHOLE set of 3–4 questions, tailored to that product; they are
# persisted and then served with ZERO further gateway calls — instant between
# steps. On the single upfront call failing, we degrade to a MEANINGFUL batch
# (general→detail), never a lone generic question.

# Cap the batch so onboarding never turns into an inquisition; mirrors the
# "обычно 2–4 вопроса" steer. Stays ≤ MAX_DISCOVERY_QUESTIONS.
_PLAN_MAX_QUESTIONS = 4
_MAX_QUESTION_LEN = 200
# Retry budget for the single upfront plan pass. A transient gateway blip / cold
# start / occasional non-strict reply would otherwise persist the GENERIC fallback
# PERMANENTLY on the project (the plan is cached on first turn and NEVER recomputed
# — owner saw «СРМ для школы» stuck on generic «что за проект» questions, 2026-06-21).
# The model returns a tailored batch in ~3-4s, so a couple of retries land a real
# batch well within the request budget; only a sustained outage reaches the fallback.
_PLAN_ATTEMPTS = 3
_PLAN_TIMEOUT = 15.0
_PLAN_RETRY_BACKOFF = 0.5

_PLAN_SYSTEM = (
    "Ты — продуктовый дизайнер Omnia.AI. Пользователь прислал ПЕРВЫЙ запрос на "
    "сайт/приложение. Прежде чем строить, надо задать ему 3–4 КОРОТКИХ "
    "уточняющих вопроса — но НЕ дженерик, а ЗАТОЧЕННЫХ ИМЕННО под его продукт.\n\n"
    "ПРАВИЛА:\n"
    "1. Вопросы конкретно про ЭТОТ продукт. Пример: запрос «сайт школы МБОУ СОШ "
    "15» → спрашивай про ступени/классы, что разместить (расписание, новости, "
    "электронный журнал, приём в 1 класс), нужен ли вход для родителей, стиль — "
    "а НЕ дженерик «какой тип сайта». Запрос «магазин кофе» → про ассортимент, "
    "доставку, опт/розницу, тон бренда.\n"
    "2. Двигайся от сути к деталям: суть/главная цель → аудитория/наполнение → "
    "ключевые разделы/функции → стиль/цвета/референс.\n"
    "3. Каждый вопрос — ОДНА короткая фраза, с 2–5 вариантами-подсказками "
    "(1–3 слова каждый). Пользователь всегда сможет вписать свой ответ.\n"
    "4. multiSelect:true для вопросов, где уместно выбрать НЕСКОЛЬКО вариантов "
    "(разделы / функции / что разместить); false для одиночных (тон, да/нет).\n\n"
    "ФОРМАТ — СТРОГО один JSON-объект на одной строке, без пояснений и кода:\n"
    '{"questions":[{"message":"<вопрос>","choices":["<вариант>","<вариант>"],'
    '"multiSelect":false},{"message":"...","choices":["..."],"multiSelect":true}]}'
)


@dataclass(frozen=True)
class PlannedQuestion:
    """One pre-computed discovery question (text + quick-reply chips). The whole
    set is planned in a single upfront pass and persisted, then served one at a
    time with no further gateway call (``serve_planned_question``)."""

    message: str
    choices: tuple[str, ...]
    allow_custom: bool = True
    multi_select: bool = False

    def to_dict(self) -> dict[str, object]:
        """JSON-safe form for persistence on ``Project.discovery_plan``."""
        return {
            "message": self.message,
            "choices": list(self.choices),
            "allow_custom": self.allow_custom,
            "multi_select": self.multi_select,
        }

    @classmethod
    def from_dict(cls, raw: object) -> PlannedQuestion | None:
        """Rebuild from a persisted dict; None when it carries no question text."""
        if not isinstance(raw, dict):
            return None
        message = str(raw.get("message") or "").strip()
        if not message:
            return None
        choices = tuple(
            str(c) for c in (raw.get("choices") or []) if isinstance(c, str)
        )
        return cls(
            message=message,
            choices=choices,
            allow_custom=bool(raw.get("allow_custom", True)),
            multi_select=bool(raw.get("multi_select", False)),
        )


def _plan_fallback() -> list[PlannedQuestion]:
    """Deterministic, MEANINGFUL batch when the single upfront pass fails — the
    stage-keyed general→detail questions (суть → аудитория → разделы → стиль),
    each with its paired chips. Not a lone generic question (owner rule 13 #1)."""
    return [
        PlannedQuestion(
            message=_FALLBACK_QUESTIONS[i],
            choices=_FALLBACK_CHOICES[i],
            multi_select=_infer_multi_select(_FALLBACK_QUESTIONS[i]),
        )
        for i in range(len(_FALLBACK_QUESTIONS))
    ]


def _questions_from_parsed(parsed: dict[str, object] | None) -> list[PlannedQuestion]:
    """Normalise the model's ``{"questions":[…]}`` into ≤ ``_PLAN_MAX_QUESTIONS``
    clean questions. Each lands with chips (the stage floor fills an omitted set)
    and a model-independent multi-select flag (``_infer_multi_select`` catches a
    question the model forgot to flag). Junk / wrong shape → empty (caller uses
    the deterministic batch)."""
    if not isinstance(parsed, dict):
        return []
    raw = parsed.get("questions")
    if not isinstance(raw, list):
        return []
    out: list[PlannedQuestion] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        message = str(item.get("message") or "").strip()[:_MAX_QUESTION_LEN].strip()
        if not message:
            continue
        choices = _clean_choices(item.get("choices"))
        if not choices:
            choices = _fallback_choices(len(out))
        multi = bool(item.get("multiSelect")) or _infer_multi_select(message)
        out.append(
            PlannedQuestion(message=message, choices=choices, multi_select=multi)
        )
        if len(out) >= _PLAN_MAX_QUESTIONS:
            break
    return out


async def plan_discovery_questions(
    prompt: str, language: str = "ru"
) -> list[PlannedQuestion]:
    """ONE upfront gateway pass → the WHOLE batch of 3–4 product-tailored
    questions. Never raises: any gateway/parse failure degrades to the
    deterministic general→detail batch (:func:`_plan_fallback`) so onboarding
    always lands a sensible set of questions, never a single generic one.

    The single call gets a generous budget (one pass replaces N per-question
    round-trips), but stays under the client's ``POST /prompt`` timeout so a cold
    gateway degrades to the batch fallback within the window (R-10 fail fast).

    ``language`` is the project's detected language (BCP-47-ish, e.g. ``"en"``).
    RU is the default and leaves the system prompt unchanged (zero diff from the
    pre-i18n baseline).
    """
    settings = get_settings()
    url = f"{settings.llm_gateway_url.rstrip('/')}/v1/chat/completions"
    system_content = _PLAN_SYSTEM + _reply_language_line(language)
    convo = [
        {"role": "system", "content": system_content},
        {"role": "user", "content": (prompt or "").strip()[:4000] or "(пусто)"},
    ]
    payload = {
        # A FAST, reliable model — this call sits inside the POST /prompt budget
        # and a cold-start timeout drops onboarding to the generic batch (owner
        # rule 13 #1). The dedicated ``discovery_plan`` role keeps it swappable.
        "model": model_for_role("discovery_plan"),
        "messages": convo,
        "max_tokens": 900,
        "stream": False,
    }
    # Retry the pass (see _PLAN_ATTEMPTS): a single transient miss must NOT stick
    # the project on the generic fallback forever (it is cached and never recomputed).
    # Each attempt re-samples the model, so an occasional non-strict reply is also
    # shaken off. Success exits immediately (normal case = one ~4s attempt).
    for attempt in range(_PLAN_ATTEMPTS):
        parsed: dict[str, object] | None = None
        try:
            async with httpx.AsyncClient(timeout=_PLAN_TIMEOUT) as client:
                resp = await client.post(url, json=payload)
            if resp.status_code < 400:
                body = resp.json()
                content = (body.get("choices") or [{}])[0].get("message", {}).get("content", "")
                parsed = _parse(content)
            else:
                log.warning(
                    "discovery plan: gateway %d (attempt %d/%d)",
                    resp.status_code,
                    attempt + 1,
                    _PLAN_ATTEMPTS,
                )
        except Exception as exc:
            log.warning(
                "discovery plan: gateway error (attempt %d/%d): %r",
                attempt + 1,
                _PLAN_ATTEMPTS,
                exc,
            )
        questions = _questions_from_parsed(parsed)
        if questions:
            return questions
        if attempt + 1 < _PLAN_ATTEMPTS:
            await asyncio.sleep(_PLAN_RETRY_BACKOFF)
    log.warning(
        "discovery plan: all %d attempts failed — generic batch fallback", _PLAN_ATTEMPTS
    )
    return _plan_fallback()


# ── Result-type classifier (RT-1) — the primary signal behind the keyword net ──
_RESULT_TYPE_SYSTEM = (
    "Ты — продуктовый аналитик Omnia.AI. Пользователь прислал ПЕРВЫЙ запрос на "
    "сайт/приложение. Определи ТИП результата. Ровно один из:\n"
    "- \"landing\" — маркетинговый лендинг/промо. Цель — заявка, запись, бронь, "
    "звонок. Форма-заявка БЕЗ регистрации и личных кабинетов («лендинг с записью "
    "на приём»). Запись/бронь сами по себе — это landing, НЕ web_app.\n"
    "- \"web_app\" — приложение с АККАУНТАМИ и сохраняемыми данными: личный "
    "кабинет, регистрация, у каждого пользователя свои данные, CRM, каталог+"
    "корзина+оформление, админка.\n"
    "- \"tool\" — интерактивный инструмент на фронтенде без бэкенда.\n"
    "- \"site\" — обычный информационный сайт (портфолио, блог, визитка).\n"
    "- \"code\" — НЕ сайт, а программа/скрипт на любом языке.\n\n"
    "Аккаунты/кабинет → web_app. Заявка/запись без кабинета → landing. Сомнения "
    "между двумя — меньшая confidence.\n"
    "ФОРМАТ — СТРОГО один JSON-объект на одной строке:\n"
    '{"type":"landing|web_app|tool|site|code","confidence":0.0}'
)
_RESULT_TYPE_TIMEOUT = 12.0
_RESULT_TYPE_ATTEMPTS = 2
_RESULT_TYPE_MIN_CONFIDENCE = 0.6


async def classify_result_type(
    prompt: str, language: str = "ru"
) -> tuple[str | None, float]:
    """Classify the first prompt's RESULT TYPE → (type, confidence) or (None, 0.0)
    on any failure / unknown type. Never raises (R-10). One fast structured call
    inside the POST /prompt budget; a couple of retries shake off a transient
    non-strict reply (like plan_discovery_questions)."""
    settings = get_settings()
    url = f"{settings.llm_gateway_url.rstrip('/')}/v1/chat/completions"
    convo = [
        {"role": "system", "content": _RESULT_TYPE_SYSTEM + _reply_language_line(language)},
        {"role": "user", "content": (prompt or "").strip()[:2000] or "(пусто)"},
    ]
    payload = {
        "model": model_for_role("result_type"),
        "messages": convo,
        "max_tokens": 60,
        "stream": False,
    }
    for attempt in range(_RESULT_TYPE_ATTEMPTS):
        try:
            async with httpx.AsyncClient(timeout=_RESULT_TYPE_TIMEOUT) as client:
                resp = await client.post(url, json=payload)
            if resp.status_code < 400:
                body = resp.json()
                content = (body.get("choices") or [{}])[0].get("message", {}).get(
                    "content", ""
                )
                parsed = _parse(content)
                if parsed:
                    rt = str(parsed.get("type") or "").strip().lower()
                    if rt in RESULT_TYPES:
                        try:
                            conf = float(parsed.get("confidence") or 0.0)
                        except (TypeError, ValueError):
                            conf = 0.0
                        return rt, max(0.0, min(1.0, conf))
            else:
                log.warning(
                    "result_type: gateway %d (attempt %d)", resp.status_code, attempt + 1
                )
        except Exception as exc:
            log.warning("result_type: gateway error (attempt %d): %r", attempt + 1, exc)
    return None, 0.0


def resolve_result_type(
    prompt: str, llm_type: str | None, llm_conf: float
) -> str | None:
    """Combine the LLM verdict with the deterministic net. The keyword net WINS
    when it fires (code/explicit-static/account/conversion are high-precision and
    override even a confident LLM — the LLM can drift «запись»→web_app, the exact
    BS-7 mistake). Else trust a CONFIDENT LLM; else None (→ clarify / default)."""
    det = infer_result_type_from_text(prompt)
    if det is not None:
        return det
    if llm_type and llm_conf >= _RESULT_TYPE_MIN_CONFIDENCE:
        return llm_type
    return None


def serve_planned_question(
    plan: object, asked_count: int
) -> DiscoveryResult | None:
    """Serve the pre-computed question at cursor ``asked_count`` as an ASK turn —
    NO gateway call (instant). Returns None when the plan is exhausted (the caller
    then builds from the gathered answers) or malformed. The multi-select floor
    re-fires on serve so a persisted question stays correct even if it was stored
    before the inference rule existed."""
    if not isinstance(plan, list) or asked_count >= len(plan):
        return None
    q = PlannedQuestion.from_dict(plan[asked_count])
    if q is None:
        return None
    choices = q.choices or _fallback_choices(asked_count)
    return DiscoveryResult(
        action=ASK,
        message=q.message,
        brief="",
        stack=_DEFAULT_STACK,
        choices=choices,
        allow_custom=q.allow_custom,
        multi_select=q.multi_select or _infer_multi_select(q.message),
        question_index=asked_count + 1,
        question_total=len(plan),
    )


async def run_discovery(
    history: list[dict[str, str]],
    latest_prompt: str,
    *,
    asked_count: int,
    force_build: bool = False,
    language: str = "ru",
) -> DiscoveryResult:
    """Decide the next discovery turn: ask one more question, or build.

    ``history`` is the prior conversation (``[{"role","content"}]``), ``latest_prompt``
    the user's newest message (not yet in ``history``). ``asked_count`` is how many
    questions the assistant has already asked (drives the hard cap). ``force_build``
    short-circuits to BUILD (explicit user request).

    ``language`` is the project's detected language (BCP-47-ish, e.g. ``"en"``).
    RU is the default and leaves the system prompt unchanged (zero diff from the
    pre-i18n baseline).

    Never raises — degrades to a sensible question, or a from-history build at the
    cap / on force, so onboarding can never dead-end.
    """
    capped = asked_count >= MAX_DISCOVERY_QUESTIONS
    must_build = force_build or capped

    # Code/script request → NO design interview (owner 2026-06-19: «без дизайна
    # только скрипт» — palette/audience/sections questions are off-target for a
    # program). On the FIRST turn build straight away with the user's prompt as the
    # brief; the code writer prompt + the user's words are enough. Skips the gateway
    # entirely (instant). Gated to asked_count == 0 so a code-flavoured answer mid-
    # interview can't hijack an in-flight site build.
    if asked_count == 0 and _infer_code_from_text(latest_prompt):
        log.info("discovery: code/script intent — skipping interview, build now")
        return DiscoveryResult(
            action=BUILD,
            message="Понял — пишу код. Это займёт минуту.",
            brief=_fallback_brief(history, latest_prompt),
            stack="code",
        )

    # Zero-question intent compile (V2.12). On the FIRST turn, if the raw prompt
    # already pins enough design axes, build straight away — the popup never
    # appears and we skip the gateway round-trip entirely (shared floor, see
    # ``zero_question_build``). Gated to ``asked_count == 0`` so it never cuts an
    # interview already in flight, and to non-forced/non-capped so the
    # explicit/forced paths keep their own brief-compilation.
    if not must_build and asked_count == 0:
        zq = zero_question_build(history, latest_prompt)
        if zq is not None:
            return zq

    settings = get_settings()
    url = f"{settings.llm_gateway_url.rstrip('/')}/v1/chat/completions"
    system_content = _SYSTEM + _reply_language_line(language)
    convo: list[dict[str, str]] = [{"role": "system", "content": system_content}]
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
        intent_text = "\n".join(
            [brief, *(m.get("content") or "" for m in history), latest_prompt or ""]
        )
        # Explicit "plain static HTML page" wins OUTRIGHT (owner 2026-06-18 escape
        # hatch): force static and let NO net below upgrade it. Without this a brief
        # that merely says "без скриптов" trips the code net ("скрипт" substring),
        # and a model spa/other pick would override the user's explicit static ask.
        _static_req = _explicit_static(intent_text)
        if _static_req and stack != "static":
            log.info("discovery: stack '%s'→'static' (explicit static page)", stack)
            stack = "static"
        # Code net (owner 2026-06-18) — a standalone program/script ask routes to
        # the `code` template. Runs BEFORE the backend escalation so "напиши парсер
        # на python" lands as code, not an auth-backed web app. Only overrides a
        # non-backend pick (static/spa); a confident entities/fullstack choice
        # carries explicit data/account intent, so it's left alone.
        code_inferred = _infer_code_from_text(intent_text)
        if code_inferred and not _static_req and stack in ("static", "spa", "code"):
            if stack != "code":
                log.info("discovery: stack '%s'→'code' (program/script intent)", stack)
            stack = "code"
        inferred = _infer_stack_from_text(intent_text)
        # Landing override (RT-1 / BS-7, DARK): a conversion landing («запись/
        # бронь/заявка») WITHOUT an account ask must stay a public lead-form spa,
        # not escalate to an auth-gated entities app behind /signin. Gated by both
        # router flags; getattr-defensive so an older Settings degrades to legacy.
        _s = get_settings()
        _low_intent = intent_text.lower()
        _landing_override = (
            getattr(_s, "use_result_type_router", False)
            and getattr(_s, "result_type_landing_lead_sink", False)
            and _has_conversion_intent(intent_text)
            and not _has_account_intent(intent_text)
            and not any(f in _low_intent for f in _APPIFY_FRAMING)
        )
        if (
            stack not in ("fullstack", "nextjs_entities", "code")
            and inferred
            and not _static_req
            and not _landing_override
        ):
            log.info("discovery: stack '%s'→'%s' (backend intent signals)", stack, inferred)
            stack = inferred
        elif _landing_override:
            log.info("discovery: landing override — public lead form, stay spa (BS-7)")
            if stack == "static" and not _static_req:
                stack = "spa"
        # Negative safety-net (Phase 7.x): the mirror of the upgrade above. The
        # model OVER-escalated a tool the user explicitly said needs no accounts
        # ("без регистрации") to an auth-backed stack — which would gate the tool
        # behind /signin. No positive backend signal survived the negation check
        # (``inferred is None``), so downgrade to spa: a no-backend interactive
        # React tool. Mutually exclusive with the upgrade; only ever fires on an
        # explicit refusal, so a genuine app ("магазин без регистрации" — commerce
        # signals keep ``inferred`` truthy) is untouched.
        elif (
            stack in ("nextjs_entities", "fullstack")
            and inferred is None
            and _explicit_no_backend(intent_text)
        ):
            log.info("discovery: stack '%s'→'spa' (explicit no-account tool)", stack)
            stack = "spa"
        # Static is opt-in (owner 2026-06-18: «убрать статику, оставляем только
        # если задача явно требует»). Anything that ended up `static` but isn't an
        # EXPLICIT request for a plain HTML page becomes `spa` (interactive React) —
        # landings/portfolios/blogs included. Runs LAST so code/backend/no-backend
        # picks above are untouched; only a bare static fallthrough is upgraded.
        if stack == "static" and not _static_req:
            log.info("discovery: stack 'static'→'spa' (static is opt-in)")
            stack = "spa"
        return DiscoveryResult(action=BUILD, message=message, brief=brief, stack=stack)

    # ASK path — one more question (+ quick-reply chips, always present).
    message = ""
    choices: tuple[str, ...] = ()
    model_multi = False
    if parsed and action == ASK:
        message = str(parsed.get("message") or "").strip()
        choices = _clean_choices(parsed.get("choices"))
        model_multi = bool(parsed.get("multiSelect"))
    if not message:
        # Gateway/parse failed → deterministic question for this turn index.
        message = _fallback_question(asked_count)
    if not choices:
        # Guarantee the discovery card lands with tappable chips, never bare text
        # (V2.1 — чипы СРАЗУ на первый промпт). The model often omits choices for
        # an "open" question; the stage-keyed deterministic floor fills the gap,
        # model-independent. "Другое" (allow_custom) stays open so it never traps.
        choices = _fallback_choices(asked_count)
    # Multi-select when the model flagged it OR the question text reads as an
    # inherently multi-answer one (which sections/features) — the deterministic
    # floor catches a model that omitted the flag, and fires for the fallback
    # "разделы/возможности" question too (NORTH STAR pillar 2 — мультивыбор).
    multi_select = model_multi or _infer_multi_select(message)
    return DiscoveryResult(
        action=ASK,
        message=message,
        brief="",
        stack=stack,
        choices=choices,
        multi_select=multi_select,
    )


__all__ = [
    "ASK",
    "BUILD",
    "MAX_DISCOVERY_QUESTIONS",
    "RESULT_TYPES",
    "DiscoveryResult",
    "PlannedQuestion",
    "classify_result_type",
    "confident_enough_to_build",
    "cumulative_idea",
    "gather_answers",
    "infer_niche_label",
    "infer_result_type_from_text",
    "plan_discovery_questions",
    "recap_labels",
    "resolve_result_type",
    "result_type_to_stack",
    "run_discovery",
    "serve_planned_question",
    "wants_build_now",
    "zero_question_build",
    "_has_account_intent",
    "_has_conversion_intent",
]

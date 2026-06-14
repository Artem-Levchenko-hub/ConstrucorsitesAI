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
from omnia_api.services.chip_pixel_gate import (
    compile_build_spec,
    spec_confidence,
    spec_from_discovery,
)

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
    stack = _infer_stack_from_text(intent_text) or _DEFAULT_STACK
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
    'детали>","stack":"static|spa|nextjs_entities|fullstack"}'
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


async def plan_discovery_questions(prompt: str) -> list[PlannedQuestion]:
    """ONE upfront gateway pass → the WHOLE batch of 3–4 product-tailored
    questions. Never raises: any gateway/parse failure degrades to the
    deterministic general→detail batch (:func:`_plan_fallback`) so onboarding
    always lands a sensible set of questions, never a single generic one.

    The single call gets a generous budget (one pass replaces N per-question
    round-trips), but stays under the client's ``POST /prompt`` timeout so a cold
    gateway degrades to the batch fallback within the window (R-10 fail fast)."""
    settings = get_settings()
    url = f"{settings.llm_gateway_url.rstrip('/')}/v1/chat/completions"
    convo = [
        {"role": "system", "content": _PLAN_SYSTEM},
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
    parsed: dict[str, object] | None = None
    try:
        async with httpx.AsyncClient(timeout=22.0) as client:
            resp = await client.post(url, json=payload)
        if resp.status_code < 400:
            body = resp.json()
            content = (body.get("choices") or [{}])[0].get("message", {}).get("content", "")
            parsed = _parse(content)
        else:
            log.warning("discovery plan: gateway %d — using batch fallback", resp.status_code)
    except Exception as exc:
        log.warning("discovery plan: gateway error (batch fallback): %r", exc)
    questions = _questions_from_parsed(parsed)
    if not questions:
        questions = _plan_fallback()
    return questions


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
    "DiscoveryResult",
    "PlannedQuestion",
    "confident_enough_to_build",
    "cumulative_idea",
    "gather_answers",
    "infer_niche_label",
    "plan_discovery_questions",
    "recap_labels",
    "run_discovery",
    "serve_planned_question",
    "wants_build_now",
    "zero_question_build",
]

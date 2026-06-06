"""Intent triage — decide per prompt whether to run the full BUILD orchestration
or a cheap SURGICAL EDIT (Phase N, reworked 2026-06-06 per owner directive).

The user never picks a model; the server routes by TASK, not by model. There are
two regimes:

* **BUILD** (``ORCHESTRATE``) — make or replace a whole page. Earned by the FIRST
  prompt of a project, an explicit "rebuild from scratch / full redesign", or a
  genuinely structural addition (backend, auth, payments, catalog). Runs the
  expensive Art-Director → Writer pipeline that regenerates the page.

* **EDIT** (``CHEAP``) — change ONE thing IN an existing page: a selected element,
  a recolour, a text swap, "добавь интро". Routed to a single cheap model that
  emits a surgical ``<edit>`` patch and is forbidden (by a lean edit-only prompt
  AND by skipped guards) from touching the rest of the page.

EDIT is the DEFAULT for follow-ups. Once the site exists, almost every prompt is
an edit; the old heuristic treated build-noun words ("сайт", "раздел", "интро")
as real work and ran a full rebuild for "добавь интро к сайту" — which both cost
a premium pipeline AND re-rolled the palette, so "всё терялось". The fix: on an
existing project, only an EXPLICIT rebuild or a structural addition leaves the
edit path.

Deterministic by design: a signal/keyword heuristic, no LLM on the hot path — no
extra latency, cost, or failure mode. The narrow public surface (``decide_intent``)
hides the rule set so callers stay trivial (R-01); an LLM tie-breaker can be
slotted in later without touching any caller.
"""

from __future__ import annotations

ORCHESTRATE = "orchestrate"  # BUILD — regenerate the whole page
CHEAP = "cheap"              # EDIT — surgical patch, preserve everything else

# Explicit "throw the page away and rebuild" intent — forces BUILD even on a
# project that already has a page. Kept deliberately TIGHT: a bare "переделай"
# is NOT here, because "переделай кнопку" / "переделай заголовок" is an edit, not
# a rebuild. We only match phrases that unambiguously mean the WHOLE page.
# Russian stems matched as substrings, so падежи are covered.
_REBUILD_KEYWORDS: frozenset[str] = frozenset(
    {
        "с нуля", "заново", "пересоздай", "пересобери",
        "переделай сайт", "переделай страниц", "переделай весь",
        "переделай всё", "переделай все", "переделай лендинг",
        "редизайн", "redesign", "rebuild", "from scratch",
        "другой сайт", "новый дизайн", "смени дизайн", "сменить дизайн",
        "поменяй дизайн", "перестрой", "полностью переделай",
        "полностью обнови", "совершенно друг",
    }
)

# Genuinely structural / full-stack work — a follow-up that needs the whole
# orchestrated build because it changes the project's ARCHITECTURE, not one
# block. Kept DELIBERATELY MINIMAL: the owner's complaint is over-orchestration,
# so we bias hard toward EDIT. Section-ish asks ("добавь корзину/каталог/форму
# оплаты/раздел отзывов") are NOT here — a cheap surgical insert handles them and
# inherits the page's design. Only unambiguous "build me a backend/full app"
# signals stay. Stems chosen to be distinctive (bare "api"/"бд" excluded — they
# hit inside ordinary words like "капитал"⊃"апи", "обдумай"⊃"бд"; "оплат"/
# "авториз"/"логин" excluded — they fire on a simple "кнопка оплатить"/"форма
# входа" edit).
_STRUCTURAL_KEYWORDS: frozenset[str] = frozenset(
    {
        "бэкенд", "backend", "fullstack", "full-stack", "фуллстек",
        "серверн",  # серверная часть/логика — distinct from "сервис"
        "база данных", "базу данных", "базы данных", "базой данных",
        "многостраничн", "много страниц",
    }
)


def _has_any(text: str, keywords: frozenset[str]) -> bool:
    return any(k in text for k in keywords)


def decide_intent(
    prompt: str,
    *,
    is_first_prompt: bool,
    selected_count: int = 0,
) -> str:
    """Return ``ORCHESTRATE`` (BUILD) or ``CHEAP`` (surgical EDIT).

    First match wins:
    1. first prompt in the project   → ORCHESTRATE (the initial build)
    2. explicit rebuild / redesign   → ORCHESTRATE (replace the whole page)
    3. structural / full-stack add   → ORCHESTRATE (changes architecture)
    4. otherwise (existing project)  → CHEAP (the edit default — surgical patch)

    ``selected_count`` wins over everything: a pointed element proves a built
    page already exists on screen, so it's always a scoped edit — never a
    (re)build. This MUST be checked before ``is_first_prompt`` because a rollback
    to the project's starter snapshot makes the next prompt look like a first
    build, and a click on that page must still edit, not regenerate from scratch.
    """
    if selected_count >= 1:
        return CHEAP

    if is_first_prompt:
        return ORCHESTRATE

    text = (prompt or "").strip().lower()
    if _has_any(text, _REBUILD_KEYWORDS):
        return ORCHESTRATE
    if _has_any(text, _STRUCTURAL_KEYWORDS):
        return ORCHESTRATE

    # Existing project, no rebuild/structural signal → it's an edit. Cheap,
    # surgical, preserves the rest. This is the key change: build-noun words
    # ("сайт", "раздел", "интро", "секция") no longer drag a follow-up into a
    # full rebuild that re-rolls the palette.
    return CHEAP


__all__ = ["CHEAP", "ORCHESTRATE", "decide_intent"]

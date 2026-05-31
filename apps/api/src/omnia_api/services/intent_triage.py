"""Intent triage — decide per prompt whether to run the full orchestration or
a single cheap model (Phase N).

The user never picks a model; the server routes by TASK, not by model. Real
work — the first build, structural / backend / redesign changes, or a batch of
edits at once — earns the expensive Director(Opus)→Polish→Audit pipeline. A
trivial touch-up (recolour a button, swap a word, one selected element) gets a
single cheap reliable model, so Opus never burns budget colouring a login
button and the client pays pennies for the long tail of small edits.

Deterministic by design: a signal/keyword heuristic, no LLM call on the hot
path — no extra latency, cost, or failure mode to make "железно" harder. The
narrow public surface (`decide_intent`) hides the rule set so callers stay
trivial (R-01); an LLM tie-breaker can be slotted in later without touching any
caller.
"""

from __future__ import annotations

ORCHESTRATE = "orchestrate"
CHEAP = "cheap"

# The prompt is real work → full orchestration. Russian + English stems matched
# as substrings, so падежи / plurals are covered ("раздел" ⊂ "раздела").
_COMPLEX_KEYWORDS: frozenset[str] = frozenset(
    {
        # structure / scope
        "лендинг", "landing", "сайт", "страниц", "раздел", "секци", "section",
        "блок", "hero", "экран", "многостранич", "структур",
        # backend / data / auth
        "бэкенд", "backend", "сервер", "api", "база", "database", "бд",
        "авториз", "регистрац", "логин", "login", "auth", "аккаунт",
        "интеграц", "оплат", "платёж", "платеж", "payment", "корзин",
        "каталог", "форм", "заявк", "crm", "дашборд", "dashboard",
        # big rework
        "редизайн", "redesign", "переделай", "пересоздай", "с нуля", "заново",
        "перестрой", "переработай",
        # build / creation intent — "make me X" is real work even when the
        # prompt is short ("Создай дизайн ресторана" is 57 chars and was wrongly
        # caught by the very-short -> CHEAP rule). Owner: builds must orchestrate.
        "созда", "дизайн", "design", "разработ", "построй", "свёрст",
        "сверст", "сгенерир", "генерир", "нарисуй", "придумай", "запили",
        "make", "build", "create", "generate",
    }
)

# The prompt is a trivial touch-up → single cheap model.
_TRIVIAL_KEYWORDS: frozenset[str] = frozenset(
    {
        "цвет", "покрас", "перекрас", "colou", "color",
        "текст", "надпис", "слово", "опечат", "typo", "заголов",
        "шрифт", "font", "размер", "size", "отступ", "padding", "margin",
        "подвинь", "сдвинь", "вырав", "align", "поменяй", "замен",
        "иконк", "icon", "кнопк", "button", "ссылк",
    }
)

# Tunables — keep the thresholds named so the rule reads like prose.
_MANY_EDITS_SELECTED = 3   # this many picked elements = a batch → orchestrate
_LONG_PROMPT = 200         # chars; a long detailed brief = real work → orchestrate
_TARGETED_EDIT_MAX = 140   # chars; short prompt + a picked element = tweak → cheap
_SHORT_PROMPT = 60         # chars; a very short follow-up = trivial tweak → cheap


def _has_any(text: str, keywords: frozenset[str]) -> bool:
    return any(k in text for k in keywords)


def decide_intent(
    prompt: str,
    *,
    is_first_prompt: bool,
    selected_count: int = 0,
) -> str:
    """Return ``ORCHESTRATE`` or ``CHEAP`` for a single prompt.

    First match wins:
    1. first prompt in the project   → ORCHESTRATE (the initial build)
    2. ``≥ _MANY_EDITS_SELECTED`` picks → ORCHESTRATE (a batch of changes)
    3. long detailed prompt           → ORCHESTRATE
    4. complexity keyword             → ORCHESTRATE (structure / backend / rework)
    5. trivial keyword                → CHEAP (recolour / retext / resize …)
    6. short prompt on a single pick  → CHEAP (targeted tweak)
    7. very short prompt              → CHEAP
    8. otherwise                      → ORCHESTRATE (favour quality when unsure)
    """
    if is_first_prompt:
        return ORCHESTRATE
    if selected_count >= _MANY_EDITS_SELECTED:
        return ORCHESTRATE

    text = (prompt or "").strip().lower()
    if len(text) > _LONG_PROMPT:
        return ORCHESTRATE
    if _has_any(text, _COMPLEX_KEYWORDS):
        return ORCHESTRATE
    if _has_any(text, _TRIVIAL_KEYWORDS):
        return CHEAP
    if selected_count >= 1 and len(text) <= _TARGETED_EDIT_MAX:
        return CHEAP
    if len(text) < _SHORT_PROMPT:
        return CHEAP
    return ORCHESTRATE


__all__ = ["decide_intent", "ORCHESTRATE", "CHEAP"]

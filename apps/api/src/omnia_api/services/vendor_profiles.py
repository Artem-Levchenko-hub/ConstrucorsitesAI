"""Per-vendor instruction layer (Phase N+).

The pipeline already adapts the prompt by **tier** (premium/balanced/budget —
see ``core.config.tier_for_model``): tier decides *how much* brief a model
gets. This module adds the orthogonal **vendor** axis: a thin directive block
calibrated per model *family* (Claude / OpenAI / Google / Yandex / Sber /
DeepSeek) that nudges each family's known quirks — not a second full copy of
every prompt.

    tier   = how much capability / how long a brief the model can hold
    vendor = the dialect that family responds to best

Why a directive block, not N full prompt copies
================================================

Calibrating by vendor *family* (the owner's call) keeps maintenance sane —
one short block per family instead of N hand-tuned 14K prompts. The real,
already-documented wins are formatting-shaped:

* **claude**   — thinks well in ``<thinking>``, holds a long system prompt;
  ask it to reason privately and emit only the final structure.
* **openai**   — gpt-5/nano burn the output budget on hidden reasoning and
  return empty (see ``config.py`` ROLE_MODEL_MAP note); push for minimal
  reasoning and "JSON only, no prose".
* **google**   — Gemini wraps output in ```` ```json ```` fences and breaks
  strict ``PageIR`` validation (the root cause of the catalog IR-retry);
  demand raw JSON with NO markdown fences and no commentary.
* **yandex / sber** — weaker at holding a strict JSON contract; Russian-first,
  simplified schema, explicit "exactly one JSON object" framing.
* **deepseek** — capable but chatty; insist on verbatim JSON, no reasoning
  out loud.
* **generic**  — empty string: behaviour is exactly as before, no regression.

Prompt-cache safety
====================

The vendor block depends only on the model id, and the model is constant
within a single pass, so the **system** prompt stays byte-identical between
e.g. Director and Polish — Anthropic's ephemeral cache is not broken. Callers
MUST append the directive to the *user* turn (next to the existing role
instruction), exactly as the tier brief is already assembled.

The module is a leaf — no imports from routers / models / DB / gateway. Inputs
are strings, outputs are strings. Public surface: ``vendor_for_model`` and
``vendor_directive``.
"""

from __future__ import annotations

# Canonical vendor-family ids. Stable strings — callers and tests compare
# against these, so treat them as a small enum.
CLAUDE = "claude"
OPENAI = "openai"
GOOGLE = "google"
YANDEX = "yandex"
SBER = "sber"
DEEPSEEK = "deepseek"
GENERIC = "generic"


def vendor_for_model(model_id: str | None) -> str:
    """Map a gateway model id to its vendor family.

    Prefix-based so a new model id in an existing family (``gpt-5-nano`` →
    ``gpt-5-ultra``) is covered without a code change. ``None`` and any
    unrecognised id fall back to :data:`GENERIC`, which yields an empty
    directive — i.e. "behave exactly as before".
    """
    if not model_id:
        return GENERIC
    mid = model_id.strip().lower()
    if mid.startswith("claude"):
        return CLAUDE
    # OpenAI ships chat (gpt-*) and reasoning (o3/o4) families on the same key.
    if mid.startswith("gpt") or mid.startswith("o3") or mid.startswith("o4"):
        return OPENAI
    if mid.startswith("gemini"):
        return GOOGLE
    if mid.startswith("yandexgpt") or mid.startswith("yandex"):
        return YANDEX
    if mid.startswith("gigachat"):
        return SBER
    if mid.startswith("deepseek"):
        return DEEPSEEK
    return GENERIC


# Base directive per family — appended to a role instruction regardless of
# whether the pass wants JSON or freeform HTML. Calibrated to each family's
# documented failure mode (see module docstring). GENERIC is intentionally
# absent → empty string.
VENDOR_DIRECTIVES: dict[str, str] = {
    CLAUDE: (
        "[Профиль модели: Claude] Рассуждай кратко про себя в <thinking>…</thinking>, "
        "а финальный ответ выдавай строго в требуемой структуре сразу после. Можешь "
        "опираться на весь системный промпт — ты держишь длинный контекст."
    ),
    OPENAI: (
        "[Профиль модели: OpenAI/GPT] Минимум скрытых рассуждений (reasoning_effort "
        "низкий). НЕ размышляй вслух и не пересказывай задачу — это сжигает бюджет "
        "вывода и приводит к пустому ответу. Сразу выдавай финальный результат."
    ),
    GOOGLE: (
        "[Профиль модели: Google/Gemini] Выдавай ТОЛЬКО запрошенный контент без "
        "пояснений и без вводных фраз. Не оборачивай ответ в markdown."
    ),
    YANDEX: (
        "[Профиль модели: YandexGPT] Отвечай по-русски, держись простой и явной "
        "структуры. Один цельный ответ в требуемом формате, без отступлений."
    ),
    SBER: (
        "[Профиль модели: GigaChat] Отвечай по-русски, держись простой и явной "
        "структуры. Один цельный ответ в требуемом формате, без отступлений."
    ),
    DEEPSEEK: (
        "[Профиль модели: DeepSeek] Не рассуждай вслух. Выдавай результат дословно "
        "в требуемом формате, без вступлений и комментариев."
    ),
}


# JSON-strict add-on — appended ONLY for passes that must emit clean JSON
# (director / polish / skeleton / content / visual). Assembly / freeform HTML
# passes pass ``json_strict=False`` and never see these. The Google line is the
# load-bearing one (no fences → fixes PageIR IR-retry); the test suite asserts
# its presence in gemini-built messages.
_JSON_STRICT_DIRECTIVES: dict[str, str] = {
    CLAUDE: (
        "Верни РОВНО ОДИН валидный JSON-объект, без markdown-обёрток и без текста "
        "до или после него."
    ),
    OPENAI: (
        "Верни ТОЛЬКО валидный JSON и ничего больше — без прозы, без префиксов, без "
        "markdown-обёрток."
    ),
    GOOGLE: (
        "КРИТИЧНО: верни строго валидный JSON БЕЗ markdown-обёрток (никаких ```json "
        "и ```), без комментариев и без любого текста до/после объекта. Только сам "
        "JSON-объект."
    ),
    YANDEX: (
        "Верни строго один валидный JSON-объект, без markdown-обёрток и без "
        "пояснений. Соблюдай схему буквально."
    ),
    SBER: (
        "Верни строго один валидный JSON-объект, без markdown-обёрток и без "
        "пояснений. Соблюдай схему буквально."
    ),
    DEEPSEEK: (
        "Верни ТОЛЬКО валидный JSON-объект, без markdown-обёрток, без рассуждений и "
        "без текста вокруг."
    ),
}


def vendor_directive(model_id: str | None, *, json_strict: bool = False) -> str:
    """Build the vendor directive block for ``model_id``.

    Returns the family's base directive, plus a JSON-strict add-on when
    ``json_strict=True`` (passes that must emit clean JSON: director, polish,
    skeleton, content, visual). Freeform/HTML passes (assembly, non-catalog
    single-shot) pass ``json_strict=False``.

    Returns an empty string for the :data:`GENERIC` family (unknown / ``None``
    model id) so appending it is always a no-op there — zero regression on
    models we haven't calibrated.
    """
    vendor = vendor_for_model(model_id)
    base = VENDOR_DIRECTIVES.get(vendor, "")
    if not base:
        return ""
    if json_strict:
        extra = _JSON_STRICT_DIRECTIVES.get(vendor, "")
        if extra:
            return f"{base}\n{extra}"
    return base


__all__ = [
    "CLAUDE",
    "DEEPSEEK",
    "GENERIC",
    "GOOGLE",
    "OPENAI",
    "SBER",
    "YANDEX",
    "VENDOR_DIRECTIVES",
    "vendor_directive",
    "vendor_for_model",
]

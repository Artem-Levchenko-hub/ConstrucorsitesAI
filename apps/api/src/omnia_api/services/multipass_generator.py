"""Multi-pass design generation for budget models.

Cheap models (Claude Haiku 4.5, GPT-5 Nano) lose focus on the ~15K
single-shot prompt — the lost-in-the-middle effect on long context.
This pipeline breaks generation into 4 narrow passes so the model only
holds one concern per call:

    user_prompt
      ├─ pass_skeleton (~3K prompt)              → SectionPlan (JSON)
      ├─ pass_content  (skeleton + ~4K prompt)    ┐ parallel after
      │                                            │ skeleton —
      │                                            │ asyncio.gather
      ├─ pass_visual   (skeleton + ~5K prompt)    ┘
      └─ pass_assembly (all above + ~3K prompt)  → final HTML (<file>)

Why 4 instead of 2:
  Pass 1 (skeleton)   — structural decisions: section types, order, headlines
  Pass 2 (content)    — body copy with real numbers / names / prices
  Pass 3 (visual)     — per-section style tokens (motion classes, atmosphere)
  Pass 4 (assembly)   — pure stitching into HTML, no creative decisions

Each pass has a single concern → cheap model can hold it. Passes 2+3
are independent (both consume skeleton) so they run in parallel via
``asyncio.gather`` — net latency = pass1 + max(pass2, pass3) + pass4 ≈
3× pass time instead of 4×.

Each pass uses ``stream_chat_completion`` so it goes through the gateway's
existing routing, billing, fallback chain, and Anthropic prompt caching.
The system prompt is identical across all 4 passes → Anthropic's
ephemeral cache hits on the system block from pass 2 onward (saves
~70% of input tokens after pass 1).

The orchestrator yields the same ``{"delta": str}`` / ``{"usage": dict}``
events as ``stream_chat_completion`` so callers in ``routers/messages.py``
don't need to know which path is active.

Pass-progress events (``pass.started`` / ``pass.completed``) are emitted
via the same downstream channel — the router fans them out as WS events
for the progress UI. Phase B.3 in the plan.

Gating: ``Settings.multipass_models_set`` (env ``MULTIPASS_MODELS``) is
empty by default. Nothing is routed through this path unless the operator
opts a model in explicitly. Disable in seconds if anything misbehaves
in prod.
"""

from __future__ import annotations

import asyncio
import json
import re
from collections.abc import AsyncIterator
from typing import Any, TypedDict
from uuid import UUID

from omnia_api.core.config import model_for_role
from omnia_api.services.llm_client import stream_chat_completion


class SectionSpec(TypedDict):
    """One section in the skeleton — structural intent only."""

    id: str  # url-safe slug, used as section <id> in HTML
    type: str  # "hero" | "features" | "pricing" | "faq" | "cta" | "footer" | …
    headline: str
    subheadline: str
    key_points: list[str]


class SectionPlan(TypedDict):
    """Output of the skeleton pass — list of sections in render order."""

    title: str
    sections: list[SectionSpec]


class SectionContent(TypedDict):
    """Body copy for one section, produced by the content pass.

    Keyed by section id from the skeleton. ``body_html`` is plain text or
    light inline markup the assembly pass will splice into the final HTML;
    ``cta`` is an optional explicit CTA spec (label + target anchor) for
    sections that need one.
    """

    id: str
    body_html: str
    cta_label: str
    cta_target: str  # "#contacts" / "tel:+7…" / etc — assembly validates


class ContentPlan(TypedDict):
    sections: list[SectionContent]


class SectionVisual(TypedDict):
    """Per-section visual tokens picked by the visual pass.

    Names refer to omnia-kit classes the assembly pass MUST emit on the
    section's root element. The model picks them; assembly applies them.
    """

    id: str
    background: str  # "bg-canvas" | "bg-mesh" | "bg-aurora" | "bg-grain" | …
    motion: str  # "reveal" | "scroll-fade-up" | "scroll-clip-reveal" | "kinetic-type"
    accent: str  # "card-soft" | "glass" | "gradient-border" | "depth-2" | …


class VisualDirectives(TypedDict):
    """Output of the visual pass — atmosphere + per-section style tokens."""

    atmosphere: str  # "calm" | "energetic" | "editorial" | "tech" | "luxury"
    cursor: str  # "none" | "cursor-blob" | "cursor-magnetic"
    grain: bool  # film grain overlay
    sections: list[SectionVisual]


# ─── Skeleton pass ────────────────────────────────────────────────────────

_SKELETON_INSTRUCTIONS = """\
Ты архитектор страницы Omnia.AI. Получаешь user_prompt и контекст проекта
(preset с палитрой и шрифтами, design brief). Твоя задача — спланировать
СТРУКТУРУ финального сайта, не верстать его.

Вернуть СТРОГО валидный JSON, БЕЗ markdown-обёртки, БЕЗ комментариев,
БЕЗ текста до/после. Формат:

{
  "title": "Название проекта в одну строку",
  "sections": [
    {
      "type": "hero | features | how_it_works | testimonials | pricing | faq | cta | footer | gallery | stats | about | contact",
      "headline": "конкретный H1/H2 с реальной выгодой",
      "subheadline": "подзаголовок с дополнительной ценностью",
      "key_points": ["3-5 пунктов или один абзац"]
    }
  ]
}

КРИТЕРИИ:
1. 7-9 секций для лендинга, 5-6 для портфолио, 10-12 для долгого продающего лендинга.
2. Sticky-header + footer обязательно. Sticky-header → type="header"; footer → type="footer".
3. Каждая секция — РЕАЛЬНЫЙ контент. Реальные цифры в ₽, имена, города,
   названия услуг — не «Заголовок 1 / Текст 1». Этот JSON станет основой
   HTML без редактирования между проходами.
4. Подбирай тип секций под ВЕРТИКАЛЬ. Аптеке — assortment+consultations+
   delivery+pricing+pharmacist_advice; SaaS — features+integrations+pricing+
   testimonials. Не штампуй один шаблон.
5. Tone copy = preset.copywriting_tone из дизайн-брифа выше.
6. Никаких заглушек («Lorem», «Coming soon», «Add your content»).

ТОЛЬКО JSON, ничего больше."""


# ─── Content pass ─────────────────────────────────────────────────────────

_CONTENT_INSTRUCTIONS = """\
Ты копирайтер Omnia.AI. Получаешь SKELETON (структура секций уже выбрана) и
user_prompt + дизайн-anchor с copywriting_tone сверху.

Твоя задача — наполнить КАЖДУЮ секцию реальным продающим контентом:
длинные абзацы (200-400 символов), конкретные цифры в ₽, имена людей,
города, названия услуг. НЕ изменяй структуру (sections, типы, id). НЕ
переписывай headlines (они уже выбраны). Дополняешь, не подменяешь.

Вернуть СТРОГО валидный JSON, БЕЗ markdown, БЕЗ комментариев:

{
  "sections": [
    {
      "id": "same-as-skeleton",
      "body_html": "Полный текст секции — 200-400 символов реального копирайта. Можно с <strong> и <em> внутри, но без блочной разметки.",
      "cta_label": "Текст CTA — короткий, императивный («Записаться», «Узнать цену»). Пустая строка если секция без CTA.",
      "cta_target": "#contacts ИЛИ tel:+7… ИЛИ mailto:… ИЛИ https://wa.me/… Пустая строка если cta_label пуст."
    }
  ]
}

ТРЕБОВАНИЯ:
1. Tone строго по copywriting_tone из дизайн-брифа.
2. Реальные числа: «приём 1 200 ₽» вместо «доступная цена», «доставка за 2 часа»
   вместо «быстрая доставка», «12 врачей-стоматологов» вместо «команда экспертов».
3. Региональность: упоминай Москву / Санкт-Петербург / конкретные районы где уместно.
4. cta_target ОБЯЗАН ссылаться на существующий anchor (#<id> из skeleton) или
   быть внешним протоколом (tel:/mailto:/https://wa.me/).
5. Никаких placeholder'ов, lorem ipsum, «расскажите о...».

ТОЛЬКО JSON, ничего больше."""


# ─── Visual pass ──────────────────────────────────────────────────────────

_VISUAL_INSTRUCTIONS = """\
Ты арт-директор Omnia.AI. Получаешь SKELETON (структуру) и дизайн-anchor
(палитру/шрифты/preset). Решаешь как ВИЗУАЛЬНО оформить каждую секцию,
используя только классы из omnia-kit.

Доступные ТОКЕНЫ (выбирай ровно один из каждой колонки на секцию):

background:    "bg-canvas" (нейтральный) | "bg-mesh" (рассеянная градиентная
               сетка) | "bg-aurora" (мягкие пятна) | "bg-grain" (зерно) |
               "bg-dark" (тёмный фон если preset того требует)
motion:        "reveal" (классический fade+slide-up) | "scroll-fade-up"
               (по scroll progress) | "scroll-clip-reveal" (clip-path mask) |
               "kinetic-type" (заголовок morph variable-font) | "none"
accent:        "card-soft" (мягкие карточки с тенью) | "glass" (стеклянный
               блок с blur) | "gradient-border" (градиентная рамка) |
               "depth-2" (тень-уровень) | "shine" (блик при hover) | "none"

И глобальные:
atmosphere:    "calm" | "energetic" | "editorial" | "tech" | "luxury"
cursor:        "none" | "cursor-blob" (мягкий blob следует за курсором —
               подходит wellness/lifestyle) | "cursor-magnetic" (кнопки
               притягивают курсор — подходит SaaS/tech)
grain:         true (плёночное зерно поверх hero) | false

Вернуть СТРОГО JSON:

{
  "atmosphere": "tech",
  "cursor": "cursor-magnetic",
  "grain": false,
  "sections": [
    {"id": "hero", "background": "bg-mesh", "motion": "kinetic-type", "accent": "gradient-border"},
    {"id": "features", "background": "bg-canvas", "motion": "scroll-fade-up", "accent": "card-soft"}
  ]
}

ПРАВИЛА:
1. Один атмосферный приём на сайт. НЕ комбинируй cursor-blob + cursor-magnetic.
2. Один motion на секцию. НЕ нагружай "kinetic-type" + "scroll-clip-reveal" одновременно.
3. Hero обычно получает самый яркий motion (kinetic-type / scroll-clip-reveal),
   остальные секции — calm (reveal / scroll-fade-up).
4. Backgrounds чередуй ритмично: bg-canvas / bg-mesh / bg-canvas / bg-aurora.
   НЕ ставь 3 подряд bg-mesh.
5. Footer всегда "background": "bg-canvas" или "bg-dark", "motion": "none".

ТОЛЬКО JSON."""


# ─── Assembly pass ────────────────────────────────────────────────────────

_ASSEMBLY_INSTRUCTIONS = """\
Ты верстальщик Omnia.AI. Получаешь 3 готовых артефакта:

1. SKELETON — структура (типы секций + headlines)
2. CONTENT — body_html + cta для каждой секции (id матчится со skeleton)
3. VISUAL — atmosphere, cursor, grain + per-section {background, motion, accent}
            (id матчится со skeleton)

Все три решения ПРИНЯТЫ — ты их ИСПОЛНЯЕШЬ, не пересматриваешь. Контент
из CONTENT — дословно (можно перенести в <h1>/<h2>/<p>/<ul> по смыслу,
но текст не переписывать). Классы из VISUAL — дословно на root каждой
секции.

ФОРМАТ ОТВЕТА — СТРОГО:
<file path="index.html">
<!DOCTYPE html>
<html lang="ru">
...полный HTML...
</html>
</file>

ТРЕБОВАНИЯ К HTML:
1. Tailwind CDN + Google Fonts из дизайн-брифа в <head>.
2. tailwind.config inline настраивает colors и fontFamily из палитры.
3. `<link rel="stylesheet" href="assets/omnia-kit.css">` +
   `<script src="assets/omnia-kit.js" defer></script>` в <head>
   (omnia-kit инжектится Omnia, ты его НЕ создаёшь и НЕ переписываешь).
4. <body data-cursor="{visual.cursor}"> если cursor != "none".
   Применяй .grain на hero если visual.grain == true.
5. <section id="{spec.id}" class="{visual.background} {visual.motion}
   {visual.accent} {custom layout classes}"> для каждой секции.
6. CTA внутри секции: <a href="{content.cta_target}" class="...магнит/кнопка...">{content.cta_label}</a>
   когда CTA непустой.
7. Семантика: <header><nav><main><section><footer>. Ровно один <h1>.
8. Mobile-first: grid-cols-1 sm:grid-cols-2 lg:grid-cols-3.
   overflow-x-hidden на body.
9. SVG-иконки инлайн (stroke 1.5-2, Heroicons/Lucide-style). НИ ОДНОГО emoji в UI.

ЗАПРЕЩЕНО:
- Цвета вне палитры из дизайн-anchor (особенно indigo/violet/purple).
- Шрифты вместо тех, что в анкоре.
- Подмена visual.{background,motion,accent} на "более подходящие" — арт-директор
  уже выбрал, не спорь.
- Перевёрстывание body_html в свой текст.
- Lorem ipsum / placeholder-текст.
- `<file path="...">` для чего-то кроме index.html в одном ответе.

ТОЛЬКО блок <file path="index.html">...</file>, ничего до и после."""


def _strip_json_fence(raw: str) -> str:
    """Strip markdown ```json fences if the model added them anyway."""
    s = raw.strip()
    if s.startswith("```"):
        # ```json\n{...}\n```  → drop first line and last fence
        s = re.sub(r"^```[a-zA-Z]*\n", "", s, count=1)
        s = re.sub(r"\n```\s*$", "", s, count=1)
    return s.strip()


async def _run_pass(
    *,
    messages: list[dict[str, str]],
    model: str,
    user_id: UUID,
    project_id: UUID,
    message_id: UUID,
    forward_chunks: bool,
) -> tuple[str, dict[str, Any] | None, str | None]:
    """Run one pass, return (accumulated_text, usage, error).

    forward_chunks=True yields each chunk back to the caller via the
    async-gen contract; False accumulates silently (used for intermediate
    passes whose output the user shouldn't see — only the final HTML
    streams to the preview).
    """
    accumulated: list[str] = []
    usage: dict[str, Any] | None = None
    error: str | None = None
    async for event in stream_chat_completion(
        messages=messages,
        model=model,
        user_id=str(user_id),
        project_id=str(project_id),
        message_id=str(message_id),
    ):
        if delta := event.get("delta"):
            accumulated.append(delta)
        if u := event.get("usage"):
            usage = u
        if err := event.get("error"):
            error = err
            break
    return "".join(accumulated), usage, error


def _build_skeleton_messages(
    base_messages: list[dict[str, str]],
    user_prompt: str,
) -> list[dict[str, str]]:
    """Skeleton pass uses the same system prompt as single-shot (so it gets
    palette + skill brief + AWWWARDS_PRINCIPLES) but adds an explicit
    instruction at the end of the user turn telling it to return JSON
    only. We keep the original system prompt to preserve prompt-caching
    cache hit on Anthropic — only the last user message differs across
    passes."""
    msgs = list(base_messages[:-1])  # everything except the original user turn
    msgs.append(
        {
            "role": "user",
            "content": f"{user_prompt}\n\n{_SKELETON_INSTRUCTIONS}",
        }
    )
    return msgs


def _build_content_messages(
    base_messages: list[dict[str, str]],
    user_prompt: str,
    skeleton_json: str,
) -> list[dict[str, str]]:
    """Content pass — base system prompt + skeleton JSON + content
    instructions. System prompt unchanged → Anthropic prompt cache hits."""
    msgs = list(base_messages[:-1])
    msgs.append(
        {
            "role": "user",
            "content": (
                f"{user_prompt}\n\n"
                f"SKELETON (структура зафиксирована):\n"
                f"```json\n{skeleton_json}\n```\n\n"
                f"{_CONTENT_INSTRUCTIONS}"
            ),
        }
    )
    return msgs


def _build_visual_messages(
    base_messages: list[dict[str, str]],
    user_prompt: str,
    skeleton_json: str,
) -> list[dict[str, str]]:
    """Visual pass — base system prompt + skeleton JSON + visual instructions."""
    msgs = list(base_messages[:-1])
    msgs.append(
        {
            "role": "user",
            "content": (
                f"{user_prompt}\n\n"
                f"SKELETON (структура зафиксирована):\n"
                f"```json\n{skeleton_json}\n```\n\n"
                f"{_VISUAL_INSTRUCTIONS}"
            ),
        }
    )
    return msgs


def _build_assembly_messages(
    base_messages: list[dict[str, str]],
    user_prompt: str,
    skeleton_json: str,
    content_json: str,
    visual_json: str,
) -> list[dict[str, str]]:
    """Assembly pass — base system prompt + all 3 intermediates + final HTML
    instruction. Cache-friendly: system prompt unchanged so Anthropic
    ephemeral cache hits the system block across all 4 passes."""
    msgs = list(base_messages[:-1])
    msgs.append(
        {
            "role": "user",
            "content": (
                f"{user_prompt}\n\n"
                f"SKELETON:\n```json\n{skeleton_json}\n```\n\n"
                f"CONTENT:\n```json\n{content_json}\n```\n\n"
                f"VISUAL:\n```json\n{visual_json}\n```\n\n"
                f"{_ASSEMBLY_INSTRUCTIONS}"
            ),
        }
    )
    return msgs


def _aggregate_usage(*usages: dict[str, Any] | None) -> dict[str, Any]:
    """Sum tokens / cost across pass usages. None entries treated as zeros."""
    return {
        "tokens_in": sum(int((u or {}).get("tokens_in", 0)) for u in usages),
        "tokens_out": sum(int((u or {}).get("tokens_out", 0)) for u in usages),
        "cost_rub": sum(float((u or {}).get("cost_rub", 0.0)) for u in usages),
        "passes": len(usages),
    }


async def multipass_generate(
    *,
    base_messages: list[dict[str, str]],
    user_prompt: str,
    model: str | None = None,
    user_id: UUID,
    project_id: UUID,
    message_id: UUID,
) -> AsyncIterator[dict[str, Any]]:
    """Run skeleton → (content || visual) → assembly pipeline.

    Yields events compatible with ``stream_chat_completion``:
      * ``{"delta": str}`` — chunk of the FINAL HTML (assembly pass)
      * ``{"usage": {...}}`` — aggregate usage across all 4 passes
      * ``{"error": str}`` — terminal failure
      * ``{"pass": "skeleton" | "content" | "visual" | "assembly",
            "stage": "start" | "end", ...}`` — progress markers

    Only the assembly pass streams chunks to the UI — passes 1-3 produce
    JSON intermediates the user shouldn't see in the preview pane.

    Passes 2 (content) + 3 (visual) run in parallel via ``asyncio.gather``
    since both consume skeleton and produce independent outputs. Net
    latency ≈ pass1 + max(pass2, pass3) + pass4 ≈ 3× single pass time
    instead of 4×.

    Per-pass models come from ``model_for_role`` (skeleton/visual → Haiku,
    content → DeepSeek). An explicit ``model`` (admin override) forces all
    four passes onto that one model.
    """
    skeleton_model = model or model_for_role("skeleton")
    content_model = model or model_for_role("content")
    visual_model = model or model_for_role("visual")
    # Multipass assembly stitches the 3 intermediates into freeform HTML — a
    # mechanical, structured job → cheap content-tier model is plenty.
    assembly_model = model or model_for_role("content")

    # ─── Pass 1: SKELETON ────────────────────────────────────────────
    yield {"pass": "skeleton", "stage": "start"}
    skeleton_msgs = _build_skeleton_messages(base_messages, user_prompt)
    skeleton_raw, sk_usage, sk_err = await _run_pass(
        messages=skeleton_msgs,
        model=skeleton_model,
        user_id=user_id,
        project_id=project_id,
        message_id=message_id,
        forward_chunks=False,
    )
    if sk_err:
        yield {"error": f"skeleton pass failed: {sk_err}"}
        return

    skeleton_clean = _strip_json_fence(skeleton_raw)
    skeleton_valid = True
    try:
        json.loads(skeleton_clean)
    except json.JSONDecodeError as exc:
        # Don't fail — downstream passes can still produce plausible
        # output even from broken skeleton (they have user_prompt + brief).
        skeleton_valid = False
        yield {
            "pass": "skeleton",
            "stage": "end",
            "valid_json": False,
            "error": f"non-JSON skeleton (continuing): {exc}",
        }
    if skeleton_valid:
        yield {"pass": "skeleton", "stage": "end", "valid_json": True}

    # ─── Passes 2 + 3: CONTENT + VISUAL (parallel) ──────────────────
    # Both consume skeleton output; neither depends on the other.
    yield {"pass": "content", "stage": "start"}
    yield {"pass": "visual", "stage": "start"}

    content_msgs = _build_content_messages(base_messages, user_prompt, skeleton_clean)
    visual_msgs = _build_visual_messages(base_messages, user_prompt, skeleton_clean)

    content_task = _run_pass(
        messages=content_msgs,
        model=content_model,
        user_id=user_id,
        project_id=project_id,
        message_id=message_id,
        forward_chunks=False,
    )
    visual_task = _run_pass(
        messages=visual_msgs,
        model=visual_model,
        user_id=user_id,
        project_id=project_id,
        message_id=message_id,
        forward_chunks=False,
    )
    (content_raw, co_usage, co_err), (visual_raw, vi_usage, vi_err) = await asyncio.gather(
        content_task, visual_task
    )

    # Soft-fail: if either side errored, log via event but continue —
    # assembly will see an empty/partial intermediate and degrade gracefully.
    content_clean = _strip_json_fence(content_raw)
    try:
        json.loads(content_clean)
        yield {"pass": "content", "stage": "end", "valid_json": True}
    except json.JSONDecodeError as exc:
        yield {
            "pass": "content",
            "stage": "end",
            "valid_json": False,
            "error": f"non-JSON content (continuing): {exc}",
        }
    if co_err:
        yield {"pass": "content", "stage": "end", "error": co_err}

    visual_clean = _strip_json_fence(visual_raw)
    try:
        json.loads(visual_clean)
        yield {"pass": "visual", "stage": "end", "valid_json": True}
    except json.JSONDecodeError as exc:
        yield {
            "pass": "visual",
            "stage": "end",
            "valid_json": False,
            "error": f"non-JSON visual (continuing): {exc}",
        }
    if vi_err:
        yield {"pass": "visual", "stage": "end", "error": vi_err}

    # ─── Pass 4: ASSEMBLY (streams to UI) ───────────────────────────
    yield {"pass": "assembly", "stage": "start"}
    assembly_msgs = _build_assembly_messages(
        base_messages, user_prompt, skeleton_clean, content_clean, visual_clean
    )
    asm_usage: dict[str, Any] | None = None
    asm_err: str | None = None
    async for event in stream_chat_completion(
        messages=assembly_msgs,
        model=assembly_model,
        user_id=str(user_id),
        project_id=str(project_id),
        message_id=str(message_id),
    ):
        if "delta" in event:
            yield {"delta": event["delta"]}
        elif "usage" in event:
            asm_usage = event["usage"]
        elif "error" in event:
            asm_err = event["error"]
            break

    if asm_err:
        yield {"error": f"assembly pass failed: {asm_err}"}
        return

    yield {"pass": "assembly", "stage": "end"}

    # ─── Aggregate usage across all 4 passes ────────────────────────
    if any((sk_usage, co_usage, vi_usage, asm_usage)):
        yield {"usage": _aggregate_usage(sk_usage, co_usage, vi_usage, asm_usage)}


__all__ = [
    "ContentPlan",
    "SectionContent",
    "SectionPlan",
    "SectionSpec",
    "SectionVisual",
    "VisualDirectives",
    "multipass_generate",
]

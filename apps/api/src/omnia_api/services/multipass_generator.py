"""Multi-pass design generation for budget models.

Cheap models (Claude Haiku 4.5, GPT-5 Nano) lose focus on the ~15K
single-shot prompt the static-template path builds — the lost-in-the-
middle effect on long context. This pipeline breaks generation into
narrower passes so the model only holds one concern per call.

V1 ships with 2 passes (skeleton + assembly). V2 will split assembly
into content + visual + assembly. Even 2-pass beats single-shot
empirically because the skeleton call forces architectural decisions
BEFORE the model has spent its attention budget on HTML mechanics.

    user_prompt
      ├─ pass_skeleton(prompt, preset, skill_brief)  → SectionPlan (JSON)
      └─ pass_assembly(skeleton, prompt, preset)     → final HTML (<file>)

Each pass uses ``stream_chat_completion`` so it goes through the gateway's
existing routing, billing, fallback chain, and Anthropic prompt caching.
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

import json
import re
from collections.abc import AsyncIterator
from typing import Any, TypedDict
from uuid import UUID

from omnia_api.services.llm_client import stream_chat_completion


class SectionSpec(TypedDict):
    """One section in the skeleton — type + headline + key points."""

    type: str  # "hero" | "features" | "pricing" | "faq" | "cta" | "footer" | etc
    headline: str
    subheadline: str
    key_points: list[str]


class SectionPlan(TypedDict):
    """Output of the skeleton pass — list of sections in render order."""

    title: str
    sections: list[SectionSpec]


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


# ─── Assembly pass ────────────────────────────────────────────────────────

_ASSEMBLY_INSTRUCTIONS = """\
Ты верстальщик Omnia.AI. Получаешь:
- SKELETON (JSON со структурой и контентом) — сверху в этом промпте
- Дизайн-anchor с палитрой и шрифтами — выше в системном промпте
- Дизайн-брифу с UX-правилами

Твоя задача — собрать ГОТОВЫЙ index.html, который дословно реализует
SKELETON. Структуру не выдумываешь — она уже задана. Контент не
переписываешь — берёшь из skeleton.sections как есть.

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
3. `<link rel="stylesheet" href="assets/omnia-kit.css">` + `<script src="assets/omnia-kit.js" defer></script>` в <head> (omnia-kit инжектится Omnia, ты его НЕ создаёшь и НЕ переписываешь).
4. Семантика: <header><nav><main><section><footer>. Один <h1> в hero.
5. Mobile-first: grid-cols-1 sm:grid-cols-2 lg:grid-cols-3. overflow-x-hidden на body.
6. Кнопки и ссылки ведут на реальные якоря — никаких href="#". Каждой секции
   осмысленный id чтобы навигация работала.
7. Используй классы из omnia-kit (`.reveal`, `.card-soft`, `.magnetic`,
   `.eyebrow`, `.glass`, `.bg-mesh`, `.orb` etc) для движения и атмосферы.
   НЕ пиши свои keyframes — всё в omnia-kit.
8. SVG-иконки инлайн (stroke 1.5-2, Heroicons/Lucide-style). НИ ОДНОГО emoji в UI.

ЗАПРЕЩЕНО:
- Любые цвета вне палитры из дизайн-anchor (особенно indigo/violet/purple).
- Любые шрифты вместо тех, что в анкоре.
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


def _build_assembly_messages(
    base_messages: list[dict[str, str]],
    user_prompt: str,
    skeleton_json: str,
) -> list[dict[str, str]]:
    """Assembly pass message list — base system prompt + skeleton + final
    instruction. Cache-friendly: system prompt unchanged from skeleton
    pass so Anthropic ephemeral cache hits the system block."""
    msgs = list(base_messages[:-1])
    msgs.append(
        {
            "role": "user",
            "content": (
                f"{user_prompt}\n\n"
                f"SKELETON (план секций — реализуй ДОСЛОВНО):\n"
                f"```json\n{skeleton_json}\n```\n\n"
                f"{_ASSEMBLY_INSTRUCTIONS}"
            ),
        }
    )
    return msgs


async def multipass_generate(
    *,
    base_messages: list[dict[str, str]],
    user_prompt: str,
    model: str,
    user_id: UUID,
    project_id: UUID,
    message_id: UUID,
) -> AsyncIterator[dict[str, Any]]:
    """Run skeleton → assembly pipeline.

    Yields events compatible with ``stream_chat_completion``:
      * ``{"delta": str}`` — chunk of the FINAL HTML (assembly pass)
      * ``{"usage": {...}}`` — aggregate usage across both passes
      * ``{"error": str}`` — terminal failure
      * ``{"pass": "skeleton" | "assembly", "stage": "start"|"end", ...}``
        — progress markers; routers can republish as ``llm.pass`` events

    Skeleton chunks are NOT forwarded — the user shouldn't see JSON in
    the preview pane. Only the assembly pass streams chunks to the UI.
    """
    # ─── Pass 1: SKELETON ────────────────────────────────────────────
    yield {"pass": "skeleton", "stage": "start"}
    skeleton_msgs = _build_skeleton_messages(base_messages, user_prompt)
    skeleton_raw, sk_usage, sk_err = await _run_pass(
        messages=skeleton_msgs,
        model=model,
        user_id=user_id,
        project_id=project_id,
        message_id=message_id,
        forward_chunks=False,
    )
    if sk_err:
        yield {"error": f"skeleton pass failed: {sk_err}"}
        return

    skeleton_clean = _strip_json_fence(skeleton_raw)
    try:
        # Validate — assembly pass shouldn't crash on malformed skeleton.
        json.loads(skeleton_clean)
    except json.JSONDecodeError as exc:
        # Don't fail the whole pipeline — fall through to assembly with
        # whatever the model emitted. Assembly pass can still produce a
        # plausible site even from a broken skeleton (it has the original
        # user_prompt + design brief).
        yield {
            "pass": "skeleton",
            "stage": "end",
            "valid_json": False,
            "error": f"non-JSON skeleton (kept anyway): {exc}",
        }
    else:
        yield {"pass": "skeleton", "stage": "end", "valid_json": True}

    # ─── Pass 2: ASSEMBLY (streams to UI) ───────────────────────────
    yield {"pass": "assembly", "stage": "start"}
    assembly_msgs = _build_assembly_messages(
        base_messages, user_prompt, skeleton_clean
    )
    asm_usage: dict[str, Any] | None = None
    asm_err: str | None = None
    async for event in stream_chat_completion(
        messages=assembly_msgs,
        model=model,
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

    # ─── Aggregate usage across both passes ────────────────────────
    if asm_usage or sk_usage:
        total: dict[str, Any] = {
            "tokens_in": int((sk_usage or {}).get("tokens_in", 0))
            + int((asm_usage or {}).get("tokens_in", 0)),
            "tokens_out": int((sk_usage or {}).get("tokens_out", 0))
            + int((asm_usage or {}).get("tokens_out", 0)),
            "cost_rub": float((sk_usage or {}).get("cost_rub", 0.0))
            + float((asm_usage or {}).get("cost_rub", 0.0)),
            "passes": 2,
        }
        yield {"usage": total}


__all__ = [
    "SectionPlan",
    "SectionSpec",
    "multipass_generate",
]

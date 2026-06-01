"""Art-Director → Writer 2-pass generator for FREEFORM HTML (owner directive
2026-06-01).

The fixed build orchestration. Splits one generation across two models so the
design intelligence runs on a strong model while the bulk HTML tokens run on a
cheap one:

* **Art-Director** (role ``art_director`` → Opus) — the *вдохновитель*. Sees the
  full freeform system prompt (palette anchor, kit, taste codex) + the user
  brief and emits an ULTRA-DETAILED design brief: the РАЗБОР (feeling → idea →
  reference → system) plus a per-section spec — exact palette HEX, fonts, motion
  signature, layout, real Russian copy, which kit classes/effects, and the exact
  ``data-omnia-gen`` image prompts. It writes NO code, so its expensive tokens
  stay few.

* **Writer** (role ``freeform_writer`` → DeepSeek) — executes the brief
  literally into the full HTML and streams it to the caller. The brief carries
  every design decision, so the cheap model only has to realise it, not invent
  it. This is the bulk-token pass — hence the cheap model.

Net latency ≈ Art-Director (short output) + Writer (full page). Token spend is
dominated by the Writer's cheap output; the Opus pass is a small brief.

The async-generator event contract matches
``services.llm_client.stream_chat_completion`` so the caller treats it
identically:
* ``{"delta": str}`` — chunks of the FINAL (Writer) HTML
* ``{"usage": dict}`` — summed tokens / cost across both passes
* ``{"error": str}`` — terminal failure
* ``{"pass": "art_director|writer", "stage": "start|end"}`` — progress

Fail-soft (R-10): if the Art-Director returns an empty brief, the Writer still
runs on the base freeform prompt alone — a page without the brief beats no page.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any
from uuid import UUID

from omnia_api.core.config import model_for_role
from omnia_api.services.llm_client import stream_chat_completion
from omnia_api.services.vendor_profiles import vendor_directive


# Pass 1 — the art-director writes a brief, never code. Appended to the last
# user turn so the shared system prompt (with the palette anchor + kit + taste
# codex) stays byte-identical across both passes → Anthropic prompt-cache hit.
_ART_DIRECTOR_INSTRUCTION = """\
Ты — АРТ-ДИРЕКТОР и вдохновитель (проход 1 из 2). Ты НЕ пишешь код. Твоя работа —
выдать УЛЬТРАПОДРОБНЫЙ дизайн-бриф, по которому второй исполнитель (верстальщик)
соберёт страницу, НЕ принимая ни одного дизайн-решения сам. Думай как сеньор-арт-
директор: сначала чувство, потом система, потом точная посекционная спека.

Палитру и шрифты бери ТОЧНО из обязательного блока системного промпта выше (те же
HEX). Запрещённые training-дефолты (indigo/violet) — не предлагай.

Выдай бриф СТРОГО в этом формате (плотно, по делу, без вступлений и без воды):

1. РАЗБОР
   • ЧУВСТВО — одна фраза: что человек ощущает за первую секунду.
   • ОДНА ИДЕЯ — единственная организующая мысль, которую он запомнит.
   • РЕФЕРЕНС-ВАЙБ — «как X встретил Y» (настроение-якорь, не копия чужого сайта).
2. СИСТЕМА
   • Палитра: доминанта <HEX>, акцент <HEX> (дозой!), нейтраль/фон/текст <HEX> — и роль каждого.
   • Шрифты: дисплейный <имя> + текстовый <имя> — и какой характер несут.
   • Движение: ОДНА сигнатура — какой kit-эффект и в какой секции (.omnia-shader / .line-rise / .omnia-spotlight / scramble), reduced-motion-safe.
   • Сетка и ритм: как ведём глаз, где асимметрия, чем цепляет первый экран.
3. СЕКЦИИ — по порядку, КАЖДАЯ как точная спека для верстальщика:
   • id и назначение секции.
   • Раскладка: колонки/выравнивание/асимметрия; для hero — что именно на первом экране.
   • Контент: РЕАЛЬНЫЙ русский копирайт — заголовок, подзаголовок, булиты, лейблы CTA. Живые цифры/факты в ₽. Никаких «ваш текст»/lorem.
   • Визуал: точные kit-классы и эффекты (.omnia-shader data-omnia-colors="#..,#..,#..,#..", .line-rise, .omnia-spotlight, .grain, .depth-2/3, градиенты тон-в-тон). Для картинок — ТОЧНЫЙ prompt для <img data-omnia-gen="...">.
   • Глубина: где тени с подтоном / градиент / фактура — чтобы не плоско.
4. КРАФТ-ФЛОР — чек анти-дёшево: цвет иерархией (ярко дозой, не фоном блоков), один primary-CTA на экран, объёмные кнопки, настоящие store-бейджи, без эмодзи-иконок.

Пиши плотно и конкретно: каждая строка — принятое решение. Только текст брифа,
без кода, без markdown-ограждений вокруг всего ответа."""


# Pass 2 — the writer realises the brief. ``{brief}`` is injected verbatim.
_WRITER_INSTRUCTION_TEMPLATE = """\
Ты — ВЕРСТАЛЬЩИК (проход 2 из 2). Арт-директор уже принял ВСЕ дизайн-решения —
они в брифе ниже. Твоя задача: собрать полную страницу, ИСПОЛНЯЯ бриф БУКВАЛЬНО.

ПРАВИЛА:
• Делай ровно то, что в брифе: те же HEX, те же шрифты, те же секции в том же
  порядке, те же kit-классы и эффекты, тот же русский копирайт и цифры.
• НЕ передизайнивай, НЕ переименовывай и НЕ выкидывай секции, НЕ меняй палитру,
  НЕ упрощай. Бриф — закон; твоя свобода только в чистой реализации.
• Картинки — ровно те <img data-omnia-gen="..."> с prompt'ами из брифа.
• Соблюдай системный промпт выше: контракт «ноль тупиков» (живые ссылки/кнопки),
  kit-классы, и ВЫДАЙ ответ в том же формате, который требует системный промпт.

ДИЗАЙН-БРИФ АРТ-ДИРЕКТОРА (исполнять буквально):
<<<БРИФ
{brief}
БРИФ>>>"""


def _aggregate_usage(*usages: dict[str, Any] | None) -> dict[str, Any]:
    """Sum tokens / cost across pass usages. None entries treated as zeros."""
    return {
        "tokens_in": sum(int((u or {}).get("tokens_in", 0)) for u in usages),
        "tokens_out": sum(int((u or {}).get("tokens_out", 0)) for u in usages),
        "cost_rub": sum(float((u or {}).get("cost_rub", 0.0)) for u in usages),
        "passes": len([u for u in usages if u is not None]),
    }


def _build_art_director_messages(
    base_messages: list[dict[str, str]],
    user_prompt: str,
    model_id: str | None,
) -> list[dict[str, str]]:
    """Art-Director pass: shared system prompt, last user turn appends the
    brief directive. ``json_strict=False`` — the brief is prose, not JSON."""
    directive = vendor_directive(model_id, json_strict=False)
    suffix = f"\n\n{directive}" if directive else ""
    msgs = list(base_messages[:-1])
    msgs.append({
        "role": "user",
        "content": f"{user_prompt}\n\n{_ART_DIRECTOR_INSTRUCTION}{suffix}",
    })
    return msgs


def _build_writer_messages(
    base_messages: list[dict[str, str]],
    user_prompt: str,
    brief: str,
    model_id: str | None,
) -> list[dict[str, str]]:
    """Writer pass: shared system prompt + the brief injected into the last
    user turn. ``json_strict=False`` — freeform HTML, never a JSON nudge. An
    empty ``brief`` degrades to the base freeform prompt (R-10 fail-soft)."""
    directive = vendor_directive(model_id, json_strict=False)
    suffix = f"\n\n{directive}" if directive else ""
    msgs = list(base_messages[:-1])
    if brief:
        tail = _WRITER_INSTRUCTION_TEMPLATE.format(brief=brief)
        content = f"{user_prompt}\n\n{tail}{suffix}"
    else:
        content = f"{user_prompt}{suffix}"
    msgs.append({"role": "user", "content": content})
    return msgs


async def art_director_writer_generate(
    *,
    base_messages: list[dict[str, str]],
    user_prompt: str,
    art_director_model: str | None = None,
    writer_model: str | None = None,
    user_id: UUID,
    project_id: UUID,
    message_id: UUID,
) -> AsyncIterator[dict[str, Any]]:
    """Run Art-Director (Opus, brief) → Writer (DeepSeek, HTML).

    Pass 1 streams silently and accumulates the design brief. Pass 2 streams its
    chunks to the caller as the FINAL HTML, executing the brief. Per-pass models
    default from ``model_for_role`` but can be forced (admin override). The
    yielded events match ``stream_chat_completion`` so the caller is agnostic to
    the 2-pass split.
    """
    art_director_model = art_director_model or model_for_role("art_director")
    writer_model = writer_model or model_for_role("freeform_writer")

    # ─── Pass 1: Art-Director (silent — accumulate the brief) ────────────
    yield {"pass": "art_director", "stage": "start", "model": art_director_model}
    ad_msgs = _build_art_director_messages(base_messages, user_prompt, art_director_model)
    brief_parts: list[str] = []
    ad_usage: dict[str, Any] | None = None
    async for event in stream_chat_completion(
        ad_msgs,
        art_director_model,
        str(user_id),
        str(project_id),
        str(message_id),
    ):
        if delta := event.get("delta"):
            brief_parts.append(delta)
        if u := event.get("usage"):
            ad_usage = u
        if err := event.get("error"):
            # The brief failed — let the Writer carry the page alone rather than
            # losing the whole build (R-10 fail-soft). Don't propagate the error.
            brief_parts = []
            break

    brief = "".join(brief_parts).strip()
    yield {"pass": "art_director", "stage": "end", "chars": len(brief)}

    # ─── Pass 2: Writer (streams the HTML to the caller) ─────────────────
    yield {"pass": "writer", "stage": "start", "model": writer_model}
    writer_msgs = _build_writer_messages(base_messages, user_prompt, brief, writer_model)
    writer_usage: dict[str, Any] | None = None
    async for event in stream_chat_completion(
        writer_msgs,
        writer_model,
        str(user_id),
        str(project_id),
        str(message_id),
    ):
        if delta := event.get("delta"):
            yield {"delta": delta}
        if u := event.get("usage"):
            writer_usage = u
        if err := event.get("error"):
            yield {"error": f"writer pass failed: {err}"}
            return

    yield {"pass": "writer", "stage": "end"}
    yield {"usage": _aggregate_usage(ad_usage, writer_usage)}


__all__ = ["art_director_writer_generate"]

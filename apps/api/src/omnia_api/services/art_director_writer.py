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
Ты — АРТ-ДИРЕКТОР и автор СПЕЦИФИКАЦИИ (проход 1 из 2). Ты НЕ пишешь код. Ты пишешь
НАСТОЛЬКО подробный бриф, что верстальщик (проход 2) физически не сможет сделать
дёшево или ошибиться: все решения уже приняты ТОБОЙ, ему остаётся ТОЛЬКО перенести
твой текст в HTML. ЖЕЛЕЗНОЕ ПРАВИЛО: чего нет в брифе — верстальщик выдумает и
испортит. Значит, не оставляй НИ ОДНОГО пробела для импровизации — ни цвета, ни
слова, ни класса, ни отступа.

Думай как сеньор-арт-директор: сначала чувство, потом система, потом посекционная
спека с ГОТОВЫМ дословным контентом и ТОЧНЫМИ классами. Палитру и шрифты бери ТОЧНО
из обязательного блока системного промпта выше (те же HEX, акцент — дозой).
Запрещённые training-дефолты indigo/violet — не предлагай.

Формат — СТРОГО так, плотно, без воды, без markdown-ограждений вокруг ответа:

# 1. РАЗБОР (3 строки)
ЧУВСТВО: <что ощущает человек за первую секунду>
ИДЕЯ: <единственная организующая мысль, которую он запомнит>
РЕФЕРЕНС: <«как X встретил Y» — вайб-якорь, не копия чужого сайта>

# 2. ГЛОБАЛ (точные токены — верстальщик ставит их 1:1)
ФОН <#HEX> · ТЕКСТ <#HEX> · PRIMARY <#HEX> · АКЦЕНТ <#HEX> (дозой: только CTA+цифры, НЕ заливка блоков)
ШРИФТЫ: дисплей "<точное имя>" · текст "<точное имя>"
MOTION-СИГНАТУРА: ОДНА — <kit-эффект и в какой секции: .omnia-shader data-omnia-colors="#..,#..,#..,#.." | .line-rise | .omnia-spotlight | scramble> (reduced-motion-safe)
РИТМ: отступы секций <py-24|py-32>, контейнер <max-w-6xl|7xl>, радиус <rounded-xl|2xl>, 8pt-сетка
ФАКТУРА/ГЛУБИНА: где .grain / .depth-2|3 / градиент тон-в-тон
ХЕДЕР (распознаваемый, задаёт тон — НЕ шаблон «лого слева + 4 ссылки по центру + кнопка»): выбери ХАРАКТЕРНЫЙ приём под вайб и опиши точно. Варианты: стеклянный sticky с blur (.glass-dark / backdrop-blur) + тонкая нижняя граница; крупный логотип-вордмарк или боковой логотип; mega-menu или pill-навигация (.rounded-full фон под пунктами); primary-CTA капсулой справа; хедер, ужимающийся при скролле. Укажи: фон, поведение при скролле, типографику пунктов, где CTA, есть ли тонкая граница/тень.

# 3. СЕКЦИИ — по порядку (7–9 для лендинга), КАЖДАЯ со ВСЕМИ полями:
[N] <назначение> | id="<anchor>"
ФОН/ОТСТУП/КОНТЕЙНЕР: <#HEX или kit-класс> · py-<N> · max-w-<N>
РАСКЛАДКА: <точно: число колонок, выравнивание, асимметрия; для hero — что на первом экране>
КОПИРАЙТ (ДОСЛОВНО, готовый — верстальщик копирует как есть, в кавычках):
  Eyebrow: "<...>" · Заголовок: "<...>" · Подзаголовок: "<...>"
  Карточки/булиты: "<каждый пункт ЦЕЛИКОМ>" (а не «три преимущества»)
  Кнопки: "<лейбл>" → href="<#якорь|tel:|mailto:>"
  Цифры/цены в ₽, имена людей, город, адрес, телефон — РЕАЛЬНЫЕ и правдоподобные
KIT-КЛАССЫ: <точные классы на ключевые элементы: .btn-cta-primary, .reveal|.line-rise, .depth-2|3, .bento, .glass-dark ...>
ФОН-ГРАФИКА (для hero и ещё ≥2 секций ОБЯЗАТЕЛЬНО): НАСТОЯЩИЙ тематический фон <img data-omnia-gen="<детальный EN-prompt: предмет, сцена, свет, настроение, ракурс, объектив; без текста и логотипов>"> — full-bleed ЗА контентом + затемняющий overlay (контраст текста ≥4.5:1). Это «прикольная графика в тему», НЕ плоский градиент. Класс на фото: .depth-3/.img-zoom. Плоский градиент/mesh — только тон-в-тон поверх фото или мелким акцентом, НИКОГДА как главный визуал секции.
АДАПТИВ: <что складывается в столбец на мобильном, что прячется, как мельчает заголовок>

# 4. КРАФТ-ФЛОР (явный запрет дешевизны — повтори для верстальщика)
— ЗАПРЕЩЕНО generic-AI: центрованный текст + три одинаковые карточки + одна кнопка + пустой/градиентный фон ВМЕСТО тематической графики.
— ВЁРСТКА РАЗНООБРАЗНА: соседние секции УЗНАВАЕМО разные по раскладке — чередуй full-bleed фото-band, асимметричный сплит (фото слева/справа от текста), overlap/слои (карточка наезжает на фото), bento-сетку, гигантский тип, цитату во весь экран. НИКОГДА не 3-4 одинаковые секции-карточки подряд.
— Тематическая фон-графика (data-omnia-gen, в тему ниши) в hero + ≥2 секциях — ОБЯЗАТЕЛЬНА. Плоский градиент как главный визуал = брак.
— ГРАФ-ДЕТАЛИ В КАЖДОЙ СЕКЦИИ: минимум один графический слой поверх плоского фона — .grain / .depth-2|3 (тень с подтоном) / градиент тон-в-тон / .line-grid|.dot-grid / .orb-блик / декоративный инлайн-SVG / разделитель-волна между секциями. Плоская секция без единой фактуры = брак.
— Акцент дозой (CTA/цифры/иконки), НЕ заливкой блоков. Цвет — иерархией.
— Один primary-CTA на экран. Кнопки объёмные (.btn-cta-primary). Иконки — SVG, НЕ эмодзи.
— Реальные контакты/бейджи/цифры. Ноль «ваш текст», «слоган здесь», lorem.

Пиши плотно: каждая строка — принятое решение с конкретикой и готовым текстом.
Только текст брифа, без кода."""


# Pass 2 — the writer realises the brief. ``{brief}`` is injected verbatim.
_WRITER_INSTRUCTION_TEMPLATE = """\
Ты — ВЕРСТАЛЬЩИК-ИСПОЛНИТЕЛЬ (проход 2 из 2). Арт-директор уже принял ВСЕ решения —
бриф ниже это готовая СПЕЦИФИКАЦИЯ. Ты НЕ дизайнер: ты ТРАНСКРИБИРУЕШЬ бриф в HTML.

ЖЕЛЕЗНЫЕ ПРАВИЛА:
• Каждый HEX, шрифт, класс, отступ, заголовок, подзаголовок, булит, лейбл кнопки,
  цену, имя, телефон и img-prompt бери ИЗ БРИФА ДОСЛОВНО — слово в слово. Ничего не
  выдумывай, не сокращай, не упрощай, не переименовывай, не выкидывай.
• Секции — в том же порядке, с теми же id, тем же текстом, теми же kit-классами и
  эффектами, что в брифе. Если поле есть в брифе — оно ОБЯЗАНО быть в HTML.
• Картинки — ровно те <img data-omnia-gen="..."> с prompt'ами из брифа, на своих
  местах, с указанными классами.
• Соблюдай системный промпт выше: контракт «ноль тупиков» (живые ссылки/кнопки),
  kit-классы, и выдай ответ РОВНО в том формате (<file ...>), который требует
  системный промпт.
• Твоя единственная свобода — чистая, валидная, адаптивная реализация. Дизайн уже
  сделан до тебя.

ДИЗАЙН-БРИФ (исполнять буквально, слово в слово):
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

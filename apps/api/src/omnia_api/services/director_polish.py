"""Director→Polish 2-pass generator for catalog mode (Phase L7).

Premium-tier fallback when single-pass + retry doesn't reach the
target quality bar (≥ 85/100 on golden eval). Splits the generation
into two passes the same model executes back-to-back:

* **Director** — sees the lean catalog system prompt + user prompt
  and emits a *structural* PageIR JSON: full section list, variant
  choices, palette, fonts, theme. Headlines/body are kept SHORT and
  placeholder-style — the model focuses on *which* variants to pick,
  not on *what to say*.

* **Polish** — sees the same system prompt + Director's IR JSON + a
  short "rewrite every text field with real content" instruction.
  Reuses Director's structure verbatim, but every `headline`,
  `subheadline`, `body`, `quote`, `feature.body`, `pricing.features`
  gets a real, concrete Russian rewrite (real numbers in ₽, real
  names, real cities, real services). NO structural changes allowed.

Each pass runs against its OWN model (role-orchestration era): Director uses
role ``director`` (Opus — hard structural reasoning), Polish uses role
``polish`` (Haiku — cheap, reliable proxyapi copy). Net latency ≈ 2×
single-shot. Token cost is dominated by the Director's input, so the Director
should see a lean prompt to keep that cheap.

Activated by ``Settings.use_director_polish=True`` AND catalog mode is on.
Per-pass models come from ``model_for_role`` (not the user).

The async-generator event contract matches
``services.llm_client.stream_chat_completion``:
* ``{"delta": str}`` — chunks of the FINAL (Polish) JSON
* ``{"usage": dict}`` — summed tokens / cost across both passes
* ``{"error": str}`` — terminal failure
* ``{"pass": "director|polish", "stage": "start|end"}`` — progress
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any
from uuid import UUID

from omnia_api.core.config import model_for_role
from omnia_api.services.llm_client import stream_chat_completion
from omnia_api.services.vendor_profiles import vendor_directive


# Director sees a single appended user-turn instruction to keep its
# output structural.
_DIRECTOR_INSTRUCTION = """\
Ты — Director: первый из двух проходов. Решаешь СТРУКТУРУ страницы, не
наполняешь её контентом.

Вернуть СТРОГО валидный PageIR JSON (как описано в <output_format>), но с
ПЛЕЙСХОЛДЕРНЫМ контентом:
* headlines = "<HEADLINE_<тип секции>>" — буквально такой плейсхолдер
* subheadlines / body / quote / feature.body = "<TEXT>"
* pricing.features = ["<FEATURE>"]
* cta.label оставь короткое осмысленное действие ("Записаться", "Купить")
* meta.title / meta.description можно оставить короткими и плейсхолдерными

ТЫ выбираешь:
* набор и порядок секций (variant_id из <catalog>)
* theme (palette HEX, fonts, neutral) — конкретные значения, не плейсхолдеры
* background / motion / id на каждой секции
* минимальные структурные значения: tiers.name, tiers.price, items length

Polish-проход (следующий) перепишет ВСЕ <HEADLINE_X> / <TEXT> / <FEATURE> в
реальный русский контент с цифрами в ₽. НЕ пытайся уже на этом шаге писать
длинные продающие заголовки — это работа Polish.

Возврат — РОВНО ОДИН JSON, без markdown-обёртки."""


_POLISH_INSTRUCTION_TEMPLATE = """\
Ты — Polish: второй проход. Director уже выбрал структуру (ниже). Твоя
задача — переписать ТОЛЬКО текстовые поля плейсхолдеров (<HEADLINE_X>,
<TEXT>, <FEATURE>) в РЕАЛЬНЫЙ русский контент enterprise-уровня.

ЗАПРЕЩЕНО:
* Менять список секций, их порядок, variant_id, theme, palette, fonts,
  background, motion, id. Это уже принято.
* Менять количество tiers / items / columns / links. Длина массивов
  зафиксирована.
* Lorem ipsum / "ваш текст" / "пример заголовка".

ОБЯЗАНО:
* Каждый headline / subheadline — конкретная выгода, не «Добро пожаловать».
* Реальные цифры: «приём 1 200 ₽», «доставка за 2 часа», «12 врачей».
* Реальные имена / города / районы где уместно.
* Tone matches preset.copywriting_tone из <preset>, если он выше есть.
* meta.title / meta.description — финальный SEO-уровень.

Director IR (структура зафиксирована):
```json
{director_ir}
```

Верни РОВНО ОДИН валидный PageIR JSON с заполненным контентом. Без
markdown, без префикса, без комментариев. Структурно — копия Director IR
с заменёнными текстовыми полями."""


def _build_director_messages(
    base_messages: list[dict[str, str]],
    user_prompt: str,
    model_id: str | None = None,
) -> list[dict[str, str]]:
    """Director pass: same system prompt as base catalog, last user turn
    appends the Director directive + the per-vendor block for ``model_id``
    (json_strict — Director emits structural PageIR JSON). System message
    stays identical → Anthropic prompt-cache hit on the second pass."""
    directive = vendor_directive(model_id, json_strict=True)
    suffix = f"\n\n{directive}" if directive else ""
    msgs = list(base_messages[:-1])
    msgs.append({
        "role": "user",
        "content": f"{user_prompt}\n\n{_DIRECTOR_INSTRUCTION}{suffix}",
    })
    return msgs


def _build_polish_messages(
    base_messages: list[dict[str, str]],
    user_prompt: str,
    director_ir: str,
    model_id: str | None = None,
    *,
    language: str = "ru",
) -> list[dict[str, str]]:
    """Polish pass: same system + Director's IR injected into the last
    user turn + the per-vendor block for ``model_id`` (json_strict — Polish
    also emits PageIR JSON). System is byte-identical to Director's →
    prompt-cache hits on Anthropic for the entire system block."""
    directive = vendor_directive(model_id, json_strict=True)
    suffix = f"\n\n{directive}" if directive else ""
    # Phase A3 — for non-RU projects, prepend a compact language reminder to
    # the polish turn so the instruction-level «реальный русский контент / ₽»
    # doesn't override the system-level language directive.
    from omnia_api.services.prompt_builder import _language_directive
    _lang_note = _language_directive(language)
    lang_prefix = f"{_lang_note}\n\n" if _lang_note else ""
    msgs = list(base_messages[:-1])
    msgs.append({
        "role": "user",
        "content": (
            f"{lang_prefix}{user_prompt}\n\n"
            f"{_POLISH_INSTRUCTION_TEMPLATE.format(director_ir=director_ir)}"
            f"{suffix}"
        ),
    })
    return msgs


def _aggregate_usage(*usages: dict[str, Any] | None) -> dict[str, Any]:
    """Sum tokens / cost across pass usages. None entries treated as zeros."""
    return {
        "tokens_in": sum(int((u or {}).get("tokens_in", 0)) for u in usages),
        "tokens_out": sum(int((u or {}).get("tokens_out", 0)) for u in usages),
        "cost_rub": sum(float((u or {}).get("cost_rub", 0.0)) for u in usages),
        "passes": len([u for u in usages if u is not None]),
    }


async def director_polish_generate(
    *,
    base_messages: list[dict[str, str]],
    user_prompt: str,
    director_model: str | None = None,
    polish_model: str | None = None,
    user_id: UUID,
    project_id: UUID,
    message_id: UUID,
    language: str = "ru",
) -> AsyncIterator[dict[str, Any]]:
    """Run Director → Polish with a different model per pass.

    Pass 1 (Director, role ``director`` → Opus) streams silently and decides
    the page structure. Pass 2 (Polish, role ``polish`` → DeepSeek) streams its
    chunks to the caller as the FINAL output and writes the real content. The
    per-pass models default from ``model_for_role`` but can be forced (admin
    override). Caller treats the yielded events identically to
    ``stream_chat_completion``.
    """
    director_model = director_model or model_for_role("director")
    polish_model = polish_model or model_for_role("polish")

    # ─── Pass 1: Director ────────────────────────────────────────────
    yield {"pass": "director", "stage": "start", "model": director_model}
    director_msgs = _build_director_messages(base_messages, user_prompt, director_model)
    director_parts: list[str] = []
    director_usage: dict[str, Any] | None = None
    async for event in stream_chat_completion(
        director_msgs,
        director_model,
        str(user_id),
        str(project_id),
        str(message_id),
    ):
        if delta := event.get("delta"):
            director_parts.append(delta)
        if u := event.get("usage"):
            director_usage = u
        if err := event.get("error"):
            yield {"error": f"director pass failed: {err}"}
            return

    director_acc = "".join(director_parts).strip()
    if not director_acc:
        yield {"error": "director pass returned empty output"}
        return

    yield {"pass": "director", "stage": "end", "chars": len(director_acc)}

    # ─── Pass 2: Polish (streams to user) ────────────────────────────
    yield {"pass": "polish", "stage": "start", "model": polish_model}
    polish_msgs = _build_polish_messages(base_messages, user_prompt, director_acc, polish_model, language=language)
    polish_usage: dict[str, Any] | None = None
    async for event in stream_chat_completion(
        polish_msgs,
        polish_model,
        str(user_id),
        str(project_id),
        str(message_id),
    ):
        if delta := event.get("delta"):
            yield {"delta": delta}
        if u := event.get("usage"):
            polish_usage = u
        if err := event.get("error"):
            yield {"error": f"polish pass failed: {err}"}
            return

    yield {"pass": "polish", "stage": "end"}
    yield {"usage": _aggregate_usage(director_usage, polish_usage)}


__all__ = ["director_polish_generate"]

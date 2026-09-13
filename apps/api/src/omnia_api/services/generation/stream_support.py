from __future__ import annotations

from typing import Any
from uuid import UUID

from omnia_api.core.config import model_for_role
from omnia_api.services import image_edit
from omnia_api.services.llm_client import stream_chat_completion

_AUDIT_JUDGE_LOW = 6


_AUDIT_JUDGE_HIGH = 7


_AUDIT_JUDGE_SYSTEM = (
    "Ты — придирчивый арт-директор Omnia.AI. Тебе дают объективный аудит "
    "лендинга (балл из 10 + список нарушений) и его HTML. Реши, годится ли "
    "страница в продакшен или нужна перегенерация. Ответь РОВНО одним словом: "
    "PASS или RETRY. Без пояснений."
)


async def _audit_judge_wants_retry(
    *,
    html: str,
    report: Any,
    model: str,
    user_id: UUID,
    project_id: UUID,
    message_id: UUID,
) -> bool | None:
    """LLM second opinion (role ``audit``, Sonnet) on a BORDERLINE rubric score.

    Returns True (re-roll), False (ship as-is), or None when the judge errored
    or gave no clear verdict — the caller then keeps the deterministic rubric
    decision. Best-effort: never raises, never streamed to the user.
    """
    failed = "; ".join(f.check_id for f in report.failures) or "—"
    judge_messages = [
        {"role": "system", "content": _AUDIT_JUDGE_SYSTEM},
        {
            "role": "user",
            "content": (
                f"Объективный аудит: {report.score}/{report.max}. "
                f"Нарушения: {failed}.\n\nHTML:\n{html[:6000]}\n\n"
                "Вердикт одним словом: PASS или RETRY."
            ),
        },
    ]
    parts: list[str] = []
    try:
        async for ev in stream_chat_completion(
            judge_messages,
            model,
            str(user_id),
            str(project_id),
            str(message_id),
        ):
            if "delta" in ev:
                parts.append(str(ev["delta"]))
            elif "error" in ev:
                return None
    except Exception:
        return None
    verdict = "".join(parts).strip().upper()
    if "RETRY" in verdict:
        return True
    if "PASS" in verdict:
        return False
    return None


async def _catalog_fallback_generate(
    *,
    history: list[dict[str, str]],
    prompt_text: str,
    selected_elements: list[dict[str, Any]] | None,
    preset_id: str | None,
    project_id: UUID,
    user_id: UUID,
    assistant_message_id: UUID,
    current_files: dict[str, str],
    discovery_spec: dict[str, object] | None = None,
) -> dict[str, str] | None:
    """Acceptance fallback — regenerate a page via the catalog/IR path.

    Freeform output that fails the acceptance gate after its retries falls
    here: the catalog path emits validated PageIR JSON that renders to a
    structurally guaranteed page (the director model holds the strict schema).
    Returns rendered files, or None on any failure — the caller then keeps the
    freeform attempt rather than shipping nothing (R-10 fail-soft).
    """
    import json as _json

    from pydantic import ValidationError as _VE

    from omnia_api.core.config import model_for_role as _mfr
    from omnia_api.sections import PageIR as _PageIR
    from omnia_api.sections import apply_smart_defaults as _asd
    from omnia_api.sections.renderer import render_to_files as _rtf
    from omnia_api.services.lean_prompt import build_catalog_messages as _bcm

    try:
        cat_messages = _bcm(
            history=history,
            user_prompt=prompt_text,
            selected_elements=selected_elements,
            preset_id=preset_id,
            project_id=str(project_id),
            # V2.5c — regeneration must honour the same chip-spec the gate judges
            # against, else reject→regen ignores chips and loops forever (V2.5d).
            discovery_spec=discovery_spec,
        )
        parts: list[str] = []
        async for ev in stream_chat_completion(
            cat_messages,
            _mfr("director"),
            str(user_id),
            str(project_id),
            str(assistant_message_id),
        ):
            if "delta" in ev:
                parts.append(str(ev["delta"]))
        raw = "".join(parts).strip()
        if raw.startswith("```"):
            raw = raw.split("\n", 1)[1] if "\n" in raw else raw[3:]
            if raw.endswith("```"):
                raw = raw[:-3]
            raw = raw.strip()
        ir = _PageIR.model_validate(_json.loads(raw))
        ir = _asd(ir, preset_id=preset_id)
        kit_css = current_files.get("src/assets/omnia-kit.css", "")
        kit_js = current_files.get("src/assets/omnia-kit.js", "")
        return dict(_rtf(ir, kit_css=kit_css, kit_js=kit_js))
    except (_json.JSONDecodeError, _VE, ValueError, KeyError) as exc:
        print(f"[PP] catalog_fallback_parse_failed err={exc!r}", flush=True)
        return None
    except Exception as exc:
        print(f"[PP] catalog_fallback_failed err={exc!r}", flush=True)
        return None


_IMG_PROMPT_SYSTEM = (
    "Ты пишешь КОРОТКИЙ детальный промпт НА АНГЛИЙСКОМ для модели генерации фото "
    "(flux) под премиум-бренд. По запросу пользователя и контексту бренда верни "
    "ОДНУ строку 20–40 слов: subject/scene, lighting, angle, lens, mood, palette. "
    "БЕЗ текста и логотипов в кадре, без кавычек, без префиксов, только сам промпт."
)


async def _craft_image_prompt(
    user_request: str,
    preset_id: str | None,
    old_img_tag: str,
    force_model: str | None,
    user_id: UUID,
    project_id: UUID,
    message_id: UUID,
) -> tuple[str, dict[str, Any] | None]:
    """Turn a (often vague, Russian) image request into a detailed EN flux prompt
    via the cheap ``image_prompt`` role — the only LLM on the direct-image path.
    Fail-soft: returns a sensible template prompt if the call errors/empties."""
    _alt = image_edit.alt_of(old_img_tag)
    ctx = (
        f"Бренд/пресет: {preset_id or 'премиум, тёмная элегантная эстетика'}. "
        f"Текущая картинка (alt): {_alt or '—'}. "
        f"Запрос пользователя: {user_request}"
    )
    parts: list[str] = []
    usage: dict[str, Any] | None = None
    try:
        async for ev in stream_chat_completion(
            [
                {"role": "system", "content": _IMG_PROMPT_SYSTEM},
                {"role": "user", "content": ctx},
            ],
            model_for_role("image_prompt", override=force_model),
            str(user_id),
            str(project_id),
            str(message_id),
        ):
            if "delta" in ev:
                parts.append(str(ev["delta"]))
            elif "usage" in ev:
                usage = ev["usage"]
    except Exception as exc:
        print(f"[PP] craft_image_prompt failed {exc!r}", flush=True)
    prompt = " ".join("".join(parts).split()).strip().strip('"')[:600]
    if len(prompt) < 12:
        prompt = (
            f"atmospheric premium brand photograph, {user_request}, moody elegant "
            "lighting, dark refined palette, shallow depth of field, 85mm"
        )
    return prompt, usage

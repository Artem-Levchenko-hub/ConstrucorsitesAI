from __future__ import annotations

import json
import re
from typing import Any
from uuid import UUID

from pydantic import ValidationError

from omnia_api.core.config import get_settings, model_for_role
from omnia_api.schemas.hero_media import (
    HeroMediaDecision,
    HeroMediaFocusPreference,
    HeroMediaMotionPreference,
)
from omnia_api.services.llm_client import LLMError, complete_chat

_JSON_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", re.IGNORECASE)

_PLANNER_SYSTEM = """Ты планировщик hero-media для Omnia.AI.

Твоя задача — НЕ делать видео по умолчанию. Сначала реши, какой тип hero-подачи
уместен для продукта и страницы: static / product-demo / motion / video /
cinematic.

Центральное правило:
- messenger, B2B SaaS, кабинет, utilitarian tool, dashboard -> почти всегда
  static или product-demo; иногда motion.
- physical product, travel, fashion, food, sports, event, real estate, brand
  -> video или cinematic уместны чаще, если есть сильные фотографии.
- services, portfolio, expert business -> часто лучше сильная фотография или
  результат услуги + тонкая motion-подача, без ролика.

Оцени:
1. тип продукта и цель страницы;
2. что пользователь хочет показать главным: product / interface / atmosphere / result;
3. насколько загруженные фото подходят для первого экрана;
4. риск замедления страницы и уместность видео для бренда.

Выбирай video/cinematic ТОЛЬКО если:
- это реально усиливает hero, и
- фото/сцены позволяют сделать сильный ролик, и
- пользователь явно просит живую/кинематографичную подачу ИЛИ уверенность высокая.

Верни СТРОГО JSON, без markdown и без пояснений вне JSON. Схема:
{
  "plan_kind": "static|product-demo|motion|video|cinematic",
  "confidence": 0.0,
  "explanation": "коротко и по-человечески, почему этот план лучший",
  "recommended_focus": "что лучше показать в hero",
  "recommended_tone": "спокойный|живой|кинематографичный|другой",
  "brand_fit_note": "1 предложение",
  "performance_note": "1 предложение",
  "accessibility_note": "1 предложение",
  "requires_confirmation": true,
  "hero_headline": "готовый headline",
  "hero_subheadline": "готовый subheadline",
  "primary_cta_label": "короткий CTA",
  "visual_style": "краткое описание визуальной подачи для сборщика",
  "storyboard": [
    {
      "label": "shot-1",
      "purpose": "что должна донести сцена",
      "primary_asset_index": 0,
      "secondary_asset_index": null,
      "use_source_as_poster": true,
      "still_prompt": "короткий EN prompt для poster/still если нужен",
      "motion_prompt": "EN prompt for motion/camera move if video is used",
      "first_frame_prompt": "EN prompt only if first frame must be generated",
      "last_frame_prompt": "EN prompt only if last frame must be generated"
    }
  ]
}

Если plan_kind не video/cinematic, storyboard может содержать один shot без
motion_prompt. Если video/cinematic не нужен, не пытайся насильно впихнуть
motion/video. Пиши лаконично и структурно."""

_PLANNER_REPAIR_SYSTEM = """Ты превращаешь ответ другой модели в СТРОГИЙ JSON
для hero-media planner.

На входе будет сырое текстовое описание рекомендации. Нужно вернуть только один
валидный JSON-объект по схеме planner'a. Никакого markdown, никаких пояснений
вне JSON. Если каких-то полей явно нет в тексте, восстанови их максимально
консервативно из контекста задачи, но не меняй главный выбор plan_kind без
основания."""


def _strip_json_fence(raw: str) -> str:
    return _JSON_FENCE_RE.sub("", raw.strip()).strip()


def _extract_json_object(raw: str) -> dict[str, Any]:
    """Best-effort JSON object extraction from an LLM response.

    Real upstreams sometimes wrap an otherwise-correct object in prose or fenced
    markdown. For planner reliability we accept the first top-level `{...}` blob
    that parses as JSON instead of assuming the whole payload is object-only.
    """
    cleaned = _strip_json_fence(raw)
    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError:
        start = cleaned.find("{")
        end = cleaned.rfind("}")
        if start == -1 or end == -1 or end <= start:
            raise
        data = json.loads(cleaned[start : end + 1])
    if not isinstance(data, dict):
        raise json.JSONDecodeError("planner output is not a JSON object", cleaned, 0)
    return data


async def _repair_planner_output(
    *,
    owner_id: UUID,
    project_id: UUID,
    raw: str,
    prompt: str,
    business_type: str | None,
    style_preference: str | None,
) -> dict[str, Any]:
    repair_messages = [
        {
            "role": "system",
            "content": f"{_PLANNER_SYSTEM}\n\n{_PLANNER_REPAIR_SYSTEM}",
        },
        {
            "role": "user",
            "content": (
                f"User brief: {prompt}\n"
                f"Business type: {business_type or 'not specified'}\n"
                f"Style preference: {style_preference or 'not specified'}\n\n"
                "Raw planner output:\n"
                f"{raw}"
            ),
        }
    ]
    repaired = await complete_chat(
        repair_messages,
        model_for_role("director"),
        user_id=str(owner_id),
        project_id=str(project_id),
        max_tokens=1400,
        temperature=0.0,
    )
    if not repaired.strip():
        raise LLMError("hero-media planner repair returned empty output")
    return _extract_json_object(repaired)


def _stub_plan(
    *,
    prompt: str,
    business_type: str | None,
    style_preference: str | None,
    focus_preference: HeroMediaFocusPreference,
    motion_preference: HeroMediaMotionPreference,
    asset_urls: list[str],
) -> HeroMediaDecision:
    joined = " ".join(
        filter(
            None,
            [prompt.lower(), (business_type or "").lower(), (style_preference or "").lower()],
        )
    )
    static_keywords = ("saas", "dashboard", "кабинет", "b2b", "messenger", "crm", "интерфейс")
    cinematic_keywords = (
        "fashion",
        "travel",
        "brand",
        "food",
        "еда",
        "отель",
        "event",
        "real estate",
    )
    if focus_preference == "interface" or any(
        keyword in joined for keyword in static_keywords
    ):
        plan_kind = "product-demo"
    elif motion_preference == "cinematic" or any(
        keyword in joined for keyword in cinematic_keywords
    ):
        plan_kind = "motion" if not asset_urls else "cinematic"
    elif focus_preference in {"product", "result"}:
        plan_kind = "motion" if asset_urls else "static"
    else:
        plan_kind = "motion" if asset_urls else "static"
    return HeroMediaDecision(
        plan_kind=plan_kind,  # type: ignore[arg-type]
        confidence=0.66,
        explanation=(
            "Локальный stub-planner рекомендует этот тип подачи по правилам продукта: "
            "видео не включается по умолчанию, а product/interface-first сценарии "
            "тяготеют к static/product-demo/motion."
        ),
        recommended_focus=(
            focus_preference if focus_preference != "auto" else "главный объект страницы"
        ),
        recommended_tone=motion_preference if motion_preference != "auto" else "живой",
        brand_fit_note=(
            "Это симулированный планировщик для локального E2E, "
            "не финальный creative judgment."
        ),
        performance_note=(
            "Stub-mode всегда предпочитает более безопасную по скорости "
            "подачу, чем video-by-default."
        ),
        accessibility_note="В preview и apply сохраняются still/mobile/reduced-motion fallback.",
        requires_confirmation=True,
        hero_headline="Hero, который показывает главное без перегруза эффектами.",
        hero_subheadline="Сначала правильный тип подачи, потом уже медиа и движение.",
        primary_cta_label="Открыть подробнее",
        visual_style=(style_preference or "clean editorial framing with premium spacing"),
        storyboard=[
            {
                "label": "hero-shot",
                "purpose": "показать главный объект страницы в первом экране",
                "primary_asset_index": 0 if asset_urls else None,
                "secondary_asset_index": 1 if len(asset_urls) > 1 else None,
                "use_source_as_poster": bool(asset_urls),
                "still_prompt": "premium hero still of the main product or scene",
                "motion_prompt": "subtle premium motion around the hero subject",
                "first_frame_prompt": None,
                "last_frame_prompt": None,
            }
        ],
    )


async def plan_hero_media(
    *,
    owner_id: UUID,
    project_id: UUID,
    prompt: str,
    business_type: str | None,
    style_preference: str | None,
    focus_preference: HeroMediaFocusPreference,
    motion_preference: HeroMediaMotionPreference,
    asset_urls: list[str],
) -> HeroMediaDecision:
    settings = get_settings()
    if settings.hero_media_stub_mode or settings.mock_llm:
        return _stub_plan(
            prompt=prompt,
            business_type=business_type,
            style_preference=style_preference,
            focus_preference=focus_preference,
            motion_preference=motion_preference,
            asset_urls=asset_urls,
        )
    allowed_plans = (
        "static, product-demo, motion, video, cinematic"
        if settings.use_video_gen
        else "static, product-demo, motion"
    )
    asset_note = (
        f"Uploaded assets: {len(asset_urls)} image(s). Assess their quality and hero usefulness."
        if asset_urls
        else "Uploaded assets: none."
    )
    planner_input = (
        f"User brief: {prompt}\n"
        f"Business type: {business_type or 'not specified'}\n"
        f"Style preference: {style_preference or 'not specified'}\n"
        f"What to show first: {focus_preference}\n"
        f"Desired character: {motion_preference}\n"
        f"Allowed plan kinds today: {allowed_plans}\n"
        f"{asset_note}\n"
        "Be strict about performance and product fit. If the page is product-led or interface-led,"
        " avoid cinematic video unless it clearly improves comprehension."
    )
    content: list[dict[str, Any]] = [{"type": "text", "text": planner_input}]
    for asset_url in asset_urls[: settings.hero_media_max_assets]:
        content.append({"type": "image_url", "image_url": {"url": asset_url}})

    messages: list[dict[str, Any]] = [
        {"role": "system", "content": _PLANNER_SYSTEM},
        {"role": "user", "content": content},
    ]
    raw = await complete_chat(
        messages,
        model_for_role("director"),
        user_id=str(owner_id),
        project_id=str(project_id),
        max_tokens=1600,
        temperature=0.1,
    )
    if not raw.strip():
        raise LLMError("hero-media planner returned empty output")
    try:
        data = _extract_json_object(raw)
        decision = HeroMediaDecision.model_validate(data)
    except (json.JSONDecodeError, ValidationError):
        data = await _repair_planner_output(
            owner_id=owner_id,
            project_id=project_id,
            raw=raw,
            prompt=prompt,
            business_type=business_type,
            style_preference=style_preference,
        )
        decision = HeroMediaDecision.model_validate(data)
    if not settings.use_video_gen and decision.plan_kind in {"video", "cinematic"}:
        decision = decision.model_copy(
            update={
                "plan_kind": "motion",
                "explanation": (
                    "Видео сейчас недоступно в этом окружении, поэтому план безопасно "
                    "понижен до motion без ложного обещания ролика."
                ),
                "requires_confirmation": True,
            }
        )
    return decision

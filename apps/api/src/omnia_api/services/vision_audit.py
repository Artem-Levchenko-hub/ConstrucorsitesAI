"""Vision audit for the acceptance gate (Phase 11, Sprint 2.1).

Renders a freeform page → screenshot → asks a vision model "broken / generic
/ beautiful?" against a fixed rubric, and returns a score + concrete issues
that feed the self-repair loop.

Best-effort by design (R-10 fail fast → fail SOFT): any gateway error, empty
answer, or unparseable JSON degrades to a "skipped" verdict that does NOT
block the page. Vision is a quality signal layered on top of the deterministic
structural/responsive checks — never the sole gate.
"""

from __future__ import annotations

import base64
import json
import logging
from dataclasses import dataclass

from omnia_api.core.config import get_settings, model_for_role
from omnia_api.services.llm_client import LLMError, complete_chat

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class VisionVerdict:
    """A vision model's read on a rendered page."""

    verdict: str  # "broken" | "generic" | "beautiful" | "skipped"
    score: int  # 0..10
    issues: tuple[str, ...]
    skipped: bool = False
    raw: str = ""


# Neutral pass — used whenever vision can't run (mock mode, no gateway, parse
# fail). Score 10 so a skipped vision never fails the gate on its own.
_SKIPPED = VisionVerdict(verdict="skipped", score=10, issues=(), skipped=True)

# Cap how many viewports we ship to the model — one wide + one narrow is enough
# to judge composition and mobile, and keeps the multimodal payload small.
_VISION_WIDTHS = (1440, 375)

_RUBRIC = """\
Ты — член жюри Awwwards. Тебе дают скриншот(ы) сгенерированного лендинга (десктоп +
мобайл). Суди СТРОГО, как на Awwwards: в «beautiful» пропускай ТОЛЬКО то, что не стыдно
показать в галерее награждённых. «Просто аккуратно / чисто / работает» — это НЕ
beautiful, это generic. Верни СТРОГО один JSON-объект, без markdown-обёртки.

Измерения (каждое тянет общий score 0–10):
1. КОНЦЕПТ/АРТ-ДИРЕКЦИЯ — есть одна сильная идея и характер, или безликий универсал.
2. ТИПОГРАФИКА — иерархия и контраст кеглей, крупный выразительный герой-заголовок,
   опинионированный шрифт; «всё одним средним кеглем» = слабо.
3. ЦВЕТ — палитра целостная, доминанта + акцент дозой, контраст достаточный;
   радуга / неон / дефолтный indigo-violet AI = брак.
4. КОМПОЗИЦИЯ/ВОЗДУХ/РИТМ — режиссура кадра, намеренный whitespace, разнообразие
   раскладок секций; центр-в-столбик + одинаковые карточки + плоский фон = generic.
5. ДЕТАЛЬ/КРАФТ — глубина (слои, тень с подтоном), выравнивание, консистентность
   радиусов/отступов, качество изображений; плоско / случайно / дёшево = штраф.
6. ОРИГИНАЛЬНОСТЬ — не похоже на типовой бесплатный AI-лендинг.

verdict:
  • "broken"    — сломанная вёрстка, наложения, пустые/обрезанные/серые секции, нечитаемо.
  • "generic"   — рабочее, но безликое/шаблонное, «ещё один AI-лендинг» (бар Awwwards НЕ взят).
  • "beautiful" — цельный, выразительный, уровня Awwwards-галереи.

ВЫВОД — РОВНО ОДИН JSON, без markdown:
{"verdict": "broken|generic|beautiful", "score": 0-10, "issues": ["<правка>", "..."]}

issues — КОНКРЕТНЫЕ дельты «что → где → как», которые верстальщик применит дословно.
НЕ абстракции.
  ПЛОХО: "улучшить иерархию", "сделать современнее".
  ХОРОШО: "Hero: заголовок мелкий — увеличь до clamp(3rem,6vw,6rem), убери дубль-подзаголовок";
          "Тарифы: 3 одинаковые карточки по центру — сделай асимметрию (одна выделенная, bento),
           добавь eyebrow и разный вес"; "Секция отзывов: плоский белый фон — добавь тон-в-тон
           mesh/grain и разведи карточки по сетке".
Если уровень Awwwards реально взят — issues: []."""


def _data_url(png: bytes) -> str:
    return "data:image/png;base64," + base64.b64encode(png).decode("ascii")


def _coerce_score(value: object) -> int:
    try:
        return max(0, min(10, round(float(value))))  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0


def _parse(raw: str) -> VisionVerdict:
    """Parse the model's JSON verdict; fail-soft to skipped on garbage."""
    text = raw.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1] if "\n" in text else text[3:]
        if text.endswith("```"):
            text = text[:-3]
        text = text.strip()
    # Tolerate a leading sentence before the JSON object.
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end != -1 and end > start:
        text = text[start : end + 1]
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        log.warning("vision_audit: unparseable verdict (fail-soft): %r", raw[:200])
        return _SKIPPED
    verdict = str(data.get("verdict", "")).strip().lower()
    if verdict not in {"broken", "generic", "beautiful"}:
        verdict = "generic"
    issues_raw = data.get("issues") or []
    issues = tuple(str(i).strip() for i in issues_raw if str(i).strip())[:8]
    return VisionVerdict(
        verdict=verdict,
        score=_coerce_score(data.get("score", 0)),
        issues=issues,
        raw=raw[:500],
    )


async def audit_screenshots(
    screenshots: dict[int, bytes],
    *,
    prompt_context: str = "",
    user_id: str | None = None,
    project_id: str | None = None,
    model: str | None = None,
) -> VisionVerdict:
    """Send screenshots to a vision model and return its verdict.

    `screenshots` maps viewport width → PNG bytes. `prompt_context` is the
    user's original request (gives the model intent — "is this what they
    asked for?"). Returns `_SKIPPED` on mock mode / gateway error / empty.
    """
    settings = get_settings()
    if settings.mock_llm or not screenshots:
        return _SKIPPED

    model = model or model_for_role("audit")
    chosen = {w: screenshots[w] for w in _VISION_WIDTHS if w in screenshots}
    if not chosen:
        chosen = screenshots

    intro = "Оцени качество сгенерированного лендинга."
    if prompt_context:
        intro += f"\nЗапрос пользователя: «{prompt_context[:300]}»"
    content: list[dict[str, object]] = [{"type": "text", "text": intro}]
    for w, png in sorted(chosen.items(), reverse=True):
        label = "десктоп" if w >= 1000 else "мобильный"
        content.append({"type": "text", "text": f"Скриншот ({label}, {w}px):"})
        content.append({"type": "image_url", "image_url": {"url": _data_url(png)}})

    messages = [
        {"role": "system", "content": _RUBRIC},
        {"role": "user", "content": content},
    ]
    try:
        raw = await complete_chat(
            messages,
            model,
            user_id=user_id,
            project_id=project_id,
            max_tokens=1000,
        )
    except LLMError as exc:
        log.warning("vision_audit gateway error (fail-soft): %r", exc)
        return _SKIPPED
    if not raw.strip():
        return _SKIPPED
    return _parse(raw)


__all__ = ["VisionVerdict", "audit_screenshots"]

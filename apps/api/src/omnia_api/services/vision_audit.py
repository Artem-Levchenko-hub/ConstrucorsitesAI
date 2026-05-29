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
Ты — придирчивый арт-директор. Тебе дают скриншот(ы) сгенерированного лендинга.
Оцени по рубрике и верни СТРОГО один JSON-объект, без markdown-обёртки.

Рубрика (каждый пункт влияет на score 0–10):
1. layout — композиция, сетка, ритм секций, выравнивание, дыхание (whitespace).
2. typography — иерархия, контраст размеров, читабельность, не «всё одним кеглем».
3. color — палитра целостная, контраст достаточный, НЕ дефолтный indigo/violet AI-шаблон.
4. composition — герой цепляет, секции разнообразны, есть визуальные акценты/графика.
5. originality — не похоже на типовой бесплатный AI-лендинг (центрованный текст,
   три карточки, одна кнопка, пустой фон). Шаблонность штрафуется.

verdict:
  • "broken"    — сломанная вёрстка, наложения, пустые/обрезанные секции, нечитаемо.
  • "generic"   — рабочее, но безликое/шаблонное, «ещё один AI-лендинг».
  • "beautiful" — цельный, выразительный, не стыдно показать клиенту.

ВЫВОД — РОВНО ОДИН JSON:
{"verdict": "broken|generic|beautiful", "score": 0-10, "issues": ["правка", "..."]}

issues — короткие конкретные действия для исправления (что и где), не похвала.
Если всё отлично — issues: []."""


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
            max_tokens=700,
        )
    except LLMError as exc:
        log.warning("vision_audit gateway error (fail-soft): %r", exc)
        return _SKIPPED
    if not raw.strip():
        return _SKIPPED
    return _parse(raw)


__all__ = ["VisionVerdict", "audit_screenshots"]

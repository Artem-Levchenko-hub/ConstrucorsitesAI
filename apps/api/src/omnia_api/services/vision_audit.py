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
from typing import Any

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

# Skip telemetry. A skip that is DEGRADATION (gateway error, empty answer,
# unparseable JSON) silently passes the page at score=10 — i.e. a broken judge
# invisibly disables the whole quality loop. We count skips by reason and emit a
# stable, greppable `metric=vision_skip` line so a degraded judge is observable
# (the real metrics sink is a future cross-cutting item; this is the minimum
# signal). "mock"/"no_input" are legitimate no-ops, not degradation.
_DEGRADED_SKIPS = frozenset({"gateway_error", "empty_answer", "parse_fail"})
_skip_counts: dict[str, int] = {}


def _skip(reason: str, detail: str = "") -> VisionVerdict:
    """Record a vision SKIP and return the neutral ``_SKIPPED`` verdict.

    Degradation reasons log at WARNING with a stable ``metric=vision_skip`` tag so a
    silently-failing judge (which would pass every page at score=10) is visible;
    legitimate no-ops (mock / no screenshots) log at DEBUG."""
    _skip_counts[reason] = _skip_counts.get(reason, 0) + 1
    emit = log.warning if reason in _DEGRADED_SKIPS else log.debug
    count = _skip_counts[reason]
    if detail:
        emit("metric=vision_skip reason=%s count=%d detail=%r", reason, count, detail)
    else:
        emit("metric=vision_skip reason=%s count=%d", reason, count)
    return _SKIPPED


def skip_stats() -> dict[str, int]:
    """Snapshot of vision-skip counts by reason (for a future metrics endpoint)."""
    return dict(_skip_counts)


# Cap how many viewports we ship to the model. Web pages need wide + narrow;
# MAX Mini Apps need two real phone widths and must never be judged as landings.
_VISION_WIDTHS = (1440, 360)
_MAX_VISION_WIDTHS = (390, 360)

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
   раскладок секций; центр-в-столбик + монотонный ряд одинаковых ПО ВЁРСТКЕ
   карточек + плоский фон = generic.
5. ДЕТАЛЬ/КРАФТ — глубина (слои, тень с подтоном), выравнивание, консистентность
   радиусов/отступов, качество изображений; плоско / случайно / дёшево = штраф.
6. ОРИГИНАЛЬНОСТЬ — не похоже на типовой бесплатный AI-лендинг.

НЕ ШТРАФУЙ ЗА (осознанные решения, НЕ брак — не снижай за это score, не пиши в issues):
• ОДНО И ТО ЖЕ ФОТО на карточках товара / меню / портфолио-плитках — намеренная
  экономия генерации. Разнообразие таким карточкам дают подписи, цены, текст и
  компоновка, а НЕ разные картинки. Суди сетку карточек по типографике, иерархии,
  ритму и воздуху, а НЕ по тому, одинаковы ли на ней фото. Один общий кадр на сетку
  карточек = норма, это НЕ generic и НЕ «лень».

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

_MAX_RUBRIC = """\
Ты — независимый senior product designer мобильных приложений и строгий ревьюер
MAX Mini Apps. Тебе дают один экран приложения на двух ширинах телефона. Это НЕ
лендинг: не требуй hero, длинных маркетинговых секций, pricing cards или desktop-
композиции. Сверь результат с запросом пользователя и верни СТРОГО один JSON-
объект без markdown.

Оцени общий score 0–10 по семи осям:
1. ПРОДУКТОВАЯ ЯСНОСТЬ — за 3 секунды понятно, что это за продукт, где главное
   действие и что пользователь получит после него.
2. КОНЦЕПТ И ХАРАКТЕР — есть узнаваемая идея и профессиональная арт-дирекция,
   соответствующая нише; дефолтный AI-dashboard, фиолетовый градиент и россыпь
   одинаковых карточек = generic.
3. МОБИЛЬНАЯ ИЕРАРХИЯ — сильный, но не рекламный первый экран; удобный ритм,
   читаемая типографика, управляемая плотность данных, нет случайной пустоты.
4. НАВИГАЦИЯ И TOUCH UX — очевидно текущее место, CTA и интерактивные элементы;
   нижняя навигация не перекрывает контент, safe-area учтён, подписи не обрезаны,
   tap targets выглядят пригодными для пальца. Не требуй hover.
5. ДЕТАЛЬ И КРАФТ — консистентны отступы, радиусы, иконки, поверхности, линии,
   состояния выбранного/нажатого элемента; визуальные эффекты дозированы.
6. КОНТЕНТ И ДОВЕРИЕ — реальные тексты и данные из брифа, без lorem ipsum,
   заглушек, «Feature 1» и выдуманных обещаний; профиль MAX встроен естественно.
7. АДАПТАЦИЯ — обе ширины полноценны: нет горизонтального overflow, наложений,
   микроскопического текста, обрезанных кнопок и контента под системными зонами.

Не штрафуй за отсутствие desktop-версии и за анимацию, которую невозможно увидеть
на статичном кадре. Не навязывай конкретный стиль: минимализм, data-rich, editorial,
brutalist или playful могут быть beautiful, если решение цельное и соответствует
задаче. MAX UI — средство нативности, а не обязанность превратить всё в одинаковый
набор системных карточек.

verdict:
• "broken" — приложение невозможно нормально использовать: пусто, наложения,
  обрезка, нечитаемость, сломанная адаптация или основной сценарий визуально исчез.
• "generic" — работает, но выглядит как сырой шаблон/вайрфрейм или не раскрывает
  бриф; аккуратности без характера и продуктовой глубины недостаточно.
• "beautiful" — цельный production-grade мобильный продукт с сильной идеей,
  ясным действием, профессиональной детализацией и score не ниже 8.

ВЫВОД — РОВНО ОДИН JSON:
{"verdict":"broken|generic|beautiful","score":0-10,"issues":["<правка>","..."]}

issues — только конкретные дельты «где → что не так → как исправить». Для generic
обязательно назови 1–4 самых сильных изменения, способных поднять продукт на уровень
профессиональной работы. Не пиши абстрактное «улучшить дизайн». Для beautiful верни
issues: []."""


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
        return _skip("parse_fail", raw[:200])
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
    product_kind: str = "web",
) -> VisionVerdict:
    """Send screenshots to a vision model and return its verdict.

    `screenshots` maps viewport width → PNG bytes. `prompt_context` is the
    user's original request (gives the model intent — "is this what they
    asked for?"). Returns `_SKIPPED` on mock mode / gateway error / empty.
    """
    settings = get_settings()
    if settings.mock_llm:
        return _skip("mock")
    if not screenshots:
        return _skip("no_input")

    model = model or model_for_role("audit")
    is_max = product_kind == "max_miniapp"
    preferred_widths = _MAX_VISION_WIDTHS if is_max else _VISION_WIDTHS
    chosen = {w: screenshots[w] for w in preferred_widths if w in screenshots}
    if not chosen:
        chosen = screenshots

    intro = (
        "Оцени качество сгенерированного MAX Mini App как мобильного продукта."
        if is_max
        else "Оцени качество сгенерированного лендинга."
    )
    if prompt_context:
        intro += f"\nЗапрос пользователя: «{prompt_context[:300]}»"
    content: list[dict[str, object]] = [{"type": "text", "text": intro}]
    for w, png in sorted(chosen.items(), reverse=True):
        label = "десктоп" if w >= 1000 else "телефон"
        content.append({"type": "text", "text": f"Скриншот ({label}, {w}px):"})
        content.append({"type": "image_url", "image_url": {"url": _data_url(png)}})

    messages: list[dict[str, Any]] = [
        {"role": "system", "content": _MAX_RUBRIC if is_max else _RUBRIC},
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
        return _skip("gateway_error", repr(exc)[:200])
    if not raw.strip():
        return _skip("empty_answer")
    return _parse(raw)


__all__ = ["VisionVerdict", "audit_screenshots", "skip_stats"]

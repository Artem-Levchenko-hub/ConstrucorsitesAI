"""Auto-classifier project → design preset id.

Pipeline:
1. **Heuristic pass** — двуязычный keyword-match (ru/en) против
   ``preset.industries + preset.keywords``. Score = доля токенов промпта,
   совпавших с любым keyword пресета. Возвращает preset с максимальным
   score, если он ≥ ``MIN_HEURISTIC_SCORE`` и в одиночестве (нет другого
   пресета с близким score).
2. **LLM fallback (Haiku 4.5)** — если эвристика амбивалентна или дала
   ничего. Промпт-классификатор отдаёт ровно один id. ~150 токенов вход
   + 5 токенов выход. Стоимость ≤ ₽0.05 за проект.
3. **Default** — ``DEFAULT_PRESET_ID`` (editorial-trust) если LLM вернул
   мусор.

Результат кешируется вызывающим (``routers/messages.py`` / ``routers/projects.py``)
в ``projects.design_preset_id`` — повторно не дёргаем.
"""

from __future__ import annotations

import json
import logging
import re
import uuid

from omnia_api.services.design_presets import PRESETS
from omnia_api.services.llm_client import stream_chat_completion

log = logging.getLogger(__name__)

DEFAULT_PRESET_ID = "editorial-trust"
CLASSIFIER_MODEL = "claude-haiku-4-5"
MIN_HEURISTIC_SCORE = 1  # минимум совпавших keyword-стемов
HEURISTIC_LEAD = 1  # лидер должен опережать второго на это число matches
STEM_LEN = 5  # длина префикса для матчинга русских падежных форм

# Синтетические UUID для атрибуции телеметрии classifier-вызовов в LLM-gateway.
# Gateway валидирует user/project/message как UUID, поэтому реальный путь
# (фоновый сервис, не пользовательский) использует фиксированные стабильные id.
_CLASSIFIER_USER_ID = "00000000-0000-0000-0000-c0de4c1a55ef"
_CLASSIFIER_PROJECT_ID = "00000000-0000-0000-0000-c0deca5511fe"


_TOKEN_RE = re.compile(r"[A-Za-zА-Яа-яЁё0-9]+", re.UNICODE)


def _tokenize(text: str) -> list[str]:
    """Разбить текст на нижние токены ≥ 2 символов (ru+en+цифры)."""
    return [t.lower() for t in _TOKEN_RE.findall(text or "") if len(t) >= 2]


def _stem(token: str) -> str:
    """Грубый стем: первые ``STEM_LEN`` символов (русские падежи / en-plural).

    Не идеально, но без морфологического анализатора это даёт «фотограф»
    ⇄ «фотографа» / «электронн» ⇄ «электронной/электронная».
    """
    return token[:STEM_LEN] if len(token) > STEM_LEN else token


def _stem_set(tokens: list[str]) -> set[str]:
    return {_stem(t) for t in tokens}


def _heuristic_score(text: str) -> dict[str, int]:
    """Score по каждому пресету: число matched keyword-стемов в тексте.

    Алгоритм:
    1. tokenize+stem текст → ``text_stems``.
    2. tokenize+stem каждый keyword/industry пресета → ``kw_stems``.
    3. score = |text_stems ∩ kw_stems_за_keyword| (уникальные совпавшие
       keyword-сущности, чтобы много-словный «коммерческая недвижимость»
       считался за один сигнал, а не два).
    """
    if not text:
        return {pid: 0 for pid in PRESETS}
    text_stems = _stem_set(_tokenize(text))
    scores: dict[str, int] = {}
    for pid, preset in PRESETS.items():
        matched = 0
        for kw in (*preset.industries, *preset.keywords):
            kw_stems = _stem_set(_tokenize(kw))
            if kw_stems and kw_stems.issubset(text_stems):
                matched += 1
        scores[pid] = matched
    return scores


def _pick_heuristic(scores: dict[str, int]) -> str | None:
    """Однозначный лидер или None.

    Лидер обязан:
    - иметь score >= ``MIN_HEURISTIC_SCORE``;
    - опережать второго минимум на ``HEURISTIC_LEAD``.
    """
    if not scores:
        return None
    sorted_pairs = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)
    top_id, top_score = sorted_pairs[0]
    if top_score < MIN_HEURISTIC_SCORE:
        return None
    if len(sorted_pairs) > 1:
        _, second_score = sorted_pairs[1]
        if top_score - second_score < HEURISTIC_LEAD:
            return None
    return top_id


def _build_classifier_prompt(project_name: str, template: str, first_prompt: str | None) -> str:
    """Промпт для Haiku-классификатора. Короткий и закрытый."""
    options = "\n".join(
        f"- {pid}: {preset.one_liner}" for pid, preset in PRESETS.items()
    )
    description = first_prompt.strip() if first_prompt else "(нет описания)"
    return f"""Ты классификатор проектов на дизайн-пресеты.

Доступные пресеты (id: краткое описание):
{options}

Проект:
- Название: {project_name}
- Тип шаблона: {template}
- Первое описание/промпт: {description}

Верни ОДНИМ словом id пресета, который подходит лучше всего.
Без объяснений, без JSON, без markdown — ТОЛЬКО id из списка выше."""


async def _llm_classify(project_name: str, template: str, first_prompt: str | None) -> str | None:
    """Спросить Haiku. Вернуть preset_id или None при ошибке/мусоре."""
    prompt = _build_classifier_prompt(project_name, template, first_prompt)
    messages = [
        {
            "role": "system",
            "content": "Ты строгий классификатор. Отвечаешь ровно одним id из заданного списка.",
        },
        {"role": "user", "content": prompt},
    ]
    chunks: list[str] = []
    try:
        async for event in stream_chat_completion(
            messages=messages,
            model=CLASSIFIER_MODEL,
            user_id=_CLASSIFIER_USER_ID,
            project_id=_CLASSIFIER_PROJECT_ID,
            message_id=str(uuid.uuid4()),
        ):
            if delta := event.get("delta"):
                chunks.append(delta)
            if event.get("error"):
                log.warning("preset classifier llm error: %s", event["error"])
                return None
    except Exception:  # noqa: BLE001 — fallback всегда возможен
        log.exception("preset classifier llm exception")
        return None
    raw = "".join(chunks).strip()
    # ищем подстроку с валидным preset_id (модель иногда оборачивает в кавычки/json)
    raw_low = raw.lower()
    for pid in PRESETS:
        if pid in raw_low:
            return pid
    # last chance — json-формат
    try:
        data = json.loads(raw)
        if isinstance(data, dict) and (pid := data.get("preset_id")) in PRESETS:
            return pid
    except json.JSONDecodeError:
        pass
    log.info("preset classifier llm returned garbage: %r", raw[:200])
    return None


async def classify_preset(
    project_name: str,
    template: str,
    first_prompt: str | None = None,
) -> str:
    """Главный API. Возвращает preset_id; никогда не падает.

    Порядок:
    1. heuristic по объединённому ``project_name + first_prompt``;
    2. LLM-fallback (Haiku) если эвристика амбивалентна;
    3. DEFAULT_PRESET_ID.
    """
    combined = f"{project_name or ''}\n{first_prompt or ''}"
    scores = _heuristic_score(combined)
    picked = _pick_heuristic(scores)
    if picked is not None:
        log.info(
            "preset classifier heuristic picked %s (scores=%s)",
            picked,
            {k: v for k, v in scores.items() if v > 0},
        )
        return picked

    llm_pick = await _llm_classify(project_name, template, first_prompt)
    if llm_pick is not None:
        log.info("preset classifier llm picked %s", llm_pick)
        return llm_pick

    log.info("preset classifier defaulting to %s", DEFAULT_PRESET_ID)
    return DEFAULT_PRESET_ID


def classify_preset_sync(
    project_name: str,
    template: str,
    first_prompt: str | None = None,
) -> str:
    """Sync-обёртка для не-async вызывающих (heuristic-only, без LLM).

    На горячем пути POST /api/projects не хотим блокировать на LLM —
    если эвристика однозначна, кладём preset_id сразу; иначе остаётся
    null, и асинхронный classify_preset() добьёт его на первом промпте.
    """
    combined = f"{project_name or ''}\n{first_prompt or ''}"
    scores = _heuristic_score(combined)
    return _pick_heuristic(scores) or ""


__all__ = [
    "DEFAULT_PRESET_ID",
    "CLASSIFIER_MODEL",
    "classify_preset",
    "classify_preset_sync",
]

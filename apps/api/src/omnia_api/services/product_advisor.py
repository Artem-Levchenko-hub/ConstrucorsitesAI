"""Bounded product advice for generated MAX Mini Apps.

The local layer owns safety and usefulness: it classifies material changes,
extracts a small feature inventory without exposing source, filters a curated
catalog, and always has a deterministic top-three fallback. A later ranking
step may reorder these server-owned candidates but cannot invent executable
work outside this module.
"""

from __future__ import annotations

import json
import re
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Any, Literal

from omnia_api.core.config import get_settings
from omnia_api.services import llm_client
from omnia_api.services.design_plugin import classify_product_archetype
from omnia_api.services.secret_safety import redact_provider_secrets

ADVISOR_VERSION = "1.0.0"
MAX_ADVICE_ITEMS = 3

_ALLOWED_SUFFIXES = frozenset({".ts", ".tsx", ".js", ".jsx", ".json", ".css", ".html", ".md"})
_EXCLUDED_PARTS = frozenset(
    {
        "node_modules",
        ".next",
        "dist",
        "build",
        "coverage",
        ".git",
        "vendor",
        "secrets",
    }
)
_MAX_FILE_CHARS = 200_000
_MAX_SCAN_CHARS = 500_000

_MATERIAL_SIGNALS = (
    "авторизац",
    "база данных",
    "бронир",
    "избран",
    "интеграц",
    "истори",
    "каталог",
    "корзин",
    "напомин",
    "оплат",
    "отдельн экран",
    "поиск",
    "профил",
    "раздел",
    "роль",
    "сохран",
    "статус заказ",
    "уведом",
    "фильтр",
    "экран",
    "analytics",
    "auth",
    "booking",
    "database",
    "favorite",
    "integration",
    "notification",
    "payment",
    "search",
)
_COSMETIC_SIGNALS = (
    "анимац",
    "иконк",
    "отступ",
    "син",
    "текст",
    "типограф",
    "фон",
    "цвет",
    "шрифт",
    "border",
    "color",
    "font",
    "icon",
    "spacing",
)
_MATERIAL_ACTION_SIGNALS = (
    "добав",
    "настрой",
    "подключ",
    "реализ",
    "создай",
    "add ",
    "connect",
    "create",
    "implement",
    "set up",
)

_FEATURE_PATTERNS: dict[str, tuple[str, ...]] = {
    "adaptive_plan": ("адаптивн", "adaptive plan", "adjust plan"),
    "analytics": ("аналитик", "analytics", "metric", "метрик"),
    "anomaly": ("аномал", "anomaly", "отклонен"),
    "audit_log": ("audit log", "журнал действ", "история изменен"),
    "booking": ("бронир", "booking", "appointment", "записаться"),
    "bulk_actions": ("bulk", "массов", "выбрать все"),
    "cart": ("корзин", "cart"),
    "continue_learning": ("продолжить обуч", "continue learning", "resume lesson"),
    "date_compare": ("сравнен период", "date comparison", "previous period"),
    "empty_states": ("empty state", "нет данных", "пока нет", "ничего не найден"),
    "error_states": ("error", "ошиб", "не удалось"),
    "export": ("экспорт", "export", "download csv", "скачать csv"),
    "favorites": ("избран", "favorite", "wishlist"),
    "filters": ("фильтр", "filter"),
    "history": ("истори", "history", "timeline"),
    "loading_states": ("loading", "загрузка", "загружаем", "skeleton"),
    "notifications": ("уведом", "notification", "push"),
    "offline_state": ("offline", "indexeddb", "localstorage", "persist"),
    "onboarding": ("онбординг", "onboarding", "первый запуск"),
    "order_status": ("статус заказ", "order status", "tracking"),
    "payments": ("оплат", "payment", "checkout", "yookassa"),
    "pins": ("закреп", "pinned", "pin conversation"),
    "priority": ("приоритет", "priority", "urgent"),
    "progress": ("прогресс", "progress", "achievement"),
    "quiz": ("тест", "quiz", "knowledge check"),
    "reminders": ("напомин", "reminder"),
    "repeat_action": ("повторить заказ", "repeat order", "записаться снова", "rebook"),
    "reschedule": ("перенести запис", "reschedule"),
    "roles": ("роль", "permission", "доступ", "role-based"),
    "saved_content": ("закладк", "bookmark", "сохраненные материал"),
    "saved_filters": ("сохранен фильтр", "saved filter"),
    "saved_state": ("localstorage", "persist", "сохранен состояни"),
    "search": ("поиск", "search"),
    "sharing": ("поделиться", "share", "referral", "реферал"),
    "streaks": ("серия", "streak"),
    "success_states": ("success", "успеш", "готово"),
    "unread": ("непрочитан", "unread"),
    "waitlist": ("лист ожид", "waitlist"),
}


@dataclass(frozen=True)
class SnapshotInput:
    id: str
    commit_sha: str
    prompt_text: str | None


@dataclass(frozen=True)
class AdviceItem:
    id: str
    kind: Literal["feature", "improvement"]
    title: str
    benefit: str
    prompt: str


@dataclass(frozen=True)
class AdviceContext:
    project_name: str
    material_prompt: str
    archetype: str
    inventory: tuple[str, ...]


@dataclass(frozen=True)
class AdviceCandidate(AdviceItem):
    archetypes: tuple[str, ...]
    presence_signals: tuple[str, ...]
    priority: int


@dataclass(frozen=True)
class ProductAdviceResult:
    archetype: str
    items: tuple[AdviceItem, ...]
    source: Literal["model", "fallback"]


def is_material_change(prompt: str | None) -> bool:
    """Whether a prompt changes product capability rather than presentation."""
    text = (prompt or "").strip().casefold()
    if not text:
        return False
    if "восстановление версии" in text or "restore version" in text:
        return True
    has_material_signal = any(signal in text for signal in _MATERIAL_SIGNALS)
    has_cosmetic_signal = any(signal in text for signal in _COSMETIC_SIGNALS)
    has_material_action = any(signal in text for signal in _MATERIAL_ACTION_SIGNALS) or any(
        f"сделай {signal}" in text for signal in _MATERIAL_SIGNALS
    )
    if has_cosmetic_signal and not has_material_action:
        return False
    if has_material_signal:
        return True
    action = any(signal in text for signal in ("добав", "подключ", "реализ", "сделай", "создай"))
    return action and len(text) >= 80


def choose_analysis_snapshot(snapshots: Sequence[SnapshotInput]) -> SnapshotInput:
    """Choose the newest material snapshot from newest-first history."""
    if not snapshots:
        raise ValueError("at least one snapshot is required")
    for snapshot in snapshots:
        if is_material_change(snapshot.prompt_text):
            return snapshot
    for snapshot in reversed(snapshots):
        if (snapshot.prompt_text or "").strip():
            return snapshot
    return snapshots[0]


def _safe_source_path(raw_path: str) -> PurePosixPath | None:
    path = PurePosixPath(raw_path.replace("\\", "/"))
    lowered = tuple(part.casefold() for part in path.parts)
    name = path.name.casefold()
    if any(part in _EXCLUDED_PARTS for part in lowered):
        return None
    if name == ".env" or name.startswith(".env."):
        return None
    if any(marker in name for marker in ("secret", "credential", "token", "lock")):
        return None
    if path.suffix.casefold() not in _ALLOWED_SUFFIXES:
        return None
    return path


def extract_feature_inventory(files: Mapping[str, str]) -> tuple[str, ...]:
    """Return normalized product signals; never return source or secret values."""
    chunks: list[str] = []
    remaining = _MAX_SCAN_CHARS
    for raw_path, content in sorted(files.items()):
        if remaining <= 0:
            break
        if _safe_source_path(raw_path) is None or not isinstance(content, str):
            continue
        snippet = content[: min(_MAX_FILE_CHARS, remaining)].casefold()
        chunks.append(snippet)
        remaining -= len(snippet)
    corpus = "\n".join(chunks)
    found = {
        signal
        for signal, patterns in _FEATURE_PATTERNS.items()
        if any(pattern in corpus for pattern in patterns)
    }
    if {"loading_states", "empty_states", "error_states", "success_states"} <= found:
        found.add("complete_states")
    return tuple(sorted(found))


_SECRET_ASSIGNMENT = re.compile(r"(?i)\b(?:api[_ -]?key|token|secret|password)\s*[:=]\s*\S+")
_LONG_SECRET = re.compile(r"\b[A-Za-z0-9_-]{32,}\b")
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")


def _sanitize_context_text(value: str, *, limit: int = 2000) -> str:
    cleaned = redact_provider_secrets(_CONTROL.sub(" ", value or ""))
    cleaned = _SECRET_ASSIGNMENT.sub("[credential redacted]", cleaned)
    cleaned = _LONG_SECRET.sub("[credential redacted]", cleaned)
    return " ".join(cleaned.split())[:limit]


def _discovery_text(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, Mapping):
        return " ".join(_discovery_text(item) for item in value.values())
    if isinstance(value, (list, tuple)):
        return " ".join(_discovery_text(item) for item in value)
    return ""


def build_advice_context(
    *,
    project_name: str,
    material_prompt: str,
    discovery_spec: Mapping[str, Any] | None,
    files: Mapping[str, str],
) -> AdviceContext:
    safe_name = _sanitize_context_text(project_name, limit=100)
    safe_prompt = _sanitize_context_text(material_prompt)
    safe_discovery = _sanitize_context_text(_discovery_text(discovery_spec or {}))
    classifier_brief = " ".join(part for part in (safe_name, safe_prompt, safe_discovery) if part)
    return AdviceContext(
        project_name=safe_name,
        material_prompt=safe_prompt,
        archetype=classify_product_archetype(classifier_brief),
        inventory=extract_feature_inventory(files),
    )


def _implementation_prompt(title: str, requirement: str) -> str:
    return (
        f"Добавь в текущую MAX Mini App вертикальный продуктовый срез «{title}». "
        f"{requirement} Реализуй реальные действия и сохранение данных, где это нужно. "
        "Добавь полезные состояния loading, empty, error и success рядом с действием. "
        "Сохрани текущий визуальный язык, рабочие сценарии, MAX Bridge и интеграции. "
        "Не имитируй подключение внешнего провайдера: если он недоступен, покажи честный "
        "путь подключения или локально работающий сценарий. Проверь сборку и основной поток."
    )


def _candidate(
    id: str,
    kind: Literal["feature", "improvement"],
    title: str,
    benefit: str,
    requirement: str,
    archetypes: tuple[str, ...],
    presence: tuple[str, ...],
    priority: int,
) -> AdviceCandidate:
    return AdviceCandidate(
        id=id,
        kind=kind,
        title=title,
        benefit=benefit,
        prompt=_implementation_prompt(title, requirement),
        archetypes=archetypes,
        presence_signals=presence,
        priority=priority,
    )


_CANDIDATES = (
    _candidate(
        "smart-search",
        "feature",
        "Умный поиск",
        "Поможет быстрее находить нужное в каталоге.",
        "Добавь поиск с подсказками, понятным нулевым результатом и фильтрами.",
        ("commerce",),
        ("search",),
        92,
    ),
    _candidate(
        "saved-favorites",
        "feature",
        "Избранное",
        "Вернёт пользователя к понравившимся позициям без повторного поиска.",
        "Добавь избранное с сохранением, отдельным экраном и удалением из списка.",
        ("commerce",),
        ("favorites",),
        91,
    ),
    _candidate(
        "transparent-order-status",
        "feature",
        "Статус заказа",
        "Снизит неопределённость после оформления заказа.",
        "Добавь историю и понятные этапы статуса заказа с деталями.",
        ("commerce",),
        ("order_status",),
        90,
    ),
    _candidate(
        "repeat-order",
        "improvement",
        "Повтор заказа",
        "Сократит путь постоянного клиента до одного действия.",
        "Добавь повтор последнего заказа с просмотром состава перед подтверждением.",
        ("commerce",),
        ("repeat_action",),
        89,
    ),
    _candidate(
        "quick-rebooking",
        "improvement",
        "Повторная запись",
        "Позволит записаться снова без повторного выбора всех параметров.",
        "Добавь повтор прошлой записи с выбором нового свободного времени.",
        ("booking-service",),
        ("repeat_action",),
        92,
    ),
    _candidate(
        "booking-reminders",
        "feature",
        "Напоминания о записи",
        "Уменьшит число пропущенных визитов.",
        "Добавь настройки напоминаний и честный канал доставки уведомления.",
        ("booking-service",),
        ("reminders",),
        91,
    ),
    _candidate(
        "waitlist",
        "feature",
        "Лист ожидания",
        "Не потеряет клиента, когда удобное время занято.",
        "Добавь вступление в лист ожидания и предложение освободившегося слота.",
        ("booking-service",),
        ("waitlist",),
        90,
    ),
    _candidate(
        "reschedule-flow",
        "improvement",
        "Перенос записи",
        "Сделает изменение планов быстрым и прозрачным.",
        "Добавь перенос записи с проверкой доступности и подтверждением нового времени.",
        ("booking-service",),
        ("reschedule",),
        89,
    ),
    _candidate(
        "progress-insights",
        "feature",
        "Разбор прогресса",
        "Покажет пользователю результат и следующий полезный шаг.",
        "Добавь динамику прогресса, понятные выводы и следующую рекомендацию.",
        ("fitness-health",),
        ("progress",),
        92,
    ),
    _candidate(
        "adaptive-plan",
        "feature",
        "Адаптивный план",
        "Подстроит следующую тренировку под реальные результаты.",
        "Добавь корректировку плана на основе выполненных тренировок и обратной связи.",
        ("fitness-health",),
        ("adaptive_plan",),
        91,
    ),
    _candidate(
        "habit-streaks",
        "improvement",
        "Серия занятий",
        "Поддержит регулярность без давления.",
        "Добавь серию занятий, мягкое восстановление серии и недельную цель.",
        ("fitness-health",),
        ("streaks",),
        90,
    ),
    _candidate(
        "workout-reminders",
        "feature",
        "Напоминания о тренировке",
        "Поможет не забыть запланированное занятие.",
        "Добавь расписание напоминаний с управлением частотой.",
        ("fitness-health",),
        ("reminders",),
        89,
    ),
    _candidate(
        "unread-priorities",
        "improvement",
        "Важные непрочитанные",
        "Поможет быстро понять, где нужен ответ.",
        "Добавь непрочитанные, приоритет и переход к первому важному сообщению.",
        ("communication",),
        ("unread", "priority"),
        92,
    ),
    _candidate(
        "conversation-search",
        "feature",
        "Поиск по общению",
        "Ускорит возвращение к нужному решению или файлу.",
        "Добавь поиск по диалогам и сообщениям с подсветкой совпадений.",
        ("communication",),
        ("search",),
        91,
    ),
    _candidate(
        "pinned-context",
        "feature",
        "Закреплённый контекст",
        "Сохранит важное решение на виду у участников.",
        "Добавь закрепление сообщений и компактный список закреплённого.",
        ("communication",),
        ("pins",),
        90,
    ),
    _candidate(
        "smart-notifications",
        "improvement",
        "Управляемые уведомления",
        "Оставит только действительно важные сигналы.",
        "Добавь настройки уведомлений по типам событий и тихий режим.",
        ("communication",),
        ("notifications",),
        89,
    ),
    _candidate(
        "continue-learning",
        "improvement",
        "Продолжить обучение",
        "Вернёт пользователя точно к незавершённому шагу.",
        "Добавь продолжение с последнего места и сохранение позиции в уроке.",
        ("learning-content",),
        ("continue_learning",),
        92,
    ),
    _candidate(
        "knowledge-checks",
        "feature",
        "Проверка знаний",
        "Даст понятную обратную связь после материала.",
        "Добавь короткие проверки знаний с объяснением ответа и повтором ошибок.",
        ("learning-content",),
        ("quiz",),
        91,
    ),
    _candidate(
        "saved-content",
        "feature",
        "Сохранённые материалы",
        "Позволит собрать личную подборку полезного.",
        "Добавь закладки с отдельным экраном и быстрым удалением.",
        ("learning-content",),
        ("saved_content",),
        90,
    ),
    _candidate(
        "progress-streaks",
        "improvement",
        "Прогресс и регулярность",
        "Сделает следующий учебный шаг очевидным.",
        "Добавь прогресс по программе, недельную цель и серию занятий.",
        ("learning-content",),
        ("progress", "streaks"),
        89,
    ),
    _candidate(
        "priority-inbox",
        "improvement",
        "Очередь приоритетов",
        "Покажет, что требует внимания прямо сейчас.",
        "Добавь рабочую очередь с приоритетом, сроком и быстрым действием.",
        ("operations",),
        ("priority",),
        92,
    ),
    _candidate(
        "bulk-actions",
        "feature",
        "Массовые действия",
        "Сократит повторяющуюся обработку однотипных объектов.",
        "Добавь безопасный выбор нескольких записей и подтверждаемые массовые действия.",
        ("operations",),
        ("bulk_actions",),
        91,
    ),
    _candidate(
        "audit-history",
        "feature",
        "История изменений",
        "Объяснит, кто и когда изменил важные данные.",
        "Добавь журнал значимых действий с фильтрами и деталями.",
        ("operations",),
        ("audit_log",),
        90,
    ),
    _candidate(
        "role-aware-workspace",
        "feature",
        "Рабочие роли",
        "Упростит интерфейс и защитит действия для разных сотрудников.",
        "Добавь роли с реальными ограничениями действий и понятными отказами доступа.",
        ("operations",),
        ("roles",),
        89,
    ),
    _candidate(
        "anomaly-highlights",
        "feature",
        "Важные изменения",
        "Подсветит отклонения, которые требуют решения.",
        "Добавь обнаружение заметных изменений метрик и объяснение причины.",
        ("analytics",),
        ("anomaly",),
        92,
    ),
    _candidate(
        "date-comparison",
        "improvement",
        "Сравнение периодов",
        "Поможет понять, стало лучше или хуже.",
        "Добавь выбор и сравнение периодов с абсолютной и процентной разницей.",
        ("analytics",),
        ("date_compare",),
        91,
    ),
    _candidate(
        "saved-filters",
        "feature",
        "Сохранённые фильтры",
        "Вернёт пользователя к регулярному срезу данных одним касанием.",
        "Добавь сохранение, переименование и применение наборов фильтров.",
        ("analytics",),
        ("saved_filters",),
        90,
    ),
    _candidate(
        "export-share",
        "feature",
        "Экспорт результата",
        "Упростит передачу выводов коллегам.",
        "Добавь экспорт текущего среза и честное сообщение о составе файла.",
        ("analytics",),
        ("export", "sharing"),
        89,
    ),
    _candidate(
        "guided-onboarding",
        "improvement",
        "Первый полезный шаг",
        "Быстрее доведёт нового пользователя до результата.",
        "Добавь короткий онбординг вокруг первого реального действия с возможностью пропуска.",
        ("productivity",),
        ("onboarding",),
        92,
    ),
    _candidate(
        "fast-search",
        "feature",
        "Быстрый поиск",
        "Сократит путь к нужному объекту.",
        "Добавь поиск, недавние запросы и полезный нулевой результат.",
        ("productivity",),
        ("search",),
        91,
    ),
    _candidate(
        "saved-state",
        "improvement",
        "Продолжение с места остановки",
        "Сохранит контекст работы между открытиями приложения.",
        "Добавь восстановление последнего безопасного состояния и явный сброс.",
        ("productivity",),
        ("saved_state",),
        90,
    ),
    _candidate(
        "actionable-empty-states",
        "improvement",
        "Полезные пустые экраны",
        "Подскажет действие вместо тупика без данных.",
        "Добавь доменные пустые состояния с одним реальным первым действием.",
        ("productivity",),
        ("empty_states",),
        89,
    ),
    _candidate(
        "resilient-states",
        "improvement",
        "Понятные состояния",
        "Сделает приложение предсказуемым при ожидании и ошибках.",
        "Дополни главные действия согласованными состояниями ожидания, пустоты, ошибки и успеха.",
        ("*",),
        ("complete_states",),
        96,
    ),
    _candidate(
        "faster-repeat-action",
        "improvement",
        "Быстрое повторное действие",
        "Уберёт повторный ввод в частом сценарии.",
        "Найди самое частое завершённое действие и добавь безопасный повтор с подтверждением.",
        ("*",),
        ("repeat_action",),
        85,
    ),
    _candidate(
        "useful-notifications",
        "improvement",
        "Полезные уведомления",
        "Вернёт пользователя только по действительно важному событию.",
        "Добавь управляемые уведомления для одного ключевого события продукта.",
        ("*",),
        ("notifications",),
        84,
    ),
)


def candidate_advice(context: AdviceContext) -> tuple[AdviceItem, ...]:
    """Return a stable, present-feature-aware top three."""
    present = set(context.inventory)
    eligible = [
        item
        for item in _CANDIDATES
        if (context.archetype in item.archetypes or "*" in item.archetypes)
        and not any(signal in present for signal in item.presence_signals)
    ]
    eligible.sort(
        key=lambda item: (
            0 if context.archetype in item.archetypes else 1,
            -item.priority,
            item.id,
        )
    )
    picked = eligible[:MAX_ADVICE_ITEMS]
    if picked and not any(item.kind == "improvement" for item in picked):
        improvement = next((item for item in eligible if item.kind == "improvement"), None)
        if improvement is not None:
            picked[-1] = improvement
    return tuple(
        AdviceItem(
            id=item.id,
            kind=item.kind,
            title=item.title,
            benefit=item.benefit,
            prompt=item.prompt,
        )
        for item in picked
    )


_Complete = Callable[..., Awaitable[str]]
_MARKUP = re.compile(r"<[^>]*>")


def _clean_display(value: object, *, limit: int) -> str:
    if not isinstance(value, str):
        return ""
    cleaned = _MARKUP.sub("", _CONTROL.sub(" ", value))
    return " ".join(cleaned.split())[:limit].strip()


def _parse_ranked_items(raw: str) -> list[dict[str, object]] | None:
    text = (raw or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE)
        text = re.sub(r"\s*```$", "", text)
    try:
        payload = json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return None
    if not isinstance(payload, dict) or not isinstance(payload.get("items"), list):
        return None
    return [item for item in payload["items"] if isinstance(item, dict)]


def _ranking_messages(
    context: AdviceContext, candidates: Sequence[AdviceItem]
) -> list[dict[str, str]]:
    candidate_payload = [
        {
            "id": item.id,
            "kind": item.kind,
            "title": item.title,
            "benefit": item.benefit,
        }
        for item in candidates
    ]
    user_payload = {
        "project_name": context.project_name,
        "archetype": context.archetype,
        "material_request": context.material_prompt,
        "present_features": list(context.inventory),
        "candidates": candidate_payload,
    }
    return [
        {
            "role": "system",
            "content": (
                "Ты продуктовый советник MAX Mini Apps. Выбери до трёх самых полезных "
                "кандидатов только из переданных id. Верни только JSON вида "
                '{"items":[{"id":"...","title":"до 80 символов",'
                '"benefit":"до 180 символов"}]}. Не добавляй новые функции, код, '
                "промпты или поля. Поставь первым самое сильное улучшение для пользователя."
            ),
        },
        {
            "role": "user",
            "content": json.dumps(user_payload, ensure_ascii=False, separators=(",", ":")),
        },
    ]


async def generate_product_advice(
    context: AdviceContext,
    *,
    complete: _Complete | None = None,
    model: str | None = None,
) -> ProductAdviceResult:
    """Rank bounded candidates; malformed/provider failures use the fallback."""
    fallback = candidate_advice(context)
    if not fallback:
        return ProductAdviceResult(
            archetype=context.archetype,
            items=(),
            source="fallback",
        )

    complete_fn = complete or llm_client.complete_chat
    selected_model = model or get_settings().product_advisor_model
    try:
        raw = await complete_fn(
            _ranking_messages(context, fallback),
            selected_model,
            stage="product_advisor",
            free=True,
            max_tokens=700,
            temperature=0.1,
            timeout_seconds=12.0,
        )
    except Exception:
        return ProductAdviceResult(
            archetype=context.archetype,
            items=fallback,
            source="fallback",
        )

    ranked = _parse_ranked_items(raw)
    if ranked is None:
        return ProductAdviceResult(
            archetype=context.archetype,
            items=fallback,
            source="fallback",
        )

    by_id = {item.id: item for item in fallback}
    selected: list[AdviceItem] = []
    used: set[str] = set()
    for raw_item in ranked:
        candidate_id = raw_item.get("id")
        if not isinstance(candidate_id, str) or candidate_id in used:
            continue
        candidate = by_id.get(candidate_id)
        if candidate is None:
            continue
        title = _clean_display(raw_item.get("title"), limit=80) or candidate.title
        benefit = _clean_display(raw_item.get("benefit"), limit=180) or candidate.benefit
        selected.append(
            AdviceItem(
                id=candidate.id,
                kind=candidate.kind,
                title=title,
                benefit=benefit,
                prompt=candidate.prompt,
            )
        )
        used.add(candidate.id)
        if len(selected) == MAX_ADVICE_ITEMS:
            break
    if not selected:
        return ProductAdviceResult(
            archetype=context.archetype,
            items=fallback,
            source="fallback",
        )
    for candidate in fallback:
        if len(selected) == MAX_ADVICE_ITEMS:
            break
        if candidate.id not in used:
            selected.append(candidate)
            used.add(candidate.id)
    return ProductAdviceResult(
        archetype=context.archetype,
        items=tuple(selected),
        source="model",
    )


__all__ = [
    "ADVISOR_VERSION",
    "MAX_ADVICE_ITEMS",
    "AdviceContext",
    "AdviceItem",
    "ProductAdviceResult",
    "SnapshotInput",
    "build_advice_context",
    "candidate_advice",
    "choose_analysis_snapshot",
    "extract_feature_inventory",
    "generate_product_advice",
    "is_material_change",
]

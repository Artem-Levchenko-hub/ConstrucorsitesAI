"""Directional compatibility checks and capability diff (AV05, AV06 groundwork).

Turns structural diagnostics into owner-readable checks with a concrete object,
operation and way forward. An analyzer gap is ``unknown`` ("needs verification"),
never a proven conflict and never a ban on the generated schema.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping

from omnia_orchestrator.services.restoration_data_contract import Diagnostic
from omnia_orchestrator.services.versioning.contracts import (
    Capability,
    CapabilityDiff,
    CompatibilityCheck,
)

_EXPLAIN: dict[str, tuple[str, str | None]] = {
    "required_field_missing_on_create": (
        "Выбранная версия создаёт записи без поля {field}, а в текущей базе оно обязательно "
        "и не заполняется автоматически.",
        "Адаптировать форму: запрашивать значение у пользователя или заполнять его по "
        "подтверждённому правилу.",
    ),
    "required_field_default_needs_confirmation": (
        "Выбранная версия не передаёт обязательное поле {field}; база подставит значение "
        "по умолчанию, но его смысл для старых форм не подтверждён.",
        "Подтвердить, что значение по умолчанию подходит, или адаптировать форму.",
    ),
    "required_field_filled_technically": (
        "Поле {field} обязательно, но база заполняет его сама (идентификатор или время).",
        None,
    ),
    "read_compatible": (
        "Выбранная версия может читать таблицу {table}: все её поля есть в текущей базе.",
        None,
    ),
    "table_missing": (
        "Выбранная версия читает таблицу {table}, которой больше нет в текущей базе.",
        "Адаптировать чтение к новой структуре данных.",
    ),
    "column_missing": (
        "Выбранная версия читает поле {field}, которого больше нет в текущей базе.",
        "Адаптировать чтение к новой структуре данных.",
    ),
    "column_type_changed": (
        "У поля {field} изменился тип ({detail}); старые формы могут записывать значения "
        "в прежнем формате.",
        "Проверить преобразование значений или адаптировать форму.",
    ),
    "enum_values_changed": (
        "У поля {field} изменился список допустимых значений.",
        "Проверить, что старые формы используют только допустимые значения.",
    ),
    "column_meaning_changed": (
        "У поля {field} изменилось описанное назначение.",
        "Подтвердить, что старая версия использует поле в том же смысле.",
    ),
    "column_became_required": (
        "Поле {field} стало обязательным; старая версия могла оставлять его пустым.",
        "Адаптировать форму, чтобы значение всегда передавалось.",
    ),
    "json_keys_lost": (
        "Старая форма может перезаписать JSON-поле {field} без новых ключей.",
        "Адаптировать сохранение, чтобы менялись только свои ключи.",
    ),
    "owner_rule_changed": (
        "У таблицы {table} изменилось правило владельца записей.",
        "Проверить доступ разных пользователей в адаптированной версии.",
    ),
    "keys_or_relations_changed": (
        "У таблицы {table} изменились ключи или связи.",
        "Проверить создание и удаление связанных записей в адаптированной версии.",
    ),
    "check_unchanged": ("Ограничение {object} совпадает с выбранной версией.", None),
    "check_changed": (
        "Ограничение {object} изменилось по сравнению с выбранной версией; старые формы "
        "могут записывать значения, которые оно теперь отклоняет.",
        "Проверить запись старыми формами на копии данных или адаптировать форму.",
    ),
    "check_added": (
        "В текущей базе появилось ограничение {object}, которого не было в выбранной версии.",
        "Проверить запись старыми формами на копии данных или адаптировать форму.",
    ),
}

_UNSUPPORTED: dict[str, str] = {
    "trigger": "Триггер {object} выполняет свой код при записи; анализатор его пока не проверяет.",
    "default": "Поле {object} заполняется собственной функцией; анализатор её пока не проверяет.",
    "index": "Нестандартный индекс {object} (частичный или по выражению) пока не проверяется.",
    "constraint": "Ограничение {object} (отложенное, непроверенное или исключающее) пока "
    "не проверяется.",
    "column_behavior": "Поле или правило {object} (вычисляемое, домен, правило) пока не "
    "проверяется.",
    "composite_foreign_key": "Составная связь в {object} пока не проверяется.",
    "event_trigger": "В базе включены событийные триггеры; анализатор их пока не проверяет.",
    "relation": "В базе есть представления, наследование или внешние таблицы; анализатор их "
    "пока не проверяет.",
}


def _names(subject: str) -> dict[str, str]:
    parts = subject.split(".")
    table = parts[1] if len(parts) > 1 else subject
    return {"object": subject, "table": table, "field": ".".join(parts[1:]) or subject}


def checks_from_diagnostics(diagnostics: Iterable[Diagnostic]) -> list[CompatibilityCheck]:
    result = []
    for item in diagnostics:
        template, resolution = _EXPLAIN.get(
            item.code, ("Проверка {object}: " + item.code + ".", None)
        )
        values = {**_names(item.object), "detail": item.detail or ""}
        blocking = item.status in {"incompatible", "unknown"}
        result.append(CompatibilityCheck(
            code=item.code, status=item.status,
            severity="blocking" if blocking else "info",
            operation=item.operation, object=item.object, evidence="structural_rule",
            explanation=template.format(**values), resolution=resolution,
        ))
    return result


def checks_from_unsupported(objects: Iterable[Mapping[str, str]]) -> list[CompatibilityCheck]:
    return [
        CompatibilityCheck(
            code="analysis_not_supported", status="unknown", severity="blocking",
            operation="schema.analysis", object=item["object"], evidence="observed_catalog",
            explanation=_UNSUPPORTED.get(item["kind"], "Объект {object} пока не проверяется.")
            .format(object=item["object"]),
            resolution="Адаптировать версию с проверкой этого объекта на копии данных.",
        )
        for item in objects
    ]


def delete_warnings(tables: Iterable[str]) -> list[CompatibilityCheck]:
    return [
        CompatibilityCheck(
            code="delete_may_cascade_into_newer_data", status="unknown", severity="warning",
            operation=f"{table}.delete", object=f"public.{table}", evidence="structural_rule",
            explanation=f"Удаление записи {table} в выбранной версии может затронуть связанные "
            "данные, которых она не знает.",
        )
        for table in tables
    ]


# --- Capabilities: HTTP routes of the Next.js app router -------------------

_ROUTE_FILE = re.compile(r"^src/app/(?P<dir>(?:.+/)?)route\.(?:ts|js|tsx|jsx|mjs)$")
_EXPORT = re.compile(
    r"export\s+(?:async\s+)?(?:function\s+|const\s+)(GET|POST|PUT|PATCH|DELETE)\b"
)
# Routes of the managed MAX kit are served by the platform in every version.
_PLATFORM_PREFIXES = ("/api/omnia/", "/api/max/", "/api/health")


def route_capabilities(files: Mapping[str, str]) -> set[tuple[str, str]]:
    found: set[tuple[str, str]] = set()
    for path, text in files.items():
        match = _ROUTE_FILE.match(path)
        if not match:
            continue
        segments = [
            segment for segment in match["dir"].strip("/").split("/")
            if segment and not (segment.startswith("(") and segment.endswith(")"))
        ]
        route = "/" + "/".join(segments)
        if route.startswith(_PLATFORM_PREFIXES) or route == "/api/health":
            continue
        for method in _EXPORT.findall(text):
            found.add((method, route))
    return found


def capability_diff(current: Mapping[str, str], historical: Mapping[str, str]) -> CapabilityDiff:
    now, then = route_capabilities(current), route_capabilities(historical)
    return CapabilityDiff(
        lost=[Capability(method=m, path=p) for m, p in sorted(now - then)],
        restored=[Capability(method=m, path=p) for m, p in sorted(then - now)],
    )


_VERBS = {
    "GET": "чтение",
    "POST": "создание",
    "PUT": "изменение",
    "PATCH": "изменение",
    "DELETE": "удаление",
}


def describe_capability(capability: Capability) -> str:
    verb = _VERBS.get(capability.method, capability.method)
    return f"{verb}: {capability.method} {capability.path}"

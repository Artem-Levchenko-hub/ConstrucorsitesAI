"""Directional compatibility checks and capability diff (AV05, AV06 groundwork).

Turns structural diagnostics into owner-readable checks with a concrete object,
operation and way forward. An analyzer gap is ``unknown`` ("needs verification"),
never a proven conflict and never a ban on the generated schema.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass

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
# Same rules as apps/api/src/omnia_api/services/versioning_capabilities.py; the
# golden corpus tests/fixtures/versioning_v4/routes/corpus.json keeps them equal.

METHODS = frozenset({"GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"})
_ROUTE_FILE = re.compile(r"^src/app/(?P<dir>(?:.+/)?)route\.(?:ts|js|tsx|jsx|mjs|cjs)$")
_PLATFORM_PREFIXES = ("/api/omnia/", "/api/max/")
_PLATFORM_ROUTES = frozenset({"/api/health"})
_EXPORT_FUNCTION = re.compile(r"\bexport\s+(?:async\s+)?function\s*\*?\s*([A-Za-z_$][\w$]*)")
_EXPORT_DECLARATION = re.compile(r"\bexport\s+(?:const|let|var)\s+")
_STATEMENT_START = re.compile(
    r"(?:export|import|const|let|var|function|async|class|interface|type|enum|declare|"
    r"if|for|while|do|switch|try|return|throw)\b"
)
_IDENTIFIER = re.compile(r"[A-Za-z_$][\w$]*")
_EXPORT_LIST = re.compile(r"\bexport\s*\{([^}]*)\}")
_EXPORT_STAR = re.compile(r"\bexport\s*\*\s*from\b")


def _blank(text: str) -> str:
    return re.sub(r"[^\n]", " ", text)


def blank_comments_and_strings(text: str) -> str:
    """Comment bodies and string/template literal bodies become spaces."""
    out: list[str] = []
    i, n = 0, len(text)
    while i < n:
        ch = text[i]
        nxt = text[i + 1] if i + 1 < n else ""
        if ch == "/" and nxt == "/":
            j = text.find("\n", i)
            j = n if j == -1 else j
            out.append(" " * (j - i))
            i = j
            continue
        if ch == "/" and nxt == "*":
            j = text.find("*/", i + 2)
            j = n if j == -1 else j + 2
            out.append(_blank(text[i:j]))
            i = j
            continue
        if ch in ("'", '"', "`"):
            j = i + 1
            while j < n:
                c = text[j]
                if c == "\\":
                    j += 2
                    continue
                if c == ch or (ch != "`" and c == "\n"):
                    break
                j += 1
            closing = 1 if j < n and text[j] == ch else 0
            out.append(ch + _blank(text[i + 1 : j]) + (ch if closing else ""))
            i = j + closing
            continue
        out.append(ch)
        i += 1
    return "".join(out)


def _declaration(code: str, start: int) -> str:
    """The declarator list of one ``export const|let|var`` statement: up to the
    first ``;`` at bracket depth 0, or a line break followed by another statement
    (no-semicolon style)."""
    depth = 0
    i = start
    while i < len(code):
        char = code[i]
        if char in "([{":
            depth += 1
        elif char in ")]}":
            depth -= 1
            if depth < 0:
                break
        elif depth == 0:
            if char == ";":
                break
            if char == "\n":
                j = i + 1
                while j < len(code) and code[j].isspace():
                    j += 1
                if _STATEMENT_START.match(code, j):
                    break
        i += 1
    return code[start:i]


def _split_top_level(text: str) -> list[str]:
    parts: list[str] = []
    depth = 0
    start = 0
    for i, char in enumerate(text):
        if char in "([{":
            depth += 1
        elif char in ")]}":
            depth -= 1
        elif char == "," and depth == 0:
            parts.append(text[start:i])
            start = i + 1
    parts.append(text[start:])
    return parts


def _declared_names(declarators: str) -> set[str]:
    """Names bound by ``A = ..., B = ...``, ``{ A, B: local } = ...`` and
    ``[A] = ...`` (comments and strings are already blanked)."""
    names: set[str] = set()
    for part in _split_top_level(declarators):
        part = part.strip()
        if not part:
            continue
        if part[0] in "{[":
            end = part.find("}" if part[0] == "{" else "]")
            for member in part[1 : end if end >= 0 else len(part)].split(","):
                if ":" in member:  # `{ GET: local }` binds `local`, not GET
                    member = member.split(":", 1)[1]
                member = member.split("=", 1)[0].strip()
                if _IDENTIFIER.fullmatch(member):
                    names.add(member)
            continue
        match = _IDENTIFIER.match(part)
        if match:
            names.add(match.group(0))
    return names


def exported_methods(source: str) -> tuple[frozenset[str], bool]:
    """(HTTP methods a route module exports, whether an `export * from` is present)."""
    code = blank_comments_and_strings(source)
    names: set[str] = set()
    names.update(_EXPORT_FUNCTION.findall(code))
    for match in _EXPORT_DECLARATION.finditer(code):
        names.update(_declared_names(_declaration(code, match.end())))
    for group in _EXPORT_LIST.findall(code):
        for item in group.split(","):
            item = re.sub(r"^type\s+", "", item.strip())
            parts = item.split()
            if len(parts) == 3 and parts[1] == "as":
                names.add(parts[2])
            elif len(parts) == 1:
                names.add(parts[0])
    # `export * as ns from` never matches _EXPORT_STAR; a bare star is unresolvable.
    unresolved = _EXPORT_STAR.search(code) is not None
    return frozenset(name for name in names if name in METHODS), unresolved


def normalize_route(directory: str) -> str:
    segments: list[str] = []
    for segment in directory.strip("/").split("/"):
        if not segment or segment.startswith("@"):
            continue
        if segment.startswith("(") and segment.endswith(")"):
            continue
        if segment.startswith("[[...") and segment.endswith("]]"):
            segments.append("[[...]]")
        elif segment.startswith("[...") and segment.endswith("]"):
            segments.append("[...]")
        elif segment.startswith("[") and segment.endswith("]"):
            segments.append("[*]")
        else:
            segments.append(segment)
    return "/" + "/".join(segments)


def _canonical_source_path(path: str) -> str:
    return path.replace("\\", "/")


def is_platform_route_path(path: str) -> bool:
    match = _ROUTE_FILE.match(_canonical_source_path(path))
    if not match:
        return False
    route = normalize_route(match["dir"])
    return route.startswith(_PLATFORM_PREFIXES) or route in _PLATFORM_ROUTES


@dataclass(frozen=True)
class RouteManifest:
    capabilities: frozenset[tuple[str, str]]
    unresolved: frozenset[str]

    def routes(self) -> frozenset[str]:
        return frozenset(route for _, route in self.capabilities) | self.unresolved


def route_manifest(
    files: Mapping[str, str], *, ignore: frozenset[str] = frozenset()
) -> RouteManifest:
    capabilities: set[tuple[str, str]] = set()
    unresolved: set[str] = set()
    canonical_ignore = {_canonical_source_path(path) for path in ignore}
    for path, text in files.items():
        canonical_path = _canonical_source_path(path)
        if canonical_path in canonical_ignore or not isinstance(text, str):
            continue
        match = _ROUTE_FILE.match(canonical_path)
        if not match:
            continue
        route = normalize_route(match["dir"])
        methods, star = exported_methods(text)
        capabilities.update((method, route) for method in methods)
        if star:
            unresolved.add(route)
    return RouteManifest(frozenset(capabilities), frozenset(unresolved))


def route_capabilities(files: Mapping[str, str]) -> set[tuple[str, str]]:
    platform = frozenset(path for path in files if is_platform_route_path(path))
    return set(route_manifest(files, ignore=platform).capabilities)


def capability_diff(current: Mapping[str, str], historical: Mapping[str, str]) -> CapabilityDiff:
    """Functions the draft has now but the selected version lacks (``lost``) and
    the reverse (``restored``). Platform kit routes count only when a side changed
    them; an unresolvable ``export *`` route is listed with method ``*``."""
    untouched_platform = frozenset(
        path
        for path in current
        if is_platform_route_path(path) and historical.get(path) == current[path]
    )
    now = route_manifest(current, ignore=untouched_platform)
    then = route_manifest(historical, ignore=untouched_platform)
    lost = sorted(now.capabilities - then.capabilities)
    lost.extend(("*", route) for route in sorted(now.unresolved) if route not in then.routes())
    restored = sorted(then.capabilities - now.capabilities)
    restored.extend(
        ("*", route) for route in sorted(then.unresolved) if route not in now.routes()
    )
    return CapabilityDiff(
        lost=[Capability(method=m, path=p) for m, p in lost],
        restored=[Capability(method=m, path=p) for m, p in restored],
    )


_VERBS = {
    "*": "маршрут (export *)",
    "GET": "чтение",
    "POST": "создание",
    "PUT": "изменение",
    "PATCH": "изменение",
    "DELETE": "удаление",
}


def describe_capability(capability: Capability) -> str:
    verb = _VERBS.get(capability.method, capability.method)
    return f"{verb}: {capability.method} {capability.path}"

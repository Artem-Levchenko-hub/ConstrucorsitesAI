"""Build an editable privacy draft from a saved MAX schema, without reading visitor records."""

from __future__ import annotations

import json
import re
from collections.abc import Iterator, Mapping
from html import escape
from urllib.parse import urlsplit
from uuid import UUID

from fastapi.responses import HTMLResponse
from tree_sitter import Node, Parser

from yleum_api.schemas.max_studio import MaxPrivacyDraft, MaxProjectConfigPayload
from yleum_api.services import repo
from yleum_api.services.max_privacy_writes import product_writes
from yleum_api.services.max_typescript_source import (
    SCRIPT_SUFFIXES,
    language,
    node_text,
    resolve_local_import,
    static_imports,
    string_literal,
)

# These are labels for fields actually declared on a visitor's product table.
# An industry, requested feature, starter table or UI label is not such evidence.
_FIELD_LABELS = {
    "email": "электронная почта",
    "contactemail": "электронная почта",
    "phone": "номер телефона",
    "contactphone": "номер телефона",
    "firstname": "имя",
    "lastname": "фамилия",
    "birthdate": "дата рождения",
    "address": "адрес",
    "deliveryaddress": "адрес доставки",
    "dietarypreferences": "пищевые предпочтения",
    "foodpreferences": "пищевые предпочтения",
    "nutritiongoal": "цель питания",
    "favoritefoods": "любимые продукты",
    "excludedfoods": "исключённые продукты",
    "allergies": "указанные аллергии",
    "waterml": "количество выпитой воды",
    "waterintakeml": "количество выпитой воды",
    "mealtype": "приём пищи",
    "mealname": "название блюда",
    "waterintake": "количество выпитой воды",
    "dietaryrestrictions": "ограничения в питании",
    "mealplan": "план питания",
    "fooddiary": "записи дневника питания",
    "shoppinglist": "список покупок",
    "calories": "калорийность",
}
_USER_FIELDS = {"maxuserid", "userid", "customerid", "memberid", "accountid", "ownerid"}

_TECH_FIELDS = {"id", "createdat", "updatedat", "deletedat", "version"}
_NON_SOURCE_DIRS = {"__tests__", "tests", "test", "fixtures", "node_modules", "vendor", "dist"}
_PRODUCT_ROUTE = re.compile(
    r"(?:src/)?(?:app/api/(?!max/|omnia/|yleum-readiness/).+/route|pages/api/.+)\.[jt]sx?"
)
_DRIZZLE_META = re.compile(r"drizzle/meta/(?:_journal|\d+_snapshot)\.json")


def _descendants(root: Node, kind: str) -> Iterator[Node]:
    stack = [root]
    while stack:
        node = stack.pop()
        if node.type == kind:
            yield node
        stack.extend(reversed(node.named_children))


def _schema_inventory(files: Mapping[str, str]) -> tuple[dict[str, set[str]], set[str], bool]:
    """Follow the saved schema's module graph, including split/re-exported tables."""
    pending = [p for p in ("src/lib/db/schema.ts", "lib/db/schema.ts") if p in files]
    visited: set[str] = set()
    tables: dict[str, set[str]] = {}
    metadata_fields, partial = _migration_fields(files)
    labels: set[str] = set()
    parser = Parser(language(False))
    while pending:
        path = pending.pop()
        if path in visited:
            continue
        visited.add(path)
        root = parser.parse(files[path].encode()).root_node
        if root.has_error:
            partial = True
            continue
        constructors: set[str] = set()
        for module, statement in static_imports(root):
            if target := resolve_local_import(path, module, files):
                if target not in visited:
                    pending.append(target)
            if module != "drizzle-orm/pg-core":
                continue
            for spec in _descendants(statement, "import_specifier"):
                if node_text(spec.child_by_field_name("name")) == "pgTable":
                    constructors.add(node_text(spec.child_by_field_name("alias")) or "pgTable")
        for name, fields in _personal_table_fields(root, constructors):
            tables[name] = fields
            metadata_fields.append(fields)
    for fields in metadata_fields:
        labels.update(_FIELD_LABELS[key] for key in fields if key in _FIELD_LABELS)
        partial |= bool(fields - _FIELD_LABELS.keys() - _USER_FIELDS - _TECH_FIELDS)
    return tables, labels, partial


def _migration_fields(files: Mapping[str, str]) -> tuple[list[set[str]], bool]:
    """Use only the last journalled Drizzle snapshot; do not read SQL seed values."""
    journal = files.get("drizzle/meta/_journal.json")
    if journal is None:
        return [], False
    result: list[set[str]] = []
    try:
        entries = json.loads(journal)["entries"]
        if not entries:
            return [], False
        index = entries[-1]["idx"]
        if type(index) is not int or index < 0:
            return [], True
        snapshot = json.loads(files[f"drizzle/meta/{index:04d}_snapshot.json"])
        if snapshot.get("dialect") != "postgresql":
            return [], True
        for table in snapshot["tables"].values():
            if table["name"].startswith(("max_", "yleum_", "__")):
                continue
            fields = {col["name"].replace("_", "").lower() for col in table["columns"].values()}
            if fields & _USER_FIELDS:
                result.append(fields)
    except (KeyError, ValueError, TypeError, IndexError, AttributeError, RecursionError):
        return [], True
    return result, False


def _personal_table_fields(root: Node, constructors: set[str]) -> Iterator[tuple[str, set[str]]]:
    if not constructors:
        return
    for statement in root.named_children:
        decl = (
            statement.child_by_field_name("declaration")
            if statement.type == "export_statement"
            else statement
        )
        if decl is None or decl.type not in {"lexical_declaration", "variable_declaration"}:
            continue
        for binding in decl.named_children:
            call = binding.child_by_field_name("value")
            if call is None or call.type != "call_expression":
                continue
            if node_text(call.child_by_field_name("function")) not in constructors:
                continue
            args = call.child_by_field_name("arguments")
            if args is None or args.named_child_count < 2:
                continue
            table, fields = args.named_children[:2]
            name = string_literal(table)
            if not name or name.startswith(("max_", "yleum_", "__")) or fields.type != "object":
                continue
            names = {
                node_text(pair.child_by_field_name("key")).strip("'\"").replace("_", "").lower()
                for pair in fields.named_children
                if pair.type == "pair"
            }
            if names & _USER_FIELDS:
                yield node_text(binding.child_by_field_name("name")), names


def _object_properties(obj: Node) -> dict[str, Node]:
    return {
        node_text(pair.child_by_field_name("key")): value
        for pair in obj.named_children
        if pair.type == "pair" and (value := pair.child_by_field_name("value")) is not None
    }


def _executed_queries(handler: Node) -> Iterator[Node]:
    """Only a directly awaited or returned query executes a Drizzle builder."""
    body = handler.child_by_field_name("body")
    if body is None:
        return
    for statement in body.named_children:
        if statement.type == "return_statement":
            if statement.named_children:
                yield statement.named_children[0]
            break
        if statement.type == "throw_statement":
            break
        if statement.type == "expression_statement":
            expressions = statement.named_children
        elif statement.type == "lexical_declaration":
            expressions = [
                value
                for binding in statement.named_children
                if (value := binding.child_by_field_name("value")) is not None
            ]
        else:
            continue
        for expr in expressions:
            if expr.type == "await_expression" and expr.named_children:
                yield expr.named_children[0]


def _written_fields(
    query: Node, bindings: Mapping[str, str], tables: Mapping[str, set[str]]
) -> set[str]:
    if query.type == "await_expression" and query.named_children:
        query = query.named_children[0]
    while query.type == "call_expression":
        member = query.child_by_field_name("function")
        args = query.child_by_field_name("arguments")
        if member is None or args is None or member.type != "member_expression":
            break
        receiver = member.child_by_field_name("object")
        if receiver is None or receiver.type != "call_expression":
            break
        operation = node_text(member.child_by_field_name("property"))
        if operation in {"returning", "where", "onConflictDoNothing", "onConflictDoUpdate"}:
            query = receiver
            continue
        if operation not in {"values", "set"}:
            break
        method, table_args = (
            receiver.child_by_field_name("function"),
            receiver.child_by_field_name("arguments"),
        )
        if method is None or table_args is None or table_args.named_child_count != 1:
            break
        target = table_args.named_children[0]
        if not (
            bindings.get(node_text(method.child_by_field_name("object"))) == "db"
            and node_text(method.child_by_field_name("property"))
            == ("insert" if operation == "values" else "update")
            and bindings.get(node_text(target.child_by_field_name("object"))) == "schema"
            and args.named_child_count == 1
            and args.named_children[0].type == "object"
        ):
            break
        allowed = tables.get(node_text(target.child_by_field_name("property")), set())
        result = set()
        for prop in args.named_children[0].named_children:
            if prop.type == "shorthand_property_identifier":
                key = node_text(prop)
            elif prop.type == "pair" and node_text(prop.child_by_field_name("value")) not in {
                "undefined",
                "null",
            }:
                key = node_text(prop.child_by_field_name("key")).strip("'\"")
            else:
                continue
            name = key.replace("_", "").lower()
            if name in allowed and name in _FIELD_LABELS:
                result.add(_FIELD_LABELS[name])
        return result
    return set()


def _route_writes(files: Mapping[str, str], tables: Mapping[str, set[str]]) -> set[str]:
    labels: set[str] = set()
    if not tables:
        return labels
    for path, text in files.items():
        if not _PRODUCT_ROUTE.fullmatch(path):
            continue
        root = Parser(language(path.endswith("x"))).parse(text.encode()).root_node
        if root.has_error:
            continue
        bindings = {}
        for module, statement in static_imports(root):
            if module != "@/lib/db":
                continue
            for spec in _descendants(statement, "import_specifier"):
                name = node_text(spec.child_by_field_name("name"))
                bindings[node_text(spec.child_by_field_name("alias")) or name] = name
        for statement in root.named_children:
            handler = statement.child_by_field_name("declaration")
            if handler is None or node_text(handler.child_by_field_name("name")) not in {
                "POST",
                "PUT",
                "PATCH",
            }:
                continue
            parameters = handler.child_by_field_name("parameters")
            hidden = (
                {
                    node_text(node)
                    for kind in ("required_parameter", "optional_parameter")
                    for param in _descendants(parameters, kind)
                    for node in _descendants(param, "identifier")
                }
                if parameters
                else set()
            )
            hidden.update(
                node_text(var.child_by_field_name("name"))
                for var in _descendants(handler, "variable_declarator")
            )
            active = {key: value for key, value in bindings.items() if key not in hidden}
            for query in _executed_queries(handler):
                labels.update(_written_fields(query, active, tables))
    return labels


def _max_session_facts(source: str) -> tuple[bool, bool]:
    """Check actual writes in the protected MAX login handler, never the preview or SDK types."""
    root = Parser(language(False)).parse(source.encode()).root_node
    cookie = opened = False
    for statement in root.named_children:
        handler = statement.child_by_field_name("declaration")
        if handler is None or node_text(handler.child_by_field_name("name")) != "POST":
            continue
        for call in _descendants(handler, "call_expression"):
            fn, args = call.child_by_field_name("function"), call.child_by_field_name("arguments")
            if fn is None or args is None:
                continue
            values = args.named_children
            if node_text(fn) == "response.cookies.set" and len(values) == 3:
                options = _object_properties(values[2])
                cookie |= (
                    node_text(values[0]) == "MAX_SESSION_COOKIE"
                    and node_text(values[1]) == "session.value"
                    and node_text(options.get("httpOnly")) == "true"
                )
            elif node_text(fn.child_by_field_name("property")) == "values" and len(values) == 1:
                insert = fn.child_by_field_name("object")
                if insert is None or insert.type != "call_expression":
                    continue
                target = insert.child_by_field_name("arguments")
                props = _object_properties(values[0])
                opened |= (
                    node_text(insert.child_by_field_name("function")) == "tx.insert"
                    and target is not None
                    and target.named_child_count == 1
                    and node_text(target.named_children[0]) == "schema.maxAnalyticsEvents"
                    and string_literal(props.get("eventName")) == "app_open"
                    and node_text(props.get("maxUserId")) == "user.id"
                )
    return cookie, opened


def _privacy_source(path: str) -> bool:
    if path == "tsconfig.json" or _DRIZZLE_META.fullmatch(path):
        return True
    parts = path.split("/")
    if any(part.startswith(".") or part in _NON_SOURCE_DIRS for part in parts):
        return False
    return path.endswith(SCRIPT_SUFFIXES) and not re.search(
        r"\.(?:spec|test|d)\.[cm]?[jt]sx?$", path
    )


def build_privacy_draft(
    config: MaxProjectConfigPayload, files: Mapping[str, str]
) -> MaxPrivacyDraft:
    tables, declared, partial_schema = _schema_inventory(files)
    written = _route_writes(files, tables)
    writes = product_writes(files)
    cookie, opened = _max_session_facts(files.get("src/app/api/max/session/route.ts", ""))
    paragraphs = [f"Политика конфиденциальности приложения «{config.app_name}»"]
    if operator := config.operator.legal_name.strip():
        paragraphs.append(f"Владелец приложения — {operator}.")
    paragraphs.append(
        "При открытии приложения в MAX используется идентификатор пользователя. "
        "Он позволяет связать действия в приложении с нужной учётной записью."
    )
    if cookie:
        paragraphs.append("Для поддержания входа на устройстве сохраняется сеансовый файл cookie.")
    if opened:
        paragraphs.append(
            "При входе в опубликованное приложение регистрируется событие открытия, "
            "связанное с идентификатором пользователя MAX."
        )
    for lead, group in (
        (
            "На сервере приложения могут сохраняться следующие данные",
            sorted(written | set(writes.server)),
        ),
        ("В браузере на устройстве могут сохраняться следующие данные", writes.browser),
        ("На время работы вкладки в браузере могут сохраняться следующие данные", writes.session),
    ):
        if group:
            paragraphs.append(lead + ": " + ", ".join(group) + ".")
    if config.support.email:
        paragraphs.append(
            "По вопросам обработки данных, их уточнения или удаления можно обратиться "
            f"к владельцу приложения по адресу {config.support.email}."
        )
    if written or writes.server or writes.browser or writes.session:
        review = "Сверьте перечисленные категории с тем, что пользователь вводит и сохраняет."
    else:
        review = (
            "В проверенных экранах и схеме этой версии сохранение дополнительных данных "
            "пользователя не подтверждено. Названия разделов сами по себе не вошли в политику."
        )
    if pending := sorted(declared - written - set(writes.server)):
        review += (
            " В схеме есть поля, для которых запись из приложения ещё нужно проверить: "
            + ", ".join(pending)
            + "."
        )
    if partial_schema or not writes.complete or any(_PRODUCT_ROUTE.fullmatch(p) for p in files):
        review += " Проверьте также собственные серверные обработчики и нестандартные хранилища."
    return MaxPrivacyDraft(text="\n\n".join(paragraphs), review_note=review)


def saved_privacy_draft(
    project_id: UUID,
    commit: str,
    config: MaxProjectConfigPayload,
) -> MaxPrivacyDraft:
    files = repo.read_files(project_id, commit, include=_privacy_source)
    return build_privacy_draft(config, files)


def has_public_release(project_id: UUID, deployment: dict[str, object]) -> bool:
    active = deployment.get("active_publication")
    if deployment.get("project_id") != str(project_id) or not isinstance(active, dict):
        return False
    try:
        UUID(active["release_id"])
        UUID(active["snapshot_id"])
        url = urlsplit(active["prod_url"])
        return bool(
            url.scheme == "https"
            and url.hostname
            and re.fullmatch(r"[0-9a-f]{40}", active["commit_sha"])
        )
    except (KeyError, TypeError, ValueError, AttributeError):
        return False


def approved_policy_page(text: str) -> HTMLResponse:
    # Render only owner-approved plain text. Document lives on the platform origin.
    body = "".join(f"<p>{escape(part)}</p>" for part in re.split(r"\n[ \t]*\n", text))
    return HTMLResponse(
        '<!doctype html><html lang="ru"><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        "<title>Политика конфиденциальности</title>"
        "<style>body{max-width:44rem;margin:auto;padding:1rem;font:1rem/1.6 system-ui;"
        "color:#182027;background:white}p{white-space:pre-line;overflow-wrap:anywhere}</style>"
        f'<main aria-label="Политика конфиденциальности">{body}</main></html>',
        headers={
            "Cache-Control": "private, no-store",
            "X-Content-Type-Options": "nosniff",
            "Referrer-Policy": "no-referrer",
            "X-Robots-Tag": "noindex",
            "Content-Security-Policy": (
                "default-src 'none'; style-src 'unsafe-inline'; base-uri 'none'; "
                "form-action 'none'; frame-ancestors 'self' https://max.ru https://web.max.ru; "
                "sandbox"
            ),
        },
    )

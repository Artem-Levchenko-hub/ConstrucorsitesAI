"""Build an editable privacy draft from a saved MAX schema, without reading visitor records."""

from __future__ import annotations

import re
from collections.abc import Iterator
from html import escape
from urllib.parse import urlsplit
from uuid import UUID

from fastapi.responses import HTMLResponse
from tree_sitter import Node, Parser

from yleum_api.schemas.max_studio import MaxProjectConfigPayload
from yleum_api.services import repo
from yleum_api.services.max_typescript_source import (
    language,
    node_text,
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
    "calories": "калорийность",
}
_USER_FIELDS = {"maxuserid", "userid", "customerid", "memberid", "accountid", "ownerid"}


def _descendants(root: Node, kind: str) -> Iterator[Node]:
    stack = [root]
    while stack:
        node = stack.pop()
        if node.type == kind:
            yield node
        else:
            stack.extend(reversed(node.named_children))


def _field_labels(schema: bytes) -> list[str]:
    root = Parser(language(False)).parse(schema).root_node
    if root.has_error:
        return []
    constructors = set()
    for module, statement in static_imports(root):
        if module == "drizzle-orm/pg-core":
            for spec in _descendants(statement, "import_specifier"):
                if node_text(spec.child_by_field_name("name")) == "pgTable":
                    constructors.add(node_text(spec.child_by_field_name("alias")) or "pgTable")
    labels: set[str] = set()
    declarations = (
        node.child_by_field_name("declaration") if node.type == "export_statement" else node
        for node in root.named_children
    )
    for decl in (
        child
        for declaration in declarations
        if declaration is not None
        and declaration.type in {"lexical_declaration", "variable_declaration"}
        for child in declaration.named_children
        if child.type == "variable_declarator"
    ):
        call = decl.child_by_field_name("value")
        if (
            call is None
            or call.type != "call_expression"
            or (node_text(call.child_by_field_name("function")) not in constructors)
        ):
            continue
        args = call.child_by_field_name("arguments")
        if args is None or args.named_child_count < 2:
            continue
        table = string_literal(args.named_children[0])
        fields = args.named_children[1]
        if not table or table.startswith(("max_", "yleum_", "__")) or fields.type != "object":
            continue
        names = {
            node_text(pair.child_by_field_name("key")).strip("'\"").replace("_", "").lower()
            for pair in fields.named_children
            if pair.type == "pair"
        }
        if names & _USER_FIELDS:
            labels.update(_FIELD_LABELS[key] for key in names if key in _FIELD_LABELS)
    return sorted(labels)


def build_privacy_draft(config: MaxProjectConfigPayload, schema: bytes) -> str:
    paragraphs = [f"Политика конфиденциальности приложения «{config.app_name}»"]
    if operator := config.operator.legal_name.strip():
        paragraphs.append(f"Владелец приложения — {operator}.")
    paragraphs.append(
        "При открытии приложения в MAX используется идентификатор пользователя. "
        "Он позволяет связать действия в приложении с нужной учётной записью."
    )
    if fields := _field_labels(schema):
        paragraphs.append(
            "В личных записях приложения предусмотрены следующие данные: " + ", ".join(fields) + "."
        )
    if config.support.email:
        paragraphs.append(
            "По вопросам обработки данных, их уточнения или удаления можно обратиться "
            f"к владельцу приложения по адресу {config.support.email}."
        )
    return "\n\n".join(paragraphs)


def saved_privacy_draft(project_id: UUID, commit: str, config: MaxProjectConfigPayload) -> str:
    schema = repo.read_file(
        project_id,
        commit,
        "src/lib/db/schema.ts",
        max_bytes=repo.MAX_FILE_BYTES,
    )
    return build_privacy_draft(config, schema or b"")


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

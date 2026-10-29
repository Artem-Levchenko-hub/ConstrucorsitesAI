"""Explicit adoption of the one reviewed legacy order shape; no generic SQL adapter."""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
from collections.abc import Iterator
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession
from tree_sitter import Language, Node, Parser

from yleum_api.core.errors import ApiError
from yleum_api.models.project import Project
from yleum_api.models.snapshot import Snapshot
from yleum_api.services import repo

LEGACY_ORDER_SQL_PATH = "drizzle/0090_yleum_legacy_orders_management.sql"
LEGACY_ORDER_METADATA_PATH = ".omnia/legacy-orders.json"
LEGACY_ORDER_MANAGED_PATHS = frozenset({LEGACY_ORDER_SQL_PATH, LEGACY_ORDER_METADATA_PATH})
_RECIPE_PATH = "compat/legacy-orders-owner-mirror.sql.template"
_TABLE = re.compile(r"[a-z][a-z0-9_]{0,47}")
_BASE_COLUMNS = ("id", "owner_id", "customer_name", "items", "total_rub", "status", "operation_key")
_PICKUP_COLUMNS = ("pickup_option", "pickup_at", "barista_comment")
_SELECT_COLUMNS = frozenset((*_BASE_COLUMNS, "created_at", *_PICKUP_COLUMNS))


def validate_legacy_order_table(value: object) -> str:
    """Reject SQL syntax, system/platform names and noncanonical spellings."""
    if (
        not isinstance(value, str)
        or _TABLE.fullmatch(value) is None
        or value.startswith(("max_", "pg_", "yleum_"))
    ):
        raise ValueError("legacy orders table must be a bounded product SQL identifier")
    return value


def _text(node: Node | None) -> str:
    return (node.text or b"").decode() if node is not None else ""


def _nodes(root: Node) -> Iterator[Node]:
    pending = [root]
    while pending:
        node = pending.pop()
        yield node
        pending.extend(reversed(node.named_children))


def _literal(node: Node | None, columns: str | None = None) -> str | None:
    if node is None or node.type not in {"string", "template_string"}:
        return None
    # Escape processing and arbitrary interpolation would expand this recognizer
    # into a SQL interpreter. Only the fixed COLUMNS projection is supported.
    value = _text(node)[1:-1]
    if "\\" in value:
        return None
    if node.type == "template_string":
        substitutions = [
            child for child in node.named_children if child.type == "template_substitution"
        ]
        if any(_text(child) != "${COLUMNS}" for child in substitutions) or (
            substitutions and columns is None
        ):
            return None
        value = value.replace("${COLUMNS}", columns or "")
    return value


def _projection(value: str) -> bool:
    names = tuple(part.strip() for part in value.split(","))
    return bool(names) and len(set(names)) == len(names) and set(names) <= _SELECT_COLUMNS


def detect_legacy_order_table(source: str) -> str | None:
    """Recognize static pool queries in an accepted orders route, never execute source.

    This only selects the explicit recipe. The transaction's schema guard still
    verifies physical columns/types, legacy values and preexisting DB objects.
    Unknown queries, dynamic names, conflicting tables and syntax fail closed.
    """
    if not isinstance(source, str) or len(source.encode("utf-8")) > 256 * 1024:
        return None
    import tree_sitter_typescript

    root = (
        Parser(Language(tree_sitter_typescript.language_typescript()))
        .parse(
            source.encode("utf-8"),
        )
        .root_node
    )
    if root.has_error:
        return None
    nodes = list(_nodes(root))
    if len(nodes) > 50_000:
        return None
    imports = [
        node
        for node in root.named_children
        if node.type == "import_statement"
        and _text(node.child_by_field_name("source")) in {'"@/lib/db"', "'@/lib/db'"}
    ]
    if len(imports) != 1 or not re.search(r"\{\s*pool\s*\}", _text(imports[0])):
        return None
    declarations = [
        node
        for node in nodes
        if node.type == "variable_declarator"
        and _text(node.child_by_field_name("name")) == "COLUMNS"
    ]
    columns = (
        _literal(declarations[0].child_by_field_name("value")) if (len(declarations) == 1) else None
    )
    if columns is None or not _projection(columns):
        return None
    routes: dict[str, Node] = {}
    for node in root.named_children:
        if node.type != "export_statement":
            continue
        declaration = node.child_by_field_name("declaration")
        if declaration is not None and declaration.type == "function_declaration":
            name = _text(declaration.child_by_field_name("name"))
            if name in {"GET", "POST"}:
                if name in routes:
                    return None
                routes[name] = declaration
    if set(routes) != {"GET", "POST"}:
        return None
    tables: set[str] = set()
    seen: set[tuple[str, str]] = set()
    for node in nodes:
        if node.type != "call_expression" or _text(node.child_by_field_name("function")) != (
            "pool.query"
        ):
            continue
        route = next(
            (
                name
                for name, declaration in routes.items()
                if declaration.start_byte <= node.start_byte < declaration.end_byte
            ),
            None,
        )
        args = node.child_by_field_name("arguments")
        query = _literal(args.named_children[0], columns) if args and args.named_children else None
        if route is None or query is None:
            return None
        query = " ".join(query.split())
        select = re.fullmatch(
            r"SELECT (?P<columns>[a-z_, ]+) FROM (?P<table>[a-z][a-z0-9_]*) "
            r"WHERE owner_id\s*=\s*\$1(?: AND operation_key\s*=\s*\$2)?"
            r"(?: ORDER BY created_at DESC LIMIT 100)?",
            query,
        )
        insert = re.fullmatch(
            r"INSERT INTO (?P<table>[a-z][a-z0-9_]*) \((?P<columns>[a-z_, ]+)\) "
            r"VALUES \((?P<values>[^)]+)\) ON CONFLICT \(owner_id, operation_key\) "
            r"DO NOTHING RETURNING (?P<returning>[a-z_, ]+)",
            query,
        )
        match = select or insert
        if match is None or not _projection(match["columns"]):
            return None
        try:
            table = validate_legacy_order_table(match["table"])
        except ValueError:
            return None
        if insert:
            names = tuple(part.strip() for part in insert["columns"].split(","))
            if names not in {_BASE_COLUMNS, (*_BASE_COLUMNS, *_PICKUP_COLUMNS)} or (
                not _projection(insert["returning"]) or route != "POST"
            ):
                return None
            values = re.sub(r"\s", "", insert["values"])
            expected = "$1,$2,$3,$4::jsonb,$5,'accepted',$6"
            if names != _BASE_COLUMNS:
                expected += ",$7,$8,$9"
            if values != expected:
                return None
        tables.add(table)
        seen.add((route, "select" if select else "insert"))
    return (
        next(iter(tables))
        if len(tables) == 1
        and {
            ("GET", "select"),
            ("POST", "insert"),
        }
        <= seen
        else None
    )


def render_legacy_order_management(project_id: UUID | str, table: str) -> dict[str, str]:
    """Render trusted SQL only from the packaged reviewed template."""
    from yleum_api.services.max_project_kit import _template_file

    project = UUID(str(project_id))
    if str(project) != str(project_id):
        raise ValueError("legacy orders project must be a canonical UUID")
    table = validate_legacy_order_table(table)
    template = _template_file(_RECIPE_PATH)
    parameters = set(re.findall(r"__[A-Z_]+__", template))
    if parameters != {"__PROJECT_ID__", "__ORDERS_TABLE__"}:
        raise ValueError("legacy orders template has an unexpected parameter contract")
    sql = template.replace("__PROJECT_ID__", str(project)).replace("__ORDERS_TABLE__", table)
    metadata = {
        "protocol": 1,
        "project_id": str(project),
        "orders_table": table,
        "template_sha256": hashlib.sha256(template.encode()).hexdigest(),
    }
    return {
        LEGACY_ORDER_SQL_PATH: sql,
        LEGACY_ORDER_METADATA_PATH: json.dumps(metadata, sort_keys=True, separators=(",", ":"))
        + "\n",
    }


async def read_legacy_order_table(
    session: AsyncSession,
    project_id: UUID,
    owner_id: UUID,
) -> str | None:
    """Inspect only the owner's accepted snapshot; unknown custom history fails closed."""
    from yleum_api.services.max_project_kit import _template_file

    project = await session.get(Project, project_id)
    if project is None or project.owner_id != owner_id or project.id != project_id:
        raise ApiError("not_found", "Проект не найден", 404)
    if project.current_snapshot_id is None:
        return None
    snapshot = await session.get(Snapshot, project.current_snapshot_id)
    if (
        snapshot is None
        or snapshot.project_id != project_id
        or (snapshot.id != project.current_snapshot_id)
    ):
        raise ApiError("conflict", "Не удалось проверить текущую версию приложения", 409)
    try:
        raw = await asyncio.to_thread(
            repo.read_file,
            project_id,
            snapshot.commit_sha,
            "src/app/api/orders/route.ts",
        )
    except FileNotFoundError:
        return None
    except Exception:
        raise ApiError(
            "unavailable", "Не удалось прочитать текущую версию приложения", 503
        ) from None
    if raw is None or raw == b"":
        return None
    source = None
    if isinstance(raw, bytes) and len(raw) <= 256 * 1024:
        try:
            source = raw.decode("utf-8", errors="strict")
        except UnicodeError:
            pass
    if source is not None:
        table = detect_legacy_order_table(source)
        if table is not None:
            try:
                files = await asyncio.to_thread(repo.read_files, project_id, snapshot.commit_sha)
            except Exception:
                raise ApiError(
                    "unavailable",
                    "Не удалось проверить миграции приложения",
                    503,
                ) from None
            expected = render_legacy_order_management(project_id, table)[LEGACY_ORDER_SQL_PATH]
            if (
                not isinstance(files, dict)
                or len(files) > repo.MAX_FILES
                or any(
                    not isinstance(path, str)
                    or len(path) > 1024
                    or (
                        path.startswith("drizzle/")
                        and path.count("/") == 1
                        and path.endswith(".sql")
                        and path >= LEGACY_ORDER_SQL_PATH
                        and not (path == LEGACY_ORDER_SQL_PATH and content == expected)
                    )
                    for path, content in files.items()
                )
            ):
                raise ApiError(
                    "unsupported",
                    "Порядок миграций приложения требует отдельного подключения",
                    409,
                )
            return table
        if (
            source.strip()
            == _template_file(
                "src/app/api/omnia/business/[resource]/route.ts",
            ).strip()
        ):
            return None
    raise ApiError(
        "unsupported",
        "Для существующих заказов требуется проверенное подключение",
        409,
    )

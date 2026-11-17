"""Helpers for picking a meaningful MAX runtime route during signed preview proof."""

from __future__ import annotations

from collections.abc import Mapping

import tree_sitter_typescript
from tree_sitter import Language, Node, Parser

_MAX_HOME_PAGE = "src/app/page.tsx"
MAX_PRODUCT_READINESS_PATH = "/api/yleum-readiness"
_ROUTE_LANGUAGE = Language(tree_sitter_typescript.language_typescript())
_PORTABLE_ROOT_FALLBACKS: tuple[tuple[str, str], ...] = (
    ("/support", "src/app/support/page.tsx"),
    ("/legal/privacy", "src/app/legal/privacy/page.tsx"),
    ("/legal/terms", "src/app/legal/terms/page.tsx"),
)


def _exports_get(statement: Node) -> bool:
    if statement.type != "export_statement" or statement.has_error:
        return False
    declaration = statement.child_by_field_name("declaration")
    if declaration is None or any(child.type == "default" for child in statement.children):
        return False
    handlers: tuple[Node, ...]
    if declaration.type == "function_declaration":
        handlers = (declaration,)
    elif declaration.type == "lexical_declaration" and declaration.children[0].type == "const":
        handlers = tuple(
            child for child in declaration.named_children
            if child.type == "variable_declarator"
            and child.child_by_field_name("value") is not None
        )
    else:
        return False
    return any(
        (name := handler.child_by_field_name("name")) is not None
        and name.type == "identifier" and name.text == b"GET"
        for handler in handlers
    )


def resolve_max_business_probe_path(files: Mapping[str, str]) -> str | None:
    """Opt in via the reserved, read-only product endpoint, without guessing API paths."""
    for extension in ("ts", "js"):
        source = files.get(f"src/app{MAX_PRODUCT_READINESS_PATH}/route.{extension}")
        if source is None:
            continue
        module = Parser(_ROUTE_LANGUAGE).parse(source.encode("utf-8")).root_node
        if any(_exports_get(statement) for statement in module.named_children):
            return MAX_PRODUCT_READINESS_PATH
        break
    return None


def _nonempty_file(files: Mapping[str, str], path: str) -> bool:
    return bool(str(files.get(path) or "").strip())


def resolve_max_runtime_probe_paths(
    files: Mapping[str, str],
    *,
    requested_path: str = "/",
) -> tuple[str, tuple[str, ...]]:
    """Keep ``/`` strict once the home page exists, but offer stable fallbacks before it does.

    MAX starter v13 intentionally has no product home page. During that window we
    still need a deterministic signed-preview proof without pretending that the
    final home page already renders. The legal/support routes are platform-owned
    and always present in the seeded core, so they are safe fallbacks for an
    intermediate runtime/data-plane check.
    """

    normalized_path = str(requested_path or "/")
    if normalized_path != "/" or _nonempty_file(files, _MAX_HOME_PAGE):
        return normalized_path, ()
    fallback_paths = tuple(
        route
        for route, source_path in _PORTABLE_ROOT_FALLBACKS
        if _nonempty_file(files, source_path)
    )
    return normalized_path, fallback_paths


__all__ = [
    "MAX_PRODUCT_READINESS_PATH",
    "resolve_max_business_probe_path",
    "resolve_max_runtime_probe_paths",
]

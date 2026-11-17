"""Read local TypeScript modules from an in-memory MAX source snapshot."""

from __future__ import annotations

import posixpath
from collections.abc import Iterator, Mapping
from functools import cache

import tree_sitter_typescript
from tree_sitter import Language, Node

SCRIPT_SUFFIXES = (".tsx", ".ts", ".jsx", ".js", ".mts", ".cts", ".mjs", ".cjs")
NEXT_ROUTE_SUFFIXES = (".tsx", ".ts", ".jsx", ".js")
RESERVED_API_SEGMENTS = frozenset({"auth", "max", "omnia", "yleum-readiness"})


def next_router_roots(files: Mapping[str, str]) -> dict[str, str]:
    roots: dict[str, str] = {}
    for directory in ("app/", "pages/"):
        # Next resolves the root directory before the src/ variant.
        for root in (directory, "src/" + directory):
            if any(path.startswith(root) for path in files):
                roots[directory] = root
                break
    return roots


@cache
def language(jsx: bool) -> Language:
    return Language(
        tree_sitter_typescript.language_tsx()
        if jsx
        else tree_sitter_typescript.language_typescript()
    )


def node_text(node: Node | None) -> str:
    return (node.text or b"").decode() if node is not None else ""


def string_literal(node: Node | None) -> str:
    if node is not None and node.type == "jsx_expression" and node.named_child_count == 1:
        node = node.named_children[0]
    if node is None or node.type != "string" or node.named_child_count != 1:
        return ""
    fragment = node.named_children[0]
    return node_text(fragment) if fragment.type == "string_fragment" else ""


def static_imports(root: Node) -> Iterator[tuple[str, Node]]:
    for statement in root.named_children:
        if statement.type not in {"import_statement", "export_statement"} or any(
            child.type == "type" for child in statement.children
        ):
            continue
        if source := string_literal(statement.child_by_field_name("source")):
            yield source, statement


def resolve_local_import(
    path: str,
    specifier: str,
    files: Mapping[str, str],
    *,
    aliases: Mapping[str, str] | None = None,
) -> str | None:
    if specifier.startswith(("./", "../")):
        base = posixpath.join(posixpath.dirname(path), specifier)
    else:
        prefixes = {"@/": "src/"} if aliases is None else aliases
        prefix = next((prefix for prefix in prefixes if specifier.startswith(prefix)), None)
        if prefix is None:
            return None
        base = posixpath.join(prefixes[prefix], specifier[len(prefix) :])
    base = posixpath.normpath(base)
    if base in files:
        return base
    return next(
        (
            stem + suffix
            for stem in (base, f"{base}/index")
            for suffix in SCRIPT_SUFFIXES
            if stem + suffix in files
        ),
        None,
    )

"""Check explicit MAX plan routes against canonical Next.js file conventions."""

from __future__ import annotations

import re
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass

from tree_sitter import Node, Parser

from yleum_api.services.build_plan import BuildPlan
from yleum_api.services.max_typescript_source import (
    NEXT_ROUTE_SUFFIXES,
    RESERVED_API_SEGMENTS,
    language,
    next_router_roots,
    node_text,
)


@dataclass(frozen=True)
class ApiRouteSource:
    path: str
    parts: tuple[str, ...]
    app_router: bool

    def matches(self, requested: str) -> bool:
        parts = []
        for segment in self.parts:
            if segment.startswith("[[..."):
                parts.append(r"(?:/.+)?")
            elif segment.startswith("[..."):
                parts.append(r"/.+")
            elif segment.startswith("["):
                parts.append(r"/[^/]+")
            else:
                parts.append("/" + re.escape(segment))
        return re.fullmatch("".join(parts), requested) is not None

    @property
    def precedence(self) -> tuple[int, ...]:
        return tuple(
            2 if "..." in segment and segment.startswith("[") else int(segment.startswith("["))
            for segment in self.parts
        )


def next_api_sources(files: Mapping[str, str]) -> tuple[ApiRouteSource, ...]:
    routes: list[ApiRouteSource] = []
    for directory, root in next_router_roots(files).items():
        app_router = directory == "app/"
        for path, content in files.items():
            if not path.startswith(root) or not path.endswith(NEXT_ROUTE_SUFFIXES):
                continue
            if not content.strip() or path.endswith((".d.ts", ".spec.ts", ".test.ts")):
                continue
            parts = path[len(root) :].rsplit(".", 1)[0].split("/")
            if app_router:
                if parts.pop() != "route" or any(p.startswith(("_", "@")) for p in parts):
                    continue
                parts = [p for p in parts if not (p.startswith("(") and p.endswith(")"))]
            elif parts[-1] == "index":
                parts.pop()
            if len(parts) >= 2 and parts[0] == "api" and parts[1] not in RESERVED_API_SEGMENTS:
                routes.append(ApiRouteSource(path, tuple(parts), app_router))
    return tuple(sorted(routes, key=lambda route: route.precedence))


def _binding_names(pattern: Node | None) -> Iterator[str]:
    if pattern is None:
        return
    if pattern.type in {"identifier", "shorthand_property_identifier_pattern"}:
        yield node_text(pattern)
    elif pattern.type in {"object_pattern", "array_pattern", "rest_pattern"}:
        for child in pattern.named_children:
            yield from _binding_names(child)
    elif pattern.type == "pair_pattern":
        yield from _binding_names(pattern.child_by_field_name("value"))
    elif pattern.type in {"assignment_pattern", "object_assignment_pattern"}:
        yield from _binding_names(pattern.child_by_field_name("left"))


def _exported_names(root: Node) -> Iterator[str]:
    for statement in root.named_children:
        if statement.type != "export_statement" or any(
            child.type in {"default", "type"} for child in statement.children
        ):
            continue
        declaration = statement.child_by_field_name("declaration")
        if declaration is not None and declaration.type == "function_declaration":
            yield node_text(declaration.child_by_field_name("name"))
        elif declaration is not None and declaration.type in {
            "lexical_declaration",
            "variable_declaration",
        }:
            for binding in declaration.named_children:
                yield from _binding_names(binding.child_by_field_name("name"))
        for child in statement.named_children:
            if child.type == "export_clause":
                for specifier in child.named_children:
                    if not any(part.type == "type" for part in specifier.children):
                        yield node_text(
                            specifier.child_by_field_name("alias")
                            or specifier.child_by_field_name("name")
                        )
        if any(child.type == "*" for child in statement.children):
            # Delegated module exports are verified by the application's tests.
            yield "*"


def max_api_source_gap(
    files: Mapping[str, str],
    routes: Sequence[ApiRouteSource],
    plan: BuildPlan,
) -> str | None:
    if plan.is_empty or not next_router_roots(files):
        return None
    observed: dict[str, set[str]] = {}
    missing: list[str] = []
    for cap in plan.capabilities:
        route = next((route for route in routes if route.matches(cap.path)), None)
        if route is None:
            missing.append(cap.action)
        elif route.app_router:
            if route.path not in observed:
                parser = Parser(language(route.path.endswith((".tsx", ".jsx"))))
                observed[route.path] = set(
                    _exported_names(
                        parser.parse(files[route.path].encode()).root_node,
                    )
                )
            names = observed[route.path]
            # App Router implements HEAD from GET and supplies OPTIONS by default.
            if (
                cap.method not in names
                and "*" not in names
                and not (cap.method == "OPTIONS" or (cap.method == "HEAD" and "GET" in names))
            ):
                missing.append(cap.action)
        # Pages Router dispatches internally; a file can verify only the path.
    if not missing:
        return None
    return "Implement the explicitly requested API handler(s): " + ", ".join(missing) + "."

"""Describe supported storage calls in the saved product. Never execute or quote product code."""

from __future__ import annotations

import posixpath
from collections.abc import Iterator, Mapping
from dataclasses import dataclass

from tree_sitter import Node, Parser

from yleum_api.services.max_typescript_source import (
    SCRIPT_SUFFIXES,
    language,
    node_text,
    resolve_local_import,
    static_imports,
    string_literal,
)
from yleum_api.services.portable_product_source import _next_screen_paths, reachable_product_source

_SDK_METHODS = {
    "lib/omnia/client": {"createMaxAction"},
    "lib/omnia/integration-client": {"createMaxAction"},
    "lib/omnia/business-client": {
        "createManagedBooking",
        "createManagedOrder",
        "createManagedLead",
    },
}
_MANAGED_LABELS = {
    "createManagedBooking": "заявки на запись",
    "createManagedLead": "обращения пользователя",
    "createManagedOrder": "заказы пользователя",
}
# Only exact, unambiguous names get specific labels. Never expose custom keys or payload values.
_DATA_LABELS = {
    "dietarypreferences": "пищевые предпочтения",
    "nutritiongoal": "цель питания",
    "fooddiary": "дневник питания",
    "mealhistory": "история приёмов пищи",
    "mealplan": "план питания",
    "waterintakeml": "количество выпитой воды",
    "waterml": "количество выпитой воды",
    "contactemail": "контактный email",
    "contactphone": "контактный телефон",
    "customername": "имя клиента",
    "theme": "настройки оформления",
}
_ACTION = "записи о действиях пользователя"
_LOCAL = "данные приложения в хранилище браузера"
_STORAGE = {"localStorage", "sessionStorage"}
_FUNCTIONS = {
    "function_declaration",
    "function_expression",
    "arrow_function",
    "method_definition",
    "generator_function",
    "generator_function_declaration",
}
_SCOPES = _FUNCTIONS | {"statement_block", "program", "catch_clause"}


@dataclass(frozen=True)
class ProductWrites:
    server: tuple[str, ...]
    browser: tuple[str, ...]
    session: tuple[str, ...]
    complete: bool


def _walk(node: Node) -> Iterator[Node]:
    pending = [node]
    while pending:
        current = pending.pop()
        yield current
        pending.extend(reversed(current.named_children))


def _module(path: str, specifier: str, files: Mapping[str, str]) -> str | None:
    if specifier.startswith("@/"):
        normalized = specifier[2:]
    elif specifier.startswith("."):
        normalized = posixpath.normpath(posixpath.join(posixpath.dirname(path), specifier))
        normalized = normalized.removeprefix("src/")
    else:
        return None
    if normalized in _SDK_METHODS:
        return normalized
    return resolve_local_import(path, specifier, files)


Binding = tuple[str, str]


def _bindings(
    path: str,
    root: Node,
    files: Mapping[str, str],
) -> tuple[dict[str, Binding], dict[str, Binding]]:
    imports: dict[str, Binding] = {}
    exports: dict[str, Binding] = {}
    for specifier, statement in static_imports(root):
        module = _module(path, specifier, files)
        if not module:
            continue
        for node in _walk(statement):
            if node.type == "import_clause" and node.named_children[0].type == "identifier":
                imports[node_text(node.named_children[0])] = (module, "default")
            if node.type == "namespace_import" and node.named_children:
                imports[node_text(node.named_children[-1])] = (module, "*")
            if node.type not in {"import_specifier", "export_specifier"} or any(
                child.type == "type" for child in node.children
            ):
                continue
            name = node_text(node.child_by_field_name("name"))
            alias = node_text(node.child_by_field_name("alias")) or name
            (exports if node.type == "export_specifier" else imports)[alias] = (module, name)
    return imports, exports


def _target(
    binding: Binding | None, exports: Mapping[str, Mapping[str, Binding]]
) -> Binding | None:
    seen = set()
    while binding and binding not in seen:
        module, name = binding
        if name.startswith("#") or name in _SDK_METHODS.get(module, set()):
            return binding
        seen.add(binding)
        binding = exports.get(module, {}).get(name)
    return None


def _declarations(root: Node, sensitive: set[str]) -> dict[str, list[tuple[int, int]]]:
    """Index shadowing before examining calls, including declarations that follow a call."""
    spans: dict[str, list[tuple[int, int]]] = {}
    for node in _walk(root):
        if node.type in {"variable_declarator", "class_declaration", "function_declaration"}:
            pattern = node.child_by_field_name("name")
        elif node.type in {"required_parameter", "optional_parameter"}:
            pattern = node.child_by_field_name("pattern")
        elif node.type in {"arrow_function", "catch_clause"}:
            pattern = node.child_by_field_name("parameter")
        elif node.type in {"import_clause", "namespace_import", "import_specifier"}:
            # A module may import a stand-in named localStorage or window.
            for child in node.named_children:
                if child.type == "identifier" and node_text(child) in _STORAGE | {"window"}:
                    spans.setdefault(node_text(child), []).append((0, root.end_byte))
            continue
        else:
            continue
        if pattern is None:
            continue
        scope = node if node.type in {"arrow_function", "catch_clause"} else node.parent
        while scope and scope.type not in _SCOPES:
            scope = scope.parent
        if scope:
            for part in _walk(pattern):
                if part.type in {"identifier", "shorthand_property_identifier_pattern"}:
                    name = node_text(part)
                    if name in sensitive:
                        spans.setdefault(name, []).append((scope.start_byte, scope.end_byte))
    return spans


def _functions(root: Node, path: str, exports: dict[str, Binding]) -> dict[str, Node]:
    functions: dict[str, Node] = {}
    ambiguous: set[str] = set()
    for node in _walk(root):
        if node.type not in _FUNCTIONS:
            continue
        declaration = node
        name = node_text(node.child_by_field_name("name"))
        parent = node.parent
        if parent is not None and parent.type == "variable_declarator":
            name = node_text(parent.child_by_field_name("name"))
            declaration = parent.parent or parent
        elif parent is not None and parent.type == "export_statement":
            name = name or "default"
        elif node.type != "function_declaration":
            continue
        if not name:
            continue
        if name in functions:
            ambiguous.add(name)
        functions[name] = node
        export = declaration.parent
        if export is not None and export.type == "export_statement":
            public = (
                "default" if any(child.type == "default" for child in export.children) else name
            )
            exports[public] = (path, "#" + name)
    for statement in root.named_children:
        value = statement.child_by_field_name("value")
        if statement.type == "export_statement" and value and value.type == "identifier":
            if any(child.type == "default" for child in statement.children):
                exports["default"] = (path, "#" + node_text(value))
    return {name: node for name, node in functions.items() if name not in ambiguous}


def _executed_nodes(
    roots: Mapping[str, Node],
    imports: Mapping[str, dict[str, Binding]],
    exports: Mapping[str, dict[str, Binding]],
    functions: Mapping[str, dict[str, Node]],
    entries: list[str],
) -> Iterator[tuple[str, Node]]:
    # Start with module initialization and actual Next screen exports. An exported
    # writer in an imported library is not a UI action until a caller reaches it.
    pending = list(roots.items())
    visited: set[tuple[str, int, int]] = set()

    def follow(path: str, expr: Node | None) -> None:
        if expr is None:
            return
        if expr.type in _FUNCTIONS:
            pending.append((path, expr.child_by_field_name("body") or expr))
            return
        name = node_text(expr)
        if name in functions[path]:
            fn = functions[path][name]
            scope = fn.parent
            while scope and scope.type not in _SCOPES:
                scope = scope.parent
            if scope and scope.start_byte <= expr.start_byte < scope.end_byte:
                pending.append((path, fn.child_by_field_name("body") or fn))
                return
        binding = imports[path].get(name)
        if expr.type == "member_expression":
            namespace = imports[path].get(node_text(expr.child_by_field_name("object")))
            if namespace and namespace[1] == "*":
                binding = (namespace[0], node_text(expr.child_by_field_name("property")))
        symbol = _target(binding, exports)
        if symbol and symbol[0] in functions:
            imported = functions[symbol[0]].get(symbol[1].removeprefix("#"))
            if imported is not None:
                pending.append((symbol[0], imported.child_by_field_name("body") or imported))

    for path in entries:
        target = _target((path, "default"), exports)
        if target and target[0] in functions:
            follow(target[0], functions[target[0]].get(target[1].removeprefix("#")))

    while pending:
        path, node = pending.pop()
        key = (path, node.start_byte, node.end_byte)
        if key in visited or node.type in _FUNCTIONS:
            continue
        visited.add(key)
        if node.type == "call_expression":
            yield path, node
            follow(path, node.child_by_field_name("function"))
        if node.type in {"jsx_self_closing_element", "jsx_opening_element"}:
            follow(path, node.child_by_field_name("name"))
        if node.type == "jsx_attribute" and len(node.named_children) == 2:
            attr, value = node.named_children
            if node_text(attr).startswith("on") and value.type == "jsx_expression":
                follow(path, value.named_child(0))
        if node.type == "if_statement":
            condition = node.child_by_field_name("condition")
            if condition and condition.named_child_count == 1:
                literal = condition.named_children[0].type
                if literal in {"false", "true"}:
                    selected = node.child_by_field_name(
                        "alternative" if literal == "false" else "consequence"
                    )
                    if selected is not None:
                        pending.append((path, selected))
                    continue
        children = node.named_children
        if node.type == "statement_block":
            for index, statement in enumerate(children):
                if statement.type in {"return_statement", "throw_statement"}:
                    children = children[: index + 1]
                    break
        pending.extend((path, child) for child in reversed(children))


def _unknown_storage(target: Node, args: list[Node]) -> bool:
    name = node_text(target)
    if name.startswith(("indexedDB.", "window.indexedDB.", "navigator.storage.")):
        return True
    if name not in {"fetch", "window.fetch", "globalThis.fetch"}:
        return False
    if len(args) < 2:
        return bool(args and args[0].type == "new_expression")
    if args[1].type != "object":
        return True
    for child in args[1].named_children:
        if child.type == "spread_element":
            return True
        key = child.child_by_field_name("key")
        if (string_literal(key) or node_text(key)) == "method":
            if string_literal(child.child_by_field_name("value")).upper() not in {"GET", "HEAD"}:
                return True
    return False


def _label(key: str) -> str | None:
    return _DATA_LABELS.get(key.replace("_", "").replace("-", "").lower())


def _action_fields(payload: Node, *, managed: bool = False) -> set[str]:
    if payload.type != "object":
        return set()
    fields: dict[str, str | None] = {}
    unknown = False
    for item in payload.named_children:
        key = item.child_by_field_name("key") if item.type == "pair" else item
        if key is None or key.type not in {
            "string",
            "property_identifier",
            "shorthand_property_identifier",
        }:
            unknown = True
            continue
        name = string_literal(key) or node_text(key)
        value = item.child_by_field_name("value")
        if value is not None and (
            value.type in _FUNCTIONS
            or value.type == "undefined"
            or (value.type == "unary_expression" and value.children[0].type == "void")
        ):
            fields.pop(name, None)
            continue
        if managed and name not in {"contactEmail", "contactPhone", "customerName"}:
            continue
        fields[name] = _label(name)
    labels = {label for label in fields.values() if label}
    if not managed and (unknown or None in fields.values()):
        labels.add(_ACTION)
    return labels


def product_writes(files: Mapping[str, str]) -> ProductWrites:
    graph = reachable_product_source(files, {})
    if graph is None:
        return ProductWrites((), (), (), False)
    modules, _ = graph
    roots = {
        path: Parser(language(path.endswith(("x", ".js", ".mjs", ".cjs"))))
        .parse(source.encode())
        .root_node
        for path, source in modules.items()
        if path.endswith(SCRIPT_SUFFIXES)
    }
    complete = len(roots) == len(modules) and all(not root.has_error for root in roots.values())
    imports, exports, functions, shadows = {}, {}, {}, {}
    for path, root in roots.items():
        if not root.has_error:
            imports[path], exports[path] = _bindings(path, root, files)
            functions[path] = _functions(root, path, exports[path])
            shadows[path] = _declarations(root, set(imports[path]) | _STORAGE | {"window"})
    server: set[str] = set()
    device: dict[str, set[str]] = {key: set() for key in _STORAGE}
    parsed = {path: roots[path] for path in imports}
    write_names = {
        path: set(names) | _STORAGE | {"fetch", "indexedDB"} for path, names in imports.items()
    }
    entries = list(_next_screen_paths(modules))
    complete = complete and all("default" in exports.get(path, {}) for path in entries)
    for path, call in _executed_nodes(parsed, imports, exports, functions, entries):
        target = call.child_by_field_name("function")
        arguments = call.child_by_field_name("arguments")
        if target is None or arguments is None or not arguments.named_children:
            continue
        args = arguments.named_children
        member = target.child_by_field_name("property")
        obj = target.child_by_field_name("object")
        base = node_text(obj) if obj is not None else node_text(target)
        prop = node_text(member)
        origin = base
        store = ""
        if member is not None and obj is not None:
            if obj.type == "member_expression" and (
                node_text(obj.child_by_field_name("object")) == "window"
            ):
                origin = "window"
                store = node_text(obj.child_by_field_name("property"))
            elif obj.type == "identifier":
                store = base
        if any(start <= call.start_byte < end for start, end in shadows[path].get(origin, [])):
            continue
        if prop == "setItem" and store in _STORAGE and len(args) == 2:
            label = _label(string_literal(args[0]))
            if args[1].type == "string" and label != _DATA_LABELS["theme"]:
                label = None  # A fixed string can be a placeholder, flag or empty default.
            device[store].add(label or _LOCAL)
            continue
        if _unknown_storage(target, args) or any(
            arg.type in _FUNCTIONS
            and any(
                child.type in {"identifier", "property_identifier"}
                and node_text(child) in write_names[path]
                for child in _walk(arg)
            )
            for arg in args
        ):
            complete = False
        binding = imports[path].get(base)
        if member is not None:
            binding = (binding[0], prop) if binding and binding[1] == "*" else None
        symbol = _target(binding, exports)
        method = symbol[1] if symbol and symbol[0] in _SDK_METHODS else ""
        if method == "createMaxAction":
            labels = _action_fields(args[1]) if len(args) > 1 else set()
            server.update(labels or {_ACTION})
        elif method in _MANAGED_LABELS:
            server.add(_MANAGED_LABELS[method])
            server.update(_action_fields(args[0], managed=True))
    return ProductWrites(
        tuple(sorted(server)),
        tuple(sorted(device["localStorage"])),
        tuple(sorted(device["sessionStorage"])),
        complete,
    )

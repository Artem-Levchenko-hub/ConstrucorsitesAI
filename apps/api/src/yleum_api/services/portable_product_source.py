"""Find source evidence reachable from a portable product entrypoint."""

from __future__ import annotations

import ast
import json
import posixpath
import re
from collections.abc import Iterator, Mapping

from tree_sitter import Node, Parser

from yleum_api.services.max_typescript_source import (
    NEXT_ROUTE_SUFFIXES,
    SCRIPT_SUFFIXES,
    language,
    next_router_roots,
    node_text,
    resolve_local_import,
    static_imports,
    string_literal,
)

_APP_SCREEN = re.compile(r"(?:[^/]+/)*(?:page|layout)\.(?:tsx|ts|jsx|js)")
_MANAGED_PREFIXES = ("lib/max/", "lib/omnia/", "lib/db/", "app/api/max/", "app/api/omnia/")
_MANAGED_COMPONENTS = {"components/MaxAppProvider.tsx", "components/YleumCompliance.tsx"}
_TEST_DIRS = {"__tests__", "test", "tests", "node_modules"}
_SOURCE_SCAN_BYTES = 4 * 1024 * 1024


def is_product_file(path: str) -> bool:
    relative = path.removeprefix("src/")
    return (
        not relative.startswith(_MANAGED_PREFIXES)
        and relative not in _MANAGED_COMPONENTS
        and not any(part.startswith(".") or part in _TEST_DIRS for part in path.split("/"))
        and not re.search(r"\.(?:d|spec|test)\.[cm]?[jt]sx?$", path)
    )


def _next_screen_paths(scripts: Mapping[str, str]) -> Iterator[str]:
    for directory, root in next_router_roots(scripts).items():
        for path in scripts:
            if not path.startswith(root) or not path.endswith(NEXT_ROUTE_SUFFIXES):
                continue
            relative = path[len(root) :]
            if (
                directory == "app/"
                and _APP_SCREEN.fullmatch(relative)
                and not any(part.startswith("_") for part in relative.split("/"))
            ):
                yield path
            if directory == "pages/" and not relative.startswith("api/"):
                yield path


def _service_entrypoints(scripts: Mapping[str, str], manifest: Mapping[str, object]) -> list[str]:
    routes = manifest.get("routes")
    services = manifest.get("services")
    if not isinstance(routes, list) or not isinstance(services, list):
        return []
    root_services = {
        route["service"]
        for route in routes
        if isinstance(route, dict)
        and route.get("path") == "/"
        and isinstance(route.get("service"), str)
    }
    entries: list[str] = []
    for service in services:
        if not isinstance(service, dict):
            continue
        name = service.get("name")
        if not isinstance(name, str) or name not in root_services:
            continue
        argv = service.get("argv")
        if not isinstance(argv, list) or len(argv) < 2 or not all(isinstance(x, str) for x in argv):
            continue
        program = posixpath.basename(argv[0])
        if program not in {"node", "bun", "deno", "python", "python3", "uvicorn"}:
            continue
        cwd = service.get("cwd", ".")
        if not isinstance(cwd, str):
            continue
        candidates = argv[1:2] if program != "deno" else argv[2:3]
        for candidate in candidates:
            if program == "uvicorn":
                candidate = candidate.partition(":")[0].replace(".", "/") + ".py"
            path = posixpath.normpath(posixpath.join(cwd, candidate))
            if path in scripts:
                entries.append(path)
    return entries


def _aliases(scripts: Mapping[str, str], files: Mapping[str, str]) -> dict[str, str]:
    aliases = {"@/": "src" if any(path.startswith("src/") for path in scripts) else "."}
    try:
        options = json.loads(files.get("tsconfig.json") or files.get("jsconfig.json") or "{}")
        compiler = options.get("compilerOptions", {})
        base = compiler.get("baseUrl", ".")
        for prefix, targets in compiler.get("paths", {}).items():
            if prefix.endswith("/*") and isinstance(targets, list) and targets:
                target = targets[0]
                if isinstance(target, str) and target.endswith("*"):
                    aliases[prefix[:-1]] = posixpath.join(base, target[:-1])
    except (ValueError, AttributeError, TypeError):
        pass
    return dict(sorted(aliases.items(), key=lambda entry: -len(entry[0])))


def _binding_names(clause: Node) -> set[str]:
    names: set[str] = set()
    for child in clause.named_children:
        if child.type == "identifier":
            names.add(node_text(child))
        elif child.type == "namespace_import":
            names.update(node_text(name) for name in child.named_children)
        elif child.type == "named_imports":
            for specifier in child.named_children:
                if any(part.type == "type" for part in specifier.children):
                    continue
                names.add(
                    node_text(
                        specifier.child_by_field_name("alias")
                        or specifier.child_by_field_name("name")
                    )
                )
    return names - {""}


def _with_ranges_removed(source: bytes, ranges: list[tuple[int, int]]) -> str:
    kept: list[bytes] = []
    start = 0
    for left, right in sorted(ranges):
        if left >= start:
            kept.append(source[start:left])
        start = max(start, right)
    kept.append(source[start:])
    return b" ".join(kept).decode()


def _script_evidence(source: str, parser: Parser) -> tuple[str, str, list[str]]:
    data = source.encode()
    root = parser.parse(data).root_node
    comments: list[tuple[int, int]] = []
    non_executable: list[tuple[int, int]] = []
    dependencies: list[str] = []
    references: set[str] = set()
    pending = [root]
    while pending:
        node = pending.pop()
        span = (node.start_byte, node.end_byte)
        if node.type == "comment":
            comments.append(span)
        elif node.type in {"import_statement", "type_alias_declaration", "interface_declaration"}:
            non_executable.append(span)
        elif node.type == "export_statement" and node.child_by_field_name("source") is not None:
            non_executable.append(span)
        else:
            if node.type == "identifier":
                references.add(node_text(node))
            if node.type == "call_expression":
                function = node.child_by_field_name("function")
                arguments = node.child_by_field_name("arguments")
                if (
                    node_text(function) in {"import", "require"}
                    and arguments is not None
                    and arguments.named_children
                    and (name := string_literal(arguments.named_children[0]))
                ):
                    dependencies.append(name)
            pending.extend(node.named_children)

    for specifier, statement in static_imports(root):
        clause = next((n for n in statement.named_children if n.type == "import_clause"), None)
        if clause is None or _binding_names(clause) & references:
            dependencies.append(specifier)
    return (
        _with_ranges_removed(data, comments),
        _with_ranges_removed(data, comments + non_executable),
        dependencies,
    )


def _python_evidence(
    entry: str,
    path: str,
    source: str,
    scripts: Mapping[str, str],
) -> tuple[str, set[str]]:
    try:
        program = ast.parse(source)
    except (SyntaxError, RecursionError):
        return "", set()
    dependencies: set[str] = set()
    for node in ast.walk(program):
        if isinstance(node, ast.Import):
            names = [item.name for item in node.names]
            base = posixpath.dirname(entry)
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            names = [module, *(f"{module}.{item.name}".lstrip(".") for item in node.names)]
            base = (
                posixpath.join(posixpath.dirname(path), "../" * (node.level - 1))
                if node.level
                else posixpath.dirname(entry)
            )
        else:
            continue
        for name in names:
            stem = posixpath.normpath(posixpath.join(base, name.replace(".", "/")))
            dependencies.update(
                candidate
                for candidate in (stem + ".py", stem + "/__init__.py")
                if candidate in scripts
            )
    try:
        return ast.unparse(program), dependencies
    except RecursionError:
        return "", dependencies


def reachable_product_source(
    files: Mapping[str, str],
    manifest: Mapping[str, object],
) -> tuple[dict[str, str], str] | None:
    """Return reachable modules and comment-free evidence, if the entrypoints are known."""
    scripts = {
        path: text
        for path, text in files.items()
        if is_product_file(path) and path.endswith((*SCRIPT_SUFFIXES, ".py"))
    }
    entries = list(_next_screen_paths(scripts)) or _service_entrypoints(scripts, manifest)
    if not entries:
        return None

    aliases = _aliases(scripts, files)
    parsers = {jsx: Parser(language(jsx)) for jsx in (True, False)}
    pending = [(path, path) for path in entries]
    reached: dict[str, str] = {}
    snippets: list[str] = []
    remaining = _SOURCE_SCAN_BYTES
    while pending:
        path, entry = pending.pop()
        if path in reached:
            continue
        # This is synchronous source evidence. Large valid products still run
        # their normal build/tests without a partial negative source verdict.
        remaining -= len(scripts[path].encode())
        if remaining < 0:
            return None
        if path.endswith(".py"):
            code, dependencies = _python_evidence(entry, path, scripts[path], scripts)
            text = code
        else:
            parser = parsers[not path.endswith((".ts", ".mts", ".cts"))]
            code, text, specifiers = _script_evidence(scripts[path], parser)
            dependencies = {
                target
                for specifier in specifiers
                if (target := resolve_local_import(path, specifier, scripts, aliases=aliases))
            }
        reached[path] = code
        snippets.append(text.lower())
        pending.extend((target, entry) for target in sorted(dependencies))
    return reached, "\n".join(snippets)

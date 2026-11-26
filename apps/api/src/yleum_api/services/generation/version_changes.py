"""Summarize a MAX source delta without executing it or inferring runtime behavior."""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from html import unescape

from tree_sitter import Node, Parser

from yleum_api.services.generation.handoff_advice import normalize_handoff
from yleum_api.services.max_project_kit import MAX_RETIRED_MANAGED_FILES, MAX_SECURITY_LOCKED_FILES
from yleum_api.services.max_typescript_source import (
    NEXT_ROUTE_SUFFIXES,
    language,
    node_text,
    resolve_local_import,
    static_imports,
    string_literal,
)
from yleum_api.services.portable_product_source import (
    _aliases,
    _next_screen_paths,
    is_product_file,
    reachable_product_source,
)
from yleum_api.services.secret_safety import is_secret_file

_MAX_FILES = 12
_MAX_FILE_BYTES = 96_000
_MAX_TOTAL_BYTES = 384_000
_MAX_NODES = 6_000
_MAX_ELEMENTS = 100
_MAX_TREE_FILES = 5_000
_MAX_LINES = 3

_KIT_ASSETS = {"assets/omnia-kit.css", "assets/omnia-kit.js", "assets/anime.min.js"}
_SERVER_PREFIXES = ("app/api/", "pages/api/", "api/", "server/", "backend/", "lib/server/")
_UI_PREFIXES = ("app/", "pages/", "components/", "views/", "screens/")
_PRIVATE_DIRS = {"__fixtures__", "__mocks__", "data", "fixtures", "mocks", "private", "secrets"}
_FALLBACKS = {
    "style": "Обновили файлы оформления приложения.",
    "server": "Обновили серверную часть приложения.",
    "screen": "Обновили код экранов приложения.",
    "technical": "Обновили технические файлы приложения.",
}

_TAGS = {
    **dict.fromkeys(("h1", "h2", "h3", "h4", "h5", "h6"), "heading"),
    **dict.fromkeys(("button", "Button"), "button"),
    **dict.fromkeys(("input", "Input", "textarea", "Textarea", "select", "Select"), "field"),
    **dict.fromkeys(("label", "Label", "legend"), "label"),
    "p": "text",
}
_DESCRIPTIONS = {
    "heading": ("заголовок", "заголовок", ""),
    "button": ("кнопку", "подпись кнопки", "действие кнопки"),
    "field": ("поле", "подпись поля", "обработку поля"),
    "label": ("подпись поля", "подпись поля", ""),
    "text": ("текст", "текст", ""),
}
_TRANSPARENT_TAGS = {"span", "strong", "em", "b", "i", "small"}
_HIDDEN_TAGS = {"script", "style", "template", "noscript", "svg"}
_LAYOUT_TAGS = {"article", "aside", "form", "main", "nav", "ol", "section", "table", "ul"}
_ACTION_PROPS = ("onClick", "onChange", "onPress", "formAction", "type")
_JSX = {"jsx_element", "jsx_self_closing_element"}
_FUNCTIONS = {"function_declaration", "function_expression", "arrow_function"}
_IDENTIFIERS = {
    "identifier",
    "shorthand_property_identifier",
    "shorthand_property_identifier_pattern",
}

_LABEL = re.compile(r"[А-Яа-яЁёA-Za-z][А-Яа-яЁёA-Za-z .,!?(…)%–—-]{0,69}")
_DO_NOT_QUOTE = re.compile(
    r"\b(?:игнорир\w*|инструкц\w*|промпт\w*|раскрой\w*|выведи\w*|забудь\w*|"
    r"ответь\w*|секрет\w*|паспорт\w*|снилс|инн|улица|переулок|проспект|шоссе|бульвар|квартира)\b|"
    r"\b(?:карточка|профиль|данные) (?:клиента|покупателя|пользователя) \w+",
    re.IGNORECASE,
)
_PROPER_NAME = re.compile(r"(?<!^)\b[А-ЯЁ][а-яё]+\b")
_PUBLIC_ACRONYMS = {"AI", "CSV", "GPS", "ID", "MAX", "PDF", "QR", "SLA", "SMS", "VIP"}
# Paragraphs often contain demo data or prose. Quote a literal only when every
# word can plausibly be routine interface copy.
_COPY_WORDS = frozenset(
    (
        "ближайшие быстро вариант варианты время выберите сегодня завтра данные день для "
        "добавьте доступные или и занятие занятия запись записи заявку каждую каждый "
        "на настройки начните неделе неделю новые откройте по план питания просмотрите "
        "расписание результат результаты сохраните удобное эта этой экспорт"
    ).split()
)


@dataclass(frozen=True, slots=True)
class _Element:
    kind: str
    label: str
    location: tuple[str, ...]
    action: tuple[tuple[bytes, ...], ...] | None
    named_handler: tuple[bytes, ...] | None


@dataclass(slots=True)
class _Screen:
    elements: list[_Element]
    layout: list[tuple[str, ...]]
    styles: list[tuple[str, tuple[bytes, ...]]]
    rendered_imports: set[str]
    ambiguous: bool = False


def _area(path: str) -> str:
    if not is_product_file(path) or _PRIVATE_DIRS.intersection(path.split("/")):
        return "technical"
    relative = path.removeprefix("src/")
    if relative.startswith(_SERVER_PREFIXES) or path.startswith(("drizzle/", "migrations/")):
        return "server"
    if path.endswith((".css", ".scss", ".sass", ".less")):
        return "style"
    if path.endswith((".tsx", ".jsx", ".js")) and (
        relative.startswith(_UI_PREFIXES) or relative in {"App.tsx", "App.jsx", "App.js"}
    ):
        return "screen"
    return "technical"


def _screen_paths(files: Mapping[str, str], paths: set[str]) -> tuple[set[str], set[str] | None]:
    roots = set(_next_screen_paths(files)) & paths
    if not roots:
        roots = paths.intersection(
            prefix + "App" + suffix for prefix in ("", "src/") for suffix in NEXT_ROUTE_SUFFIXES
        )
    if not paths.intersection(files) - roots:
        return roots, roots
    try:
        graph = reachable_product_source(files, {})
    except (UnicodeError, RecursionError):
        graph = None
    return roots, roots | paths.intersection(graph[0]) if graph else None


def _safe_label(raw: str, kind: str) -> str:
    label = " ".join(raw.split())
    if (
        not _LABEL.fullmatch(label)
        or not re.search(r"[а-яё]{2}", label)
        or _DO_NOT_QUOTE.search(label)
        or _PROPER_NAME.search(label)
        or set(re.findall(r"[A-Za-z]+", label)) - _PUBLIC_ACRONYMS
        or (kind == "text" and set(re.findall(r"\w+", label.casefold())) - _COPY_WORDS)
    ):
        return ""
    return label if normalize_handoff({"primary_action": label}) else ""


def _opening(node: Node) -> Node:
    return node.child_by_field_name("open_tag") or node


def _attributes(opening: Node) -> dict[str, Node | None]:
    attributes: dict[str, Node | None] = {}
    for attribute in opening.children_by_field_name("attribute"):
        if attribute.type == "jsx_attribute" and attribute.named_children:
            name, *value = attribute.named_children
            attributes[node_text(name)] = value[0] if value else None
    return attributes


def _hidden(tag: str, attributes: Mapping[str, Node | None]) -> bool:
    for key in ("hidden", "aria-hidden"):
        if key not in attributes:
            continue
        value = attributes[key]
        if key == "aria-hidden" and string_literal(value) == "false":
            continue
        if (
            value is None
            or value.type != "jsx_expression"
            or [part.type for part in value.named_children if part.type != "comment"] != ["false"]
        ):
            return True
    return tag in _HIDDEN_TAGS or (
        tag == "input" and string_literal(attributes.get("type")) == "hidden"
    )


def _literal_text(node: Node, depth: int = 0) -> str | None:
    if depth > 3 or node.end_byte - node.start_byte > 1_000:
        return None
    text = []
    for child in node.named_children:
        if child.type in {"jsx_text", "html_character_reference"}:
            text.append(unescape(node_text(child)))
        elif child.type == "jsx_expression":
            if all(part.type == "comment" for part in child.named_children):
                continue
            if not (literal := string_literal(child)):
                return None
            text.append(literal)
        elif child.type in _JSX:
            opening = _opening(child)
            tag = node_text(opening.child_by_field_name("name"))
            if _hidden(tag, _attributes(opening)) or _TAGS.get(tag) == "field":
                continue
            if tag not in _TRANSPARENT_TAGS or (part := _literal_text(child, depth + 1)) is None:
                return None
            text.append(part)
    return "".join(text)


def _tokens(node: Node) -> tuple[bytes, ...]:
    """Retain syntax leaves so spacing and comments do not imply changed behavior."""
    pending = [node]
    values: list[bytes] = []
    while pending:
        current = pending.pop()
        if current.type in {"comment", ";"}:
            continue
        if current.type == "string" and (literal := string_literal(current)):
            values.extend((b"\0string", literal.encode()))
            continue
        if current.child_count:
            values.append(b"\0" + current.type.encode())
            pending.extend(reversed(current.children))
        else:
            values.append(current.text or b"")
    return tuple(values)


def _declared_functions(node: Node) -> Iterator[tuple[Node, Node]]:
    if node.type == "function_declaration":
        if name := node.child_by_field_name("name"):
            yield name, node
    elif (
        node.type == "lexical_declaration"
        and node_text(node.child_by_field_name("kind")) == "const"
    ):
        for declaration in node.named_children:
            name = declaration.child_by_field_name("name")
            value = declaration.child_by_field_name("value")
            if name is not None and name.type == "identifier" and value is not None:
                if value.type in _FUNCTIONS:
                    yield name, value


def _function_index(root: Node) -> tuple[Counter[str], dict[str, tuple[Node, Node]]] | None:
    references: Counter[str] = Counter()
    declared: dict[str, tuple[Node, Node]] = {}
    pending = [root]
    for _ in range(2 * _MAX_NODES):
        if not pending:
            return references, declared
        node = pending.pop()
        if node.type == "jsx_closing_element":
            continue
        if node.type in _IDENTIFIERS:
            references[node_text(node)] += 1
        scope = node.parent
        if scope is not None and scope.type == "export_statement":
            scope = scope.parent
        if scope is not None and scope.type in {"program", "statement_block"}:
            for name, function in _declared_functions(node):
                declared[node_text(name)] = (scope, function)
        pending.extend(node.named_children)
    return None


def _entry_functions(
    root: Node,
    *,
    route: bool,
) -> tuple[list[tuple[str, Node]] | None, dict[str, Node]]:
    local: dict[str, Node] = {}
    defaults: list[tuple[str, Node]] = []
    named: list[tuple[str, Node]] = []
    default_name = ""
    has_default = False
    for statement in root.named_children:
        exported = statement.type == "export_statement"
        default = exported and any(child.type == "default" for child in statement.children)
        has_default |= default
        declaration = statement.child_by_field_name("declaration") if exported else statement
        if declaration is not None:
            for name, function in _declared_functions(declaration):
                key = node_text(name)
                local[key] = function
                if default:
                    defaults.append((key, function))
                elif exported and key[:1].isupper():
                    named.append((key, function))
        if default and (value := statement.child_by_field_name("value")):
            if value.type in _FUNCTIONS:
                defaults.append((node_text(value.child_by_field_name("name")), value))
            elif value.type == "identifier":
                default_name = node_text(value)
    if default_name in local:
        defaults.append((default_name, local[default_name]))
    if has_default and not defaults:
        return None, local
    if route:
        return defaults, local
    # The import graph identifies the module. If the module has several possible
    # component exports, the summary cannot attribute UI to one of them.
    possible = defaults + named
    return possible if len(possible) <= 1 else None, local


def _boolean(node: Node | None) -> str:
    while node is not None and node.type == "parenthesized_expression":
        node = next((child for child in node.named_children if child.type != "comment"), None)
    if node is not None:
        if node.type in _JSX:
            return "true"
        if node.type in {"true", "false"}:
            return node.type
    return ""


def _unconditional_exit(node: Node) -> bool:
    # Inspect the first statement of an obvious, constant branch only.
    if node.type == "if_statement" and (state := _boolean(node.child_by_field_name("condition"))):
        branch = node.child_by_field_name("consequence" if state == "true" else "alternative")
        while branch is not None and branch.type in {"statement_block", "else_clause"}:
            branch = next(
                (child for child in branch.named_children if child.type != "comment"), None
            )
        return branch is not None and branch.type in {"return_statement", "throw_statement"}
    return node.type in {"return_statement", "throw_statement"}


def _render_children(node: Node) -> Iterator[Node]:
    """Follow only obvious returns and React render expressions."""
    if node.type == "statement_block":
        for statement in node.named_children:
            if statement.type in {"if_statement", "statement_block", "return_statement"}:
                yield statement
            if _unconditional_exit(statement):
                break
    elif node.type in {"if_statement", "ternary_expression"}:
        state = _boolean(node.child_by_field_name("condition"))
        for condition, field in (("false", "consequence"), ("true", "alternative")):
            if state != condition and (child := node.child_by_field_name(field)):
                yield child
    elif node.type == "binary_expression":
        left = node.child_by_field_name("left")
        operator = node_text(node.child_by_field_name("operator"))
        state = _boolean(left)
        if operator in {"||", "??"} and left is not None:
            yield left
        if (
            (operator == "&&" and state != "false")
            or (operator == "||" and state != "true")
            or (operator == "??" and not state)
        ):
            if right := node.child_by_field_name("right"):
                yield right
    elif node.type in _JSX | {
        "return_statement",
        "else_clause",
        "jsx_expression",
        "parenthesized_expression",
        "array",
    }:
        yield from node.named_children
    elif node.type == "call_expression":
        function = node.child_by_field_name("function")
        arguments = node.child_by_field_name("arguments")
        if (
            function is not None
            and arguments is not None
            and node_text(function.child_by_field_name("property")) in {"map", "flatMap"}
        ):
            for callback in arguments.named_children:
                if callback.type in _FUNCTIONS and (body := callback.child_by_field_name("body")):
                    yield body
                    break


def _render_imports(root: Node, rendered: Counter[str], references: Counter[str]) -> set[str]:
    specifiers = set()
    for specifier, statement in static_imports(root):
        for clause in statement.named_children:
            if clause.type != "import_clause":
                continue
            bindings = [child for child in clause.named_children if child.type == "identifier"]
            for named in clause.named_children:
                if named.type == "named_imports":
                    for item in named.named_children:
                        if binding := item.child_by_field_name("alias") or item.child_by_field_name(
                            "name"
                        ):
                            bindings.append(binding)
            if any(
                rendered[name] and references[name] == rendered[name] + 1
                for binding in bindings
                if (name := node_text(binding))
            ):
                specifiers.add(specifier)
    return specifiers


def _named_handler(
    value: Node | None,
    references: Counter[str],
    declared: Mapping[str, tuple[Node, Node]],
) -> tuple[bytes, ...] | None:
    if value is None or value.type != "jsx_expression":
        return None
    parts = [child for child in value.named_children if child.type != "comment"]
    if len(parts) != 1 or parts[0].type != "identifier":
        return None
    name = node_text(parts[0])
    if references[name] != 2 or name not in declared or references["eval"]:
        return None
    scope, function = declared[name]
    body = function.child_by_field_name("body")
    if body is None or function.end_byte - function.start_byte > 4_000:
        return None
    ancestor = value.parent
    while ancestor is not None and ancestor != scope:
        if ancestor.type == "with_statement":
            return None
        ancestor = ancestor.parent
    if ancestor is None or (
        function.type != "function_declaration" and value.start_byte < function.start_byte
    ):
        return None
    parameters = function.child_by_field_name("parameters") or function.child_by_field_name(
        "parameter"
    )
    return (_tokens(parameters) if parameters is not None else ()) + _tokens(body)


def _observe(parser: Parser, source: bytes, *, route: bool) -> _Screen | None:
    root = parser.parse(source).root_node
    if root.has_error:
        return None
    index = _function_index(root)
    if index is None:
        return None
    references, declared = index
    root_functions, local = _entry_functions(root, route=route)
    screen = _Screen([], [], [], set())
    if root_functions is None:
        screen.ambiguous = True
        return screen
    rendered: Counter[str] = Counter()
    observed = {function for _, function in root_functions}
    pending: list[tuple[Node, tuple[str, ...]]] = [
        (body, (name,))
        for name, function in reversed(root_functions)
        if (body := function.child_by_field_name("body")) is not None
    ]
    remaining = _MAX_NODES
    while pending:
        node, location = pending.pop()
        remaining -= 1
        if remaining < 0 or len(location) > 24 or len(screen.elements) > _MAX_ELEMENTS:
            return None
        if node.type in {"jsx_opening_element", "jsx_closing_element", "comment"}:
            continue
        if node.type in _JSX:
            opening = _opening(node)
            tag = node_text(opening.child_by_field_name("name"))
            attributes = _attributes(opening)
            if any(
                value is not None and value.end_byte - value.start_byte > 4_000
                for value in attributes.values()
            ):
                return None
            if _hidden(tag, attributes):
                continue
            rendered[tag] += 1
            if (
                (component := local.get(tag)) is not None
                and component not in observed
                and references[tag] == 2
            ):
                if body := component.child_by_field_name("body"):
                    pending.append((body, (tag,)))
                    observed.add(component)
            location += (tag,)
            if tag in _LAYOUT_TAGS:
                screen.layout.append(tuple(part for part in location[1:] if part in _LAYOUT_TAGS))
            for prop in ("className", "style"):
                if value := attributes.get(prop):
                    screen.styles.append((prop, _tokens(value)))
            if kind := _TAGS.get(tag):
                text = _literal_text(node) if kind != "field" else ""
                if not text and kind in {"field", "button"}:
                    text = string_literal(attributes.get("aria-label"))
                    if kind == "field" and not text:
                        text = string_literal(attributes.get("placeholder"))
                definite_action = kind in {"field", "button"} and all(
                    attribute.type == "jsx_attribute"
                    for attribute in opening.children_by_field_name("attribute")
                )
                action = (
                    tuple(
                        _tokens(value) if (value := attributes.get(prop)) else ()
                        for prop in _ACTION_PROPS
                    )
                    if definite_action
                    else None
                )
                handler = (
                    _named_handler(attributes.get("onClick"), references, declared)
                    if definite_action
                    else None
                )
                screen.elements.append(
                    _Element(kind, _safe_label(text or "", kind), location, action, handler)
                )
            if node.type == "jsx_self_closing_element":
                continue
        pending.extend((child, location) for child in reversed(list(_render_children(node))))
    screen.rendered_imports.update(_render_imports(root, rendered, references))
    return screen


def _key(element: _Element) -> tuple[str, str]:
    return element.kind, element.label


def _by_location(screen: _Screen) -> dict[tuple[str, ...], list[_Element]]:
    groups: dict[tuple[str, ...], list[_Element]] = defaultdict(list)
    for item in screen.elements:
        groups[item.location].append(item)
    return groups


def _action_changed(before: _Element, after: _Element) -> bool:
    if before.action is None or after.action is None:
        return False
    if before.named_handler is not None and after.named_handler is not None:
        return before.named_handler != after.named_handler or before.action[1:] != after.action[1:]
    return before.action != after.action


def _describe(
    kind: str,
    before: list[_Element],
    after: list[_Element],
    delta: Counter[tuple[str, str]],
) -> list[str]:
    old = Counter(element.label for element in before)
    new = Counter(element.label for element in after)
    removed, added = old - new, new - old
    noun, replacement, action = _DESCRIPTIONS[kind]
    phrases = []
    if removed.total() == added.total() == 1:
        previous, current = next(iter(removed)), next(iter(added))
        if previous and current and delta[kind, previous] < 0 < delta[kind, current]:
            phrases.append(f"Заменили {replacement} «{previous}» на «{current}».")
    elif not removed:
        phrases.extend(
            f"Добавили {noun} «{label}»." for label in added if label and delta[kind, label] > 0
        )
    elif not added:
        phrases.extend(
            f"Убрали {noun} «{label}»." for label in removed if label and delta[kind, label] < 0
        )
    if action:
        known_actions = {item.label: item for item in before if old[item.label] == 1}
        phrases.extend(
            f"Изменили {action} «{item.label}»."
            for item in after
            if item.label
            and new[item.label] == 1
            and item.label in known_actions
            and _action_changed(known_actions[item.label], item)
        )
    return phrases


def _changes(pairs: list[tuple[_Screen, _Screen]]) -> list[str]:
    delta = Counter(_key(item) for _, after in pairs for item in after.elements)
    delta.subtract(_key(item) for before, _ in pairs for item in before.elements)
    phrases: dict[str, list[str]] = {kind: [] for kind in _DESCRIPTIONS}
    for before, after in pairs:
        old, new = _by_location(before), _by_location(after)
        for location in dict.fromkeys((*old, *new)):
            earlier, later = old.get(location, []), new.get(location, [])
            kind = (earlier or later)[0].kind
            phrases[kind].extend(_describe(kind, earlier, later, delta))
    return list(dict.fromkeys(phrase for group in phrases.values() for phrase in group))


def _linked_screens(
    files: Mapping[str, str],
    observed: Mapping[str, _Screen],
    seeds: set[str],
) -> set[str]:
    # Imported utilities can be source dependencies without rendering anything.
    linked = seeds.intersection(observed)
    if linked == observed.keys():
        return linked
    aliases = _aliases(files, files)
    pending = list(linked)
    while pending:
        path = pending.pop()
        for specifier in observed[path].rendered_imports:
            target = resolve_local_import(path, specifier, files, aliases=aliases)
            if target is not None and target in observed and target not in linked:
                linked.add(target)
                pending.append(target)
    return linked


def _included(path: str) -> bool:
    return (
        path not in _KIT_ASSETS
        and path not in MAX_SECURITY_LOCKED_FILES
        and path not in MAX_RETIRED_MANAGED_FILES
        and not is_secret_file(path)
    )


def summarize_version_changes(before: Mapping[str, str], after: Mapping[str, str]) -> list[str]:
    """Return up to three ordered, safe observations about two exact source trees."""
    if before == after:
        return []
    if max(len(before), len(after)) > _MAX_TREE_FILES:
        return [_FALLBACKS["technical"]]
    changed = {
        path
        for path in before.keys() | after.keys()
        if before.get(path) != after.get(path) and _included(path)
    }
    if not changed:
        return []
    initial = [] if before else ["Создали первую версию приложения."]
    areas = {_area(path) for path in changed}
    roots_before: set[str] = set()
    roots_after: set[str] = set()
    available_before: set[str] = set()
    available_after: set[str] = set()
    if "screen" in areas:
        candidates = {
            path
            for path in before.keys() | after.keys()
            if _area(path) == "screen" and _included(path)
        }
        roots_before, graph_before = _screen_paths(before, candidates)
        roots_after, graph_after = _screen_paths(after, candidates)
        if graph_before is None or graph_after is None:
            # An incomplete graph cannot prove a component was disconnected.
            available_before, available_after = roots_before, roots_after
        else:
            available_before, available_after = graph_before, graph_after
    available = available_before | available_after
    compared = (changed & available) | (available_before ^ available_after)
    if not compared and "screen" in areas:
        areas.remove("screen")
        areas.add("technical")
    fallback = [phrase for area, phrase in _FALLBACKS.items() if area in areas]
    # A small product can be traced from its actual React entrypoints. In a large
    # product, retain direct route edits and avoid guessing about its components.
    inspected = (
        available if len(available) <= _MAX_FILES else compared & (roots_before | roots_after)
    )
    if not compared or not inspected or len(inspected) > _MAX_FILES:
        return (initial + fallback)[:_MAX_LINES]

    parser = Parser(language(jsx=True))
    remaining = _MAX_TOTAL_BYTES
    observed_before: dict[str, _Screen] = {}
    observed_after: dict[str, _Screen] = {}
    for files, roots, available, observed in (
        (before, roots_before, available_before, observed_before),
        (after, roots_after, available_after, observed_after),
    ):
        for path in sorted(inspected.intersection(available)):
            source = files[path]
            if (
                observed is observed_after
                and path in observed_before
                and (before[path] == source and (path in roots_before) == (path in roots_after))
            ):
                observed[path] = observed_before[path]
                continue
            if len(source) > _MAX_FILE_BYTES:
                return (initial + fallback)[:_MAX_LINES]
            data = source.encode("utf-8", errors="replace")
            remaining -= len(data)
            if remaining < 0 or len(data) > _MAX_FILE_BYTES:
                return (initial + fallback)[:_MAX_LINES]
            screen = _observe(parser, data, route=path in roots)
            if screen is None:
                return (initial + fallback)[:_MAX_LINES]
            observed[path] = screen

    linked_before = _linked_screens(before, observed_before, roots_before)
    linked_after = _linked_screens(after, observed_after, roots_after)
    linked_delta = (changed & (linked_before | linked_after)) | (linked_before ^ linked_after)
    if not linked_delta and "screen" in areas:
        areas.remove("screen")
        areas.add("technical")
        fallback = [phrase for area, phrase in _FALLBACKS.items() if area in areas]
    empty = _Screen([], [], [], set())
    pairs: list[tuple[_Screen, _Screen]] = []
    layout_changed = styling_changed = False
    for path in sorted(linked_delta):
        old = observed_before[path] if path in linked_before else empty
        new = observed_after[path] if path in linked_after else empty
        if old.ambiguous or new.ambiguous:
            continue
        pairs.append((old, new))
        if path in linked_before and path in linked_after:
            layout_changed |= old.layout != new.layout
            styling_changed |= old.styles != new.styles
    phrases = _changes(pairs)
    if not phrases and layout_changed:
        phrases.append("Изменили структуру экранов приложения.")
    if not phrases and styling_changed:
        phrases.append("Изменили настройки оформления экранов.")
    if phrases and len(initial) + len(phrases) < _MAX_LINES:
        other = areas.intersection({"style", "server"})
        if len(other) == 2:
            phrases.append("Обновили серверную часть и файлы оформления приложения.")
        elif other:
            phrases.append(_FALLBACKS[other.pop()])
    return (initial + (phrases or fallback))[:_MAX_LINES]

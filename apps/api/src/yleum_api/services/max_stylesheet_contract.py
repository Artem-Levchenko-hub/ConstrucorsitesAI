"""Detect a proven home-route component whose CSS styles a different class family."""
from __future__ import annotations

import posixpath
import re
from collections.abc import Callable, Iterable, Iterator, Mapping
from dataclasses import dataclass, field
from functools import cache

import tree_sitter_typescript
from tree_sitter import Language, Node, Parser

_HOME = "src/app/page.tsx"
_ROOT_LAYOUT = "src/app/layout.tsx"
_SCRIPT_SUFFIXES = (".tsx", ".ts", ".jsx", ".js")
_STYLE_SUFFIXES = (".css", ".scss", ".sass", ".less", ".styl")
# Optional source evidence runs synchronously. Bound it before parsing or retaining
# any stylesheet facts, even when a valid workspace is much larger.
_MAX_INPUT_BYTES = 512 * 1024
_MAX_SCRIPT_BYTES = 64 * 1024
_MAX_PATHS = 64
_MAX_RULE_NAMES = 4096
_JSX_TAGS = {"jsx_opening_element", "jsx_self_closing_element", "jsx_closing_element"}
_FUNCTIONS = {
    "arrow_function", "function_declaration", "function_expression",
    "generator_function", "generator_function_declaration", "method_definition",
}
_CLASS_FAMILY = re.compile(r"([a-z][a-z0-9]{0,31})-([a-z][a-z0-9-]{0,63})")
_STRUCTURAL_ROLES = frozenset({
    "app", "card", "header", "item", "list", "main", "menu", "nav", "shell", "title",
})
_STYLE_IN_JS = re.compile(
    r"<style[\s>]|CSSStyleSheet|insertRule\s*\(|"
    r"""['"](?:styled-components|styled-jsx/style|@emotion/[^'"]+|@stitches/react)['"]"""
)
_CSS_QUOTE = r'"(?:\\(?:[\s\S]|\Z)|[^"\\])*+(?:"|\Z)|\'(?:\\(?:[\s\S]|\Z)|[^\'\\])*+(?:\'|\Z)'
_CSS_ESCAPE = re.compile(r"\\(?:([0-9a-fA-F]{1,6})(?:\r\n|[ \t\r\n\f])?|([^\r\n\f]))")
_CSS_TOKEN = re.compile(
    r"/\*(?:[^*]|\*(?!/))*+(?:\*/|\Z)|" + _CSS_QUOTE + "|" + _CSS_ESCAPE.pattern + r"|[{};]"
)
_CLASS_SELECTOR = re.compile(r"\.([A-Za-z_][\w-]*)")
_TYPE_SELECTOR = re.compile(r"(?:\A|[\s,>+~(])([a-z][a-z0-9]*)(?=$|[\s.#:[,>+~)])")
_CLASS_ATTRIBUTE = re.compile(r"\[\s*+class\b", re.IGNORECASE)
_CSS_IMPORT = re.compile(r"""@import\s++(?:url\(\s*+)?["']([^"']++)["']""")
_CSS_UTILITY = re.compile(r"@utility\s+([\w-]+\*?)\Z")


@cache
def _language(jsx: bool) -> Language:
    return Language(
        tree_sitter_typescript.language_tsx()
        if jsx else tree_sitter_typescript.language_typescript()
    )


def _text(node: Node | None) -> str:
    return (node.text or b"").decode() if node is not None else ""


def _literal(node: Node | None) -> str:
    if node is not None and node.type == "jsx_expression" and node.named_child_count == 1:
        node = node.named_children[0]
    if node is None or node.type != "string" or node.named_child_count != 1:
        return ""
    fragment = node.named_children[0]
    return _text(fragment) if fragment.type == "string_fragment" else ""


def _imports(root: Node) -> Iterator[tuple[str, Node]]:
    for statement in root.named_children:
        if statement.type in {"import_statement", "export_statement"} and not any(
            child.type == "type" for child in statement.children
        ):
            if source := _literal(statement.child_by_field_name("source")):
                yield source, statement


def _resolve(path: str, source: str, files: Mapping[str, str]) -> str | None:
    if source.startswith("@/"):
        base = posixpath.normpath("src/" + source[2:])
    elif source.startswith(("./", "../")):
        base = posixpath.normpath(posixpath.join(posixpath.dirname(path), source))
    else:
        return None
    if base in files:
        return base
    return next(
        (
            stem + suffix for stem in (base, f"{base}/index")
            for suffix in _SCRIPT_SUFFIXES if stem + suffix in files
        ),
        None,
    )


def _exported_body(root: Node, name: str) -> Node | None:
    for statement in root.named_children:
        if statement.type != "export_statement":
            continue
        default = any(child.type == "default" for child in statement.children)
        declaration = statement.child_by_field_name("declaration")
        if default and name == "default":
            value = declaration or statement.child_by_field_name("value")
            return value.child_by_field_name("body") if value is not None else None
        if default or declaration is None:
            continue
        if _text(declaration.child_by_field_name("name")) == name:
            return declaration.child_by_field_name("body")
        if declaration.type == "lexical_declaration":
            for binding in declaration.named_children:
                if _text(binding.child_by_field_name("name")) == name:
                    value = binding.child_by_field_name("value")
                    return value.child_by_field_name("body") if value is not None else None
    return None


def _walk(node: Node, *, skip_functions: bool = False) -> Iterator[Node]:
    pending = [node]
    while pending:
        current = pending.pop()
        if skip_functions and current.type in _FUNCTIONS:
            continue
        yield current
        pending.extend(current.named_children)


def _rendered_tags(body: Node) -> Iterator[Node]:
    if body.type == "statement_block":
        returns = [
            node for node in _walk(body, skip_functions=True) if node.type == "return_statement"
        ]
        if len(returns) != 1 or returns[0].parent != body or returns[0].named_child_count != 1:
            return
        body = returns[0].named_children[0]
    pending = [body]
    while pending:
        node = pending.pop()
        if node.type in {"jsx_opening_element", "jsx_self_closing_element"}:
            yield node
        elif node.type in {"jsx_element", "parenthesized_expression"}:
            pending.extend(node.named_children)


def _unshadowed_jsx_names(body: Node, tags: list[Node]) -> set[str]:
    names = {_text(tag.child_by_field_name("name")) for tag in tags}
    # Uses outside JSX tag names leave the imported binding ambiguous.
    for node in _walk(body.parent or body):
        if node.type in {"identifier", "shorthand_property_identifier_pattern"}:
            parent = node.parent
            if parent is None or parent.type not in _JSX_TAGS:
                names.discard(_text(node))
    names.discard("")
    return names


def _jsx_imports(root: Node, tags: set[str]) -> Iterator[tuple[str, str]]:
    for source, statement in _imports(root):
        for clause in statement.named_children:
            if clause.type != "import_clause":
                continue
            for binding in clause.named_children:
                if binding.type == "identifier" and _text(binding) in tags:
                    yield source, "default"
                elif binding.type == "named_imports":
                    for spec in binding.named_children:
                        name = _text(spec.child_by_field_name("name"))
                        alias = _text(spec.child_by_field_name("alias"))
                        if (alias or name) in tags:
                            yield source, name


def _literal_classes(tag: Node) -> tuple[str, ...]:
    attributes = tag.children_by_field_name("attribute")
    if any(
        attr.type != "jsx_attribute" or _text(attr.named_children[0]) == "style"
        for attr in attributes
    ):
        return ()
    for attr in attributes:
        name, *values = attr.named_children
        if _text(name) == "className" and len(values) == 1:
            return tuple(_literal(values[0]).split())
    return ()


def _families(classes: Iterable[str]) -> dict[str, set[str]]:
    families: dict[str, set[str]] = {}
    for name in classes:
        if match := _CLASS_FAMILY.fullmatch(name):
            family, role = match.groups()
            families.setdefault(family, set()).add(role)
    return families


def _css_heads(stylesheet: str) -> Iterator[tuple[str, str]]:
    """Scan once, keeping quoted selector values and skipping comments."""
    parts: list[str] = []
    start = 0
    for match in _CSS_TOKEN.finditer(stylesheet):
        token = match[0][0]
        if token in "\"'\\":
            continue
        parts.append(stylesheet[start:match.start()])
        start = match.end()
        if token == "/":
            parts.append(" ")
            continue
        if token in "{;":
            yield token, "".join(parts).strip()
        parts.clear()


def _unescape_css(match: re.Match[str]) -> str:
    if match[1] is None:
        return match[2]
    codepoint = int(match[1], 16)
    return chr(codepoint) if codepoint < 128 else "\ufffd"


@dataclass
class _Stylesheet:
    selectors: set[str] = field(default_factory=set)
    tags: set[str] = field(default_factory=set)
    imports: list[str] = field(default_factory=list)
    uncertain: bool = False


def _read_css(stylesheet: str, *, module: bool) -> _Stylesheet:
    evidence = _Stylesheet()
    for token, head in _css_heads(stylesheet):
        if token == ";":
            if head.startswith("@import"):
                if not module and (source := _CSS_IMPORT.match(head)):
                    evidence.imports.append(source[1])
                else:
                    evidence.uncertain = True
                    break
            continue
        if module:
            unquoted = _CSS_ESCAPE.sub(_unescape_css, re.sub(_CSS_QUOTE, " ", head))
            evidence.uncertain |= ":global" in unquoted
        elif utility := _CSS_UTILITY.fullmatch(head):
            evidence.uncertain |= utility[1].endswith("*")
            evidence.selectors.add(utility[1])
        elif not head.startswith("@"):
            unquoted = _CSS_ESCAPE.sub(_unescape_css, re.sub(_CSS_QUOTE, " ", head))
            evidence.selectors.update(_CLASS_SELECTOR.findall(unquoted))
            evidence.tags.update(_TYPE_SELECTOR.findall(unquoted))
            evidence.uncertain |= "&-" in unquoted or _CLASS_ATTRIBUTE.search(unquoted) is not None
        if evidence.uncertain or len(evidence.selectors) + len(evidence.tags) > _MAX_RULE_NAMES:
            evidence.uncertain = True
            break
    return evidence


@dataclass
class _InputBudget:
    remaining: int = _MAX_INPUT_BYTES
    paths: set[str] = field(default_factory=set)

    def take(self, path: str, source: str) -> bool:
        if len(self.paths) >= _MAX_PATHS and path not in self.paths:
            return False
        limit = self.remaining
        if path.endswith(_SCRIPT_SUFFIXES):
            limit = min(limit, _MAX_SCRIPT_BYTES)
        if len(source) > limit:
            return False
        size = len(source.encode())
        if size > limit:
            return False
        self.remaining -= size
        self.paths.add(path)
        return True


def _loaded_styles(
    files: Mapping[str, str], program: Callable[[str], Node | None], budget: _InputBudget,
) -> dict[str, _Stylesheet] | None:
    pending = [path for path in (_HOME, _ROOT_LAYOUT) if path in files]
    visited: set[str] = set()
    styles: dict[str, _Stylesheet] = {}
    rule_names = 0
    while pending:
        path = pending.pop()
        if path in visited:
            continue
        visited.add(path)
        if not budget.take(path, files[path]):
            return None
        if path.endswith(_STYLE_SUFFIXES):
            if not path.endswith(".css"):
                return None
            sheet = _read_css(files[path], module=path.endswith(".module.css"))
            rule_names += len(sheet.selectors) + len(sheet.tags)
            if sheet.uncertain or rule_names > _MAX_RULE_NAMES:
                return None
            styles[path] = sheet
            for source in sheet.imports:
                if target := _resolve(path, source, files):
                    pending.append(target)
                elif source != "tailwindcss" and not source.startswith(
                    "https://fonts.googleapis.com/"
                ):
                    return None
        elif path.endswith(_SCRIPT_SUFFIXES):
            root = program(path)
            if root is None or _STYLE_IN_JS.search(files[path]):
                return None
            for source, _ in _imports(root):
                if target := _resolve(path, source, files):
                    pending.append(target)
                elif source.startswith(("@/", "./", "../")) or (
                    source.endswith(_STYLE_SUFFIXES)
                    and source != "@maxhub/max-ui/dist/styles.css"
                ):
                    return None
    return styles


def disconnected_component_styles_gap(
    files: Mapping[str, str], component_paths: Iterable[str],
) -> str | None:
    """Only reject a literal class mismatch along the home route's JSX imports."""
    if _HOME not in files:
        return None
    parsers = {jsx: Parser(_language(jsx)) for jsx in (True, False)}
    budget = _InputBudget()

    def program(path: str) -> Node | None:
        root = parsers[not path.endswith(".ts")].parse(files[path].encode()).root_node
        return root if not root.has_error else None

    product_paths = set(component_paths)
    pending = [(_HOME, "default")]
    visited: set[tuple[str, str]] = set()
    styles: dict[str, _Stylesheet] | None = None
    style_names: set[str] = set()
    style_tags: set[str] = set()
    while pending:
        path, export = pending.pop()
        if (path, export) in visited:
            continue
        visited.add((path, export))
        if not budget.take(path, files[path]):
            return None
        root = program(path)
        if root is None or (body := _exported_body(root, export)) is None:
            continue
        tags = list(_rendered_tags(body))
        names = _unshadowed_jsx_names(body, tags)
        for source, name in _jsx_imports(root, names):
            if (target := _resolve(path, source, files)) and target.endswith(_SCRIPT_SUFFIXES):
                pending.append((target, name))
        if path not in product_paths:
            continue
        imported_css = sorted(
            target
            for source, statement in _imports(root)
            if source.endswith(".css") and not source.endswith(".module.css")
            and not any(child.type == "import_clause" for child in statement.named_children)
            and (target := _resolve(path, source, files))
        )
        elements = {
            (name, classes) for tag in tags
            if re.fullmatch(r"[a-z][a-z0-9]*", name := _text(tag.child_by_field_name("name")))
            and (classes := _literal_classes(tag))
        }
        if not imported_css or not elements:
            continue
        # Multiple plausible families can be deliberate compatibility hooks.
        rendered = {
            family: roles
            for family, roles in _families(
                name for _, classes in elements for name in classes
            ).items()
            if len(roles) >= 3 and len(roles & _STRUCTURAL_ROLES) >= 2
        }
        if len(rendered) != 1:
            continue
        if styles is None:
            styles = _loaded_styles(files, program, budget)
            if styles is None:
                return None
            for sheet in styles.values():
                style_names.update(sheet.selectors)
                style_tags.update(sheet.tags)
        family, roles = next(iter(rendered.items()))
        family_classes = {f"{family}-{role}" for role in roles}
        if any(
            not family_classes.isdisjoint(classes) and not style_names.isdisjoint(classes)
            for _, classes in elements
        ):
            continue
        # A lone type rule can be a typography/reset rule. Several structural
        # roles styled by type are sufficient evidence of an alternative layout.
        structural_classes = {f"{family}-{role}" for role in roles & _STRUCTURAL_ROLES}
        if len({
            name for tag, classes in elements if tag in style_tags
            for name in classes if name in structural_classes
        }) >= 2:
            continue
        for stylesheet in imported_css:
            for other, other_roles in sorted(_families(styles[stylesheet].selectors).items()):
                common_roles = roles & other_roles
                if family == other or len(common_roles) < 3 or len(
                    common_roles & _STRUCTURAL_ROLES
                ) < 2:
                    continue
                examples = ", ".join(f".{name}" for name in sorted(family_classes)[:5])
                return (
                    f"{path} renders .{family}-* classes ({examples}) without matching "
                    f"stylesheet rules. Its import {stylesheet} styles .{other}-* instead. "
                    "Align the component's class names and intended CSS, then run build."
                )
    return None

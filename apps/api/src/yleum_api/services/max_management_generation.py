"""Bounded TSX consumer connections, never browser/behavioural proof.

Supports named SDK imports, local functions, React state/effects, returned JSX,
collection maps and imported components with callback props. Unproven wrappers,
dynamic imports and arbitrary control/data flow fail closed for new contracts.
Generated code is parsed, never executed. Runtime probes remain a separate gate.
"""

from __future__ import annotations

import json
import posixpath
from collections.abc import Mapping
from dataclasses import dataclass, field
from functools import lru_cache

import tree_sitter_typescript
from tree_sitter import Language, Node, Parser

from yleum_api.schemas.max_management import ManagementContract
from yleum_api.services.max_management_contract import parse_management_contract

_MANIFEST = ".omnia/management.json"
_CREATE = {
    "orders": "createManagedOrder",
    "leads": "createManagedLead",
    "bookings": "createManagedBooking",
}
_SDK = {
    "@/lib/omnia/business-client": {*_CREATE.values(), "getManagedRecords"},
    "@/lib/omnia/app-profile-client": {"useYleumAppProfile"},
    "@/lib/omnia/catalog-client": {"useYleumCatalog"},
    "react": {"useState", "useEffect", "useCallback"},
}
# Only the explicitly supported React form/button events are connection evidence.
_INTRINSIC_EVENTS = {"onClick", "onClickCapture", "onSubmit", "onSubmitCapture"}
_VISIBLE_FIELDS = {
    "input": {"value", "defaultValue", "placeholder"},
    "textarea": {"value", "defaultValue", "placeholder"},
    "img": {"alt", "src"},
}


def _text(node: Node | None) -> str:
    return (node.text or b"").decode() if node is not None else ""


@lru_cache(maxsize=1)
def _language() -> Language:
    return Language(tree_sitter_typescript.language_tsx())


@dataclass
class _Value:
    tags: set[str] = field(default_factory=set)
    props: dict[str, _Value] = field(default_factory=dict)
    function: tuple[str, Node, dict[str, _Value]] | None = None
    sdk: str = ""
    literal: object = None
    setter: _Value | None = None
    state: bool = False


def _contract(files: Mapping[str, str]) -> ManagementContract | None:
    if _MANIFEST not in files:
        return None
    return parse_management_contract(json.loads(files[_MANIFEST]))


def management_probe_resources(files: Mapping[str, str]) -> tuple[str, ...]:
    """Only validated canonical resources; malformed manifests raise ValueError."""
    contract = _contract(files)
    return tuple(str(resource.id.value) for resource in contract.resources) if contract else ()


class _Connections:
    def __init__(self, files: Mapping[str, str]):
        self.programs = {
            path: Parser(_language()).parse(source.encode()).root_node
            for path, source in files.items()
            if path.startswith("src/")
            and path.endswith((".tsx", ".ts"))
            and not path.startswith(("src/lib/omnia/", "src/app/api/"))
        }
        self.modules: dict[str, dict[str, _Value]] = {}
        self.created: set[str] = set()
        self.rendered: set[str] = set()
        self.active: set[tuple[str, int]] = set()
        self.steps = 0
        self.event_depth = 0

    def _resolve(self, path: str, source: str) -> str | None:
        base = (
            "src/" + source[2:]
            if source.startswith("@/")
            else posixpath.normpath(posixpath.join(posixpath.dirname(path), source))
        )
        return next(
            (
                name
                for name in (base, base + ".tsx", base + ".ts", base + "/index.tsx")
                if name in self.programs
            ),
            None,
        )

    def module(self, path: str) -> dict[str, _Value]:
        if path in self.modules:
            return self.modules[path]
        env: dict[str, _Value] = {}
        self.modules[path] = env
        program = self.programs[path]
        if program.has_error:
            return env
        for statement in program.named_children:
            if statement.type == "import_statement":
                source = _text(statement.child_by_field_name("source"))[1:-1]
                for clause in statement.named_children:
                    if clause.type != "import_clause":
                        continue
                    for child in clause.named_children:
                        if child.type == "identifier":
                            target = self._resolve(path, source)
                            env[_text(child)] = (
                                self.module(target).get("default", _Value()) if target else _Value()
                            )
                        elif child.type == "named_imports":
                            for spec in child.named_children:
                                name = _text(spec.child_by_field_name("name"))
                                alias = _text(spec.child_by_field_name("alias")) or name
                                if name in _SDK.get(source, ()):
                                    env[alias] = _Value(sdk=name)
                                else:
                                    target = self._resolve(path, source)
                                    env[alias] = (
                                        self.module(target).get(name, _Value())
                                        if target
                                        else _Value()
                                    )
            else:
                declaration = (
                    statement.child_by_field_name("declaration")
                    if statement.type == "export_statement"
                    else statement
                )
                if declaration is not None:
                    self.declare(declaration, env, path)
                    if statement.type == "export_statement" and any(
                        c.type == "default" for c in statement.children
                    ):
                        env["default"] = env.get(
                            _text(declaration.child_by_field_name("name")),
                            self.value(declaration, env, path),
                        )
                elif statement.type == "export_statement":
                    env["default"] = self.value(statement.child_by_field_name("value"), env, path)
        return env

    def bind(self, pattern: Node | None, value: _Value, env: dict[str, _Value]) -> None:
        if pattern is None:
            return
        if pattern.type in {"identifier", "shorthand_property_identifier_pattern"}:
            env[_text(pattern)] = value
        elif pattern.type in {"required_parameter", "optional_parameter"}:
            self.bind(pattern.child_by_field_name("pattern"), value, env)
        elif pattern.type == "object_pattern":
            for child in pattern.named_children:
                key = (
                    _text(child.child_by_field_name("key"))
                    if child.type == "pair_pattern"
                    else _text(child)
                )
                self.bind(
                    child.child_by_field_name("value") if child.type == "pair_pattern" else child,
                    self.member(value, key),
                    env,
                )
        elif pattern.type == "array_pattern" and value.state:
            children = pattern.named_children
            state = _Value()
            if children:
                self.bind(children[0], state, env)
            if len(children) > 1:
                self.bind(children[1], _Value(setter=state), env)

    def declare(self, node: Node, env: dict[str, _Value], path: str) -> None:
        if node.type == "function_declaration":
            env[_text(node.child_by_field_name("name"))] = _Value(function=(path, node, env))
        elif node.type in {"lexical_declaration", "variable_declaration"}:
            for declaration in node.named_children:
                if declaration.type == "variable_declarator":
                    self.bind(
                        declaration.child_by_field_name("name"),
                        self.value(declaration.child_by_field_name("value"), env, path),
                        env,
                    )

    def member(self, value: _Value, key: str) -> _Value:
        if key in value.props:
            return value.props[key]
        tags = set()
        for tag in value.tags:
            if tag == "profile-hook" and key == "profile":
                tags.add("profile")
            elif tag == "profile" and key in {"app_name", "summary"}:
                tags.add("profile-field")
            elif tag == "profile" and key == "support":
                tags.add("profile-support")
            elif tag == "profile-support" and key in {"email", "response_time"}:
                tags.add("profile-field")
            elif tag == "catalog-hook" and key == "items":
                tags.add("catalog")
            elif tag.startswith("response:") and key == "records":
                tags.add("records:" + tag.split(":", 1)[1])
            elif tag.startswith(("item:", "record:")):
                tags.add(tag)
        return _Value(tags=tags)

    def invoke(self, fn: _Value, args: list[_Value], render: bool = False) -> _Value:
        if fn.setter is not None and args:
            fn.setter.tags.update(args[0].tags)
            return _Value()
        if fn.function is None:
            return _Value()
        path, node, closure = fn.function
        key = (path, node.start_byte)
        if key in self.active or self.steps > 4000:
            return _Value()
        self.active.add(key)
        env = dict(closure)
        parameters = node.child_by_field_name("parameters")
        patterns = (
            parameters.named_children if parameters else [node.child_by_field_name("parameter")]
        )
        for index, pattern in enumerate(patterns):
            self.bind(pattern, args[index] if index < len(args) else _Value(), env)
        body = node.child_by_field_name("body")
        result = (
            self.block(body, env, path, render)[1]
            if body is not None and body.type == "statement_block"
            else self.value(body, env, path, render)
        )
        self.active.remove(key)
        return result

    def block(
        self, node: Node, env: dict[str, _Value], path: str, render: bool
    ) -> tuple[bool, _Value]:
        for child in node.named_children:
            if child.type == "function_declaration":
                self.declare(child, env, path)
        for child in node.named_children:
            if child.type == "return_statement":
                return True, self.value(
                    child.named_children[0] if child.named_children else None, env, path, render
                )
            if child.type == "if_statement":
                condition = self.value(child.child_by_field_name("condition"), env, path)
                branch = child.child_by_field_name(
                    "alternative" if condition.literal is False else "consequence"
                )
                if branch is not None:
                    returned, value = self.statement(branch, env, path, render)
                    if returned and condition.literal is not None:
                        return True, value
            else:
                self.statement(child, env, path, render)
        return False, _Value()

    def statement(
        self, node: Node, env: dict[str, _Value], path: str, render: bool
    ) -> tuple[bool, _Value]:
        if node.type == "statement_block":
            return self.block(node, env, path, render)
        if node.type in {"lexical_declaration", "variable_declaration", "function_declaration"}:
            self.declare(node, env, path)
        elif node.type == "expression_statement":
            for child in node.named_children:
                self.value(child, env, path)
        elif node.type == "return_statement":
            return True, self.value(
                node.named_children[0] if node.named_children else None, env, path, render
            )
        elif node.type == "try_statement":
            body = node.child_by_field_name("body")
            if body is not None:
                return self.block(body, env, path, render)
        return False, _Value()

    def value(
        self, node: Node | None, env: dict[str, _Value], path: str, render: bool = False
    ) -> _Value:
        self.steps += 1
        if node is None or self.steps > 4000:
            return _Value()
        kind = node.type
        if kind == "identifier":
            result = env.get(_text(node), _Value())
        elif kind in {"arrow_function", "function_expression", "function_declaration"}:
            result = _Value(function=(path, node, env))
        elif kind in {
            "parenthesized_expression",
            "await_expression",
            "as_expression",
            "non_null_expression",
        }:
            result = self.value(
                node.named_children[0] if node.named_children else None, env, path, render
            )
        elif kind == "unary_expression":
            operand = self.value(node.named_children[0] if node.named_children else None, env, path)
            operator = _text(node.children[0])
            result = _Value(
                literal=not operand.literal
                if operator == "!" and isinstance(operand.literal, bool)
                else None
            )
        elif kind in {"true", "false", "string", "number"}:
            result = _Value(
                literal=kind == "true" if kind in {"true", "false"} else _text(node).strip("'\"")
            )
        elif kind == "member_expression":
            result = self.member(
                self.value(node.child_by_field_name("object"), env, path),
                _text(node.child_by_field_name("property")),
            )
        elif kind == "object":
            props = {}
            for child in node.named_children:
                if child.type == "pair":
                    props[_text(child.child_by_field_name("key"))] = self.value(
                        child.child_by_field_name("value"), env, path
                    )
                elif child.type == "shorthand_property_identifier":
                    props[_text(child)] = env.get(_text(child), _Value())
            result = _Value(props=props)
        elif kind == "call_expression":
            result = self.call(node, env, path, render)
        elif kind in {"jsx_element", "jsx_self_closing_element", "jsx_fragment"}:
            return self.jsx(node, env, path) if render else _Value()
        elif kind == "binary_expression":
            left = self.value(node.child_by_field_name("left"), env, path, render)
            operator = _text(node.child_by_field_name("operator"))
            if (operator == "&&" and left.literal is False) or (
                operator == "||" and left.literal is True
            ):
                return left
            result = self.value(node.child_by_field_name("right"), env, path, render)
        elif kind == "ternary_expression":
            condition = self.value(node.child_by_field_name("condition"), env, path)
            if condition.literal is None:
                self.value(node.child_by_field_name("alternative"), env, path, render)
            result = self.value(
                node.child_by_field_name(
                    "alternative" if condition.literal is False else "consequence"
                ),
                env,
                path,
                render,
            )
        else:
            result = _Value()
        if render:
            self.rendered.update(result.tags)
        return result

    def call(self, node: Node, env: dict[str, _Value], path: str, render: bool) -> _Value:
        target = node.child_by_field_name("function")
        arguments = node.child_by_field_name("arguments")
        args = (
            [self.value(child, env, path) for child in arguments.named_children]
            if arguments
            else []
        )
        if target is not None and target.type == "member_expression":
            source = self.value(target.child_by_field_name("object"), env, path)
            operation = _text(target.child_by_field_name("property"))
            if operation == "map" and render and args:
                tags = {
                    "item:catalog" if tag == "catalog" else "record:" + tag.split(":", 1)[1]
                    for tag in source.tags
                    if tag == "catalog" or tag.startswith("records:")
                }
                return self.invoke(args[0], [_Value(tags=tags)], True)
            if operation == "then" and args:
                return self.invoke(args[0], [source])
        fn = self.value(target, env, path)
        if fn.sdk == "useState":
            return _Value(state=True)
        if fn.sdk == "useEffect":
            return self.invoke(args[0], []) if args else _Value()
        if fn.sdk == "useCallback":
            return args[0] if args else _Value()
        if fn.sdk in {"useYleumAppProfile", "useYleumCatalog"}:
            return _Value(
                tags={"profile-hook" if fn.sdk == "useYleumAppProfile" else "catalog-hook"}
            )
        if fn.sdk == "getManagedRecords" and args and args[0].literal in _CREATE:
            return _Value(tags={"response:" + str(args[0].literal)})
        if fn.sdk in _CREATE.values() and self.event_depth and args and args[0].props:
            self.created.add(
                next(resource for resource, method in _CREATE.items() if method == fn.sdk)
            )
        return self.invoke(fn, args, render)

    def jsx(self, node: Node, env: dict[str, _Value], path: str) -> _Value:
        opening = node.child_by_field_name("open_tag") if node.type == "jsx_element" else node
        props = {}
        if opening is not None:
            for attribute in opening.named_children:
                if attribute.type == "jsx_attribute":
                    name = _text(attribute.named_children[0])
                    attribute_value = (
                        attribute.named_children[1] if len(attribute.named_children) > 1 else None
                    )
                    props[name] = (
                        self.value(
                            attribute_value.named_children[0]
                            if attribute_value is not None
                            and attribute_value.type == "jsx_expression"
                            and attribute_value.named_children
                            else attribute_value,
                            env,
                            path,
                        )
                        if attribute_value
                        else _Value(literal=True)
                    )
            if any(props.get(key, _Value()).literal is True for key in ("hidden", "disabled")):
                return _Value()
            name = _text(opening.child_by_field_name("name"))
            if name and name[0].isupper():
                return self.invoke(env.get(name, _Value()), [_Value(props=props)], True)
            for key, value in props.items():
                if key in _INTRINSIC_EVENTS:
                    self.event_depth += 1
                    self.invoke(value, [])
                    self.event_depth -= 1
                elif key in _VISIBLE_FIELDS.get(name, ()):
                    self.rendered.update(value.tags)
        for child in node.named_children:
            if child.type == "jsx_expression":
                for expression in child.named_children:
                    self.value(expression, env, path, True)
            elif child.type in {"jsx_element", "jsx_self_closing_element", "jsx_fragment"}:
                self.jsx(child, env, path)
        return _Value()

    def analyze(self) -> None:
        if "src/app/page.tsx" in self.programs:
            self.invoke(self.module("src/app/page.tsx").get("default", _Value()), [], True)


def _admission(files: Mapping[str, str]) -> tuple[str | None, frozenset[str]]:
    try:
        contract = _contract(files)
    except ValueError:
        return (
            "Invalid .omnia/management.json: use the canonical managed contract and hash.",
            frozenset(),
        )
    if contract is None:
        return None, frozenset()
    connections = _Connections(files)
    connections.analyze()
    if "profile-field" not in connections.rendered:
        return (
            "Connect useYleumAppProfile from app-profile-client "
            "to a rendered app name/settings field.",
            frozenset(),
        )
    capabilities: set[str] = set()
    for module in contract.modules:
        name = str(module.value)
        if name == "catalog":
            if "item:catalog" not in connections.rendered:
                return (
                    "Connect useYleumCatalog items to the rendered catalog "
                    "(items.map with item fields).",
                    frozenset(),
                )
            capabilities.add("catalog")
        elif name in _CREATE:
            if name not in connections.created or "record:" + name not in connections.rendered:
                return (
                    f"Connect {_CREATE[name]} to a live form/button and "
                    f"getManagedRecords('{name}').records to rendered history.",
                    frozenset(),
                )
            capabilities.add("history")
            if name == "bookings":
                capabilities.add("booking")
    return None, frozenset(capabilities)


def management_source_gap(files: Mapping[str, str]) -> str | None:
    return _admission(files)[0]


def management_implemented_capabilities(files: Mapping[str, str]) -> frozenset[str]:
    return _admission(files)[1]

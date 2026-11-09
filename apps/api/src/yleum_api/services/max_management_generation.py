"""Bounded TSX consumer connections, never browser/behavioural proof.

Supports named SDK imports, local functions, React state/effects, returned JSX,
collection maps and imported components with callback props. Unproven wrappers,
dynamic imports and arbitrary control/data flow fail closed for new contracts.
Generated code is parsed, never executed. Runtime probes remain a separate gate.
"""

from __future__ import annotations

import hashlib
import json
import math
import posixpath
import re
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, field
from enum import StrEnum
from functools import lru_cache

import tree_sitter_typescript
from tree_sitter import Language, Node, Parser

from yleum_api.schemas.max_management import ManagementContract
from yleum_api.services.max_management_contract import parse_management_contract

_MANIFEST = ".omnia/management.json"
_MAX_COLLECTION_ITEMS = 16
_CATALOG_SCALAR_FIELDS = {
    "id", "title", "category", "description", "price", "availability",
    "image_url", "action_label", "active",
}
_LOOKUP_SCAN_LIMIT = 16000
_FUNCTION_SCOPES = {"function_declaration", "function_expression", "arrow_function",
                    "method_definition"}
_CREATE = {
    "orders": "createManagedOrder",
    "leads": "createManagedLead",
    "bookings": "createManagedBooking",
}
_SDK = {
    "@/lib/omnia/business-client": {*_CREATE.values(), "getManagedRecords"},
    "@/lib/omnia/app-profile-client": {"useYleumAppProfile"},
    "@/lib/omnia/catalog-client": {"useYleumCatalog", "useYleumCatalogSelection"},
    "react": {"useState", "useEffect", "useCallback"},
}
_CHECKOUT_SOURCE = "src/lib/omnia/integration-client"
_LEGACY_CHECKOUT = {"createYleumPayment", "createOmniaPayment"}
_LINKED_CHECKOUT = {"createYleumOrderCheckout", "getYleumOrderCheckout"}
_CHECKOUT_ORDER_ID = "checkout-order-id"
# Only the explicitly supported React form/button events are connection evidence.
_INTRINSIC_EVENTS = {"onClick", "onClickCapture", "onSubmit", "onSubmitCapture"}
_VISIBLE_FIELDS = {
    "input": {"value", "defaultValue", "placeholder"},
    "textarea": {"value", "defaultValue", "placeholder"},
    "img": {"alt", "src"},
}
_NULL_LITERAL = object()
_UNSUPPORTED_LITERAL = object()
_DECIMAL_LITERAL = re.compile(
    r"(?:0|[1-9][0-9]*)(?:\.[0-9]*)?(?:[eE][+-]?[0-9]+)?|\.[0-9]+(?:[eE][+-]?[0-9]+)?"
)
_TYPE_ONLY_DECLARATIONS = {
    "interface_declaration", "type_alias_declaration", "ambient_declaration", "function_signature",
}


def _primitive_literal(node: Node) -> object:
    kind = node.type
    text = _text(node)
    if kind in {"true", "false"}:
        return kind == "true"
    if kind == "null":
        return _NULL_LITERAL
    if kind == "string":
        # Do not guess JS escape semantics using Python string decoding.
        return text[1:-1] if "\\" not in text else _UNSUPPORTED_LITERAL
    if _DECIMAL_LITERAL.fullmatch(text):
        number = float(text)
        if math.isfinite(number):
            return number
    return _UNSUPPORTED_LITERAL


def _literal_truth(value: object) -> bool | None:
    if value is _NULL_LITERAL:
        return False
    return bool(value) if type(value) in {bool, str, float} else None


def _comparison_literal(left: object, right: object, operator: str) -> object:
    if left is _UNSUPPORTED_LITERAL or right is _UNSUPPORTED_LITERAL:
        return _UNSUPPORTED_LITERAL
    if left is None or right is None:
        return None
    # Only same-type loose equality needs no JS coercion. Unsupported constant
    # conditions must not turn into hypothetical runtime paths granting writes.
    same_type = type(left) is type(right)
    if operator in {"==", "!="} and not same_type:
        return _UNSUPPORTED_LITERAL
    if operator not in {"===", "!==", "==", "!="}:
        return _UNSUPPORTED_LITERAL
    equal = same_type and left == right
    return not equal if operator in {"!==", "!="} else equal


def _text(node: Node | None) -> str:
    return (node.text or b"").decode() if node is not None else ""


@lru_cache(maxsize=1)
def _language() -> Language:
    return Language(tree_sitter_typescript.language_tsx())


def _anonymous_owner_site(node: Node) -> bytes:
    path = []
    while (parent := node.parent) is not None:
        index = sum(
            child.start_byte < node.start_byte and child.type not in {"comment", "jsx_text"}
            for child in parent.named_children
        )
        path.append(f"{node.type}:{index}")
        if parent.type in _FUNCTION_SCOPES:
            break
        node = parent
    return "/".join(reversed(path)).encode()


def _rejection_fingerprint(node: Node) -> str:
    statement = node
    while statement.parent is not None and statement.type not in {
        "variable_declarator", "expression_statement", "return_statement",
        "for_in_statement", "if_statement", "statement_block", "program",
    }:
        statement = statement.parent

    parts = [node.type.encode()]
    owner = statement.parent
    while owner is not None:
        if owner.type in _FUNCTION_SCOPES:
            name = owner.child_by_field_name("name")
            if name is None and owner.parent is not None:
                if owner.parent.type == "variable_declarator":
                    name = owner.parent.child_by_field_name("name")
            parts.append(_text(name).encode() if name is not None else _anonymous_owner_site(owner))
        owner = owner.parent

    pending = [statement]
    while pending:
        token = pending.pop()
        if token.type == "comment":
            continue
        if token.child_count:
            pending.extend(reversed(token.children))
        else:
            parts.append(token.text or b"")
    return hashlib.sha256(b"\0".join(parts)).hexdigest()[:16]


_SourceIssue = tuple[str, int, int, str, str, str]


def _source_issue(node: Node, path: str, reason: str) -> _SourceIssue:
    safe_path = path if re.fullmatch(r"src/[A-Za-z0-9_./-]{1,180}\.tsx?", path) else "source"
    return (
        safe_path, node.start_point.row + 1, node.start_point.column + 1,
        node.type, reason, _rejection_fingerprint(node),
    )


def _issue_detail(issue: _SourceIssue | None) -> str:
    if issue is None:
        return ""
    path, row, column, kind, reason, fingerprint = issue
    return (
        f" Source location: {path}:{row}:{column}; "
        f"AST {kind}; reason {reason}; fingerprint {fingerprint}."
    )


@dataclass
class _Value:
    tags: set[str] = field(default_factory=set)
    props: dict[str, _Value] = field(default_factory=dict)
    function: tuple[str, Node, dict[str, _Value]] | None = None
    sdk: str = ""
    literal: object = None
    setter: _Value | None = None
    state: bool = False
    elements: tuple[_Value, ...] | None = None
    # Effects-only provenance for filtered SDK aliases. Unlike elements, this
    # never supplies rows to JSX/map or grants a rendered-connection witness.
    statistic_elements: tuple[_Value, ...] | None = None
    pending: _Value | None = None
    mutation_roots: tuple[_Value, ...] = ()
    passive: bool = False
    primitive_string: bool = False
    nullable_string: bool = False
    local_scalar_keys: bool = False
    scalar_key: bool = False
    # Refusal provenance, retained after invalidation and scheduled state changes.
    native_namespace: str = ""
    nullable_catalog_lookup: bool = False
    catalog_lookup_issue: _SourceIssue | None = None
    catalog_items_lookup_issue: _SourceIssue | None = None


def _contract(files: Mapping[str, str]) -> ManagementContract | None:
    if _MANIFEST not in files:
        return None
    return parse_management_contract(json.loads(files[_MANIFEST]))


def management_probe_resources(files: Mapping[str, str]) -> tuple[str, ...]:
    """Only validated canonical resources; malformed manifests raise ValueError."""
    contract = _contract(files)
    return tuple(str(resource.id.value) for resource in contract.resources) if contract else ()


class _Connections:
    def __init__(self, files: Mapping[str, str], *, inspect_checkout: bool = False):
        self.programs = {
            path: Parser(_language()).parse(source.encode()).root_node
            for path, source in files.items()
            if path.startswith("src/")
            and path.endswith((".tsx", ".ts"))
            and not path.startswith(("src/lib/omnia/", "src/app/api/"))
        }
        self.promise = _Value(sdk="Promise")
        self.boolean = _Value(sdk="Boolean")
        self.json_parse = _Value(sdk="trusted-json-parse")
        self.json_stringify = _Value(sdk="trusted-json-stringify")
        self.json = _Value(props={"parse": self.json_parse, "stringify": self.json_stringify})
        self.storage_get_item = _Value(sdk="trusted-storage-get-item")
        self.storage = _Value(props={"getItem": self.storage_get_item})
        self.window = _Value(props={"localStorage": self.storage}, native_namespace="window")
        self.object_keys = _Value(sdk="trusted-object-keys")
        self.object = _Value(props={"keys": self.object_keys})
        self.array = _Value()
        self.reflect = _Value()
        self.global_this = _Value(native_namespace="global")
        self.modules: dict[str, dict[str, _Value]] = {}
        self.created: set[str] = set()
        self.rendered: set[str] = set()
        self.inspect_checkout = inspect_checkout
        self.checkout_entry = False
        self.checkout_legacy_probe = False
        self.checkout_calls: set[str] = set()
        self.catalog_selection = False
        self.unsupported_constant_control = False
        self.unsupported_effects = False
        self.first_unsupported: _SourceIssue | None = None
        self.catalog_render_rejection: _SourceIssue | None = None
        self.effects_only_depth = 0
        self.strict_effects_depth = 0
        self.render_suppression = 0
        self.state_callback_depth = 0
        self.active: set[tuple[str, int]] = set()
        self.steps = 0
        self.event_depth = 0
        self.projection_depth = 0
        self.projections: dict[tuple[str, int], bool] = {}
        self.lookup_scan_steps = 0

    def unsupported(self, node: Node | None, path: str, reason: str) -> None:
        """Identify a rejected source shape without copying source values into feedback."""
        self.unsupported_effects = True
        if self.first_unsupported is None and node is not None:
            self.first_unsupported = _source_issue(node, path, reason)

    @contextmanager
    def effects_only(self, *, strict: bool = False) -> Iterator[None]:
        created, rendered = set(self.created), set(self.rendered)
        selected, checkout = self.catalog_selection, set(self.checkout_calls)
        self.effects_only_depth += 1
        self.strict_effects_depth += int(strict)
        try:
            yield
        finally:
            self.effects_only_depth -= 1
            self.strict_effects_depth -= int(strict)
            self.created, self.rendered = created, rendered
            self.catalog_selection = selected
            # Legacy checkout is negative evidence, never a connection witness.
            self.checkout_calls = checkout | (self.checkout_calls & _LEGACY_CHECKOUT)

    @staticmethod
    def _source_base(path: str, source: str) -> str:
        return (
            "src/" + source[2:]
            if source.startswith("@/")
            else posixpath.normpath(posixpath.join(posixpath.dirname(path), source))
        )

    def _resolve(self, path: str, source: str) -> str | None:
        base = self._source_base(path, source)
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
        env: dict[str, _Value] = {
            "Promise": self.promise, "Boolean": self.boolean, "JSON": self.json,
            "window": self.window, "localStorage": self.storage, "Object": self.object,
            "Array": self.array,
            "Reflect": self.reflect, "globalThis": self.global_this,
        }
        self.modules[path] = env
        program = self.programs[path]
        if program.has_error:
            return env
        for statement in program.named_children:
            if statement.type == "import_statement":
                source = _text(statement.child_by_field_name("source"))[1:-1]
                checkout_import = (
                    self.inspect_checkout and self._source_base(path, source) == _CHECKOUT_SOURCE
                )
                sdk = (
                    _LINKED_CHECKOUT | _LEGACY_CHECKOUT
                    if checkout_import
                    else _SDK.get(source, ())
                )
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
                                if name in sdk:
                                    env[alias] = _Value(sdk=name)
                                else:
                                    target = self._resolve(path, source)
                                    env[alias] = (
                                        self.module(target).get(name, _Value())
                                        if target
                                        else _Value()
                                    )
                        elif child.type == "namespace_import" and checkout_import:
                            env[_text(child.named_children[0])] = _Value(
                                props={name: _Value(sdk=name) for name in sdk}
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

    def binding_is_supported(self, pattern: Node | None, value: _Value) -> bool:
        """Prove passive bindings, never omit defaults, computed keys or getters."""
        pending = [(pattern, value)]
        while pending:
            current, source = pending.pop()
            self.steps += 1
            if current is None or self.steps > 4000:
                return False
            kind = current.type
            if kind in {"identifier", "shorthand_property_identifier_pattern"}:
                continue
            if kind in {"required_parameter", "optional_parameter"}:
                if current.child_by_field_name("value") is not None:
                    return False
                pending.append((current.child_by_field_name("pattern"), source))
            elif kind == "object_pattern":
                if not source.props and not source.tags:
                    return False
                for child in current.named_children:
                    if child.type == "comment":
                        continue
                    if child.type == "pair_pattern":
                        key_node = child.child_by_field_name("key")
                        if key_node is None or key_node.type != "property_identifier":
                            return False
                        key = _text(key_node)
                        target = child.child_by_field_name("value")
                    elif child.type == "shorthand_property_identifier_pattern":
                        key, target = _text(child), child
                    else:
                        return False
                    # Known JSX/literal props and locked SDK data are passive.
                    # An omitted/unknown property could execute a source getter.
                    if key not in source.props and not source.tags:
                        return False
                    pending.append((target, self.member(source, key)))
            elif kind == "array_pattern":
                children = [c for c in current.named_children if c.type != "comment"]
                if source.state:
                    if len(children) > 2 or any(c.type != "identifier" for c in children):
                        return False
                    expecting_value = True
                    for child in current.children:
                        if child.type == ",":
                            if expecting_value:
                                return False
                            expecting_value = True
                        elif child.is_named and child.type != "comment":
                            expecting_value = False
                elif source.elements is None:
                    return False
                else:
                    index = 0
                    for child in current.children:
                        if child.type == ",":
                            index += 1
                        elif child.is_named and child.type != "comment":
                            element = (
                                source.elements[index]
                                if index < len(source.elements) else _Value()
                            )
                            pending.append((child, element))
            else:
                return False
        return True

    def bind(
        self, pattern: Node | None, value: _Value, env: dict[str, _Value], path: str,
    ) -> None:
        if pattern is None:
            return
        if not self.binding_is_supported(pattern, value):
            self.unsupported(pattern, path, "unsupported_binding")
            return
        self.bind_supported(pattern, value, env)

    @staticmethod
    def data_alias(
        source: _Value, *, state: bool = False, target: _Value | None = None,
    ) -> _Value:
        """Copy modeled data while retaining effects through every source alias."""
        alias = _Value(state=state) if target is None else target
        previous_roots = alias.mutation_roots
        alias.tags = set(source.tags)
        alias.props = dict(source.props)
        alias.elements = source.elements
        alias.statistic_elements = source.statistic_elements
        alias.passive = source.passive
        alias.primitive_string = source.primitive_string
        alias.nullable_string = source.nullable_string
        alias.local_scalar_keys = source.local_scalar_keys
        alias.scalar_key = source.scalar_key
        alias.native_namespace = (
            "global" if "global" in {alias.native_namespace, source.native_namespace}
            else source.native_namespace or alias.native_namespace
        )
        # React setters schedule a future render. Current render aliases can
        # still reference the previous value after setState([]), so retain it.
        alias.mutation_roots = (*previous_roots, source)
        return alias

    def bind_supported(self, pattern: Node | None, value: _Value, env: dict[str, _Value]) -> None:
        if pattern is None:
            return
        if pattern.type in {"identifier", "shorthand_property_identifier_pattern"}:
            env[_text(pattern)] = value
        elif pattern.type in {"required_parameter", "optional_parameter"}:
            self.bind_supported(pattern.child_by_field_name("pattern"), value, env)
        elif pattern.type == "object_pattern":
            for child in pattern.named_children:
                if child.type == "comment":
                    continue
                key = (
                    _text(child.child_by_field_name("key"))
                    if child.type == "pair_pattern"
                    else _text(child)
                )
                self.bind_supported(
                    child.child_by_field_name("value") if child.type == "pair_pattern" else child,
                    self.member(value, key),
                    env,
                )
        elif pattern.type == "array_pattern" and value.state:
            children = [child for child in pattern.named_children if child.type != "comment"]
            state = self.data_alias(value)
            if children:
                self.bind_supported(children[0], state, env)
            if len(children) > 1:
                self.bind_supported(children[1], _Value(setter=state), env)
        elif pattern.type == "array_pattern":
            index = 0
            for child in pattern.children:
                if child.type == ",":
                    index += 1
                elif child.is_named and child.type != "comment":
                    element = (
                        value.elements[index]
                        if value.elements is not None and index < len(value.elements)
                        else _Value()
                    )
                    self.bind_supported(child, element, env)

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
                        path,
                    )
        elif node.type == "expression_statement":
            self.statement(node, env, path, False)
        elif node.type not in _TYPE_ONLY_DECLARATIONS | {"comment", "empty_statement"}:
            self.unsupported(node, path, "unsupported_declaration")

    def member(self, value: _Value, key: str) -> _Value:
        if (value.native_namespace == "global"
                or (value.native_namespace == "window"
                    and (key != "localStorage" or value.props.get(key) is not self.storage))):
            self.unsupported(None, "", "unproven_native_namespace_access")
            return _Value()
        if key in {"prototype", "__proto__", "constructor", "Reflect"}:
            # This bounded JSON trust has no prototype interpreter. Refuse
            # handles that could install inherited toJSON, including aliases.
            self.unsupported(None, "", "unproven_prototype_access")
            return _Value()
        if key in value.props:
            return value.props[key]
        if key == "refresh" and value.tags & {"catalog-hook", "profile-hook"}:
            # The locked hooks expose a read/refresh operation, not an opaque mutator.
            return _Value(sdk="managed-hook-refresh")
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
        elements = tuple(
            _Value(tags={"item:catalog" if tag == "catalog" else "record:" + tag.split(":", 1)[1]})
            for tag in tags
            if tag == "catalog" or tag.startswith("records:")
        )
        result = _Value(
            tags=tags, elements=elements or None, mutation_roots=value.mutation_roots,
            passive=value.passive or bool(value.tags),
            catalog_lookup_issue=value.catalog_items_lookup_issue if key == "items" else None,
        )
        if elements:
            # Re-reading a response property must observe mutations to that collection.
            value.props[key] = result
        return result

    def mutation_root(self, node: Node | None, env: dict[str, _Value]) -> _Value | None:
        """Find mutation authority without interpreting property/index values."""
        while node is not None:
            self.steps += 1
            if self.steps > 4000:
                return None
            if node.type == "identifier":
                return env.get(_text(node))
            if node.type in {"member_expression", "subscript_expression"}:
                key_node = node.child_by_field_name(
                    "property" if node.type == "member_expression" else "index"
                )
                key: object = None
                if node.type == "member_expression":
                    key = _text(key_node)
                elif key_node is not None:
                    key = (
                        env.get(_text(key_node), _Value()).literal
                        if key_node.type == "identifier" else _primitive_literal(key_node)
                    )
                if key in {"prototype", "__proto__", "constructor", "toJSON"}:
                    self.unsupported(None, "", "unproven_prototype_mutation")
                node = node.child_by_field_name("object")
            elif node.type in {"as_expression", "parenthesized_expression", "non_null_expression"}:
                node = node.named_children[0] if node.named_children else None
            else:
                return None
        return None

    def invalidate(
        self, value: _Value, *, node: Node | None = None, path: str = "",
    ) -> None:
        """Unknown mutation cannot retain evidence through another collection alias."""
        pending = [value]
        seen = set()
        while pending:
            current = pending.pop()
            if id(current) in seen:
                continue
            seen.add(id(current))
            if (current is self.object or current is self.array
                    or current is self.reflect or current is self.global_this):
                # Unknown reflection operations and escaped intrinsic handles
                # cannot preserve the assumption of unchanged JSON prototypes.
                self.unsupported(node, path, "unproven_native_intrinsic_effect")
            pending.extend(
                (
                    *current.props.values(),
                    *(current.elements or ()),
                    *(current.statistic_elements or ()),
                    *current.mutation_roots,
                )
            )
            current.mutation_roots = ()
            if current.pending is not None:
                pending.append(current.pending)
            current.pending = None
            if "catalog-hook" in current.tags and (items := current.props.get("items")):
                # Keep the diagnostic across a fresh `.items` read without
                # preserving the collection's evidence or its object fields.
                current.catalog_items_lookup_issue = items.catalog_lookup_issue
            current.tags.clear()
            current.props.clear()
            current.elements = None
            current.statistic_elements = None
            current.sdk = ""
            current.function = None
            current.setter = None
            current.literal = None
            current.passive = False
            current.primitive_string = False
            current.nullable_string = False
            current.local_scalar_keys = False
            current.scalar_key = False
            current.nullable_catalog_lookup = False

    def projection_is_bounded(self, path: str, node: Node) -> bool:
        key = (path, node.start_byte)
        if key not in self.projections:
            pending = [node]
            allowed = True
            while pending:
                child = pending.pop()
                self.steps += 1
                if self.steps > 4000 or child.type in {
                    "binary_expression",
                    "ternary_expression",
                    "if_statement",
                    "switch_statement",
                    "for_statement",
                    "for_in_statement",
                    "while_statement",
                    "do_statement",
                    "assignment_expression",
                    "augmented_assignment_expression",
                    "update_expression",
                    "spread_element",
                    "throw_statement",
                    "try_statement",
                    "new_expression",
                }:
                    allowed = False
                    break
                pending.extend(child.named_children)
            self.projections[key] = allowed
        return self.projections[key]

    @staticmethod
    def callback_expression(
        fn: _Value, arity: int,
    ) -> tuple[str, Node, dict[str, _Value], tuple[str, ...]] | None:
        """Extract a synchronous callback without parameter initialization effects."""
        if fn.function is None:
            return None
        path, node, closure = fn.function
        if any(child.type == "async" for child in node.children):
            return None
        parameters = node.child_by_field_name("parameters")
        children = (
            list(parameters.named_children) if parameters is not None
            else [node.child_by_field_name("parameter")]
        )
        if len(children) != arity or any(
            child is None or child.child_by_field_name("value") is not None for child in children
        ):
            return None
        patterns = [
            child if child.type == "identifier" else child.child_by_field_name("pattern")
            for child in children if child is not None
        ]
        if any(pattern is None or pattern.type != "identifier" for pattern in patterns):
            return None
        names = tuple(_text(pattern) for pattern in patterns)
        body = node.child_by_field_name("body")
        if body is None or len(set(names)) != arity:
            return None
        return path, body, closure, names

    @staticmethod
    def passive_value(value: _Value) -> bool:
        return (
            value.passive or bool(value.tags)
            or type(value.literal) in {bool, str, float}
            or value.literal is _NULL_LITERAL
        ) and value.function is None and not value.sdk and value.setter is None

    def read_only_callback(self, fn: _Value, args: list[_Value], depth: int = 0) -> bool:
        """Prove effects only: this never creates rendered-data or write witnesses."""
        callback = self.callback_expression(fn, len(args))
        if callback is None:
            return False
        path, body, closure, names = callback
        if body.type == "statement_block":
            statements = [child for child in body.named_children if child.type != "comment"]
            if len(statements) != 1 or statements[0].type != "return_statement":
                return False
            returned = [
                child for child in statements[0].named_children if child.type != "comment"
            ]
            if len(returned) != 1:
                return False
            body = returned[0]
        scope = {**closure, **dict(zip(names, args, strict=True))}
        return self.read_only_value(body, scope, path, depth + 1) is not None

    def read_only_value(
        self, node: Node | None, env: dict[str, _Value], path: str, depth: int = 0,
    ) -> _Value | None:
        """A bounded passive-expression subset for non-rendering statistics.

        SDK rows are JSON data. Pure field reads, operators and literal objects
        cannot mutate them; arbitrary calls, bindings, getters, spreads and
        statements remain unsupported. Unlike the normal interpreter this
        probe neither executes an opaque call nor grants a connection witness.
        """
        self.steps += 1
        if node is None or depth > 32 or self.steps > 4000:
            return None
        kind = node.type
        if kind == "identifier":
            bound = env.get(_text(node), _Value())
            return bound if self.passive_value(bound) else None
        if kind in {"true", "false", "string", "number", "null"}:
            literal = _primitive_literal(node)
            return None if literal is _UNSUPPORTED_LITERAL else _Value(literal=literal)
        if kind in {"parenthesized_expression", "as_expression", "non_null_expression"}:
            return self.read_only_value(
                node.named_children[0] if node.named_children else None, env, path, depth + 1,
            )
        if kind == "member_expression":
            source = self.read_only_value(node.child_by_field_name("object"), env, path, depth + 1)
            if source is not None and self.passive_value(source):
                return self.member(source, _text(node.child_by_field_name("property")))
            return None
        if kind == "array":
            values = []
            expecting_value = True
            for child in node.children:
                if child.type == ",":
                    if expecting_value:
                        return None
                    expecting_value = True
                elif child.is_named and child.type != "comment":
                    value = self.read_only_value(child, env, path, depth + 1)
                    if value is None or not self.passive_value(value):
                        return None
                    values.append(value)
                    expecting_value = False
            return (
                _Value(elements=tuple(values), passive=True)
                if len(values) <= _MAX_COLLECTION_ITEMS else None
            )
        if kind == "binary_expression":
            if _text(node.child_by_field_name("operator")) not in {
                "===", "!==", "==", "!=", "<", ">", "<=", ">=",
                "+", "-", "*", "/", "%", "&&", "||", "??",
            }:
                return None
            operands = [node.child_by_field_name(key) for key in ("left", "right")]
        elif kind == "ternary_expression":
            operands = [node.child_by_field_name(key) for key in (
                "condition", "consequence", "alternative",
            )]
        elif kind == "unary_expression" and _text(node.children[0]) in {"!", "+", "-", "typeof"}:
            operands = [node.child_by_field_name("argument")]
        elif kind == "object":
            props = {}
            for child in node.named_children:
                if child.type == "comment":
                    continue
                if child.type == "pair":
                    key = child.child_by_field_name("key")
                    if (key is None or key.type != "property_identifier"
                            or _text(key) == "__proto__"):
                        return None
                    name, value_node = _text(key), child.child_by_field_name("value")
                elif child.type == "shorthand_property_identifier":
                    name, value_node = _text(child), child
                else:
                    return None
                value = (
                    env.get(name) if value_node is child
                    else self.read_only_value(value_node, env, path, depth + 1)
                )
                if value is None or not self.passive_value(value):
                    return None
                props[name] = value
            return _Value(props=props, passive=True)
        elif kind == "call_expression":
            target = node.child_by_field_name("function")
            arguments = node.child_by_field_name("arguments")
            if target is None or target.type != "member_expression" or arguments is None:
                return None
            source = self.read_only_value(
                target.child_by_field_name("object"), env, path, depth + 1,
            )
            operation = _text(target.child_by_field_name("property"))
            arg_nodes = list(arguments.named_children)
            elements = self.statistic_source_elements(source) if source is not None else None
            if source is None or elements is None or operation not in {"filter", "reduce"}:
                return None
            if len(arg_nodes) != (1 if operation == "filter" else 2):
                return None
            callback_node = arg_nodes[0]
            if callback_node.type in {"arrow_function", "function_expression"}:
                callback = _Value(function=(path, callback_node, env))
            elif callback_node.type == "identifier":
                callback = env.get(_text(callback_node), _Value())
            else:
                return None
            initial = (
                self.read_only_value(arg_nodes[1], env, path, depth + 1)
                if operation == "reduce" else None
            )
            if operation == "reduce" and initial is None:
                return None
            for element in elements or (_Value(passive=True),):
                args = [element]
                if operation == "reduce":
                    assert initial is not None
                    args.insert(0, initial)
                if not self.read_only_callback(callback, args, depth + 1):
                    return None
            # Only this internal effects probe carries the filter collection;
            # call() deliberately discards its result's SDK identity.
            return _Value(elements=elements if operation == "filter" else None, passive=True)
        else:
            return None
        if all(
            self.read_only_value(operand, env, path, depth + 1) is not None
            for operand in operands
        ):
            return _Value(passive=True)
        return None

    @staticmethod
    def statistic_source_elements(source: _Value) -> tuple[_Value, ...] | None:
        return source.elements if source.elements is not None else source.statistic_elements

    def statistic_call_is_read_only(
        self, operation: str, args: list[_Value], source: _Value,
    ) -> bool:
        known_elements = self.statistic_source_elements(source)
        if known_elements is None:
            return False
        if (operation == "filter" and len(args) == 1 and args[0] is self.boolean
                and self.boolean.sdk == "Boolean"):
            # Only the unshadowed intrinsic applied to proven local primitive
            # scalars. Unknown objects/getters, SDK rows and callbacks stay red.
            return not self.contains_authority([source]) and all(
                type(element.literal) in {bool, str, float}
                or element.literal is _NULL_LITERAL
                for element in known_elements
            )
        elements = known_elements or (_Value(passive=True),)
        if operation == "filter" and len(args) == 1:
            return all(self.read_only_callback(args[0], [element]) for element in elements)
        if operation == "reduce" and len(args) == 2 and self.passive_value(args[1]):
            return all(
                self.read_only_callback(args[0], [args[1], element])
                for element in elements
            )
        return False

    def statistic_result(
        self, operation: str, args: list[_Value], source: _Value,
        elements: tuple[_Value, ...],
    ) -> _Value:
        if operation == "filter":
            if not self.contains_authority([source]):
                return _Value(
                    elements=elements, passive=True, local_scalar_keys=source.local_scalar_keys,
                )
            return _Value(
                props={"length": _Value(passive=True)}, statistic_elements=elements,
                mutation_roots=(source,), passive=True,
            )
        roots = tuple(
            root for element in elements or (_Value(passive=True),)
            for root in self.statistic_alias_roots(args[0], [args[1], element])
        )
        return _Value(mutation_roots=roots, passive=True)

    def statistic_alias_roots(self, fn: _Value, args: list[_Value]) -> tuple[_Value, ...]:
        callback = self.callback_expression(fn, len(args))
        if callback is None:
            return ()
        _, body, closure, names = callback
        scope = {**closure, **dict(zip(names, args, strict=True))}
        return self.referenced_authority_roots(body, scope)

    def referenced_authority_roots(
        self, body: Node, scope: dict[str, _Value],
    ) -> tuple[_Value, ...]:
        pending, referenced = [body], set()
        while pending and self.steps <= 4000:
            node = pending.pop()
            self.steps += 1
            if node.type in {"identifier", "shorthand_property_identifier"}:
                referenced.add(_text(node))
            pending.extend(node.named_children)
        return tuple(
            scope[name] for name in sorted(referenced)
            if name in scope and self.contains_authority([scope[name]])
        )

    def sort_is_read_only(self, fn: _Value, elements: tuple[_Value, ...]) -> bool:
        # Only the candidate's proven timestamp comparison, never arbitrary sort
        # callbacks which could mutate records/collections while returning them.
        callback = self.callback_expression(fn, 2)
        if callback is None:
            return False
        path, body, closure, names = callback
        if body.type != "call_expression":
            return False
        target = body.child_by_field_name("function")
        arguments = body.child_by_field_name("arguments")
        if (
            target is None
            or target.type != "member_expression"
            or _text(target.child_by_field_name("property")) != "localeCompare"
            or arguments is None
            or len(arguments.named_children) != 1
        ):
            return False
        operands = [target.child_by_field_name("object"), arguments.named_children[0]]
        for operand in operands:
            if (
                operand is None
                or operand.type != "member_expression"
                or _text(operand.child_by_field_name("property")) != "created_at"
            ):
                return False
            base: Node | None = operand
            while base is not None and base.type == "member_expression":
                base = base.child_by_field_name("object")
            if base is None or base.type != "identifier" or _text(base) not in names:
                return False
        for element in elements:
            scope = {**closure, **dict.fromkeys(names, element)}
            if any(
                not any(
                    tag.startswith("record:")
                    for tag in self.value(
                        operand,
                        scope,
                        path,
                    ).tags
                )
                for operand in operands
            ):
                return False
        return True

    def catalog_id_find_rejection(self, call: Node, fn: _Value, path: str) -> _SourceIssue | None:
        """Identify the first unproven lookup shape or use without source values."""
        reason = "unproven_catalog_id_lookup"
        callback = self.callback_expression(fn, 1)
        if callback is None:
            return _source_issue(call, path, reason)
        predicate_path, body, closure, (parameter,) = callback
        initializer = call
        wrapper = call.parent
        if wrapper is not None and wrapper.type == "binary_expression":
            fallback = wrapper.child_by_field_name("right")
            if (
                _text(wrapper.child_by_field_name("operator")) == "??"
                and wrapper.child_by_field_name("left") == call
                and fallback is not None and fallback.type == "null"
            ):
                initializer = wrapper
        declaration = initializer.parent
        lexical = declaration.parent if declaration is not None else None
        scope = lexical.parent if lexical is not None else None
        if (
            declaration is None or declaration.type != "variable_declarator"
            or declaration.child_by_field_name("value") != initializer
            or lexical is None or lexical.type != "lexical_declaration"
            or _text(lexical.child_by_field_name("kind")) != "const"
            or scope is None or scope.type != "statement_block"
        ):
            return _source_issue(call, path, reason)
        name = declaration.child_by_field_name("name")
        if name is None or name.type != "identifier" or "\\" in _text(name):
            return _source_issue(call, path, reason)
        predicate: Node | None = body
        if body.type == "binary_expression" and _text(body.child_by_field_name("operator")) == "&&":
            if not self._catalog_row_field(body.child_by_field_name("right"), parameter, "active"):
                return _source_issue(body, predicate_path, reason)
            predicate = body.child_by_field_name("left")
        if (
            predicate is None or predicate.type != "binary_expression"
            or _text(predicate.child_by_field_name("operator")) != "==="
        ):
            return _source_issue(body, predicate_path, reason)
        left = predicate.child_by_field_name("left")
        right = predicate.child_by_field_name("right")
        inline_predicate = (
            fn.function is not None
            and fn.function[1].parent == call.child_by_field_name("arguments")
        )
        for member, selected_id in ((left, right), (right, left)):
            trusted_key = (
                selected_id is not None and selected_id.type == "identifier"
                and closure.get(_text(selected_id), _Value()).scalar_key
            )
            if (
                self._catalog_row_field(member, parameter, "id")
                and selected_id is not None and _text(selected_id) != parameter
                and (selected_id.type == "string" or inline_predicate)
                and (self._catalog_id_available(selected_id, lexical, scope) or trusted_key)
            ):
                use = self._catalog_lookup_unproven_use(scope, name, path)
                return (
                    _source_issue(use, path, "unproven_catalog_lookup_use") if use else None
                )
        return _source_issue(body, predicate_path, reason)

    @staticmethod
    def _catalog_row_field(node: Node | None, parameter: str, field: str) -> bool:
        return (
            node is not None and node.type == "member_expression"
            and _text(node.child_by_field_name("object")) == parameter
            and _text(node.child_by_field_name("property")) == field
        )

    def _catalog_map_has_direct_row_content(self, fn: _Value) -> bool:
        """Localize only a simple render that would consume a row field."""
        callback = self.callback_expression(fn, 1)
        if callback is None:
            return False
        _, body, _, (row,) = callback
        if body.type == "parenthesized_expression" and len(body.named_children) == 1:
            body = body.named_children[0]
        opening = body.child_by_field_name("open_tag") if body.type == "jsx_element" else None
        name = _text(opening.child_by_field_name("name")) if opening is not None else ""
        if not name or not name[0].islower() or opening is None:
            return False
        if any(
            node.type == "jsx_attribute" and _text(node.named_children[0]) in {"hidden", "disabled"}
            for node in opening.named_children
        ):
            return False
        for child in body.named_children:
            if child.type == "jsx_expression" and len(child.named_children) == 1:
                expression = child.named_children[0]
                field = _text(expression.child_by_field_name("property"))
                if (
                    field in _CATALOG_SCALAR_FIELDS
                    and self._catalog_row_field(expression, row, field)
                ):
                    return True
        return False

    @staticmethod
    def _catalog_id_available(selected_id: Node, lexical: Node, scope: Node) -> bool:
        if selected_id.type == "string":
            return True
        if selected_id.type != "identifier":
            return False
        selected_name = _text(selected_id)
        for statement in scope.named_children:
            if statement == lexical:
                break
            if statement.type != "lexical_declaration":
                continue
            for declaration in statement.named_children:
                binding = declaration.child_by_field_name("name")
                if binding is None or declaration.child_by_field_name("value") is None:
                    continue
                names = binding.named_children if binding.type == "array_pattern" else [binding]
                if any(
                    name.type == "identifier" and _text(name) == selected_name for name in names
                ):
                    return True
        return False

    def _catalog_lookup_unproven_use(self, scope: Node, binding: Node, path: str) -> Node | None:
        # A lookup may return an alias to a live SDK row. Prove its local uses
        # before treating the result as a value without authority.
        name = _text(binding)
        pending = [scope]
        while pending:
            node = pending.pop()
            self.lookup_scan_steps += 1
            if self.lookup_scan_steps > _LOOKUP_SCAN_LIMIT:
                return binding
            if node.type in {"identifier", "shorthand_property_identifier",
                             "shorthand_property_identifier_pattern"}:
                identifier = _text(node)
                if "\\" in identifier or (
                    identifier == name and node != binding
                    and not self._catalog_lookup_read(node, path)
                ):
                    return node
            pending.extend(node.named_children)
        return None

    def _catalog_lookup_read(self, node: Node, path: str) -> bool:
        parent = node.parent
        if parent is not None and parent.type == "variable_declarator":
            # A nested declaration can use the same spelling. The binding is
            # inert; keep checking every reference, including inside its scope.
            return node == parent.child_by_field_name("name")
        if parent is not None and parent.type == "member_expression":
            field = _text(parent.child_by_field_name("property"))
            if node != parent.child_by_field_name("object"):
                return False
            if field in _CATALOG_SCALAR_FIELDS:
                return self._catalog_member_read(parent)
            if field != "options":
                return False
            method = parent.parent
            if method is None or method.type != "member_expression":
                return False
            operation = _text(method.child_by_field_name("property"))
            if operation == "length":
                return self._catalog_member_read(method)
            call = method.parent
            if (
                operation != "map" or call is None or call.type != "call_expression"
                or call.child_by_field_name("function") != method
            ):
                return False
            args = call.child_by_field_name("arguments")
            return bool(
                args is not None and len(args.named_children) == 1
                and args.named_children[0].type == "arrow_function"
                and self.callback_expression(_Value(function=(path, args.named_children[0], {})), 1)
            )
        while parent is not None:
            if parent.type in {"if_statement", "ternary_expression"}:
                return node == parent.child_by_field_name("condition")
            if parent.type == "unary_expression":
                return _text(parent.children[0]) in {"!", "typeof"}
            if parent.type == "binary_expression":
                operation = _text(parent.child_by_field_name("operator"))
                if operation in {"===", "!=="}:
                    return True
                if operation == "&&" and node == parent.child_by_field_name("left"):
                    return True
                if operation not in {"&&", "||", "??"}:
                    return False
            elif parent.type != "parenthesized_expression":
                return False
            node, parent = parent, parent.parent
        return False

    def _catalog_member_read(self, member: Node) -> bool:
        node = member
        while (parent := node.parent) is not None:
            self.lookup_scan_steps += 1
            if self.lookup_scan_steps > _LOOKUP_SCAN_LIMIT:
                return False
            if parent.type in {"assignment_expression", "augmented_assignment_expression",
                               "for_in_statement"} and node == parent.child_by_field_name("left"):
                return False
            if parent.type == "update_expression" or (
                parent.type == "unary_expression" and _text(parent.children[0]) == "delete"
            ):
                return False
            if parent.type == "statement_block":
                return True
            node = parent
        return True

    def category_find_index_is_read_only(self, fn: _Value, elements: tuple[_Value, ...]) -> bool:
        callback = self.callback_expression(fn, 1)
        if callback is None:
            return False
        path, body, closure, names = callback
        if body.type == "binary_expression" and _text(body.child_by_field_name("operator")) == "&&":
            active = body.child_by_field_name("left")
            if (active is None or active.type != "member_expression"
                    or _text(active.child_by_field_name("property")) != "active"
                    or _text(active.child_by_field_name("object")) != names[0]):
                return False
            candidate = body.child_by_field_name("right")
            if candidate is None:
                return False
            body = candidate
        if body.type != "binary_expression" or _text(body.child_by_field_name("operator")) != "===":
            return False
        operands = [body.child_by_field_name("left"), body.child_by_field_name("right")]
        roots = []
        for operand in operands:
            if (operand is None or operand.type != "member_expression"
                    or _text(operand.child_by_field_name("property")) != "category"):
                return False
            root = operand.child_by_field_name("object")
            if root is None or root.type != "identifier":
                return False
            roots.append(_text(root))
        # The exact supported category deduplication compares the candidate to
        # another live SDK row, with no helper call, mutation or alias escape.
        if roots[0] != names[0] or roots[1] == names[0]:
            return False
        for element in elements:
            scope = {**closure, names[0]: element}
            if any(
                "item:catalog" not in self.value(operand, scope, path).tags for operand in operands
            ):
                return False
        return bool(elements)

    def state_value(self, value: _Value, previous: _Value | None = None) -> _Value:
        """React executes known initializers/updaters; they are not inert data."""
        if value.function is None:
            if value.sdk or value.setter is not None:
                self.unsupported(None, "", "unproven_state_callback")
            return value
        path, node, _ = value.function
        if self.callback_expression(value, 0) is not None:
            args: list[_Value] = []
        elif previous is not None and self.callback_expression(value, 1) is not None:
            args = [previous]
        else:
            self.unsupported(node, path, "unsupported_state_callback_signature")
            return _Value()
        self.state_callback_depth += 1
        try:
            return self.invoke(value, args)
        finally:
            self.state_callback_depth -= 1

    def truthy_string_scope(
        self, condition: Node | None, env: dict[str, _Value],
    ) -> dict[str, _Value]:
        """Narrow only the direct truthy guard on a trusted storage string/null."""
        while condition is not None and condition.type == "parenthesized_expression":
            condition = condition.named_children[0] if condition.named_children else None
        if condition is None or condition.type != "identifier":
            return env
        name = _text(condition)
        value = env.get(name, _Value())
        if not value.nullable_string:
            return env
        narrowed = self.data_alias(value)
        narrowed.primitive_string, narrowed.nullable_string = True, False
        return {**env, name: narrowed}

    def plain_json_value(self, value: _Value) -> bool:
        """JSON.stringify may not invoke source getters, functions or toJSON."""
        if self.contains_authority([value]):
            return False
        pending, seen = [value], set()
        while pending:
            current = pending.pop()
            if id(current) in seen:
                continue
            seen.add(id(current))
            self.steps += 1
            if (self.steps > 4000 or len(seen) > 512 or current.function is not None
                    or current.sdk or current.setter is not None or current.pending is not None
                    or "toJSON" in current.props
                    or not (current.passive or current.primitive_string or current.nullable_string
                            or type(current.literal) in {bool, str, float}
                            or current.literal is _NULL_LITERAL)):
                return False
            pending.extend([*current.props.values(), *(current.elements or ())])
        return True

    def state_callback_effects(self, body: Node, env: dict[str, _Value], path: str) -> None:
        """Use the existing bounded effects walk, including both unknown if arms."""
        pending = [body]
        while pending:
            node = pending.pop()
            self.steps += 1
            if self.steps > 4000 or node.type in {
                "try_statement", "throw_statement", "switch_statement", "for_statement",
                "while_statement", "do_statement", "new_expression", "await_expression",
                "yield_expression",
            }:
                self.unsupported(node, path, "unsupported_state_callback_control")
                return
            if node is body or node.type not in _FUNCTION_SCOPES:
                pending.extend(node.named_children)
        if body.type == "statement_block":
            self.loop_body_effects(body, env, path)
        else:
            self.value_for_effects(body, env, path)

    def invoke(
        self, fn: _Value, args: list[_Value], render: bool = False,
        *, call_node: Node | None = None, call_path: str = "",
    ) -> _Value:
        if fn.setter is not None and args:
            if args[0].function is not None:
                updated = self.state_value(args[0], fn.setter)
                # A functional update is scheduled; current-render data stays
                # unchanged, while future returned aliases retain effects roots.
                fn.setter.mutation_roots += (updated,)
            else:
                self.data_alias(self.state_value(args[0], fn.setter), target=fn.setter)
            return _Value()
        if fn.function is None:
            if not fn.sdk:
                for arg in args:
                    self.invalidate(arg, node=call_node, path=call_path)
            return _Value()
        path, node, closure = fn.function
        key = (path, node.start_byte)
        if key in self.active or self.steps > 4000:
            if self.state_callback_depth:
                self.unsupported(node, path, "recursive_state_callback")
            return _Value()
        parameters = node.child_by_field_name("parameters")
        patterns = (
            parameters.named_children if parameters else [node.child_by_field_name("parameter")]
        )
        bindings = [
            (pattern, args[index] if index < len(args) else _Value())
            for index, pattern in enumerate(patterns)
        ]
        for pattern, value in bindings:
            if not self.binding_is_supported(pattern, value):
                self.unsupported(pattern, path, "unsupported_parameter_binding")
                return _Value()
        if (self.projection_depth and not self.projection_is_bounded(path, node)
                and not self.read_only_callback(fn, args)):
            self.unsupported(node, path, "unproven_projection_callback")
            return _Value()
        self.active.add(key)
        env = dict(closure)
        for pattern, value in bindings:
            self.bind_supported(pattern, value, env)
        body = node.child_by_field_name("body")
        if self.state_callback_depth and body is not None:
            with self.effects_only():
                self.state_callback_effects(body, dict(env), path)
        result = (
            self.block(body, env, path, render)[1]
            if body is not None and body.type == "statement_block"
            else self.value(body, env, path, render)
        )
        self.active.remove(key)
        if self.state_callback_depth:
            if result.function is not None or result.sdk or result.setter is not None:
                self.unsupported(node, path, "unproven_state_callback_value")
            alias = self.data_alias(result)
            alias.mutation_roots += self.referenced_authority_roots(body, env) if body else ()
            result = alias
        return (
            _Value(pending=result.pending or result)
            if any(child.type == "async" for child in node.children)
            else result
        )

    def block(
        self, node: Node, env: dict[str, _Value], path: str, render: bool
    ) -> tuple[bool, _Value]:
        for child in node.named_children:
            if child.type in {"lexical_declaration", "variable_declaration"}:
                for declaration in child.named_children:
                    if declaration.type == "variable_declarator":
                        # Hoisting establishes names, not executed binding effects.
                        self.bind_supported(declaration.child_by_field_name("name"), _Value(), env)
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
                if condition.literal is _UNSUPPORTED_LITERAL:
                    self.unsupported_constant_control = True
                    return True, _Value()
                truth = _literal_truth(condition.literal)
                alternative = child.child_by_field_name("alternative")
                if self.checkout_entry and alternative is not None and alternative.named_children:
                    # An unknown condition can still run a legacy charge in
                    # its else arm. That arm must not add linked-flow proof.
                    alternative = alternative.named_children[-1]
                    if truth is None:
                        self.checkout_legacy_branch(alternative, dict(env), path, render)
                if (self.state_callback_depth and alternative is not None
                        and alternative.type == "else_clause" and alternative.named_children):
                    alternative = alternative.named_children[-1]
                branch = alternative if truth is False else child.child_by_field_name("consequence")
                if branch is not None:
                    branch_env = (
                        self.truthy_string_scope(child.child_by_field_name("condition"), env)
                        if truth is not False else env
                    )
                    returned, value = self.statement(branch, branch_env, path, render)
                    if returned and truth is not None:
                        return True, value
            else:
                returned, value = self.statement(child, env, path, render)
                if self.checkout_entry and returned:
                    return True, value
        return False, _Value()

    def checkout_legacy_branch(
        self, node: Node, env: dict[str, _Value], path: str, render: bool,
    ) -> tuple[bool, _Value]:
        previous = self.checkout_legacy_probe
        self.checkout_legacy_probe = True
        try:
            return self.statement(node, env, path, render)
        finally:
            self.checkout_legacy_probe = previous

    def passive_local_array(self, value: _Value) -> bool:
        """A source-owned JSON array, with no SDK authority or opaque elements."""
        if value.elements is None:
            return False
        pending, seen = [value], set()
        while pending:
            item = pending.pop()
            if id(item) in seen:
                continue
            seen.add(id(item))
            self.steps += 1
            if (self.steps > 4000 or len(seen) > 512 or not self.passive_value(item)
                    or item.tags or item.pending is not None or item.mutation_roots):
                return False
            pending.extend([
                *item.props.values(), *(item.elements or ()), *(item.statistic_elements or ()),
            ])
        return True

    def contains_authority(self, values: list[_Value]) -> bool:
        pending, seen = list(values), set()
        while pending:
            value = pending.pop()
            if id(value) in seen:
                continue
            seen.add(id(value))
            self.steps += 1
            if self.steps > 4000 or len(seen) > 512 or value.tags or value.sdk or value.setter:
                return True
            pending.extend([
                *value.props.values(), *(value.elements or ()),
                *(value.statistic_elements or ()), *value.mutation_roots,
            ])
            if value.pending is not None:
                pending.append(value.pending)
        return False

    def local_array_loop(self, node: Node, env: dict[str, _Value], path: str) -> None:
        target, body = node.child_by_field_name("left"), node.child_by_field_name("body")
        if (target is None or target.type != "identifier" or body is None
                or _text(node.child_by_field_name("operator")) != "of"
                or _text(node.child_by_field_name("kind")) not in {"const", "let"}
                or any(child.type == "await" for child in node.children)):
            self.unsupported(node, path, "unsupported_loop_shape")
            return
        pending = [body]
        while pending:
            child = pending.pop()
            self.steps += 1
            if self.steps > 4000 or child.type in {
                "return_statement", "throw_statement", "break_statement", "continue_statement",
                "yield_expression", "await_expression", "for_statement", "for_in_statement",
                "while_statement", "do_statement", "switch_statement", "try_statement",
                "variable_declaration",
            }:
                self.unsupported(child, path, "unsupported_loop_control")
                return
            pending.extend(child.named_children)
        source = self.value(node.child_by_field_name("right"), env, path)
        if not self.passive_local_array(source):
            self.unsupported(node, path, "unproven_loop_iterable")
            return
        # Inspect one symbolic JSON element even for an initially empty state
        # array. Shared aliases retain invalidation; loop-local bindings do not
        # leak and hypothetical loop bodies cannot grant connection evidence.
        scope = {**env, _text(target): _Value(passive=True)}
        with self.effects_only(strict=True):
            self.loop_body_effects(body, scope, path)

    def loop_body_effects(self, node: Node, env: dict[str, _Value], path: str) -> None:
        if node.type == "statement_block":
            scope = dict(env)
            for child in node.named_children:
                if child.type == "lexical_declaration":
                    for declaration in child.named_children:
                        if declaration.type == "variable_declarator":
                            self.bind_supported(
                                declaration.child_by_field_name("name"), _Value(), scope,
                            )
                elif child.type == "function_declaration":
                    self.declare(child, scope, path)
            for child in node.named_children:
                self.loop_body_effects(child, scope, path)
        elif node.type == "if_statement":
            condition = self.value(node.child_by_field_name("condition"), env, path)
            if condition.literal is _UNSUPPORTED_LITERAL:
                self.unsupported_constant_control = True
                return
            truth = _literal_truth(condition.literal)
            consequence = node.child_by_field_name("consequence")
            alternative = node.child_by_field_name("alternative")
            if truth is not False and consequence is not None:
                self.loop_body_effects(
                    consequence,
                    self.truthy_string_scope(node.child_by_field_name("condition"), dict(env)),
                    path,
                )
            if truth is not True and alternative is not None and alternative.named_children:
                self.loop_body_effects(alternative.named_children[-1], dict(env), path)
        else:
            self.statement(node, env, path, False)

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
                returned, value = self.block(body, env, path, render)
                if self.checkout_entry:
                    handler = node.child_by_field_name("handler")
                    if handler is not None:
                        handler_body = handler.child_by_field_name("body")
                        if handler_body is not None:
                            handler_env = dict(env)
                            parameter = handler.child_by_field_name("parameter")
                            self.bind(parameter, _Value(), handler_env, path)
                            handled, _ = self.checkout_legacy_branch(
                                handler_body, handler_env, path, render
                            )
                            returned = returned and handled
                    finalizer = node.child_by_field_name("finalizer")
                    if finalizer is not None:
                        final_body = finalizer.child_by_field_name("body")
                        if final_body is not None:
                            finalized, final_value = self.block(final_body, env, path, render)
                            if finalized:
                                return True, final_value
                return returned, value
        elif node.type == "for_in_statement":
            self.local_array_loop(node, env, path)
        elif node.type not in _TYPE_ONLY_DECLARATIONS | {"comment", "empty_statement"}:
            self.unsupported(node, path, "unsupported_statement")
        return False, _Value()

    def value_for_effects(self, node: Node | None, env: dict[str, _Value], path: str) -> _Value:
        # A comparison discards rendering, not callback effects. Using the
        # projection path here could skip an unsupported mutating map callback.
        self.render_suppression += 1
        try:
            return self.value(node, env, path, True)
        finally:
            self.render_suppression -= 1

    def binary_value(
        self, node: Node, env: dict[str, _Value], path: str, render: bool,
    ) -> _Value:
        operator = _text(node.child_by_field_name("operator"))
        forwarding = operator in {"&&", "||", "??"}
        if forwarding:
            left = self.value(
                node.child_by_field_name("left"), env, path,
                render and operator in {"||", "??"},
            )
            if left.literal is _UNSUPPORTED_LITERAL:
                self.unsupported_constant_control = True
                return left
            right_node = node.child_by_field_name("right")
            if (
                operator == "??" and left.nullable_catalog_lookup
                and right_node is not None and right_node.type == "null"
            ):
                # A verified lookup may find a row. The literal fallback must
                # not make guards on that row unconditionally false.
                return left
            truth = _literal_truth(left.literal)
            if ((operator == "&&" and truth is False)
                    or (operator == "||" and truth is True)
                    or (operator == "??" and left.literal is not None
                        and left.literal is not _NULL_LITERAL)):
                return left
            return self.value(right_node, env, path, render)
        # Comparisons render a Boolean, not either operand's SDK data. Both
        # operands still run for effects; neither can leak a render witness.
        left = self.value_for_effects(node.child_by_field_name("left"), env, path)
        right = self.value_for_effects(node.child_by_field_name("right"), env, path)
        if (self.state_callback_depth and operator in {"<", "<=", ">", ">="}
                and type(left.literal) is float and type(right.literal) is float
                and math.isfinite(left.literal) and math.isfinite(right.literal)):
            if self.effects_only_depth:
                # Read-only finite numbers need no coercion. The effects probe
                # visits both arms; it never buys a branch connection witness.
                return _Value()
            return _Value(literal={
                "<": left.literal < right.literal, "<=": left.literal <= right.literal,
                ">": left.literal > right.literal, ">=": left.literal >= right.literal,
            }[operator])
        return _Value(literal=_comparison_literal(left.literal, right.literal, operator))

    def object_value(self, node: Node, env: dict[str, _Value], path: str) -> _Value:
        props: dict[str, _Value] = {}
        spread_roots: list[_Value] = []
        passive = True
        for child in node.named_children:
            if child.type == "comment":
                continue
            if child.type == "pair":
                key = child.child_by_field_name("key")
                name = (
                    _primitive_literal(key) if key is not None and key.type == "string"
                    else _text(key)
                )
                if (key is None or key.type not in {"property_identifier", "string", "number"}
                        or not isinstance(name, str) or name == "__proto__"):
                    self.unsupported(key, path, "unsupported_object_key")
                    passive = False
                props[str(name)] = self.value(child.child_by_field_name("value"), env, path)
            elif child.type == "shorthand_property_identifier":
                props[_text(child)] = env.get(_text(child), _Value())
            elif child.type == "spread_element":
                source = self.value(
                    child.named_children[0] if child.named_children else None, env, path,
                )
                if source.passive or source.tags:
                    props.update(source.props)
                    if (source.statistic_elements is not None
                            or (source.mutation_roots and self.contains_authority([source]))):
                        # A filtered SDK array also spreads numeric row keys
                        # that this effects-only descriptor does not expose.
                        spread_roots.append(source)
                else:
                    self.unsupported(child, path, "unproven_object_spread")
                    passive = False
                    self.invalidate(source, node=child, path=path)
            else:
                # Getter/method/computed syntax can execute source closures
                # during construction or a later spread. Never omit it silently.
                self.unsupported(child, path, "unsupported_object_member")
                passive = False
        return _Value(props=props, passive=passive, mutation_roots=tuple(spread_roots))

    def value(
        self, node: Node | None, env: dict[str, _Value], path: str, render: bool = False
    ) -> _Value:
        self.steps += 1
        if node is None or self.steps > 4000:
            return _Value()
        kind = node.type
        if kind == "comment":
            return _Value()
        if kind == "identifier":
            result = env.get(_text(node), _Value())
            if result is self.reflect:
                self.unsupported(node, path, "unproven_reflection_access")
        elif kind in {"arrow_function", "function_expression", "function_declaration"}:
            result = _Value(function=(path, node, env))
        elif kind in {
            "parenthesized_expression",
            "as_expression",
            "non_null_expression",
        }:
            result = self.value(
                node.named_children[0] if node.named_children else None, env, path, render
            )
        elif kind == "await_expression":
            result = self.value(node.named_children[0] if node.named_children else None, env, path)
            result = result.pending or result
        elif kind == "unary_expression":
            operand = self.value(node.named_children[0] if node.named_children else None, env, path)
            operator = _text(node.children[0])
            if operator == "delete":
                root = self.mutation_root(node.child_by_field_name("argument"), env)
                if root is not None:
                    self.invalidate(root, node=node, path=path)
                self.invalidate(operand, node=node, path=path)
            truth = _literal_truth(operand.literal)
            if operator == "!" and truth is not None:
                literal: object = not truth
            elif operator in {"+", "-"} and type(operand.literal) is float:
                literal = -operand.literal if operator == "-" else operand.literal
            else:
                literal = None if operand.literal is None else _UNSUPPORTED_LITERAL
            result = _Value(literal=literal)
        elif kind in {"true", "false", "string", "number", "null"}:
            result = _Value(literal=_primitive_literal(node))
        elif kind == "member_expression":
            result = self.member(
                self.value(node.child_by_field_name("object"), env, path),
                _text(node.child_by_field_name("property")),
            )
        elif kind == "subscript_expression":
            # Unknown indices never grant readable history, but cannot hide aliases
            # from later opaque calls or mutations.
            source = self.value(node.child_by_field_name("object"), env, path)
            index = self.value(node.child_by_field_name("index"), env, path)
            if (source is self.object or source is self.array or source is self.reflect
                    or source.native_namespace
                    or index.literal in {"prototype", "__proto__", "constructor", "Reflect"}):
                self.unsupported(node, path, "unproven_prototype_access")
            result = _Value(mutation_roots=(source,))
        elif kind == "array":
            children = [child for child in node.named_children if child.type != "comment"]
            expecting_value = True
            sparse = False
            for child in node.children:
                if child.type == ",":
                    sparse |= expecting_value
                    expecting_value = True
                elif child.is_named and child.type != "comment":
                    expecting_value = False
            elements = tuple(self.value(child, env, path) for child in children)
            if (
                not sparse
                and len(children) <= _MAX_COLLECTION_ITEMS
                and all(child.type != "spread_element" for child in children)
            ):
                result = _Value(elements=elements, passive=True)
            else:
                for element in elements:
                    self.invalidate(element, node=node, path=path)
                result = _Value()
        elif kind == "spread_element":
            # Unsupported arity cannot conceal an authority-bearing argument.
            for child in node.named_children:
                self.invalidate(self.value(child, env, path), node=node, path=path)
            result = _Value()
        elif kind == "new_expression":
            # Constructors are opaque calls, never an interpreted class body.
            self.value(node.child_by_field_name("constructor"), env, path)
            arguments = node.child_by_field_name("arguments")
            for argument in arguments.named_children if arguments is not None else ():
                self.invalidate(self.value(argument, env, path), node=node, path=path)
            result = _Value()
        elif kind in {
            "assignment_expression",
            "augmented_assignment_expression",
            "update_expression",
        }:
            assigned = node.child_by_field_name("left") or node.child_by_field_name("argument")
            root = self.mutation_root(assigned, env)
            if root is not None:
                self.invalidate(root, node=node, path=path)
            assigned_value = self.value(node.child_by_field_name("right"), env, path)
            if (assigned is not None and assigned.type == "subscript_expression"
                    and (assigned_value.function is not None or assigned_value.sdk
                         or assigned_value.setter is not None)):
                # An unproved computed method name can be inherited toJSON.
                # Ordinary JSON data writes need no callable/prototype model.
                self.unsupported(node, path, "unproven_computed_method_mutation")
            result = _Value()
        elif kind == "object":
            result = self.object_value(node, env, path)
        elif kind in {"class", "class_expression"}:
            self.unsupported(node, path, "unsupported_class")
            result = _Value()
        elif kind == "template_string":
            # Preserve passive labels used by existing pages, without evaluating
            # JS strings or treating their Boolean comparisons as SDK rendering.
            for child in node.named_children:
                if child.type == "template_substitution":
                    for expression in child.named_children:
                        self.value_for_effects(expression, env, path)
                elif child.type not in {"string_fragment", "escape_sequence"}:
                    self.unsupported(child, path, "unsupported_template_part")
            result = _Value(literal=_UNSUPPORTED_LITERAL)
        elif kind == "call_expression":
            result = self.call(node, env, path, render)
        elif kind in {"jsx_element", "jsx_self_closing_element", "jsx_fragment"}:
            return self.jsx(node, env, path) if render else _Value()
        elif kind == "binary_expression":
            result = self.binary_value(node, env, path, render)
        elif kind == "ternary_expression":
            condition = self.value(node.child_by_field_name("condition"), env, path)
            if condition.literal is _UNSUPPORTED_LITERAL:
                self.unsupported_constant_control = True
                return _Value()
            truth = _literal_truth(condition.literal)
            if truth is None:
                self.value(node.child_by_field_name("alternative"), env, path, render)
            result = self.value(
                node.child_by_field_name(
                    "alternative" if truth is False else "consequence"
                ),
                env,
                path,
                render,
            )
        else:
            if kind not in {"undefined", "this", "super", "regex"}:
                self.unsupported(node, path, "unsupported_expression")
            result = _Value()
        if render and not self.render_suppression:
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
        local_array_push = False
        if target is not None and target.type == "member_expression":
            source = self.value(target.child_by_field_name("object"), env, path)
            operation = _text(target.child_by_field_name("property"))
            receiver = _text(target.child_by_field_name("object"))
            if receiver == "JSON" and operation in {"parse", "stringify"}:
                expected = self.json_parse if operation == "parse" else self.json_stringify
                if source is not self.json or source.props.get(operation) is not expected:
                    self.unsupported(node, path, "untrusted_json_operation")
            if (receiver in {"window.localStorage", "localStorage"} and operation == "getItem"
                    and (source is not self.storage
                         or source.props.get("getItem") is not self.storage_get_item)):
                self.unsupported(node, path, "untrusted_storage_operation")
            if receiver == "Object" and operation == "keys" and (
                source is not self.object or source.props.get("keys") is not self.object_keys
            ):
                self.unsupported(node, path, "untrusted_object_keys")
            if (
                operation == "map" and render and not self.render_suppression
                and self.catalog_render_rejection is None
                and source.catalog_lookup_issue is not None
                and len(args) == 1 and self._catalog_map_has_direct_row_content(args[0])
            ):
                self.catalog_render_rejection = source.catalog_lookup_issue
            local_array_push = operation == "push" and self.passive_local_array(source)
            if self.strict_effects_depth and not local_array_push:
                self.unsupported(node, path, "unproven_loop_call")
            if (
                operation == "all"
                and _text(target.child_by_field_name("object")) == "Promise"
                and source is self.promise
                and source.sdk == "Promise"
                and len(args) == 1
                and args[0].elements is not None
            ):
                return _Value(
                    pending=_Value(
                        elements=tuple(element.pending or element for element in args[0].elements)
                    )
                )
            statistic_elements = self.statistic_source_elements(source)
            if (operation in {"filter", "reduce"} and statistic_elements is not None
                    and not render and not self.render_suppression):
                # Chained statistics retain effects provenance, never SDK row
                # identity. A failed callback proof invalidates every alias.
                if self.statistic_call_is_read_only(operation, args, source):
                    return self.statistic_result(operation, args, source, statistic_elements)
                self.unsupported(node, path, "unproven_statistic_callback")
                self.invalidate(source, node=node, path=path)
                return _Value()
            if source.elements is not None:
                if operation == "map" and source.local_scalar_keys:
                    if len(args) != 1 or self.callback_expression(args[0], 1) is None:
                        self.unsupported(node, path, "unsupported_key_map_callback")
                        return _Value()
                    self.state_callback_depth += 1
                    try:
                        with self.effects_only():
                            mapped = self.invoke(
                                args[0], [_Value(
                                    passive=True, primitive_string=True, scalar_key=True,
                                )],
                            )
                    finally:
                        self.state_callback_depth -= 1
                    # Unknown cardinality, one effects-only scalar-key probe.
                    # A returned SDK alias retains invalidation, not JSX proof.
                    return _Value(elements=(), passive=True, mutation_roots=(mapped,))
                if operation == "map" and len(args) == 1 and args[0].function is not None:
                    if not source.elements:
                        with self.effects_only():
                            self.invoke(args[0], [_Value(passive=True)], render)
                        return _Value(elements=(), passive=True)
                    self.projection_depth += int(not render)
                    try:
                        return _Value(
                            elements=tuple(
                                self.invoke(args[0], [element], render)
                                for element in source.elements
                            )
                        )
                    finally:
                        self.projection_depth -= int(not render)
                if operation == "flat" and not args:
                    if all(element.elements is not None for element in source.elements):
                        flat = tuple(
                            child for element in source.elements for child in element.elements or ()
                        )
                        if len(flat) <= _MAX_COLLECTION_ITEMS:
                            return _Value(elements=flat)
                if (
                    operation == "sort"
                    and len(args) == 1
                    and self.sort_is_read_only(args[0], source.elements)
                ):
                    return source
                if operation == "find" and "catalog" in source.tags:
                    issue = (
                        self.catalog_id_find_rejection(node, args[0], path)
                        if len(args) == 1
                        else _source_issue(node, path, "unproven_catalog_id_lookup")
                    )
                    if issue is None:
                        # Verified scalar reads grant no rendered catalogue witness.
                        return _Value(
                            props={
                                "options": _Value(elements=(_Value(passive=True),), passive=True),
                            },
                            passive=True,
                            nullable_catalog_lookup=True,
                        )
                    source.catalog_lookup_issue = issue
                if (
                    operation == "findIndex"
                    and "catalog" in source.tags
                    and len(args) == 1
                    and self.category_find_index_is_read_only(args[0], source.elements)
                ):
                    # A proven read-only lookup preserves the collection, but
                    # its numeric result is never rendered-item evidence.
                    return _Value()
                if operation != "then":
                    self.invalidate(source, node=node, path=path)
            if operation == "then" and args and source.pending is not None:
                result = self.invoke(args[0], [source.pending])
                return _Value(pending=result.pending or result)
        fn = self.value(target, env, path)
        if fn is self.storage_get_item:
            if (fn.sdk == "trusted-storage-get-item" and len(args) == 1
                    and (type(args[0].literal) is str or args[0].primitive_string)):
                return _Value(passive=True, nullable_string=True)
            self.unsupported(node, path, "unproven_storage_read")
            return _Value()
        if fn is self.json_parse:
            if (fn.sdk == "trusted-json-parse" and len(args) == 1
                    and (type(args[0].literal) is str or args[0].primitive_string)):
                return _Value(passive=True)
            self.unsupported(node, path, "unproven_json_parse")
            return _Value()
        if fn is self.json_stringify:
            if (fn.sdk == "trusted-json-stringify" and len(args) == 1
                    and self.plain_json_value(args[0])):
                return _Value(passive=True, primitive_string=True)
            self.unsupported(node, path, "unproven_json_stringify")
            return _Value()
        if fn is self.object_keys:
            if (fn.sdk == "trusted-object-keys" and len(args) == 1 and args[0].passive
                    and not args[0].primitive_string and not args[0].nullable_string
                    and args[0].literal is None and self.plain_json_value(args[0])):
                return _Value(elements=(), passive=True, local_scalar_keys=True)
            self.unsupported(node, path, "unproven_object_keys")
            return _Value()
        if self.effects_only_depth and fn.setter is not None and args:
            if self.strict_effects_depth or self.contains_authority([fn.setter, *args]):
                self.unsupported(node, path, "unproven_loop_setter")
            elif args[0].function is not None:
                # A React updater is executed later by React, not an inert
                # function value. Inspect it for captured SDK effects too.
                updated = self.invoke(args[0], [fn.setter])
                if self.contains_authority([updated]):
                    self.unsupported(node, path, "unproven_loop_setter")
                args = [updated, *args[1:]]
        if ((self.effects_only_depth and fn.sdk)
                or (self.strict_effects_depth and not local_array_push)):
            self.unsupported(node, path, "unproven_loop_call")
        if fn.sdk == "useState":
            initial = args[0] if args else _Value()
            return self.data_alias(self.state_value(initial), state=True)
        if fn.sdk == "useEffect":
            return self.invoke(args[0], []) if args else _Value()
        if fn.sdk == "useCallback":
            return args[0] if args else _Value()
        if fn.sdk in {"useYleumAppProfile", "useYleumCatalog"}:
            return _Value(
                tags={"profile-hook" if fn.sdk == "useYleumAppProfile" else "catalog-hook"}
            )
        if fn.sdk == "useYleumCatalogSelection":
            if args and "catalog" in args[0].tags:
                self.catalog_selection = True
            return _Value(props={
                "category": _Value(),
                **{
                    name: _Value(sdk="catalog-selection-" + name)
                    for name in ("getOption", "selectOption", "setCategory")
                },
            })
        if fn.sdk == "getManagedRecords" and args and args[0].literal in _CREATE:
            return _Value(pending=_Value(tags={"response:" + str(args[0].literal)}))
        if fn.sdk in _CREATE.values() and self.event_depth and args and args[0].props:
            self.created.add(
                next(resource for resource, method in _CREATE.items() if method == fn.sdk)
            )
            if (
                self.checkout_entry
                and not self.checkout_legacy_probe
                and fn.sdk == "createManagedOrder"
            ):
                return _Value(pending=_Value(props={"id": _Value(tags={_CHECKOUT_ORDER_ID})}))
        if self.inspect_checkout and fn.sdk in _LEGACY_CHECKOUT:
            self.checkout_calls.add(fn.sdk)
        elif self.checkout_entry and not self.checkout_legacy_probe:
            if fn.sdk == "getYleumOrderCheckout" and args and args[0].literal is None:
                self.checkout_calls.add(fn.sdk)
            elif (
                fn.sdk == "createYleumOrderCheckout"
                and self.event_depth
                and args
                and _CHECKOUT_ORDER_ID in args[0].tags
            ):
                self.checkout_calls.add(fn.sdk)
        if fn.function is None and not fn.sdk and fn.setter is None:
            root = self.mutation_root(target, env)
            if root is not None:
                self.invalidate(root, node=node, path=path)
            self.invalidate(fn, node=node, path=path)
        return self.invoke(fn, args, render, call_node=node, call_path=path)

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
            if not self.render_suppression:
                controls = [props.get(key, _Value()).literal for key in ("hidden", "disabled")]
                if any(value is _UNSUPPORTED_LITERAL for value in controls):
                    self.unsupported_constant_control = True
                    return _Value()
                if any(value is True for value in controls):
                    return _Value()
            name = _text(opening.child_by_field_name("name"))
            if name and name[0].isupper():
                if not self.render_suppression:
                    return self.invoke(
                        env.get(name, _Value()), [_Value(props=props, passive=True)], True,
                        call_node=opening, call_path=path,
                    )
            elif not self.render_suppression:
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
            # Entry props come from the framework, not source-defined getters
            # closing over the component's SDK collections.
            entry = self.module("src/app/page.tsx").get("default", _Value())
            self.checkout_entry = self.inspect_checkout
            self.invoke(entry, [_Value(passive=True)], True)


def linked_checkout_connected(files: Mapping[str, str]) -> bool:
    """Find a linked checkout on the app entry using a created order's SDK ID."""
    connections = _Connections(files, inspect_checkout=True)
    try:
        connections.analyze()
    except RecursionError:
        return False
    return (
        connections.steps <= 4000
        and not connections.unsupported_effects
        and not connections.unsupported_constant_control
        and _LINKED_CHECKOUT <= connections.checkout_calls
        and not _LEGACY_CHECKOUT & connections.checkout_calls
    )


class ManagedSourceObligation(StrEnum):
    PROFILE_FIELD = "profile_field"
    CATALOG_RENDER = "catalog_render"
    CATALOG_SELECTION = "catalog_selection"
    ORDER_HISTORY = "order_create_history"
    LEAD_HISTORY = "lead_create_history"
    BOOKING_HISTORY = "booking_create_history"


_CREATE_OBLIGATIONS = {
    "orders": ManagedSourceObligation.ORDER_HISTORY,
    "leads": ManagedSourceObligation.LEAD_HISTORY,
    "bookings": ManagedSourceObligation.BOOKING_HISTORY,
}


class ManagedSourceGap(str):
    """Diagnostic plus trusted admission identity; never acceptance evidence."""

    _obligation: ManagedSourceObligation | None
    _contract_hash: str | None
    _located_key: str | None

    def __new__(
        cls, diagnostic: str, *, obligation: ManagedSourceObligation | None = None,
        contract_hash: str | None = None,
    ) -> ManagedSourceGap:
        if (obligation is None) != (contract_hash is None) or (
            obligation is not None and (
                not isinstance(obligation, ManagedSourceObligation)
                or not isinstance(contract_hash, str)
                or re.fullmatch(r"[a-f0-9]{64}", contract_hash) is None
            )
        ):
            raise ValueError("managed source obligation requires a canonical contract identity")
        gap = super().__new__(cls, diagnostic)
        gap._obligation = obligation
        gap._contract_hash = contract_hash
        gap._located_key = cls._location_key(diagnostic)
        return gap

    def with_diagnostic(self, diagnostic: str) -> ManagedSourceGap:
        """Retain trusted identity across bounded, redacted model feedback."""
        gap = ManagedSourceGap(
            diagnostic, obligation=self._obligation, contract_hash=self._contract_hash,
        )
        gap._located_key = self._located_key
        return gap

    def rejection_key(self) -> str | None:
        if self._located_key is not None:
            return self._located_key
        if self._obligation is not None:
            # Irrelevant source edits cannot discharge the same unmet contract
            # obligation. Distinct modules/contracts never share this identity.
            payload = ["managed-obligation-v1", self._contract_hash, self._obligation.value]
            return hashlib.sha256(json.dumps(payload, separators=(",", ":")).encode()).hexdigest()
        return None

    @staticmethod
    def _location_key(diagnostic: str) -> str | None:
        # A line shift is not a repair. Keep the path, AST shape and fingerprint.
        # Unknown/unlocated messages retain the conservative source-bound fallback.
        stable, locations = re.subn(
            r"( Source location: [A-Za-z0-9_./-]+):\d+:\d+"
            r"(; AST \w+; reason \w+; fingerprint [a-f0-9]{16}\.)",
            r"\1\2", diagnostic,
        )
        return hashlib.sha256(stable.encode()).hexdigest() if locations else None


def _create_history_diagnostic(name: str, connections: _Connections) -> str | None:
    """Explain missing UI connections without granting acceptance or repair authority."""
    create_connected = name in connections.created
    history_rendered = "record:" + name in connections.rendered
    if create_connected and history_rendered:
        return None
    details = [f"Connect {_CREATE[name]} managed flow."]
    if not create_connected:
        details.append(
            "Missing mounted create event. Attach the submit handler to a visible "
            "form onSubmit or button onClick; the handler must call the SDK create "
            "method with the real typed input object. An import or unmounted helper "
            "does not connect a create event."
        )
    if not history_rendered:
        details.append(
            f"Missing rendered {name} history. Store getManagedRecords('{name}').records "
            "in resource-specific state and render that loaded state directly as "
            "records.map(record => <p key={record.id}>{record.customer_name} "
            "{record.status}</p>) in visible JSX. Loading records alone does not "
            "render history."
        )
    details.append(
        "Preserve the real typed inputs, any saved pending intent and its operationKey. "
        "This feedback grants no extra model or repair budget."
    )
    return " ".join(details)


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
    if connections.steps > 4000:
        return "Managed source connection analysis exceeded its bounded AST budget.", frozenset()
    if connections.unsupported_effects:
        return (
            "Managed source connection analysis cannot prove call or binding effects. "
            "Simplify the identified source shape to a passive expression or explicit "
            "body statements; do not change unrelated bindings or replace live SDK data "
            "with static records." + _issue_detail(connections.first_unsupported),
            frozenset(),
        )
    if connections.unsupported_constant_control:
        return (
            "Managed source connection analysis cannot prove unsupported constant control.",
            frozenset(),
        )
    if "profile-field" not in connections.rendered:
        return (
            ManagedSourceGap(
                "Connect useYleumAppProfile from app-profile-client "
                "to a rendered app name/settings field.",
                obligation=ManagedSourceObligation.PROFILE_FIELD,
                contract_hash=contract.contract_hash,
            ),
            frozenset(),
        )
    capabilities: set[str] = set()
    for module in contract.modules:
        name = str(module.value)
        if name == "catalog":
            if "item:catalog" not in connections.rendered:
                return (
                    ManagedSourceGap(
                        "Connect useYleumCatalog items to the rendered catalog "
                        "(items.map with item fields)."
                        + _issue_detail(connections.catalog_render_rejection),
                        obligation=ManagedSourceObligation.CATALOG_RENDER,
                        contract_hash=contract.contract_hash,
                    ),
                    frozenset(),
                )
            capabilities.add("catalog")
            if not connections.catalog_selection:
                return (
                    ManagedSourceGap(
                        "Connect useYleumCatalogSelection(items) from catalog-client to the live "
                        "catalog; use its category and getOption(item) "
                        "instead of stale local choices.",
                        obligation=ManagedSourceObligation.CATALOG_SELECTION,
                        contract_hash=contract.contract_hash,
                    ),
                    frozenset(),
                )
        elif name in _CREATE:
            diagnostic = _create_history_diagnostic(name, connections)
            if diagnostic is not None:
                return (
                    ManagedSourceGap(
                        diagnostic,
                        obligation=_CREATE_OBLIGATIONS[name], contract_hash=contract.contract_hash,
                    ),
                    frozenset(),
                )
            capabilities.add("history")
            if name == "bookings":
                capabilities.add("booking")
    return None, frozenset(capabilities)


_SUPPORTED_CART_REPAIR_EXAMPLE = """const cartRows = Object.keys(cart).map(id => {
  const selected = catalog.items.find(item => item.id === id) ?? null;
  return { id, qty: cart[id] ?? 0, title: selected ? selected.title : "" };
});
const cartCount = cartRows.reduce((count, row) => count + row.qty, 0);"""


def _source_repair_guidance(diagnostic: str) -> str:
    """Explain a trusted located refusal; this never changes proof or identity."""
    if not diagnostic.startswith(
        "Managed source connection analysis cannot prove call or binding effects."
    ):
        return ""
    reason = re.search(
        r"; reason (unproven_native_intrinsic_effect|unsupported_loop_control|"
        r"unproven_loop_iterable); fingerprint [a-f0-9]{16}\.", diagnostic,
    )
    if reason is None:
        return ""
    explanation = {
        "unproven_native_intrinsic_effect": (
            "Object.entries/values/fromEntries and reflection are outside the supported "
            "native proof. Do not mutate or pass native handles to unknown calls."
        ),
        "unsupported_loop_control": (
            "The local-loop proof does not support continue/break/return. Removing "
            "continue alone does not permit iteration over SDK rows."
        ),
        "unproven_loop_iterable": (
            "for..of requires a source-owned passive JSON array; SDK collections and "
            "Object.values(cart) are not proven loop sources."
        ),
    }[reason.group(1)]
    return (
        "\nSupported source repair: " + explanation
        + " For cart/catalog derivations, use Object.keys on an already proven local JSON "
        "cart, a read-only SDK id lookup and your own scalar DTO before reduce. Example "
        "for a Record<string, number> quantity cart (adapt your own DTO fields to the "
        "existing validated cart schema; do not replace its data):\n"
        + _SUPPORTED_CART_REPAIR_EXAMPLE
        + "\nKeep live SDK catalog.items.map directly in JSX. A DTO is not SDK render "
        "authority. Preserve the manifest-selected create handler and loaded-record "
        "history. Do not use mock/static SDK rows, weaken checks or add model/repair budget."
    )


def management_source_gap(files: Mapping[str, str]) -> ManagedSourceGap | None:
    gap = _admission(files)[0]
    if gap is None:
        return None
    trusted_gap = gap if isinstance(gap, ManagedSourceGap) else ManagedSourceGap(str(gap))
    diagnostic = str(trusted_gap)
    if diagnostic.startswith("Connect "):
        if diagnostic.startswith("Connect useYleumCatalog items"):
            diagnostic += (
                " A local const lookup by item.id can check item.active and use ?? null. "
                "Read its fields and pass {id: selected.id} to getOption."
            )
        diagnostic = (
            diagnostic + " The bounded source verifier cannot prove this connection. "
            "Use direct JSX from the live SDK hook/result and a real event handler "
            "with an explicit input object. Keep SDK rows out of useMemo, pre-filters "
            "and helper projections; apply category conditions inside the JSX map. "
            "In submit handlers, use separate direct guards for pending intent, readiness "
            "and form inputs. Do not hide the SDK call behind a combined reason-string "
            "condition: the bounded verifier does not interpret arbitrary JS conditions. "
            "This is a supported source shape, not a request for static or mock data."
        )
    else:
        diagnostic += _source_repair_guidance(diagnostic)
    return trusted_gap.with_diagnostic(diagnostic)


def management_implemented_capabilities(files: Mapping[str, str]) -> frozenset[str]:
    return _admission(files)[1]

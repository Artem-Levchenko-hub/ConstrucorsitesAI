"""Negative TSX witnesses for explicitly requested filter/sort controls.

This is not feature/browser proof. It rejects an inert root React state or an
unreferenced derived collection. Unknown implementations keep their existing
path. Source is parsed, never executed or sent to a provider.
"""
from __future__ import annotations

import re
from collections.abc import Iterator, Mapping
from functools import lru_cache

import tree_sitter_typescript
from tree_sitter import Language, Node, Parser


@lru_cache(maxsize=1)
def _language() -> Language:
    return Language(tree_sitter_typescript.language_tsx())


def _text(node: Node | None) -> str:
    return (node.text or b"").decode() if node is not None else ""


def _walk(node: Node) -> Iterator[Node]:
    pending = [node]
    while pending:
        current = pending.pop()
        yield current
        pending.extend(reversed(current.named_children))


def _reference(name: str, source: str) -> int:
    # Extra raw occurrences are inconclusive, including strings/comments,
    # template interpolation, callback props and aliases.
    return len(re.findall(r"(?<![\w$])" + re.escape(name) + r"(?![\w$])", source))


def _root(program: Node) -> Node | None:
    for statement in program.named_children:
        if statement.type != "export_statement" or not any(
            child.type == "default" for child in statement.children
        ):
            continue
        declaration = statement.child_by_field_name("declaration")
        if declaration is not None and declaration.type == "function_declaration":
            return declaration
    return None


def _react_hooks(program: Node) -> dict[str, str]:
    hooks: dict[str, str] = {}
    for statement in program.named_children:
        if statement.type != "import_statement" or _text(
            statement.child_by_field_name("source")
        )[1:-1] != "react":
            continue
        for node in _walk(statement):
            if node.type == "import_specifier" and _text(
                node.child_by_field_name("name")
            ) == "useState":
                hooks[_text(node.child_by_field_name("alias")) or "useState"] = "useState"
            elif node.type == "import_clause":
                for child in node.named_children:
                    if child.type == "identifier":
                        hooks[_text(child)] = "namespace"
    return hooks


def _declared(node: Node | None, name: str) -> bool:
    if node is None:
        return False
    return any(
        item.type in {"identifier", "shorthand_property_identifier_pattern"}
        and _text(item) == name for item in _walk(node)
    )


def _declarations(body: Node) -> list[tuple[Node, Node]]:
    result = []
    for statement in body.named_children:
        if statement.type not in {"lexical_declaration", "variable_declaration"}:
            continue
        for declaration in statement.named_children:
            if declaration.type != "variable_declarator":
                continue
            name = declaration.child_by_field_name("name")
            value = declaration.child_by_field_name("value")
            if name is not None and value is not None:
                result.append((name, value))
    return result


def _depends(value: Node, getter: str, operation: str) -> bool:
    if value.type != "call_expression":
        return False  # Never attribute a dormant helper/wrapper to root dataflow.
    for call in (value,):
        if call.type != "call_expression":
            continue
        function = call.child_by_field_name("function")
        if function is None or function.type != "member_expression" or _text(
            function.child_by_field_name("property")
        ) != operation:
            continue
        arguments = call.child_by_field_name("arguments")
        if arguments is None:
            continue
        for callback in arguments.named_children:
            if callback.type not in {"arrow_function", "function_expression"}:
                continue
            if _declared(callback.child_by_field_name("name"), getter):
                continue
            if _declared(callback.child_by_field_name("parameters"), getter) or _declared(
                callback.child_by_field_name("parameter"), getter,
            ):
                continue
            callback_body = callback.child_by_field_name("body")
            if callback_body is None or any(
                item.type in {"arrow_function", "function_expression",
                              "function_declaration", "class_declaration", "catch_clause"}
                for item in _walk(callback_body)
            ):
                continue  # Nested scope binding is inconclusive.
            if any(
                item.type == "variable_declarator"
                and _declared(item.child_by_field_name("name"), getter)
                for item in _walk(callback_body)
            ) or any(
                item.type in {"arrow_function", "function_expression"}
                and (_declared(item.child_by_field_name("parameters"), getter)
                     or _declared(item.child_by_field_name("parameter"), getter))
                for item in _walk(callback_body)
            ):
                continue
            if any(item.type == "identifier" and _text(item) == getter
                   for item in _walk(callback_body)):
                return True
    return False


def _delegates_collection(body: Node, values: list[Node], names: set[str]) -> bool:
    receivers = set(names)
    for value in values:
        function = value.child_by_field_name("function")
        receiver = function.child_by_field_name("object") if function else None
        if receiver is not None:
            receivers.update(_text(node) for node in _walk(receiver) if node.type == "identifier")
    for element in _walk(body):
        if element.type not in {"jsx_opening_element", "jsx_self_closing_element"}:
            continue
        component = _text(element.child_by_field_name("name"))
        if not component or not component[0].isupper():
            continue
        for child in element.named_children:
            if child.type != "jsx_attribute":
                continue
            for expression in child.named_children:
                if expression.type == "jsx_expression" and any(
                    node.type == "identifier" and _text(node) in receivers
                    for node in expression.named_children
                ):
                    return True
    return False


def _pure_copy_sort(value: Node) -> bool:
    """Only a fresh array with an expression comparator has a dead-result witness.

    In-place sorts and callbacks with possible writes/calls can affect a rendered
    receiver without reading the result binding; their effect is unknown here.
    """
    if value.type != "call_expression":
        return False
    function = value.child_by_field_name("function")
    if function is None or function.type != "member_expression":
        return False
    receiver = function.child_by_field_name("object")
    arguments = value.child_by_field_name("arguments")
    if receiver is None or receiver.type != "array" or arguments is None:
        return False
    callbacks = arguments.named_children
    if len(callbacks) != 1 or callbacks[0].type != "arrow_function":
        return False
    body = callbacks[0].child_by_field_name("body")
    return body is not None and not any(
        node.type in {"statement_block", "call_expression", "assignment_expression",
                      "augmented_assignment_expression", "update_expression", "await_expression",
                      "new_expression", "arrow_function", "function_expression"}
        for node in _walk(body)
    )


def unused_visible_filter_gap(
    prompt: str, files: Mapping[str, str], *, operations: frozenset[str] = frozenset({"filter"}),
) -> str | None:
    """None is inconclusive, never semantic PASS. Called for selected UI intent."""
    if re.search(r"\b(?:api[- ]only|только\s+api)\b", prompt, re.IGNORECASE) or re.match(
        r"\s*(?:explain|inspect|review|investigate|объясни|исследуй|проанализируй)\b",
        prompt, re.IGNORECASE,
    ):
        return None
    positive = frozenset(
        operation for operation in operations if any(
        re.search(
            r"\b(?:фильтр\w*|filters?)\b" if operation == "filter"
            else r"\b(?:сортиров\w*|sort\w*)\b", clause, re.IGNORECASE,
        )
        and re.search(
            r"\b(?:кнопк\w*|переключател\w*|видим\w*|buttons?|controls?|visible|interactive)\b",
            clause, re.IGNORECASE,
        )
        and not re.search(
            r"\b(?:не\s+(?:добав\w*|меня\w*|трог\w*|нуж\w*|треб\w*)|do\s+not|without)\b",
            clause, re.IGNORECASE,
        )
        for clause in re.split(r"[.!?;\n]", prompt)
        )
    )
    source = files.get("src/app/page.tsx", "")
    payload = source.encode()
    if not positive or not payload or len(payload) > 128 * 1024 or "\\u" in source:
        return None
    program = Parser(_language()).parse(payload).root_node
    root = _root(program)
    if program.has_error or root is None:
        return None
    body = root.child_by_field_name("body")
    if body is None:
        return None
    # These alternatives can implement controls outside local React state.
    if any(
        (item.type == "call_expression" and re.fullmatch(
            r"useSearchParams|useRouter|useReducer|URLSearchParams|use\w*(?:Filter|Sort)\w*",
            _text(item.child_by_field_name("function")),
        )) or (item.type in {"jsx_opening_element", "jsx_self_closing_element"}
               and re.fullmatch(r"\w*(?:Filter|Sort)\w*", _text(item.child_by_field_name("name"))))
        for item in _walk(body)
    ):
        return None
    hooks = _react_hooks(program)
    declarations = _declarations(body)
    if len(declarations) > 64:
        return None
    bindings: list[tuple[str, str, bool]] = []
    for name, value in declarations:
        if name.type != "array_pattern" or value.type != "call_expression":
            continue
        pair = name.named_children
        if len(pair) != 2 or any(item.type != "identifier" for item in pair):
            continue
        callee = value.child_by_field_name("function")
        imported = _text(callee)
        hook_root = imported.split(".")[0]
        hook = hooks.get(hook_root)
        if not ((hook == "useState" and imported == hook_root) or
                (hook == "namespace" and imported == hook_root + ".useState")):
            continue
        if _declared(root.child_by_field_name("parameters"), hook_root) or any(
            _declared(other, hook_root) for other, _ in declarations
        ) or any(
            statement.type in {"function_declaration", "class_declaration"}
            and _declared(statement.child_by_field_name("name"), hook_root)
            for statement in body.named_children
        ):
            continue
        arguments = value.child_by_field_name("arguments")
        literal = bool(arguments and len(arguments.named_children) == 1
                       and arguments.named_children[0].type == "string")
        bindings.append((_text(pair[0]), _text(pair[1]), literal))
    for operation in sorted(positive):
        dependent = [
            (getter, setter, literal, _text(name)) for getter, setter, literal in bindings
            for name, value in declarations if name.type == "identifier"
            and _depends(value, getter, operation)
        ]
        if len({item[0] for item in dependent}) > 1 and any(
            _reference(item[1], source) > 1 for item in dependent
        ):
            continue  # Multiple state paths are not a safe negative witness.
        for getter, setter, literal, derived in dependent:
            related_values = [
                value for name, value in declarations
                if name.type == "identifier" and _depends(value, getter, operation)
            ]
            derived_names = {
                result for other, _, _, result in dependent if other == getter
            }
            if _delegates_collection(body, related_values, derived_names):
                continue
            if any(
                other_getter != getter and _reference(other_setter, source) > 1
                and any(_depends(call, other_getter, operation) for call in _walk(body)
                        if call.type == "call_expression")
                for other_getter, other_setter, _ in bindings
            ):
                continue  # A working inline/JSX state path may supply the controls.
            if not literal or operation not in getter.casefold():
                continue
            if any(other_getter != getter and _reference(other_setter, source) > 1
                   for other_getter, other_setter, _, _ in dependent):
                continue  # An active alternative implementation is inconclusive.
            if _reference(setter, source) == 1:
                return (
                    "Requested interactive collection controls have an unreferenced React "
                    "state setter " + setter + " in src/app/page.tsx. Connect the actual "
                    "visible controls to that state, or remove stale state and verify the "
                    "real implementation. A patch helper's success text/strings and a "
                    "green build are not UI evidence."
                )
            if any(
                call.type == "call_expression"
                and call.id not in {value.id for value in related_values}
                and _depends(call, getter, operation)
                for call in _walk(body)
            ):
                continue  # Same-state inline operations may render a working result.
            if operation == "sort" and all(
                _reference(result, source) == 1 and any(
                    _text(name) == result and _pure_copy_sort(value)
                    for name, value in declarations
                ) for result in derived_names
            ):
                return (
                    "Requested interactive collection result " + derived +
                    " is never referenced in src/app/page.tsx. Connect the derived "
                    "collection to the actual rendered list, or remove stale state and "
                    "verify the real implementation. A green build is not UI evidence."
                )
    return None

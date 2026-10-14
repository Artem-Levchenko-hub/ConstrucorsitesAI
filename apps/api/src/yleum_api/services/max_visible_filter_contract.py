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


def _memo_hooks(program: Node, root: Node, body: Node) -> frozenset[str]:
    names: set[str] = set()
    for statement in program.named_children:
        if statement.type != "import_statement" or _text(
            statement.child_by_field_name("source")
        )[1:-1] != "react":
            continue
        for node in _walk(statement):
            if (node.type == "import_specifier"
                    and _text(node.child_by_field_name("name")) == "useMemo"
                    and node.child_by_field_name("alias") is None):
                names.add("useMemo")
            if node.type in {"import_clause", "namespace_import"} and any(
                child.type == "identifier" and _text(child) == "React"
                for child in node.named_children
            ):
                names.add("React.useMemo")
    shadows: list[Node | None] = [name for name, _ in _declarations(body)]
    parameters = root.child_by_field_name("parameters")
    if parameters is not None:
        shadows.extend(parameters.named_children)
    for scope in (program, body):
        for statement in scope.named_children:
            if statement.type in {"function_declaration", "class_declaration"}:
                shadows.append(statement.child_by_field_name("name"))
            elif scope == program and statement.type in {
                "lexical_declaration", "variable_declaration",
            }:
                shadows.extend(
                    child.child_by_field_name("name") for child in statement.named_children
                    if child.type == "variable_declarator"
                )
    return frozenset(name for name in names if not any(
        _declared(binding, name.split(".")[0]) for binding in shadows
    ))


def _memo_sort_input(value: Node, getter: str, hooks: frozenset[str]) -> Node | None:
    """Attribute only a pure sort expression or conditional sorts of a fresh copy."""
    if value.type != "call_expression" or _text(value.child_by_field_name("function")) not in hooks:
        return None
    arguments = value.child_by_field_name("arguments")
    args = arguments.named_children if arguments is not None else []
    if len(args) != 2 or args[0].type != "arrow_function" or args[1].type != "array":
        return None
    callback = args[0]
    parameters = callback.child_by_field_name("parameters")
    if (parameters is None or parameters.named_children or any(
        child.type == "async" for child in callback.children
    ) or any(node.type != "identifier" for node in args[1].named_children)
            or not any(_text(node) == getter for node in args[1].named_children)):
        return None
    body = callback.child_by_field_name("body")
    if body is None:
        return None
    if body.type == "call_expression":
        if any(node.type in {"new_expression", "assignment_expression", "update_expression"}
               or (node.type == "call_expression" and node.id != body.id)
               for node in _walk(body)):
            return None
        return body if _pure_copy_sort(body) and _depends(body, getter, "sort") else None
    if body.type != "statement_block":
        return None
    statements = [node for node in body.named_children if node.type != "comment"]
    if len(statements) < 3 or statements[0].type != "lexical_declaration":
        return None
    declaration = statements[0]
    variables = [node for node in declaration.named_children if node.type == "variable_declarator"]
    if _text(declaration.child_by_field_name("kind")) != "const" or len(variables) != 1:
        return None
    name = variables[0].child_by_field_name("name")
    copied = variables[0].child_by_field_name("value")
    if (name is None or name.type != "identifier" or _text(name) == getter
            or copied is None or copied.type != "array" or len(copied.named_children) != 1):
        return None
    spread = copied.named_children[0]
    if (spread.type != "spread_element" or len(spread.named_children) != 1
            or spread.named_children[0].type != "identifier"):
        return None
    returned = statements[-1]
    if (returned.type != "return_statement" or len(returned.named_children) != 1
            or _text(returned.named_children[0]) != _text(name)):
        return None

    def conditional_sort(statement: Node) -> bool:
        if statement.type != "if_statement":
            return False
        condition = statement.child_by_field_name("condition")
        if condition is None or len(condition.named_children) != 1:
            return False
        comparison = condition.named_children[0]
        left = comparison.child_by_field_name("left")
        right = comparison.child_by_field_name("right")
        if (comparison.type != "binary_expression"
                or _text(comparison.child_by_field_name("operator")) not in {
                    "===", "==", "!==", "!=",
                }
                or not any(a is not None and b is not None and a.type == "identifier"
                           and _text(a) == getter and b.type == "string"
                           for a, b in ((left, right), (right, left)))):
            return False
        consequence = statement.child_by_field_name("consequence")
        if consequence is not None and consequence.type == "statement_block":
            consequence = (
                consequence.named_children[0] if len(consequence.named_children) == 1 else None
            )
        if (consequence is None or consequence.type != "expression_statement"
                or len(consequence.named_children) != 1):
            return False
        call = consequence.named_children[0]
        function = call.child_by_field_name("function")
        arguments = call.child_by_field_name("arguments")
        if (call.type != "call_expression" or function is None
                or function.type != "member_expression"
                or _text(function.child_by_field_name("object")) != _text(name)
                or _text(function.child_by_field_name("property")) != "sort"
                or arguments is None or len(arguments.named_children) != 1):
            return False
        comparator = arguments.named_children[0]
        comparator_body = comparator.child_by_field_name("body")
        if (comparator.type != "arrow_function" or comparator_body is None
                or _declared(comparator.child_by_field_name("parameters"), getter)
                or any(child.type == "async" for child in comparator.children)
                or any(node.type in {"statement_block", "call_expression", "assignment_expression",
                                    "augmented_assignment_expression", "update_expression",
                                    "await_expression", "new_expression", "arrow_function",
                                    "function_expression"}
                       for node in _walk(comparator_body))):
            return False
        alternative = statement.child_by_field_name("alternative")
        return alternative is None or (
            len(alternative.named_children) == 1 and conditional_sort(alternative.named_children[0])
        )

    return copied if all(conditional_sort(node) for node in statements[1:-1]) else None


def _delegates_collection(body: Node, values: list[Node], names: set[str]) -> bool:
    receivers = set(names)
    for value in values:
        function = value.child_by_field_name("function")
        receiver = value if value.type == "array" else (
            function.child_by_field_name("object") if function else None
        )
        if receiver is not None:
            receivers.update(_text(node) for node in _walk(receiver) if node.type == "identifier")
    declarations = _declarations(body)
    for _ in range(len(declarations)):
        for name, value in declarations:
            if (name.type == "identifier" and value.type in {"identifier", "object", "array"}
                    and any(node.type == "identifier" and _text(node) in receivers
                            for node in _walk(value))):
                receivers.add(_text(name))
    for element in _walk(body):
        if element.type not in {"jsx_opening_element", "jsx_self_closing_element"}:
            continue
        component = _text(element.child_by_field_name("name"))
        if not component or not component[0].isupper():
            continue
        for child in element.named_children:
            if child.type not in {"jsx_attribute", "jsx_expression"}:
                continue
            if any(node.type == "identifier" and _text(node) in receivers for node in _walk(child)):
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


def _known_priority_views(program: Node, body: Node, collections: set[str]) -> bool:
    """Only inspect local, transparent list views; delegation is otherwise unknown."""
    local = {
        _text(node.child_by_field_name("name")): node
        for node in program.named_children if node.type == "function_declaration"
    }
    for element in _walk(body):
        if element.type not in {"jsx_opening_element", "jsx_self_closing_element"}:
            continue
        if any(child.type == "jsx_expression" for child in element.named_children):
            return False  # Spread props can carry another implementation.
        component = _text(element.child_by_field_name("name"))
        if not component or not component[0].isupper():
            continue
        props = set()
        for attribute in element.named_children:
            if attribute.type != "jsx_attribute":
                continue
            if any(node.type == "identifier" and _text(node) in collections
                   for node in _walk(attribute)):
                props.add(_text(attribute.named_children[0]))
        if not props:
            continue
        view = local.get(component)
        view_body = view.child_by_field_name("body") if view is not None else None
        if view is None or view_body is None or re.search(
            r"priority|приоритет", _text(view_body), re.I,
        ):
            return False
        if any(node.type in {"pair_pattern", "rest_pattern", "assignment_pattern"}
               for node in _walk(view)):
            return False  # Renamed/default/spread bindings need scope analysis.
        if _delegates_collection(view_body, [], props):
            return False
        for node in _walk(view_body):
            if node.type in {"subscript_expression", "spread_element"}:
                return False
            if node.type == "variable_declarator" and any(
                item.type in {"identifier", "property_identifier"} and _text(item) in props
                for item in _walk(node.child_by_field_name("value") or node)
            ):
                return False  # Aliased/transformed collection needs deeper analysis.
            if node.type != "call_expression":
                continue
            function = _text(node.child_by_field_name("function"))
            if re.search(r"\buse[A-Z]|\.(?:filter|sort)\b", function):
                return False
            arguments = node.child_by_field_name("arguments")
            if arguments and any(item.type in {"identifier", "property_identifier"}
                                 and _text(item) in props
                                 for item in _walk(arguments)):
                return False
    return True


def _priority_depends(value: Node, getter: str) -> bool:
    if not _depends(value, getter, "filter"):
        return False
    for node in _walk(value):
        if node.type != "binary_expression" or _text(
            node.child_by_field_name("operator")
        ) not in {"==", "===", "!=", "!=="}:
            continue
        left, right = node.child_by_field_name("left"), node.child_by_field_name("right")
        for state, field in ((left, right), (right, left)):
            if state is not None and field is not None and _text(state) == getter and any(
                item.type == "member_expression"
                and _text(item.child_by_field_name("property")) == "priority"
                for item in _walk(field)
            ):
                return True
    return False


def _only_status_filter(value: Node, getter: str) -> bool:
    """A known status predicate cannot substitute for requested priority controls."""
    arguments = value.child_by_field_name("arguments")
    if arguments is None:
        return False
    references = [node for node in _walk(arguments)
                  if node.type == "identifier" and _text(node) == getter]
    for node in references:
        parent = node.parent
        if parent is None or parent.type != "binary_expression" or _text(
            parent.child_by_field_name("operator")
        ) not in {"==", "===", "!=", "!=="}:
            return False
        left, right = parent.child_by_field_name("left"), parent.child_by_field_name("right")
        other = right if left == node else left
        if other is None or not (other.type == "string" or (
            other.type == "member_expression"
            and _text(other.child_by_field_name("property")) == "status"
        )):
            return False
    return bool(references)


def _visible_control_intent(clause: str) -> bool:
    # CSS selectors name style rules, not interactive controls. Remove only
    # that qualified phrase: a separate UI selector in the same clause counts.
    text = re.sub(
        r"\b(?:css[\s-]+(?:селектор\w*|selectors?)|"
        r"(?:селектор\w*|selectors?)[\s-]+css)\b", " ", clause, flags=re.I,
    )
    return re.search(
        r"\b(?:видим\w*|кнопк\w*|переключател\w*|селектор\w*|"
        r"visible|controls?|buttons?|interactive|selectors?)\b", text, re.I,
    ) is not None


def _catalog_sort_intent(clause: str) -> bool:
    return bool(
        re.search(r"\b(?:каталог\w*|catalog\w*)\b", clause, re.I)
        and re.search(r"\b(?:добав\w*|сдела\w*|реализ\w*|add|implement|enable)\b", clause, re.I)
        and not re.search(
            r"\b(?:sql|api|server\w*|backend|database|сервер\w*|баз\w*|"
            r"unchanged|preserve|keep|сохран\w*|остав\w*)\b|без\s+изменен", clause, re.I,
        )
    )


def _priority_filter_gap(
    prompt: str, program: Node, body: Node, source: str,
    declarations: list[tuple[Node, Node]], bindings: list[tuple[str, str, bool]],
) -> str | None:
    # Field-specific positive intent must be in the same non-negative UI clause.
    if not any(
        re.search(r"\b(?:priority|приоритет\w*)\b", clause, re.I)
        and re.search(r"\b(?:фильтр\w*|filters?)\b", clause, re.I)
        and _visible_control_intent(clause)
        and not re.search(r"\b(?:не|нет|not|without)\b", clause, re.I)
        for clause in re.split(r"[.!?;\n]", prompt)
    ):
        return None
    for getter, setter, literal in bindings:
        if not literal or not re.fullmatch(r"priorityFilter|filterPriority", getter, re.I):
            continue
        if _reference(setter, source) != 1:
            continue
        values = [(name, value) for name, value in declarations
                  if name.type == "identifier" and _priority_depends(value, getter)]
        if not values:
            continue
        if any(
            other != getter and _reference(other_setter, source) > 1
            and any(_depends(node, other, "filter") and (
                        node.id not in {value.id for _, value in values}
                        or not _only_status_filter(node, other)
                    )
                    for node in _walk(body) if node.type == "call_expression")
            for other, other_setter, _ in bindings
        ):
            continue  # Another state may implement the same requested field.
        collections = {_text(name) for name, _ in values}
        for _, value in values:
            function = value.child_by_field_name("function")
            receiver = function.child_by_field_name("object") if function else None
            if receiver is not None:
                collections.update(_text(node) for node in _walk(receiver)
                                   if node.type == "identifier")
        if _known_priority_views(program, body, collections):
            return (
                "The explicitly requested visible priority filter has an unreferenced "
                "React state setter " + setter + " in src/app/page.tsx. A working status "
                "filter does not implement the priority controls. Connect and verify "
                "the requested controls before done."
            )
    return None


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
        and (_visible_control_intent(clause) or (
            operation == "sort" and _catalog_sort_intent(clause)
        ))
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
    memo_hooks = _memo_hooks(program, root, body)

    def depends(value: Node, getter: str, operation: str) -> bool:
        return _depends(value, getter, operation) or (
            operation == "sort" and _memo_sort_input(value, getter, memo_hooks) is not None
        )

    if "filter" in positive:
        priority_gap = _priority_filter_gap(prompt, program, body, source, declarations, bindings)
        if priority_gap:
            return priority_gap
    for operation in sorted(positive):
        dependent = [
            (getter, setter, literal, _text(name)) for getter, setter, literal in bindings
            for name, value in declarations if name.type == "identifier"
            and depends(value, getter, operation)
        ]
        if len({item[0] for item in dependent}) > 1 and any(
            _reference(item[1], source) > 1 for item in dependent
        ):
            continue  # Multiple state paths are not a safe negative witness.
        for getter, setter, literal, derived in dependent:
            related_values = [
                value for name, value in declarations
                if name.type == "identifier" and depends(value, getter, operation)
            ]
            derived_names = {
                result for other, _, _, result in dependent if other == getter
            }
            collection_inputs = [
                _memo_sort_input(value, getter, memo_hooks) or value for value in related_values
            ] if operation == "sort" else related_values
            if _delegates_collection(body, collection_inputs, derived_names):
                continue
            if any(
                other_getter != getter and _reference(other_setter, source) > 1
                and any(depends(call, other_getter, operation) for call in _walk(body)
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
                and depends(call, getter, operation)
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

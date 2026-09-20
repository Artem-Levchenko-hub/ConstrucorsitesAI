"""A deliberately bounded schema-compatibility contract for historical PostgreSQL writers.

Code rollback never runs a down migration. Unknown semantics need adaptation. The
contract describes compatibility only; it grants or restricts no database access.
"""

from __future__ import annotations

import re
from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

Identifier = Annotated[str, StringConstraints(pattern=r"^[a-zA-Z_][a-zA-Z0-9_]{0,62}$")]


class StrictContract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class DataColumn(StrictContract):
    name: Identifier
    type: str = Field(min_length=1, max_length=160)
    nullable: bool = True
    meaning: str | None = Field(default=None, max_length=160)
    values: list[str] | None = None
    json_keys: list[str] | None = None
    # Observed live behaviour only; historical declarations never set these.
    default: str | None = Field(default=None, max_length=2000)
    identity: Literal["always", "by_default"] | None = None


class CheckConstraint(StrictContract):
    """A named CHECK as PostgreSQL renders it (or a historical declaration)."""

    name: str | None = Field(default=None, max_length=63)
    definition: str = Field(min_length=1, max_length=4000)


class DataForeignKey(StrictContract):
    column: Identifier
    table: Identifier
    target: Identifier
    on_delete: Literal["NO ACTION", "RESTRICT", "CASCADE", "SET NULL", "SET DEFAULT"] = "NO ACTION"
    on_update: Literal["NO ACTION", "RESTRICT", "CASCADE", "SET NULL", "SET DEFAULT"] = "NO ACTION"


class OwnerReference(StrictContract):
    column: Identifier
    table: Identifier
    target: Identifier = "id"


class DataTable(StrictContract):
    name: Identifier
    columns: list[DataColumn] = Field(min_length=1, max_length=500)
    owner_column: Identifier | None = None
    owner_reference: OwnerReference | None = None
    read_only: bool = False
    # Legacy unnamed expressions from explicit contracts; the catalog fills
    # ``check_constraints`` instead. Both are compared as one normalized set.
    checks: list[str] = Field(default_factory=list)
    check_constraints: list[CheckConstraint] = Field(default_factory=list)
    foreign_keys: list[DataForeignKey] = Field(default_factory=list)
    primary_key: list[Identifier] = Field(default_factory=list)
    unique_keys: list[list[Identifier]] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_table(self) -> Self:
        names = [column.name for column in self.columns]
        if self.name.startswith(("pg_", "sql_", "omnia_")):
            raise ValueError("reserved database name")
        if len(names) != len(set(names)):
            raise ValueError("duplicate column")
        for key in [self.primary_key, *self.unique_keys]:
            if len(key) != len(set(key)) or any(name not in names for name in key):
                raise ValueError("invalid key columns")
        if self.owner_column and self.owner_column not in names:
            raise ValueError("missing owner column")
        if self.owner_reference and self.owner_reference.column not in names:
            raise ValueError("missing owner reference")
        if self.owner_column and self.owner_reference:
            raise ValueError("a table has at most one owner rule")
        return self


class DataContract(StrictContract):
    version: Literal[1]
    tables: list[DataTable] = Field(default_factory=list, max_length=200)

    @model_validator(mode="after")
    def validate_references(self) -> Self:
        tables = {table.name: table for table in self.tables}
        if len(tables) != len(self.tables):
            raise ValueError("duplicate table")
        for table in self.tables:
            reference = table.owner_reference
            if reference:
                parent = tables.get(reference.table)
                if (
                    parent is None
                    or parent.owner_column is None
                    or reference.target not in {column.name for column in parent.columns}
                ):
                    raise ValueError("actor references require a directly owned parent")
        return self


class Diagnostic(BaseModel):
    """One directional finding: which operation of the historical app, on which
    object, with which status. Codes are stable; wording lives in the report."""

    code: str
    status: Literal["compatible", "incompatible", "unknown"]
    operation: str
    object: str
    detail: str | None = None


class ContractAssessment(BaseModel):
    blockers: list[str] = Field(default_factory=list)
    retained_columns: list[str] = Field(default_factory=list)
    blocked_deletes: list[str] = Field(default_factory=list)
    diagnostics: list[Diagnostic] = Field(default_factory=list)


# Defaults that only mint identifiers/timestamps: they cannot invent a business
# fact, so an old insert that omits such a column stays meaningful.
_TECHNICAL_DEFAULT = re.compile(
    r"^(?:now\(\)|CURRENT_TIMESTAMP|CURRENT_DATE|LOCALTIMESTAMP|clock_timestamp\(\)"
    r"|statement_timestamp\(\)|transaction_timestamp\(\)|gen_random_uuid\(\)"
    r"|uuid_generate_v4\(\)|nextval\('[^']+'::regclass\))"
    r"(?:::[a-z ]+)?$"
)


def technical_default(column: DataColumn) -> bool:
    return column.identity is not None or bool(
        column.default and _TECHNICAL_DEFAULT.match(column.default.strip())
    )


_TOKEN = re.compile(
    r"'(?:[^']|'')*'"  # string literal, kept byte-for-byte
    r'|"(?:[^"]|"")*"'  # quoted identifier
    r"|[A-Za-z_][A-Za-z0-9_$]*"  # word
    r"|\d+(?:\.\d+)?"  # number
    r"|::|<=|>=|<>|!=|\|\||[-+*/%<>=(),.\[\]]"
    r"|\S"
)
# Type names that follow a literal cast; multi-word names are listed explicitly.
_TYPE_WORDS = {
    "character",
    "varying",
    "double",
    "precision",
    "timestamp",
    "time",
    "with",
    "without",
    "zone",
}


def _word(token: str) -> str:
    if token.startswith('"'):
        return token[1:-1].replace('""', '"')
    return token.lower() if re.match(r"[A-Za-z_]", token) else token


def _is_literal(token: str) -> bool:
    return token.startswith("'") or bool(re.fullmatch(r"\d+(?:\.\d+)?", token))


def _strip_literal_casts(tokens: list[str]) -> list[str]:
    """Drop `::type` only after a literal ('new'::text, (0)::numeric). A cast of a
    column (`a::integer` vs `a::text`) changes meaning and is kept."""
    out: list[str] = []
    index = 0
    while index < len(tokens):
        token = tokens[index]
        literal_before = bool(out) and (
            _is_literal(out[-1])
            or (len(out) >= 3 and out[-1] == ")" and _is_literal(out[-2]) and out[-3] == "(")
        )
        if token == "::" and literal_before:
            index += 1
            while index < len(tokens) and (
                re.fullmatch(r"[a-z_][a-z0-9_]*", tokens[index]) is not None
            ):
                word = tokens[index]
                index += 1
                if word not in _TYPE_WORDS and not (
                    index < len(tokens) and tokens[index] in _TYPE_WORDS
                ):
                    break
            if index < len(tokens) and tokens[index] == "(":  # varchar(20)
                while index < len(tokens) and tokens[index] != ")":
                    index += 1
                index += 1
            while index + 1 < len(tokens) and tokens[index] == "[" and tokens[index + 1] == "]":
                index += 2
            continue
        out.append(token)
        index += 1
    return out


def _any_array_to_in(tokens: list[str]) -> list[str]:
    """PostgreSQL stores `x IN (a, b)` as `x = ANY (ARRAY[a, b])`."""
    out: list[str] = []
    index = 0
    while index < len(tokens):
        if tokens[index : index + 5] == ["=", "any", "(", "array", "["]:
            depth, cursor = 0, index + 4
            while cursor < len(tokens):
                depth += tokens[cursor] == "["
                depth -= tokens[cursor] == "]"
                if depth == 0:
                    break
                cursor += 1
            if cursor + 1 < len(tokens) and tokens[cursor + 1] == ")":
                out += ["in", "(", *tokens[index + 5 : cursor], ")"]
                index = cursor + 2
                continue
        out.append(tokens[index])
        index += 1
    return out


def _drop_atom_parens(tokens: list[str]) -> list[str]:
    """`(amount)` -> `amount`, `(0)` -> `0`; a call like `f(x)` keeps its parens."""
    changed = True
    while changed:
        changed = False
        for index in range(len(tokens) - 2):
            if tokens[index] == "(" and tokens[index + 2] == ")" and tokens[index + 1] not in "(),":
                previous = tokens[index - 1] if index else ""
                if re.fullmatch(r"[a-z_][a-z0-9_$]*", previous) and previous not in {
                    "and",
                    "or",
                    "not",
                    "in",
                    "is",
                    "check",
                }:
                    continue  # function call argument
                tokens = [*tokens[:index], tokens[index + 1], *tokens[index + 3 :]]
                changed = True
                break
    return tokens


def _strip_outer(tokens: list[str]) -> list[str]:
    while len(tokens) >= 2 and tokens[0] == "(" and tokens[-1] == ")":
        depth = 0
        for index, token in enumerate(tokens):
            depth += token == "("
            depth -= token == ")"
            if depth == 0 and index < len(tokens) - 1:
                return tokens
        tokens = tokens[1:-1]
    return tokens


def normalize_check(definition: str) -> str:
    """Canonical text of a CHECK across renderers (Drizzle SQL vs pg_get_constraintdef).

    Conservative on purpose: literals keep their exact case/spacing, casts are only
    dropped from literals and only redundant parentheses go. When two renderings
    still differ the CHECK is reported as changed (a safe "needs verification"),
    never as unchanged."""
    raw = re.sub(r"\s+NOT VALID\s*$", "", definition.strip(), flags=re.IGNORECASE)
    tokens = [_word(token) for token in _TOKEN.findall(raw)]
    # Table-qualified column references: "visits"."amount" -> amount.
    qualified: list[str] = []
    for token in tokens:
        if len(qualified) >= 2 and qualified[-1] == "." and not _is_literal(qualified[-2]):
            qualified = qualified[:-2]
        qualified.append(token)
    tokens = qualified
    if tokens and tokens[0] == "check":
        tokens = tokens[1:]
    tokens = _strip_literal_casts(tokens)
    tokens = _drop_atom_parens(tokens)
    tokens = _any_array_to_in(tokens)
    tokens = _strip_outer(tokens)
    return " ".join(tokens)


def _checks(table: DataTable) -> dict[str, str | None]:
    """normalized expression -> constraint name (None when undeclared)."""
    result: dict[str, str | None] = {}
    for check in table.check_constraints:
        result[normalize_check(check.definition)] = check.name
    for expression in table.checks:
        result.setdefault(normalize_check(expression), None)
    return result


def _compare_checks(result: ContractAssessment, table: DataTable, live: DataTable) -> None:
    """Live CHECKs constrain the historical writer; historical-only CHECKs are looser."""
    old_checks, live_checks = _checks(table), _checks(live)
    old_names = {name: expr for expr, name in old_checks.items() if name}
    for expression, name in live_checks.items():
        subject = f"public.{table.name}" + (f".{name}" if name else "")
        if expression in old_checks:
            result.diagnostics.append(
                Diagnostic(
                    code="check_unchanged",
                    status="compatible",
                    operation=f"{table.name}.write",
                    object=subject,
                )
            )
        elif name and name in old_names:
            result.blockers.append(f"check_changed:{table.name}.{name}")
            result.diagnostics.append(
                Diagnostic(
                    code="check_changed",
                    status="unknown",
                    operation=f"{table.name}.write",
                    object=subject,
                )
            )
        else:
            result.blockers.append(f"check_added:{table.name}" + (f".{name}" if name else ""))
            result.diagnostics.append(
                Diagnostic(
                    code="check_added",
                    status="unknown",
                    operation=f"{table.name}.write",
                    object=subject,
                )
            )


def assess_contract(old: DataContract, current: DataContract) -> ContractAssessment:
    result = ContractAssessment()
    current_tables = {table.name: table for table in current.tables}
    old_tables = {table.name: table for table in old.tables}
    for table in old.tables:
        live = current_tables.get(table.name)
        subject = f"public.{table.name}"
        if live is None:
            result.blockers.append(f"missing_table:{table.name}")
            result.diagnostics.append(
                Diagnostic(
                    code="table_missing",
                    status="incompatible",
                    operation=f"{table.name}.read",
                    object=subject,
                )
            )
            continue
        if table.owner_column != live.owner_column or table.owner_reference != live.owner_reference:
            result.blockers.append(f"actor_rule_changed:{table.name}")
            result.diagnostics.append(
                Diagnostic(
                    code="owner_rule_changed",
                    status="unknown",
                    operation=f"{table.name}.access",
                    object=subject,
                )
            )
        if (
            table.foreign_keys != live.foreign_keys
            or table.primary_key != live.primary_key
            or table.unique_keys != live.unique_keys
        ):
            result.blockers.append(f"constraints_changed:{table.name}")
            result.diagnostics.append(
                Diagnostic(
                    code="keys_or_relations_changed",
                    status="unknown",
                    operation=f"{table.name}.write",
                    object=subject,
                )
            )
        _compare_checks(result, table, live)
        columns = {column.name: column for column in live.columns}
        old_names = {column.name for column in table.columns}
        for previous in table.columns:
            column = columns.get(previous.name)
            key = f"{table.name}.{previous.name}"
            if column is None:
                result.blockers.append(f"missing_column:{key}")
                result.diagnostics.append(
                    Diagnostic(
                        code="column_missing",
                        status="incompatible",
                        operation=f"{table.name}.read",
                        object=f"public.{key}",
                    )
                )
            elif (
                previous.type != column.type
                or previous.meaning != column.meaning
                or previous.values != column.values
                or (previous.nullable and not column.nullable)
            ):
                result.blockers.append(f"column_contract_changed:{key}")
                if previous.type != column.type:
                    code, detail = "column_type_changed", f"{previous.type} -> {column.type}"
                elif previous.values != column.values:
                    code, detail = "enum_values_changed", None
                elif previous.meaning != column.meaning:
                    code, detail = "column_meaning_changed", None
                else:
                    code, detail = "column_became_required", None
                result.diagnostics.append(
                    Diagnostic(
                        code=code,
                        status="unknown",
                        operation=f"{table.name}.write",
                        object=f"public.{key}",
                        detail=detail,
                    )
                )
            elif (
                column.type in {"json", "jsonb"}
                and previous.json_keys is not None
                and (
                    column.json_keys is None
                    or not set(previous.json_keys).issubset(column.json_keys)
                )
            ):
                # Plain databases declare no JSON keys; only a known key loss blocks.
                result.blockers.append(f"json_write_contract_missing:{key}")
                result.diagnostics.append(
                    Diagnostic(
                        code="json_keys_lost",
                        status="unknown",
                        operation=f"{table.name}.update",
                        object=f"public.{key}",
                    )
                )
            if (
                column is not None
                and previous.type in {"json", "jsonb"}
                and column.type in {"json", "jsonb"}
                and previous.json_keys is None
            ):
                result.blockers.append(f"json_write_contract_unknown:{key}")
                result.blocked_deletes.append(table.name)
                result.diagnostics.append(
                    Diagnostic(
                        code="json_write_contract_unknown",
                        status="unknown",
                        operation=f"{table.name}.update",
                        object=f"public.{key}",
                    )
                )
            elif (
                column is not None
                and column.json_keys is not None
                and previous.json_keys is not None
                and set(column.json_keys) - set(previous.json_keys)
            ):
                result.blocked_deletes.append(table.name)
                result.blockers.append(f"json_write_contract_changed:{key}")
                result.diagnostics.append(
                    Diagnostic(
                        code="json_key_growth_requires_write_proof",
                        status="unknown",
                        operation=f"{table.name}.update",
                        object=f"public.{key}",
                    )
                )
        if all(previous.name in columns for previous in table.columns):
            result.diagnostics.append(
                Diagnostic(
                    code="read_compatible",
                    status="compatible",
                    operation=f"{table.name}.read",
                    object=subject,
                )
            )
        for column in live.columns:
            if column.name not in old_names:
                key = f"{table.name}.{column.name}"
                result.retained_columns.append(key)
                result.blocked_deletes.append(table.name)
                if column.nullable:
                    continue
                if technical_default(column):
                    # An identifier/timestamp minted by PostgreSQL invents no fact.
                    result.diagnostics.append(
                        Diagnostic(
                            code="required_field_filled_technically",
                            status="compatible",
                            operation=f"{table.name}.create",
                            object=f"public.{key}",
                        )
                    )
                    continue
                # A business default can fabricate a surname/status, so it is not
                # evidence that an old insert preserves the meaning of the field.
                result.blockers.append(f"new_required_column:{key}")
                result.diagnostics.append(
                    Diagnostic(
                        code="required_field_default_needs_confirmation"
                        if column.default
                        else "required_field_missing_on_create",
                        status="unknown" if column.default else "incompatible",
                        operation=f"{table.name}.create",
                        object=f"public.{key}",
                    )
                )
    for table in current.tables:
        previous_table = old_tables.get(table.name)
        unknown = previous_table is None or {column.name for column in table.columns} - {
            column.name for column in previous_table.columns
        }
        for relation in table.foreign_keys:
            if (
                unknown
                and relation.table in old_tables
                and relation.on_delete in {"CASCADE", "SET NULL", "SET DEFAULT"}
            ):
                result.blocked_deletes.append(relation.table)
    protected = set(result.blocked_deletes) | (set(current_tables) - set(old_tables))
    protected.update(
        table.name
        for table in current.tables
        if any(column.type in {"json", "jsonb"} for column in table.columns)
    )
    # Deleting one owner's parent could cascade into independently owned rows.
    for table in current.tables:
        for relation in table.foreign_keys:
            parent = current_tables.get(relation.table)
            if (
                table.owner_column
                and parent
                and parent.owner_column
                and relation.on_delete in {"CASCADE", "SET NULL", "SET DEFAULT"}
            ):
                protected.add(parent.name)
    changed = True
    while changed:
        changed = False
        for table in current.tables:
            if table.name not in protected:
                continue
            for relation in table.foreign_keys:
                if relation.on_delete in {"CASCADE", "SET NULL", "SET DEFAULT"}:
                    if relation.table not in protected:
                        protected.add(relation.table)
                        changed = True
    result.blocked_deletes = sorted(protected & set(old_tables))
    return result


def qi(value: str) -> str:
    if not re.fullmatch(r"[a-zA-Z_][a-zA-Z0-9_]{0,62}", value):
        raise ValueError("unsafe SQL identifier")
    return '"' + value + '"'


def ql(value: str) -> str:
    if "\x00" in value:
        raise ValueError("NUL in SQL literal")
    return "'" + value.replace("'", "''") + "'"

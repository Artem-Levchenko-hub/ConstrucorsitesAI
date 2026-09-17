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
    checks: list[str] = Field(default_factory=list)
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


class ContractAssessment(BaseModel):
    blockers: list[str] = Field(default_factory=list)
    retained_columns: list[str] = Field(default_factory=list)
    blocked_deletes: list[str] = Field(default_factory=list)


def assess_contract(old: DataContract, current: DataContract) -> ContractAssessment:
    result = ContractAssessment()
    current_tables = {table.name: table for table in current.tables}
    old_tables = {table.name: table for table in old.tables}
    for table in old.tables:
        live = current_tables.get(table.name)
        if live is None:
            result.blockers.append(f"missing_table:{table.name}")
            continue
        if table.owner_column != live.owner_column or table.owner_reference != live.owner_reference:
            result.blockers.append(f"actor_rule_changed:{table.name}")
        if (
            table.checks != live.checks
            or table.foreign_keys != live.foreign_keys
            or table.primary_key != live.primary_key
            or table.unique_keys != live.unique_keys
        ):
            result.blockers.append(f"constraints_changed:{table.name}")
        columns = {column.name: column for column in live.columns}
        old_names = {column.name for column in table.columns}
        for previous in table.columns:
            column = columns.get(previous.name)
            key = f"{table.name}.{previous.name}"
            if column is None:
                result.blockers.append(f"missing_column:{key}")
            elif (
                previous.type != column.type
                or previous.meaning != column.meaning
                or previous.values != column.values
                or (previous.nullable and not column.nullable)
            ):
                result.blockers.append(f"column_contract_changed:{key}")
            elif (
                column.type in {"json", "jsonb"}
                and previous.json_keys is not None
                and (column.json_keys is None
                     or not set(previous.json_keys).issubset(column.json_keys))
            ):
                # Plain databases declare no JSON keys; only a known key loss blocks.
                result.blockers.append(f"json_write_contract_missing:{key}")
            if (
                column is not None
                and column.json_keys is not None
                and previous.json_keys is not None
                and set(column.json_keys) - set(previous.json_keys)
            ):
                result.blocked_deletes.append(table.name)
        for column in live.columns:
            if column.name not in old_names:
                key = f"{table.name}.{column.name}"
                result.retained_columns.append(key)
                result.blocked_deletes.append(table.name)
                # A default can fabricate a surname/status, so it is not evidence
                # that an old insert has preserved the meaning of a required field.
                if not column.nullable:
                    result.blockers.append(f"new_required_column:{key}")
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

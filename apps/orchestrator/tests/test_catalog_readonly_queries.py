"""Offline SQL construction checks: no database, user routines or SQL execution."""

from __future__ import annotations

import ast
import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from pglast import parse_sql

from yleum_orchestrator.services import restoration_catalog as catalog


def _nodes(value):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from _nodes(child)
    elif isinstance(value, (list, tuple)):
        for child in value:
            yield from _nodes(child)


def _names(value):
    return [part["sval"] for part in value]


@pytest.mark.parametrize("sql", [catalog.CATALOG_SQL, catalog.DATABASE_INVENTORY_SQL])
def test_display_catalog_resolves_only_explicit_builtin_names(sql):
    # Public remains visible to native deparsers, never to query name resolution.
    # Check operators too: schema order alone cannot exclude overloads with a
    # different signature. IN and simple CASE contain implicit equality lookup.
    statements = parse_sql(sql)
    assert len(statements) == 1
    assert type(statements[0].stmt).__name__ == "SelectStmt"
    seen = set()
    for node in _nodes(statements[0](skip_none=True)):
        kind = node.get("@")
        if kind in {"FuncCall", "TypeName", "A_Expr"}:
            key = {"FuncCall": "funcname", "TypeName": "names", "A_Expr": "name"}[kind]
            assert len(_names(node[key])) == 2
            assert _names(node[key])[0] == "pg_catalog"
            seen.add(kind)
        elif kind == "RangeVar":
            assert node["schemaname"] == "pg_catalog"
            seen.add(kind)
        elif kind == "CaseExpr":
            assert "arg" not in node
    assert {"FuncCall", "A_Expr", "RangeVar"} <= seen


def _assert_trusted_path(sql):
    sql = sql.replace("\\connect template1", "")
    trusted = False
    observed = False
    for statement in parse_sql(sql):
        node = statement.stmt(skip_none=True)
        if node["@"] == "VariableSetStmt" and node["name"] == "search_path":
            assert [arg["val"]["sval"] for arg in node["args"]] == ["pg_catalog"]
            trusted = True
        elif node["@"] == "SelectStmt":
            assert trusted
            observed = True
        else:
            assert node["@"] in {"TransactionStmt", "VariableSetStmt", "LockStmt", "DoStmt"}
    assert observed


def test_catalog_keeps_native_enum_and_default_rendering_without_rewriting(monkeypatch):
    from tests.test_restoration_catalog import catalog_payload

    statements = parse_sql(catalog.CATALOG_SCRIPT)
    path = statements[1].stmt(skip_none=True)
    assert path["name"] == "search_path"
    assert [arg["val"]["sval"] for arg in path["args"]] == ["pg_catalog", "public"]
    payload = catalog_payload()
    payload["tables"][0]["columns"].extend(
        [
            {
                "name": "state",
                "type": '"OrderState"',
                "values": ["new", "done"],
                "default": "'new'::\"OrderState\"",
            },
            {"name": "label", "type": "text", "default": "'public.kept'::text"},
            {"name": "created", "type": "timestamp with time zone", "default": "now()"},
        ]
    )
    calls = []
    monkeypatch.setattr(
        catalog,
        "admin_sql",
        lambda _backend, sql: calls.append(sql) or json.dumps(payload).encode(),
    )
    contract, _, _ = catalog.describe_live_catalog(object())
    columns = {column.name: column for column in contract.tables[0].columns}
    assert columns["state"].type == '"OrderState"'
    assert columns["state"].values == ["new", "done"]
    assert columns["state"].default == "'new'::\"OrderState\""
    assert columns["label"].default == "'public.kept'::text"
    assert columns["created"].default == "now()"
    assert calls == [catalog.CATALOG_SCRIPT]


def test_every_inventory_batch_and_fallback_has_a_trusted_path():
    from yleum_orchestrator.services.versioning import inventory

    seen = []

    def execute(sql):
        seen.append(sql)
        _assert_trusted_path(sql)
        if "json_build_array" in sql:
            raise RuntimeError("bounded batch unavailable")
        return b"7"

    _assert_trusted_path(inventory.LIST_RELATIONS_SQL)
    assert inventory._counts(execute, [{"schema": "other", "name": 'a"b'}]) == [(7, None)]
    assert len(seen) == 2
    assert all('"other"."a""b"' in sql for sql in seen)


@pytest.mark.parametrize("kind", ["counts", "identity", "content", "empty", "template"])
def test_administrator_read_only_observations_exclude_project_lookup(monkeypatch, kind):
    from tests.test_restoration_adaptation_workspace import _inventory
    from tests.test_restoration_row_conversion import assert_guard_before_values
    from yleum_orchestrator.services import (
        restoration_adaptation_workspace as workspace,
    )
    from yleum_orchestrator.services import (
        restoration_binding as binding,
    )
    from yleum_orchestrator.services import (
        restoration_empty as empty,
    )

    seen = []

    def execute(_backend, sql, **_kwargs):
        _assert_trusted_path(sql)
        if kind == "content":
            assert_guard_before_values(sql)
        seen.append(sql)
        if kind == "identity":
            return (
                b'{"database_name":"app","role_name":"app","database_oid":42,'
                b'"system_identifier":"7612345678901234567"}'
            )
        if kind == "content":
            return json.dumps([{"rows": 1, "sha256": "a" * 64}] * 2).encode()
        return b"[1,1]"

    monkeypatch.setattr(binding, "admin_sql", execute)
    monkeypatch.setattr(workspace, "admin_sql", execute)
    if kind == "counts":
        binding.exact_inventory_partition_digests(object(), _inventory())
    elif kind == "identity":
        backend = SimpleNamespace(
            project_database_env=lambda: {"PGDATABASE": "app", "PGUSER": "app"}
        )
        assert binding._database_observation(backend)["database_oid"] == 42
    elif kind == "content":
        workspace.content_inventory_partition_digests(object(), _inventory())
        assert "REPEATABLE READ READ WRITE" in seen[0]
        assert "SET TRANSACTION READ ONLY;" in seen[0]
    else:
        sql = empty.EMPTY_WITNESS_SQL if kind == "empty" else empty.TEMPLATE1_WITNESS_SQL
        _assert_trusted_path(sql)
        if kind == "empty":
            assert_guard_before_values(sql)
        return
    assert len(seen) == 1
    assert "READ ONLY" in seen[0] or "default_transaction_read_only = on" in seen[0]


@pytest.mark.asyncio
@pytest.mark.parametrize("guard_refuses", [False, True])
async def test_activation_owner_row_query_is_trusted_and_still_binds_values(guard_refuses):
    from tests.test_restoration_row_conversion import assert_guard_before_values
    from yleum_orchestrator.core.cell_resources import CellResourceError
    from yleum_orchestrator.schemas.restoration_adaptation_activation import (
        ActivationBusinessWitness,
    )
    from yleum_orchestrator.services.versioning.inventory import quote_ident

    seen = []

    def execute(_backend, sql, **_kwargs):
        _assert_trusted_path(sql)
        assert_guard_before_values(sql)
        seen.append(sql)
        if guard_refuses:
            raise CellResourceError("unsupported conversion")
        return b'[{"id":"item","ownerId":"owner","value":"marker"}]'

    # Execute the unchanged production method without loading unrelated router
    # and POSIX backend imports. The only replaced dependency is its SQL transport.
    source = Path(catalog.__file__).with_name("restoration_adaptation_health.py")
    tree = ast.parse(source.read_text(encoding="utf-8"))
    cls = next(
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == "DockerRestorationAdaptationHealthProber"
    )
    method = next(
        node
        for node in cls.body
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "_read_database_item"
    )
    method.decorator_list = []
    module = ast.Module(
        body=[
            ast.ImportFrom(module="__future__", names=[ast.alias(name="annotations")], level=0),
            method,
        ],
        type_ignores=[],
    )
    scope = {
        "asyncio": asyncio,
        "json": json,
        "admin_sql": execute,
        "quote_ident": quote_ident,
        "CellResourceError": CellResourceError,
    }
    exec(compile(ast.fix_missing_locations(module), str(source), "exec"), scope)
    witness = ActivationBusinessWitness(
        entity="orders", id_column="id", owner_column="owner_id", value_column="value"
    )
    if guard_refuses:
        with pytest.raises(CellResourceError, match="unsupported conversion"):
            await scope["_read_database_item"](object(), witness, "item")
    else:
        result = await scope["_read_database_item"](object(), witness, "item")
        assert result == {"id": "item", "ownerId": "owner", "value": "marker"}
    assert len(seen) == 1 and 'FROM public."orders"' in seen[0]
    assert 'target."id"::text = bound.id' in seen[0]
    assert "pg_catalog.json_to_record" in seen[0]
    assert "6974656d" in seen[0] and "'item'" not in seen[0]


def test_readonly_hardening_does_not_change_administrator_ddl_contract(monkeypatch):
    from yleum_orchestrator.services import restoration_database as database

    seen = []
    monkeypatch.setattr(
        database,
        "_controller_sql",
        lambda _backend, sql, **kwargs: seen.append((sql, kwargs)) or b"ok",
    )
    # A harmless opaque string checks transport construction, not SQL execution.
    assert database.admin_sql(object(), "controller-owned DDL") == b"ok"
    assert seen[0][0] == "controller-owned DDL"
    assert "-c search_path=public" in seen[0][1]["env"]["PGOPTIONS"]
    assert "read_only" not in seen[0][1]["env"]["PGOPTIONS"]

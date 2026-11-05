"""Inert metadata-policy/ordering tests; no PostgreSQL or callbacks are run."""

from __future__ import annotations

import pytest
from pglast import parse_sql, parser

from tests.test_catalog_readonly_queries import _names, _nodes
from yleum_orchestrator.services.restoration_row_conversion import (
    UNSUPPORTED_CONVERSIONS_SQL,
    row_conversion_preamble,
)


def assert_guard_before_values(sql):
    statements = [item.stmt for item in parse_sql(sql)]
    assert type(statements[0]).__name__ == "TransactionStmt"
    assert [option.defname for option in statements[0].options] == [
        "transaction_isolation",
        "transaction_read_only",
    ]
    assert statements[0].options[0].arg.val.sval == "repeatable read"
    assert statements[0].options[1].arg.val.ival == 0
    lock = statements[4]
    assert type(lock).__name__ == "LockStmt" and lock.mode == 5  # SHARE, not ACCESS SHARE
    assert [(rel.schemaname, rel.relname) for rel in lock.relations] == [
        ("pg_catalog", name)
        for name in (
            "pg_attribute",
            "pg_cast",
            "pg_class",
            "pg_namespace",
            "pg_proc",
            "pg_type",
        )
    ]
    readonly = statements[5]
    assert readonly.name == "TRANSACTION"
    assert readonly.args[0].defname == "transaction_read_only"
    assert readonly.args[0].arg.val.ival == 1
    assert type(statements[6]).__name__ == "DoStmt"
    body = statements[6].args[0].arg.sval
    assert UNSUPPORTED_CONVERSIONS_SQL in body
    assert body.index(UNSUPPORTED_CONVERSIONS_SQL) < body.index("row observation requires")
    assert all(type(stmt).__name__ != "SelectStmt" for stmt in statements[:7])
    parser.parse_plpgsql_json(
        "DO "
        + sql.split("DO ", 1)[1].split("$row_conversion_guard$;", 1)[0]
        + "$row_conversion_guard$;"
    )


def test_catalog_locks_precede_readonly_metadata_and_values_until_transaction_end():
    sql = row_conversion_preamble([("public", "orders")]) + "SELECT 1; COMMIT;"
    assert_guard_before_values(sql)
    for removed in ("LOCK TABLE ", "SET TRANSACTION READ ONLY;"):
        changed = "\n".join(line for line in sql.splitlines() if not line.startswith(removed))
        with pytest.raises((AssertionError, AttributeError)):
            assert_guard_before_values(changed)
    assert sql.rstrip().endswith("COMMIT;")


def test_guard_inspects_only_catalog_metadata_and_native_oid_constants():
    statement = parse_sql(UNSUPPORTED_CONVERSIONS_SQL)[0]
    relations = set()
    for node in _nodes(statement(skip_none=True)):
        assert node.get("@") != "FuncCall"  # Not even a project function is consulted.
        if node.get("@") == "RangeVar":
            if node.get("relname") == "value_types":
                assert node.get("schemaname") is None
            else:
                assert node["schemaname"] == "pg_catalog"
                relations.add(node["relname"])
        elif node.get("@") == "TypeCast":
            assert _names(node["typeName"]["names"]) in [
                ["pg_catalog", "regnamespace"],
                ["pg_catalog", "regproc"],
            ]
            assert node["arg"]["@"] == "A_Const"
    assert relations == {
        "pg_attribute",
        "pg_class",
        "pg_namespace",
        "pg_cast",
        "pg_type",
        "pg_proc",
    }
    cte = statement.stmt.withClause.ctes[0]
    assert statement.stmt.withClause.recursive
    assert cte.ctequery.rarg.targetList[0].val.fields[1].sval == "typelem"


def _metadata_predicate(node, row):
    """Evaluate only the actual fixed WHERE predicates against symbolic OIDs.

    This models SQL null/Boolean comparisons, not functions, SQL execution or
    catalog locking. Native behavior is a separate acceptance requirement.
    """
    kind = node["@"]
    if kind == "ColumnRef":
        return row[".".join(_names(node["fields"]))]
    if kind == "A_Const":
        value = node["val"]
        return value.get("sval", value.get("ival"))
    if kind == "TypeCast":
        return _metadata_predicate(node["arg"], row)  # Symbolic native OID identity.
    if kind == "NullTest":
        assert node["nulltesttype"]["name"] == "IS_NULL"
        return _metadata_predicate(node["arg"], row) is None
    if kind == "A_Expr":
        left = _metadata_predicate(node["lexpr"], row)
        right = _metadata_predicate(node["rexpr"], row)
        if node["kind"]["name"] == "AEXPR_DISTINCT":
            return left != right
        assert node["kind"]["name"] == "AEXPR_OP"
        if left is None or right is None:
            return None
        operator = _names(node["name"])[0]
        assert operator in {"=", "<>"}
        return left == right if operator == "=" else left != right
    assert kind == "BoolExpr"
    values = [_metadata_predicate(arg, row) for arg in node["args"]]
    mode = node["boolop"]["name"]
    if mode == "NOT_EXPR":
        return None if values[0] is None else not values[0]
    if mode == "AND_EXPR":
        return False if False in values else None if None in values else True
    assert mode == "OR_EXPR"
    return True if True in values else None if None in values else False


def _predicate(index):
    return (
        parse_sql(UNSUPPORTED_CONVERSIONS_SQL)[0]
        .stmt.targetList[0]
        .val.args[index]
        .subselect.whereClause(skip_none=True)
    )


@pytest.mark.parametrize(
    "changes,rejected",
    [
        ({}, False),
        ({"s.typnamespace": "project"}, True),
        ({"t.typnamespace": "project"}, True),
        ({"f.pronamespace": "project"}, True),
        ({"f.pronamespace": None}, True),
        ({"c.castfunc": 0, "f.pronamespace": None}, False),
    ],
)
def test_actual_cast_predicate_denies_project_types_and_callbacks(changes, rejected):
    row = {
        "s.typnamespace": "pg_catalog",
        "t.typnamespace": "pg_catalog",
        "f.pronamespace": "pg_catalog",
        "c.castfunc": 1,
    }
    assert _metadata_predicate(_predicate(0), row | changes) is rejected


@pytest.mark.parametrize(
    "changes,rejected",
    [
        ({}, False),  # Ordinary enum with native enum_out.
        ({"t.typnamespace": "pg_catalog", "t.typtype": "b"}, False),
        (
            {
                "t.typtype": "b",
                "t.typelem": 2,
                "t.typoutput": "pg_catalog.array_out",
                "t.typsubscript": "pg_catalog.array_subscript_handler",
            },
            False,
        ),
        ({"t.typtype": "d"}, True),
        ({"t.typtype": "c"}, True),
        ({"t.typtype": "r"}, True),
        ({"t.typtype": "m"}, True),
        ({"t.typtype": "b"}, True),
        ({"t.typoutput": "project.output"}, True),
        ({"f.pronamespace": "project"}, True),
        ({"f.pronamespace": None}, True),
        ({"t.oid": None}, True),
    ],
)
def test_actual_type_predicate_retains_enum_and_native_array_only(changes, rejected):
    row = {
        "t.oid": 1,
        "t.typnamespace": "project",
        "f.pronamespace": "pg_catalog",
        "t.typtype": "e",
        "t.typoutput": "pg_catalog.enum_out",
        "t.typelem": 0,
        "t.typsubscript": 0,
    }
    assert _metadata_predicate(_predicate(1), row | changes) is rejected


@pytest.mark.parametrize("allow_missing", [False, True])
def test_target_metadata_is_hex_bound_and_missing_policy_is_explicit(allow_missing):
    sql = row_conversion_preamble([("public", "Order Items")], allow_missing=allow_missing)
    assert_guard_before_values(sql)
    assert "Order Items" not in sql
    assert "jsonb_to_recordset" in sql
    assert f"c.oid IS NULL AND {'false' if allow_missing else 'true'}" in sql
    assert "c.relkind NOT IN ('r','p')" in sql


@pytest.mark.parametrize("timeout", [True, 0, 61, "5"])
def test_invalid_timeout_never_constructs_a_probe(timeout):
    with pytest.raises(ValueError, match="timeout"):
        row_conversion_preamble([], timeout_seconds=timeout)


def test_metadata_refusal_never_becomes_content_digest_or_empty_witness(monkeypatch):
    from types import SimpleNamespace
    from uuid import UUID

    from tests.test_restoration_adaptation_workspace import _inventory
    from yleum_orchestrator.services import restoration_adaptation_workspace as workspace
    from yleum_orchestrator.services import restoration_empty as empty
    from yleum_orchestrator.services.restoration_database import ControllerDatabaseError

    calls = []

    def refuse(_backend, sql, **_kwargs):
        assert_guard_before_values(sql)
        calls.append(sql)
        raise ControllerDatabaseError(sqlstate="0A000", terminated=False)

    monkeypatch.setattr(workspace, "admin_sql", refuse)
    monkeypatch.setattr(empty, "admin_sql", refuse)
    with pytest.raises(ControllerDatabaseError):
        workspace.content_inventory_partition_digests(object(), _inventory())
    assert (
        empty.observe_empty_database(
            SimpleNamespace(),
            operation_id=UUID(int=1),
            workspace_id=UUID(int=2),
            project_id=UUID(int=3),
            database_identity_digest="a" * 64,
            observation_kind="source",
        )
        is None
    )
    assert len(calls) == 2  # No fallback query and no second template observation.

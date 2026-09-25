"""Presence is evidence from rows, never a license to bypass compatibility."""

import json

import pytest

from yleum_orchestrator.services import restoration_catalog as catalog
from yleum_orchestrator.services.code_restoration_engine import preparation_report


async def test_async_database_fixture_never_resets_mismatched_database(monkeypatch):
    from tests import test_restoration_database as fixture_module

    class Admin:
        def __init__(self) -> None:
            self.executed: list[str] = []
            self.closed = False

        async def fetchval(self, _query: str) -> str:
            return "some_other_database"

        async def execute(self, query: str) -> None:
            self.executed.append(query)

        async def close(self) -> None:
            self.closed = True

    admin = Admin()

    async def connect(*_args, **_kwargs):
        return admin

    monkeypatch.setenv(
        "RESTORATION_TEST_DATABASE_URL",
        "postgresql://postgres:test@127.0.0.1/restoration_policy_test",
    )
    monkeypatch.setattr(fixture_module.asyncpg, "connect", connect)
    fixture_body = fixture_module.database.__wrapped__()

    with pytest.raises(pytest.fail.Exception, match="connected database"):
        await fixture_body.__anext__()

    assert admin.executed == []
    assert admin.closed is True


def test_disposable_pg_fixture_never_resets_mismatched_database(monkeypatch):
    from tests import _versioning_pg

    statements: list[str] = []

    def run(_database, sql: str) -> bytes:
        statements.append(sql)
        return b"some_other_database\n"

    monkeypatch.setenv(
        "RESTORATION_TEST_DATABASE_URL",
        "postgresql://postgres:test@127.0.0.1/restoration_policy_test",
    )
    monkeypatch.setattr(_versioning_pg.shutil, "which", lambda _name: "psql")
    monkeypatch.setattr(_versioning_pg.Pg, "run", run)
    fixture_body = _versioning_pg.pg.__wrapped__()

    with pytest.raises(pytest.fail.Exception, match="connected database"):
        next(fixture_body)

    assert statements == ["SELECT current_database();"]


def test_disposable_pg_fixture_resets_before_and_after(monkeypatch):
    from tests import _versioning_pg

    dsn = "postgresql://postgres:test@127.0.0.1/restoration_policy_test"
    statements: list[str] = []

    def run(_database, sql: str) -> bytes:
        statements.append(sql)
        if sql == "SELECT current_database();":
            return b"restoration_policy_test\n"
        return b""

    monkeypatch.setenv("RESTORATION_TEST_DATABASE_URL", dsn)
    monkeypatch.setattr(_versioning_pg.shutil, "which", lambda _name: "psql")
    monkeypatch.setattr(_versioning_pg.Pg, "run", run)
    fixture_body = _versioning_pg.pg.__wrapped__()

    database = next(fixture_body)
    assert database.dsn == dsn
    assert statements[-1] == _versioning_pg.RESET_DISPOSABLE_DATABASE_SQL
    with pytest.raises(StopIteration):
        next(fixture_body)
    assert statements.count(_versioning_pg.RESET_DISPOSABLE_DATABASE_SQL) == 2


@pytest.mark.parametrize("observed, expected", [(b"t\n", "present"), (b"f\n", "empty")])
def test_presence_checks_all_tables_without_returning_customer_values(
    monkeypatch, observed, expected
):
    statements = []

    def sql(_backend, query, **_kwargs):
        statements.append(query)
        if query == catalog.DATABASE_INVENTORY_SQL:
            return json.dumps({"unsupported": False, "tables": ["contacts", "omnia_orders"]})
        return observed

    monkeypatch.setattr(catalog, "admin_sql", sql)
    assert catalog.database_state(object()) == expected
    assert statements[1] == (
        'SELECT EXISTS(SELECT 1 FROM public."contacts") OR '
        'EXISTS(SELECT 1 FROM public."omnia_orders");'
    )


@pytest.mark.parametrize(
    "inventory",
    [
        {},
        {"unsupported": True, "tables": []},
        {"unsupported": False, "tables": ""},
        {"unsupported": False, "tables": ["contacts", "contacts"]},
        {"unsupported": False, "tables": ['unsafe";SELECT secret']},
        [],
    ],
)
def test_unclassified_or_malformed_inventory_never_claims_empty(monkeypatch, inventory):
    monkeypatch.setattr(catalog, "admin_sql", lambda *_args, **_kwargs: json.dumps(inventory))
    assert catalog.database_state(object()) == "unknown"


@pytest.mark.parametrize("failure", [b"", b"f\nt\n", b"false", RuntimeError("private row details")])
def test_failed_or_ambiguous_probe_is_unknown_without_exposing_values(monkeypatch, failure):
    def sql(_backend, query, **_kwargs):
        if query == catalog.DATABASE_INVENTORY_SQL:
            return b'{"unsupported":false,"tables":[]}'
        if isinstance(failure, Exception):
            raise failure
        return failure

    monkeypatch.setattr(catalog, "admin_sql", sql)
    assert catalog.database_state(object()) == "unknown"


def test_ready_report_warns_about_cascading_deletes_without_claiming_restrictions():
    report = preparation_report(
        retained=["contacts.surname"],
        cascading_deletes=["contacts"],
        observed_database_state="present",
    )
    assert report["mode"] == "exact"
    assert report["database_state"] == "present"
    assert report["unavailable_features"] == []
    assert "contacts" in " ".join(report["warnings"])
    assert "contacts.surname" in " ".join(report["retained_data"])
    assert "ограниченные права" not in json.dumps(report, ensure_ascii=False)


@pytest.mark.parametrize("state", ["empty", "present", "unknown"])
def test_presence_does_not_clear_incompatible_report(state):
    report = preparation_report(
        blockers=["missing_table:contacts"],
        observed_database_state=state,
    )
    assert report["blockers"] == ["missing_table:contacts"]
    assert report["mode"] == "adapted"
    assert report["database_state"] == state


def test_early_unclassified_files_report_never_claims_no_database_rows():
    assert preparation_report(blockers=["unclassified files"])["database_state"] == "unknown"

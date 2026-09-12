"""Presence is evidence from rows, never a license to bypass compatibility."""

import json

import pytest

from omnia_orchestrator.services import restoration_catalog as catalog
from omnia_orchestrator.services.code_restoration_engine import preparation_report


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


def test_ready_report_keeps_policy_restrictions_visible_without_claiming_ai_adaptation():
    report = preparation_report(
        retained=["contacts.surname"],
        blocked_deletes=["contacts"],
        observed_database_state="present",
    )
    assert report["mode"] == "exact"
    assert report["database_state"] == "present"
    assert "contacts" in " ".join(report["unavailable_features"])
    assert "contacts.surname" in " ".join(report["retained_data"])


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

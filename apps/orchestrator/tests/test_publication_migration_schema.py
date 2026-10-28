"""Mismatch evidence diagnoses exact rejection without retaining SQL or relaxing proof."""

import json
from types import SimpleNamespace

import pytest

from yleum_orchestrator.services.project_database_schema_proof import legacy_schema_digest
from yleum_orchestrator.services.publication_migration_pg import MigrationIOError, schema_command
from yleum_orchestrator.services.publication_migration_schema import (
    SchemaMismatchError,
    read_schema_dump,
    require_schema_dump,
    schema_failure_evidence,
)

CANARY = "private_identifier_secret_url_canary"


@pytest.mark.parametrize("stage", [
    "backup_restore", "trial_migration", "live_recheck", "source_recheck",
])
def test_same_legacy_schema_accepts_without_new_normalization(stage):
    expected = "-- old comment\n\\restrict old\n\nCREATE TABLE example (id integer);\n"
    observed = "-- new comment\n\\restrict new\n\nCREATE TABLE example (id integer);\n"
    assert legacy_schema_digest(observed) == legacy_schema_digest(expected)
    require_schema_dump(observed, expected, stage=stage)


@pytest.mark.parametrize("observed,expected,kind", [
    ("\nSET x=1;", "SET x=1;", "empty"),
    ("CREATE TABLE x(id text);", "CREATE TABLE x(id integer);", "sql_create"),
    ("SELECT 'a\n\nb';", "SELECT 'a\nb';", "empty"),
    ("ALTER TABLE x ADD y text;", "ALTER TABLE x ADD z text;", "sql_alter"),
    ("\\connect other", "\\connect postgres", "psql_connect"),
    ("\\echo other", "\\echo old", "psql_control"),
    ("SET x=1;", "SET x=2;", "sql_set"),
])
def test_formatting_and_literal_changes_still_reject(observed, expected, kind):
    with pytest.raises(SchemaMismatchError) as caught:
        require_schema_dump(observed, expected, stage="backup_restore")
    error = caught.value
    evidence = schema_failure_evidence(error)
    assert error.args == ("migration_full_schema_mismatch",)
    assert evidence["stage"] == "backup_restore"
    assert evidence["observed_sha256"] == legacy_schema_digest(observed)
    assert evidence["expected_sha256"] == legacy_schema_digest(expected)
    assert evidence["first_difference"]["observed_kind"] == kind


def test_diagnostic_contains_only_hashes_counts_and_fixed_kinds():
    with pytest.raises(SchemaMismatchError) as caught:
        require_schema_dump("\n\nSELECT '" + CANARY + "';\n\n", "SET x=1;", stage="live_recheck")
    error = caught.value
    evidence = schema_failure_evidence(error)
    assert evidence["observed"] == {
        "line_count": 4, "leading_blank_lines": 2, "trailing_blank_lines": 1,
    }
    assert evidence["expected"] == {
        "line_count": 1, "leading_blank_lines": 0, "trailing_blank_lines": 0,
    }
    assert evidence["first_difference"] == {
        "line": 1, "observed_kind": "empty", "expected_kind": "sql_set",
    }
    assert CANARY not in json.dumps(vars(error)) + repr(error.args)
    # A caller cannot mutate the exception's stored evidence through the copied projection.
    evidence["observed"]["line_count"] = 99
    assert schema_failure_evidence(error)["observed"]["line_count"] == 4


@pytest.mark.parametrize("observed,expected,nonblank,statements", [
    ("\nSELECT 1;\n\n", "SELECT 1;", True, True),
    ("SELECT 'a\n\nb';", "SELECT 'a\nb';", True, False),
    ("SELECT $body$a\n\nb$body$;", "SELECT $body$a\nb$body$;", True, False),
    ("CREATE ROLE a;\nCREATE ROLE b;", "CREATE ROLE b;\nCREATE ROLE a;", False, False),
    ("\\connect a\nSELECT 1;\n\\connect b\nSELECT 2;",
     "\\connect b\nSELECT 1;\n\\connect a\nSELECT 2;", False, False),
    ("SELECT 'private_unterminated_canary", "SELECT 1;", False, None),
    ("SELECT $body$private_unterminated_canary", "SELECT 1;", False, None),
    ("SELECT 1 /* comment */;", "SELECT 1  ;", False, True),
])
def test_optional_discriminators_never_authorize_and_preserve_literals(
    observed, expected, nonblank, statements,
):
    with pytest.raises(SchemaMismatchError) as caught:
        require_schema_dump(observed, expected, stage="backup_restore")
    evidence = schema_failure_evidence(caught.value)
    assert evidence["same_nonblank_lines"] is nonblank
    assert evidence["same_statement_stream"] is statements
    assert "private_unterminated_canary" not in json.dumps(evidence)


@pytest.mark.parametrize("field,value", [
    ("same_nonblank_lines", None), ("same_nonblank_lines", 1),
    ("same_nonblank_lines", CANARY), ("same_statement_stream", 0),
    ("same_statement_stream", CANARY),
])
def test_discriminator_types_are_exact(field, value):
    with pytest.raises(SchemaMismatchError) as caught:
        require_schema_dump("SELECT 1;", "SELECT 2;", stage="source_recheck")
    caught.value.evidence[field] = value
    assert schema_failure_evidence(caught.value) is None


@pytest.mark.parametrize("damage", ["stage", "hash", "count_bool", "count_large", "extra",
                                    "kind", "args", "missing", "foreign_type"])
def test_invalid_attributes_are_not_copied(damage):
    with pytest.raises(SchemaMismatchError) as caught:
        require_schema_dump("SELECT 1;", "SELECT 2;", stage="trial_migration")
    error = caught.value
    if damage == "stage":
        error.evidence["stage"] = CANARY
    elif damage == "hash":
        error.evidence["observed_sha256"] = CANARY
    elif damage.startswith("count"):
        error.evidence["observed"]["line_count"] = True if damage == "count_bool" else 10**20
    elif damage == "extra":
        error.evidence["sql"] = CANARY
    elif damage == "kind":
        error.evidence["first_difference"]["observed_kind"] = CANARY
    elif damage == "args":
        error.args = (CANARY,)
    elif damage == "missing":
        del error.evidence
    else:
        error = type("SchemaMismatchError", (ValueError,), {})(CANARY)
    assert schema_failure_evidence(error) is None


def test_invalid_stage_never_accepts_even_equal_schema():
    with pytest.raises(MigrationIOError, match="migration_schema_diagnostic_invalid"):
        require_schema_dump("SELECT 1;", "SELECT 1;", stage=CANARY)


def test_read_uses_one_unchanged_schema_command_and_no_secondary_observation():
    calls = []

    def command(argv):
        calls.append(argv)
        return b"SELECT 1;\n"

    assert read_schema_dump(SimpleNamespace(command=command)) == "SELECT 1;\n"
    assert calls == [schema_command()]

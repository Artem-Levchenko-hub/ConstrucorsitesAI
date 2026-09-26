"""Opt-in real PostgreSQL tests: only an explicitly named disposable container."""

from __future__ import annotations

import json
import os
import re
import subprocess
import time
from uuid import uuid4

import pytest

from yleum_orchestrator.services.project_migrations import migration_sql
from yleum_orchestrator.services.restoration_empty import EMPTY_WITNESS_SQL


def postgres_cases():
    migration = {
        "drizzle/0002_notes.sql": (
            "CREATE TABLE qa_notes(id int PRIMARY KEY, body text); "
            "CREATE SCHEMA own; CREATE TABLE own.items(id int); "
            'CREATE TABLE "public".explicit_public(id int);'
        )
    }
    apply = migration_sql(migration, verify_only=False)
    check = migration_sql(migration, verify_only=False, verify_applied=True)
    b2 = {**migration, "drizzle/0003_detail.sql": "ALTER TABLE qa_notes ADD COLUMN detail text;"}
    return [
        (
            "materialize_B",
            apply,
            True,
            "SELECT count(*) FROM pg_tables WHERE schemaname='public' AND tablename='qa_notes'",
            "1",
        ),
        (
            "preserve_row",
            "INSERT INTO qa_notes VALUES(1,'kept');",
            True,
            "SELECT count(*) FROM qa_notes",
            "1",
        ),
        ("replay", apply, True, "SELECT count(*) FROM qa_notes", "1"),
        (
            "verify_receipt",
            check,
            True,
            "SELECT count(*) FROM public.__omnia_project_migrations",
            "1",
        ),
        (
            "explicit_public",
            "SELECT 1;",
            True,
            "SELECT count(*) FROM pg_tables WHERE schemaname='public' "
            "AND tablename='explicit_public'",
            "1",
        ),
        (
            "named_schema",
            "SELECT 1;",
            True,
            "SELECT count(*) FROM pg_tables WHERE schemaname='own' AND tablename='items'",
            "1",
        ),
        (
            "no_legacy_core",
            "SELECT 1;",
            True,
            "SELECT count(*) FROM pg_tables WHERE tablename IN ('max_users','max_actions')",
            "0",
        ),
        (
            "edited_file",
            migration_sql({"drizzle/0002_notes.sql": "SELECT 1;"}, verify_only=False),
            False,
            "SELECT count(*) FROM qa_notes",
            "1",
        ),
        (
            "sql_error_atomic",
            migration_sql(
                {
                    "drizzle/0003.sql": "CREATE TABLE should_rollback(id int); "
                    "SELECT missing_function();"
                },
                verify_only=False,
            ),
            False,
            "SELECT to_regclass('public.should_rollback') IS NULL",
            "t",
        ),
        (
            "transaction_escape",
            migration_sql(
                {"drizzle/0003.sql": "CREATE TABLE should_rollback(id int); COMMIT;"},
                verify_only=False,
            ),
            False,
            "SELECT to_regclass('public.should_rollback') IS NULL",
            "t",
        ),
        (
            "adaptation_no_replay",
            migration_sql({"drizzle/historical.sql": "DROP TABLE qa_notes;"}, verify_only=True),
            True,
            "SELECT count(*) FROM qa_notes",
            "1",
        ),
        (
            "manually_applied_no_journal",
            migration_sql(
                {"drizzle/manual.sql": "CREATE TABLE qa_notes(id int);"}, verify_only=False
            ),
            False,
            "SELECT count(*) FROM public.__omnia_project_migrations",
            "1",
        ),
        (
            "timeout_cannot_commit_later",
            migration_sql(
                {
                    "drizzle/slow.sql": "SET statement_timeout=0; "
                    "CREATE TABLE late_commit(id int); SELECT pg_sleep(2);"
                },
                verify_only=False,
            ),
            False,
            "SELECT to_regclass('public.late_commit') IS NULL",
            "t",
        ),
        ("empty_business", "DELETE FROM qa_notes;", True, "SELECT count(*) FROM qa_notes", "0"),
        (
            "R0_journal_attestation",
            EMPTY_WITNESS_SQL,
            True,
            "SELECT count(*) FROM public.__omnia_project_migrations",
            "1",
        ),
        (
            "B2_before_R0",
            migration_sql(b2, verify_only=False),
            True,
            "SELECT count(*) FROM information_schema.columns WHERE table_name='qa_notes' "
            "AND column_name='detail'",
            "1",
        ),
        (
            "R0_actual_B1_SQL",
            "DROP SCHEMA public CASCADE; CREATE SCHEMA public; "
            "DROP SCHEMA own CASCADE; " + next(iter(migration.values())),
            True,
            "SELECT count(*) FROM information_schema.columns WHERE table_name='qa_notes' "
            "AND column_name='detail'",
            "0",
        ),
        (
            "R0_records_only_witnessed_B1",
            migration_sql(migration, verify_only=False, record_witnessed=True),
            True,
            "SELECT count(*) FROM public.__omnia_project_migrations",
            "1",
        ),
        (
            "B2_after_R0",
            migration_sql(b2, verify_only=False),
            True,
            "SELECT count(*) FROM information_schema.columns WHERE table_name='qa_notes' "
            "AND column_name='detail'",
            "1",
        ),
        (
            "first_adoption_fixture",
            "INSERT INTO qa_notes(id,body) VALUES (1,'kept'); "
            "DROP TABLE public.__omnia_project_migrations;",
            True,
            "SELECT count(*) FROM qa_notes",
            "1",
        ),
        (
            "nonempty_untracked_TRUNCATE",
            migration_sql({"drizzle/0009.sql": "TRUNCATE qa_notes;"}, verify_only=False),
            False,
            "SELECT count(*)=1 AND to_regclass('public.__omnia_project_migrations') IS NULL "
            "FROM qa_notes",
            "t",
        ),
        (
            "nonempty_untracked_DROP",
            migration_sql(
                {
                    "drizzle/0009.sql": "DROP TABLE IF EXISTS qa_notes; "
                    "CREATE TABLE qa_notes(id int);"
                },
                verify_only=False,
            ),
            False,
            "SELECT count(*)=1 AND to_regclass('public.__omnia_project_migrations') IS NULL "
            "FROM qa_notes",
            "t",
        ),
    ]


def assert_receipt(name, output):
    if name in {"materialize_B", "replay", "verify_receipt", "adaptation_no_replay"}:
        receipt = json.loads(output.strip())
        assert receipt["contract"] == "project-migrations-v1"
        for key in ("source_digest", "database_identity", "catalog_digest"):
            assert re.fullmatch(r"[0-9a-f]{64}", receipt[key]), key
        assert receipt["mode"] == ("verify_only" if name == "adaptation_no_replay" else "apply")
    elif name == "R0_journal_attestation":
        payload = json.loads(output.strip())
        journal = next(r for r in payload["relations"] if r["name"] == "__omnia_project_migrations")
        assert journal["project_ledger_attestation"]["shape_attested"] is True
        assert journal["project_ledger_attestation"]["rows_valid"] is True
        assert all(r["row_count"] == 0 for r in payload["relations"] if r is not journal)


def test_project_migrations_real_postgres():
    container = os.environ.get("OMNIA_PROJECT_MIGRATIONS_TEST_CONTAINER", "")
    if not container:
        pytest.skip("requires an isolated PostgreSQL container; never use a project database")
    assert re.fullmatch(r"omnia-project-migrations-test-[a-z0-9-]+", container)
    database = "omnia_migration_test_" + uuid4().hex
    prefix = ["docker", "exec", "-i", container]
    psql = ["psql", "-X", "-qAt", "-v", "ON_ERROR_STOP=1", "-U", "postgres"]

    def query(sql, *, db=database, lifetime=50):
        return subprocess.run(
            [*prefix, "timeout", "-s", "KILL", str(lifetime), *psql, "-d", db],
            input=sql,
            text=True,
            capture_output=True,
            timeout=65,
            check=False,
        )

    assert query(f'CREATE DATABASE "{database}";', db="postgres").returncode == 0
    try:
        for name, sql, expected_ok, witness, expected in postgres_cases():
            deadline_case = name == "timeout_cannot_commit_later"
            result = query(sql, lifetime=1 if deadline_case else 50)
            assert (result.returncode == 0) == expected_ok, f"{name}: unexpected SQL outcome"
            if deadline_case:
                time.sleep(3)
            if expected_ok:
                assert_receipt(name, result.stdout)
            observed = query(witness)
            assert observed.returncode == 0 and observed.stdout.strip() == expected, name
    finally:
        assert query(f'DROP DATABASE "{database}";', db="postgres").returncode == 0

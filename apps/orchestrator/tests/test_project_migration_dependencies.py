import hashlib
import json

import pytest

from yleum_orchestrator.services.project_migration_dependencies import dependency_gap

CHILD = 'CREATE TABLE coffee(subject text REFERENCES "public"."max_users"(max_user_id));'


def test_missing_core_parent_on_empty_product_catalog():
    assert "public.max_users" in dependency_gap({"drizzle/0002.sql": CHILD}, set(), {})


@pytest.mark.parametrize(
    "relations,migrations",
    [
        ({("public", "max_users")}, {"drizzle/0002.sql": CHILD}),
        (
            set(),
            {"drizzle/0002.sql": "CREATE TABLE max_users(max_user_id text PRIMARY KEY);" + CHILD},
        ),
        (
            set(),
            {
                "drizzle/0002.sql": "CREATE TABLE max_users(max_user_id text PRIMARY KEY);",
                "drizzle/0003.sql": CHILD,
            },
        ),
        (
            set(),
            {
                "drizzle/0002.sql": (
                    "CREATE TABLE parent(id int PRIMARY KEY, p int REFERENCES parent(id));"
                )
            },
        ),
    ],
)
def test_existing_ordered_or_self_parent_is_valid(relations, migrations):
    assert dependency_gap(migrations, relations, {}) is None


def test_later_migration_parent_does_not_satisfy_earlier_fk():
    assert dependency_gap(
        {
            "drizzle/0002.sql": CHILD,
            "drizzle/0003.sql": "CREATE TABLE max_users(max_user_id text);",
        },
        set(),
        {},
    )


def test_applied_sql_is_not_replayed_or_used_as_current_catalog():
    applied = {"drizzle/0002.sql": hashlib.sha256(CHILD.encode()).hexdigest()}
    assert dependency_gap({"drizzle/0002.sql": CHILD}, set(), applied) is None
    assert dependency_gap({"drizzle/0002.sql": CHILD + "--changed"}, set(), applied) is None


@pytest.mark.parametrize(
    "prefix",
    [
        "DO $$ BEGIN EXECUTE 'CREATE TABLE max_users(max_user_id text)'; END $$;",
        "SET search_path=custom;",
        "SELECT custom.install_schema();",
    ],
)
def test_dynamic_schema_is_deferred_to_postgres_not_guessed(prefix):
    assert dependency_gap({"drizzle/0002.sql": prefix + CHILD}, set(), {}) is None


def test_comments_strings_and_quoted_identifiers_are_parsed_as_sql():
    source = """-- REFERENCES missing(x)
    CREATE TABLE "Parent"("Key" text PRIMARY KEY);
    CREATE TABLE child(id text REFERENCES "Parent"("Key"),
        note text DEFAULT 'REFERENCES absent(x)');"""
    assert dependency_gap({"drizzle/0002.sql": source}, set(), {}) is None


def test_readonly_catalog_is_used_without_executing_project_sql(monkeypatch):
    from yleum_orchestrator.services import project_migration_dependencies as module

    statements = []

    def query(backend, sql, **kwargs):
        statements.append(sql)
        return json.dumps({"relations": [], "applied": {}}).encode()

    monkeypatch.setattr(module, "admin_sql", query)
    assert module.check_migration_dependencies(object(), {"drizzle/0002.sql": CHILD})
    assert len(statements) == 1
    assert "BEGIN READ ONLY" in statements[0]
    assert CHILD not in statements[0]

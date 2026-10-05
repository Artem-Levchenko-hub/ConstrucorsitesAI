from dataclasses import replace
from uuid import UUID

import pytest

from yleum_orchestrator.services.publication_additive_plan import (
    AdditivePlanError,
    MigrationBinding,
    authorize_plan,
    plan_additive,
)


def binding():
    return MigrationBinding(
        owner_id=UUID(int=1),
        project_id=UUID(int=2),
        snapshot_id=UUID(int=3),
        source_revision="a" * 40,
        build_sha256="b" * 64,
        pvc_uid=UUID(int=4),
        epoch=7,
        live_schema_sha256="c" * 64,
        target_schema_sha256="d" * 64,
        data_contract_sha256="e" * 64,
        backup_sha256="f" * 64,
    )


def plan():
    return plan_additive(
        binding(),
        "ALTER TABLE public.qa_orders ADD COLUMN note text; "
        "CREATE TABLE public.qa_logs (id uuid PRIMARY KEY, note text)",
        {"qa_orders": ("id", "actor_id")},
    )


def test_valid_additive_plan_preserves_known_columns_and_has_stable_binding():
    p = plan()
    assert len(p.statements) == 2
    assert p.sha256 == plan().sha256
    assert p.summary() == {
        "kind": "additive_preparation_only",
        "statement_count": 2,
        "plan_sha256": p.sha256,
        "execution_authorized": False,
    }
    assert (
        authorize_plan(
            p,
            current=binding(),
            owner_id=UUID(int=1),
            project_id=UUID(int=2),
            snapshot_id=UUID(int=3),
            acknowledged_plan_sha256=p.sha256,
            explicit_migration_intent=True,
        )
        == p.statements
    )


@pytest.mark.parametrize(
    "sql",
    [
        "DROP TABLE qa_orders",
        "DELETE FROM qa_orders",
        "TRUNCATE qa_orders",
        "ALTER TABLE qa_orders DROP COLUMN actor_id",
        "ALTER TABLE qa_orders ALTER COLUMN id TYPE text",
        "ALTER TABLE qa_orders ADD COLUMN amount int NOT NULL",
        "ALTER TABLE qa_orders ADD COLUMN amount int DEFAULT 0",
        "ALTER TABLE qa_orders ADD COLUMN amount int DEFAULT evil()",
        "ALTER TABLE foreign_project.qa_orders ADD COLUMN x text",
        "CREATE TABLE public.qa_orders (id uuid)",
        "CREATE TABLE qa_new AS SELECT * FROM qa_orders",
        "CREATE TABLE qa_new (id serial)",
        "CREATE TABLE qa_new (x foreign_project.secret)",
        "CREATE TABLE qa_new (id uuid CHECK (evil()))",
        "CREATE TABLE qa_new (id uuid REFERENCES qa_orders)",
        "CREATE TABLE qa_new (id uuid) INHERITS (qa_orders)",
        "CREATE TEMP TABLE qa_new (id uuid)",
        "CREATE TABLE IF NOT EXISTS qa_new (id uuid)",
        "ALTER TABLE qa_orders ADD COLUMN IF NOT EXISTS note text",
        "BEGIN; ALTER TABLE qa_orders ADD COLUMN note text; COMMIT",
        "DO $$ BEGIN DELETE FROM qa_orders; END $$",
        "CREATE FUNCTION evil() RETURNS int AS $$ SELECT 1 $$ LANGUAGE sql",
        "GRANT ALL ON qa_orders TO public",
        "ALTER TABLE qa_orders ADD COLUMN id text",
        "ALTER TABLE absent ADD COLUMN note text",
        "ALTER TABLE qa_orders ADD COLUMN note text; DELETE FROM qa_orders",
        "CREATE TABLE qa_new (id uuid, id text)",
        "ALTER TABLE qa_orders ADD COLUMN note text; ALTER TABLE qa_orders ADD COLUMN note text",
        "ALTER TABLE qa_orders ADD COLUMN note text COLLATE public.evil",
        "ALTER TABLE ONLY qa_orders ADD COLUMN note text",
    ],
)
def test_unsupported_or_destructive_sql_is_rejected_before_any_execution(sql):
    with pytest.raises(AdditivePlanError):
        plan_additive(binding(), sql, {"qa_orders": ("id", "actor_id")})


@pytest.mark.parametrize(
    "field,value",
    [
        ("owner_id", UUID(int=8)),
        ("project_id", UUID(int=8)),
        ("snapshot_id", UUID(int=8)),
        ("source_revision", "1" * 40),
        ("build_sha256", "1" * 64),
        ("pvc_uid", UUID(int=8)),
        ("epoch", 8),
        ("live_schema_sha256", "1" * 64),
        ("target_schema_sha256", "1" * 64),
        ("backup_sha256", "1" * 64),
        ("data_contract_sha256", "1" * 64),
    ],
)
def test_changed_identity_or_fence_or_backup_requires_new_review(field, value):
    p = plan()
    with pytest.raises(AdditivePlanError):
        authorize_plan(
            p,
            current=replace(binding(), **{field: value}),
            owner_id=UUID(int=1),
            project_id=UUID(int=2),
            snapshot_id=UUID(int=3),
            acknowledged_plan_sha256=p.sha256,
            explicit_migration_intent=True,
        )


@pytest.mark.parametrize(
    "kwargs",
    [
        {"explicit_migration_intent": False},
        {"explicit_migration_intent": 1},
        {"acknowledged_plan_sha256": "0" * 64},
        {"owner_id": UUID(int=9)},
        {"project_id": UUID(int=9)},
        {"snapshot_id": UUID(int=9)},
    ],
)
def test_confirmation_never_substitutes_for_owner_and_exact_plan(kwargs):
    p = plan()
    arguments = dict(
        current=binding(),
        owner_id=UUID(int=1),
        project_id=UUID(int=2),
        snapshot_id=UUID(int=3),
        acknowledged_plan_sha256=p.sha256,
        explicit_migration_intent=True,
    )
    arguments.update(kwargs)
    with pytest.raises(AdditivePlanError):
        authorize_plan(p, **arguments)


def test_sql_comment_disguised_destructive_statement_and_error_never_echoes_sql():
    private = (
        "ALTER TABLE qa_orders ADD COLUMN x text; /* harmless */ "
        "DELETE FROM qa_orders WHERE id='PRIVATE'"
    )
    with pytest.raises(AdditivePlanError) as e:
        plan_additive(binding(), private, {"qa_orders": ("id",)})
    assert "PRIVATE" not in str(e.value)


def test_comments_cannot_change_builtin_type_binding_and_sql_is_not_executed():
    p = plan_additive(
        binding(),
        "ALTER TABLE public.qa_orders ADD COLUMN /* harmless */ note varchar(200)",
        {"qa_orders": ("id",)},
    )
    assert p.statements == ("ALTER TABLE public.qa_orders ADD COLUMN note pg_catalog.varchar(200)",)


@pytest.mark.parametrize("sql", [" ", "x" * 65537, "CREATE TABLE x (id uuid);" * 65])
def test_empty_and_overbudget_fail_closed(sql):
    with pytest.raises(AdditivePlanError):
        plan_additive(binding(), sql, {"qa_orders": ("id",)})


def test_forged_destructive_plan_cannot_be_authorized_even_with_its_own_hash():
    p = replace(plan(), statements=("DELETE FROM public.qa_orders",))
    with pytest.raises(AdditivePlanError):
        authorize_plan(
            p,
            current=binding(),
            owner_id=UUID(int=1),
            project_id=UUID(int=2),
            snapshot_id=UUID(int=3),
            acknowledged_plan_sha256=p.sha256,
            explicit_migration_intent=True,
        )

from dataclasses import replace
from uuid import UUID

import pytest

from yleum_orchestrator.services.publication_additive_plan import (
    AdditivePlanError,
    MigrationBinding,
    authorize_plan,
)
from yleum_orchestrator.services.publication_additive_resolver import (
    CatalogColumn,
    CatalogReceipt,
    CatalogTable,
    binding_digest,
    catalog_digest,
    resolve_additive,
)


def binding():
    return MigrationBinding(
        UUID(int=1),
        UUID(int=2),
        UUID(int=3),
        "a" * 40,
        "b" * 64,
        UUID(int=4),
        7,
        "c" * 64,
        "d" * 64,
        "e" * 64,
        "f" * 64,
    )


def tables():
    return (
        CatalogTable(
            "public",
            "qa_orders",
            (
                CatalogColumn("id", "pg_catalog.int4", False, -1),
                CatalogColumn("actor_id", "pg_catalog.text", True, -1),
            ),
            "1" * 64,
            "2" * 64,
            "3" * 64,
            "4" * 64,
            "p",
            True,
            False,
            False,
        ),
    )


def receipts():
    b = binding()
    before = tables()
    after = (
        replace(
            before[0],
            columns=(*before[0].columns, CatalogColumn("priority", "pg_catalog.text", True, -1)),
        ),
    )

    def receipt(kind, values, digest):
        return CatalogReceipt(
            kind,
            binding_digest(b),
            digest,
            b.data_contract_sha256,
            catalog_digest(values),
            values,
            True,
        )

    return (
        b,
        receipt("published", before, b.live_schema_sha256),
        receipt("source", after, b.target_schema_sha256),
    )


def test_exact_catalog_nullable_addition_resolves_stable_plan_without_authority():
    b, live, source = receipts()
    p = resolve_additive(b, live, source, backup_verified=True)
    assert p.statements == ("ALTER TABLE public.qa_orders ADD COLUMN priority pg_catalog.text",)
    assert p.sha256 == resolve_additive(b, live, source, backup_verified=True).sha256
    assert p.summary()["execution_authorized"] is False
    assert (
        authorize_plan(
            p,
            current=b,
            owner_id=b.owner_id,
            project_id=b.project_id,
            snapshot_id=b.snapshot_id,
            acknowledged_plan_sha256=p.sha256,
            explicit_migration_intent=True,
        )
        == p.statements
    )


@pytest.mark.parametrize(
    "change",
    [
        {"owner_id": UUID(int=10)},
        {"project_id": UUID(int=10)},
        {"snapshot_id": UUID(int=10)},
        {"source_revision": "9" * 40},
        {"build_sha256": "9" * 64},
        {"pvc_uid": UUID(int=10)},
        {"epoch": 8},
        {"backup_sha256": "9" * 64},
        {"live_schema_sha256": "9" * 64},
        {"target_schema_sha256": "9" * 64},
        {"data_contract_sha256": "9" * 64},
    ],
)
def test_any_identity_receipt_change_denies_before_sql(change):
    b, live, source = receipts()
    with pytest.raises(AdditivePlanError):
        resolve_additive(replace(b, **change), live, source, backup_verified=True)


@pytest.mark.parametrize(
    "which,change",
    [
        ("live", {"kind": "source"}),
        ("source", {"kind": "published"}),
        ("live", {"catalog_sha256": "9" * 64}),
        ("source", {"schema_sha256": "9" * 64}),
        ("live", {"data_contract_sha256": "9" * 64}),
        ("live", {"event_triggers_absent": False}),
        ("source", {"event_triggers_absent": 1}),
    ],
)
def test_unbound_or_tampered_catalog_is_not_a_migration_authority(which, change):
    b, live, source = receipts()
    if which == "live":
        live = replace(live, **change)
    else:
        source = replace(source, **change)
    with pytest.raises(AdditivePlanError):
        resolve_additive(b, live, source, backup_verified=True)


@pytest.mark.parametrize("verified", [False, None, 1])
def test_missing_or_nonboolean_verified_backup_denies(verified):
    b, live, source = receipts()
    with pytest.raises(AdditivePlanError):
        resolve_additive(b, live, source, backup_verified=verified)


@pytest.mark.parametrize(
    "change",
    [
        {"sql_type": "pg_catalog.int8"},
        {"nullable": True},
        {"default_sha256": "3" * 64},
        {"generated": True},
        {"identity": True},
        {"collation_sha256": "3" * 64},
    ],
)
def test_existing_column_semantics_cannot_change_under_additive_label(change):
    b, live, source = receipts()
    table = source.tables[0]
    table = replace(table, columns=(replace(table.columns[0], **change), *table.columns[1:]))
    source = replace(source, tables=(table,), catalog_sha256=catalog_digest((table,)))
    with pytest.raises(AdditivePlanError):
        resolve_additive(b, live, source, backup_verified=True)


@pytest.mark.parametrize(
    "change",
    [
        {"nullable": False},
        {"default_sha256": "3" * 64},
        {"generated": True},
        {"identity": True},
        {"collation_sha256": "3" * 64},
        {"sql_type": "evil_domain"},
    ],
)
def test_added_column_effects_fail_closed(change):
    b, live, source = receipts()
    table = source.tables[0]
    table = replace(table, columns=(*table.columns[:-1], replace(table.columns[-1], **change)))
    with pytest.raises(AdditivePlanError):
        source = replace(source, tables=(table,), catalog_sha256=catalog_digest((table,)))
        resolve_additive(b, live, source, backup_verified=True)


@pytest.mark.parametrize(
    "change",
    [
        {"schema": "foreign"},
        {"constraints_sha256": "3" * 64},
        {"indexes_sha256": "9" * 64},
        {"policies_sha256": "9" * 64},
        {"triggers_sha256": "9" * 64},
        {"persistence": "u"},
        {"plain_heap": False},
        {"rls_enabled": True},
        {"rls_forced": True},
    ],
)
def test_namespace_relation_security_or_constraints_changes_are_not_additive(change):
    b, live, source = receipts()
    table = replace(source.tables[0], **change)
    with pytest.raises(AdditivePlanError):
        source = replace(source, tables=(table,), catalog_sha256=catalog_digest((table,)))
        resolve_additive(b, live, source, backup_verified=True)


def test_removed_reordered_duplicate_tables_and_columns_deny():
    b, live, source = receipts()
    bad = [
        (),
        source.tables * 2,
        (replace(source.tables[0], columns=source.tables[0].columns[1:]),),
        (replace(source.tables[0], columns=tuple(reversed(source.tables[0].columns))),),
        (replace(source.tables[0], columns=source.tables[0].columns * 2),),
    ]
    for values in bad:
        with pytest.raises(AdditivePlanError):
            changed = replace(source, tables=values, catalog_sha256=catalog_digest(values))
            resolve_additive(b, live, changed, backup_verified=True)


@pytest.mark.parametrize("modifier", [True, False, None, "-1", 0, 24, 524294])
def test_typmod_is_required_strict_unmodified_integer(modifier):
    table = tables()[0]
    column = replace(table.columns[0], atttypmod=modifier)
    with pytest.raises(AdditivePlanError, match="migration_catalog_typmod_unsupported"):
        catalog_digest((replace(table, columns=(column, *table.columns[1:])),))


def test_missing_typmod_cannot_construct_trusted_catalog_column():
    with pytest.raises(TypeError):
        CatalogColumn("label", "pg_catalog.varchar", True)

"""Only an explicit nonce-owned loopback database; no production fallback."""

import hashlib
import json
import os
from dataclasses import replace
from urllib.parse import urlsplit

import asyncpg
import pytest

from yleum_orchestrator.services.publication_additive_plan import AdditivePlanError, authorize_plan
from yleum_orchestrator.services.publication_additive_resolver import (
    CatalogColumn,
    CatalogReceipt,
    CatalogTable,
    binding_digest,
    catalog_digest,
    resolve_additive,
)

from .test_publication_additive_resolver import binding


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


async def catalog(conn):
    columns = await conn.fetch("""
      SELECT a.attname, t.typname, tn.nspname AS type_schema, NOT a.attnotnull AS nullable,
             a.atttypmod, pg_catalog.format_type(a.atttypid,a.atttypmod) AS formatted_type,
             pg_catalog.pg_get_expr(d.adbin,d.adrelid) AS default_expr,
             a.attgenerated <> '' AS generated, a.attidentity <> '' AS identity,
             CASE WHEN a.attcollation = t.typcollation THEN NULL
                  ELSE a.attcollation::text END AS custom_collation
      FROM pg_catalog.pg_attribute a
      JOIN pg_catalog.pg_type t ON t.oid=a.atttypid
      JOIN pg_catalog.pg_namespace tn ON tn.oid=t.typnamespace
      LEFT JOIN pg_catalog.pg_attrdef d ON d.adrelid=a.attrelid AND d.adnum=a.attnum
      WHERE a.attrelid='public.qa_orders'::regclass AND a.attnum>0 AND NOT a.attisdropped
      ORDER BY a.attnum
    """)
    values = tuple(
        CatalogColumn(
            r["attname"],
            r["type_schema"] + "." + r["typname"],
            r["nullable"],
            r["atttypmod"],
            digest(r["default_expr"]) if r["default_expr"] else None,
            r["generated"],
            r["identity"],
            digest(r["custom_collation"]) if r["custom_collation"] else None,
        )
        for r in columns
    )
    constraints = [
        r[0]
        for r in await conn.fetch(
            "SELECT pg_catalog.pg_get_constraintdef(oid) FROM pg_catalog.pg_constraint "
            "WHERE conrelid='public.qa_orders'::regclass ORDER BY conname"
        )
    ]
    indexes = [
        r[0]
        for r in await conn.fetch(
            "SELECT indexdef FROM pg_catalog.pg_indexes "
            "WHERE schemaname='public' AND tablename='qa_orders' ORDER BY indexname"
        )
    ]
    policies = [
        tuple(r)
        for r in await conn.fetch(
            "SELECT polname, polcmd, polpermissive, polroles::text FROM pg_catalog.pg_policy "
            "WHERE polrelid='public.qa_orders'::regclass ORDER BY polname"
        )
    ]
    triggers = [
        r[0]
        for r in await conn.fetch(
            "SELECT pg_catalog.pg_get_triggerdef(oid) FROM pg_catalog.pg_trigger "
            "WHERE tgrelid='public.qa_orders'::regclass AND NOT tgisinternal ORDER BY tgname"
        )
    ]
    relation = await conn.fetchrow(
        "SELECT relkind='r' AND NOT relispartition AND NOT EXISTS("
        "SELECT 1 FROM pg_catalog.pg_inherits WHERE inhrelid=c.oid) AS plain_heap, "
        "relrowsecurity, relforcerowsecurity FROM pg_catalog.pg_class c "
        "WHERE oid='public.qa_orders'::regclass"
    )
    return (
        CatalogTable(
            "public",
            "qa_orders",
            values,
            digest(constraints),
            digest(indexes),
            digest(policies),
            digest(triggers),
            "p",
            relation["plain_heap"],
            relation["relrowsecurity"],
            relation["relforcerowsecurity"],
        ),
    )


@pytest.mark.asyncio
async def test_real_pg_nullable_addition_preserves_nine_rows_and_blocks_stale_replay():
    dsn = os.environ["QA_ADDITIVE_DB_URL"]
    u = urlsplit(dsn)
    assert u.hostname in {"127.0.0.1", "localhost"} and u.port == 55432
    assert u.path.startswith("/qa_pub_additive_")
    conn = await asyncpg.connect(dsn, timeout=5, command_timeout=8)
    try:
        assert (await conn.fetchval("SELECT current_database()")).startswith("qa_pub_additive_")
        await conn.execute("CREATE TABLE public.qa_orders(id int PRIMARY KEY, actor_id text)")
        await conn.executemany(
            "INSERT INTO public.qa_orders VALUES($1,$2)",
            [(i, f"owned-actor-{i % 2}") for i in range(1, 10)],
        )
        before = [
            tuple(r)
            for r in await conn.fetch("SELECT id,actor_id FROM public.qa_orders ORDER BY id")
        ]
        live_tables = await catalog(conn)
        assert await conn.fetchval("SELECT count(*)=0 FROM pg_catalog.pg_event_trigger")
        b = binding()
        source_tables = (
            replace(
                live_tables[0],
                columns=(
                    *live_tables[0].columns,
                    CatalogColumn("priority", "pg_catalog.text", True, -1),
                ),
            ),
        )

        def receipt(kind, tables, schema):
            return CatalogReceipt(
                kind,
                binding_digest(b),
                schema,
                b.data_contract_sha256,
                catalog_digest(tables),
                tables,
                True,
            )

        live = receipt("published", live_tables, b.live_schema_sha256)
        source = receipt("source", source_tables, b.target_schema_sha256)
        plan = resolve_additive(b, live, source, backup_verified=True)
        statements = authorize_plan(
            plan,
            current=b,
            owner_id=b.owner_id,
            project_id=b.project_id,
            snapshot_id=b.snapshot_id,
            acknowledged_plan_sha256=plan.sha256,
            explicit_migration_intent=True,
        )
        # Test-only execution: no production executor or receipt is introduced.
        transaction = conn.transaction()
        await transaction.start()
        await conn.execute(statements[0])
        try:
            await conn.execute("SELECT 1/0")
        except asyncpg.DivisionByZeroError:
            await transaction.rollback()
        assert await catalog(conn) == live_tables
        assert [
            tuple(r)
            for r in await conn.fetch("SELECT id,actor_id FROM public.qa_orders ORDER BY id")
        ] == before
        async with conn.transaction():
            await conn.execute(statements[0])
        assert await catalog(conn) == source_tables
        after = await conn.fetch("SELECT id,actor_id,priority FROM public.qa_orders ORDER BY id")
        assert [(r["id"], r["actor_id"]) for r in after] == before and len(after) == 9
        assert all(r["priority"] is None for r in after)
        fresh_live = receipt("published", await catalog(conn), b.live_schema_sha256)
        with pytest.raises(AdditivePlanError, match="migration_diff_empty_or_overbudget"):
            resolve_additive(b, fresh_live, source, backup_verified=True)
    finally:
        await conn.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "case", ["existing_varchar", "existing_numeric", "new_varchar", "new_numeric"]
)
async def test_physical_type_modifiers_never_authorize_additive_plan(case):
    dsn = os.environ["QA_ADDITIVE_DB_URL"]
    u = urlsplit(dsn)
    assert u.hostname in {"127.0.0.1", "localhost"} and u.port == 55432
    assert u.path.startswith("/qa_pub_additive_")
    conn = await asyncpg.connect(dsn, timeout=5, command_timeout=8)
    tx = conn.transaction()
    await tx.start()
    try:
        await conn.execute("DROP TABLE IF EXISTS public.qa_orders")
        original = {"existing_varchar": "varchar(20)", "existing_numeric": "numeric(8,2)"}.get(
            case, "text"
        )
        await conn.execute(
            "CREATE TABLE public.qa_orders(id int PRIMARY KEY, label " + original + ")"
        )
        await conn.execute("INSERT INTO public.qa_orders VALUES(1,NULL)")
        before = await catalog(conn)
        if case == "existing_varchar":
            await conn.execute("ALTER TABLE public.qa_orders ALTER COLUMN label TYPE varchar(40)")
        elif case == "existing_numeric":
            await conn.execute("ALTER TABLE public.qa_orders ALTER COLUMN label TYPE numeric(12,4)")
        addition = {"new_varchar": "varchar(20)", "new_numeric": "numeric(8,2)"}.get(case, "text")
        await conn.execute("ALTER TABLE public.qa_orders ADD COLUMN priority " + addition)
        raw = await conn.fetch(
            "SELECT atttypmod,pg_catalog.format_type(atttypid,atttypmod) AS formatted "
            "FROM pg_catalog.pg_attribute WHERE attrelid='public.qa_orders'::regclass "
            "AND attname IN ('label','priority') ORDER BY attnum"
        )
        assert any(r["atttypmod"] != -1 and "(" in r["formatted"] for r in raw)
        b = binding()
        with pytest.raises(AdditivePlanError, match="migration_catalog_typmod_unsupported"):
            after = await catalog(conn)

            def receipt(kind, tables, schema):
                return CatalogReceipt(
                    kind,
                    binding_digest(b),
                    schema,
                    b.data_contract_sha256,
                    catalog_digest(tables),
                    tables,
                    True,
                )

            resolve_additive(
                b,
                receipt("published", before, b.live_schema_sha256),
                receipt("source", after, b.target_schema_sha256),
                backup_verified=True,
            )
        assert await conn.fetchval("SELECT count(*) FROM public.qa_orders") == 1
    finally:
        await tx.rollback()
        await conn.close()

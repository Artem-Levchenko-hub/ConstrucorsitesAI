"""Real catalog/row observer exercised only in nonce-owned loopback CI databases."""

import asyncio
import json
import os

import asyncpg
import pytest

from yleum_orchestrator.services.publication_migration_pg import (
    MigrationIOError,
    read_catalog,
    row_proofs,
    verify_ledger,
)

from .test_publication_additive_resolver_postgres import owned_additive_database


@pytest.mark.skipif(
    os.environ.get("QA_ADDITIVE_POSTGRES_ADMIN_URL") is None,
    reason="requires explicit disposable loopback PostgreSQL",
)
@pytest.mark.asyncio
async def test_catalog_policy_expressions_and_original_column_row_fingerprints():
    async with owned_additive_database(os.environ["QA_ADDITIVE_POSTGRES_ADMIN_URL"]) as dsn:
        conn = await asyncpg.connect(dsn, timeout=5, command_timeout=8)
        loop = asyncio.get_running_loop()

        class Session:
            def json(self, sql):
                value = asyncio.run_coroutine_threadsafe(conn.fetchval(sql), loop).result(10)
                return json.loads(value) if isinstance(value, str) else value

            def sql(self, sql):
                rows = asyncio.run_coroutine_threadsafe(conn.fetch(sql), loop).result(10)
                return ("\n".join(str(row[0]) for row in rows) + "\n").encode()

        try:
            await conn.execute(
                "CREATE TABLE public.orders(id bigint PRIMARY KEY, amount integer);"
                "INSERT INTO public.orders VALUES(1,630),(2,650);"
                "ALTER TABLE public.orders ENABLE ROW LEVEL SECURITY;"
                "CREATE POLICY visible ON public.orders USING(amount>0) WITH CHECK(amount>0);"
            )
            session = Session()
            tables = await asyncio.to_thread(read_catalog, session)
            before = await asyncio.to_thread(row_proofs, session, tables)
            assert before["orders"]["count"] == 2
            await conn.execute(
                "ALTER POLICY visible ON public.orders USING(amount>100) WITH CHECK(amount>200);"
            )
            changed = await asyncio.to_thread(read_catalog, session)
            assert tables[0].policies_sha256 != changed[0].policies_sha256
            await conn.execute("ALTER TABLE public.orders ADD COLUMN pickup text;")
            assert await asyncio.to_thread(row_proofs, session, tables) == before
            await conn.execute("UPDATE public.orders SET amount=1 WHERE id=1;")
            assert await asyncio.to_thread(row_proofs, session, tables) != before
        finally:
            await conn.close()


@pytest.mark.skipif(
    os.environ.get("QA_ADDITIVE_POSTGRES_ADMIN_URL") is None,
    reason="requires explicit disposable loopback PostgreSQL",
)
@pytest.mark.asyncio
async def test_reserved_ledger_view_never_executes_generated_function_as_administrator():
    async with owned_additive_database(os.environ["QA_ADDITIVE_POSTGRES_ADMIN_URL"]) as dsn:
        conn = await asyncpg.connect(dsn, timeout=5, command_timeout=8)
        loop = asyncio.get_running_loop()

        class CatalogSession:
            def json(self, sql):
                value = asyncio.run_coroutine_threadsafe(conn.fetchval(sql), loop).result(10)
                return json.loads(value) if isinstance(value, str) else value

        try:
            await conn.execute("""
              CREATE SEQUENCE public.ledger_calls;
              CREATE FUNCTION public.evil_ledger() RETURNS text LANGUAGE plpgsql AS $$
              BEGIN PERFORM nextval('public.ledger_calls'); RETURN 'drizzle/0001.sql'; END $$;
              CREATE VIEW public.__omnia_project_migrations AS SELECT public.evil_ledger() AS name,
                repeat('a',64) AS sha256, now() AS applied_at;
            """)
            with pytest.raises(MigrationIOError, match="migration_ledger_identity_invalid"):
                await asyncio.to_thread(verify_ledger, CatalogSession(), required=True)
            assert await conn.fetchval("SELECT is_called FROM public.ledger_calls") is False
        finally:
            await conn.close()

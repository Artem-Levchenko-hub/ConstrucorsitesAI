"""Real row inventory, restricted to the existing disposable PostgreSQL fixture."""

import json

from omnia_orchestrator.services.restoration_catalog import (
    DATABASE_INVENTORY_SQL,
    database_presence_query,
)
from tests.test_restoration_database import database  # noqa: F401


async def test_row_inventory_includes_unpublished_rows_and_later_writes(database):  # noqa: F811
    admin = database.admin
    inventory = json.loads(await admin.fetchval(DATABASE_INVENTORY_SQL))
    assert inventory["unsupported"] is False
    query = database_presence_query(inventory)
    assert query is not None
    assert await admin.fetchval(query) is True
    await admin.execute(
        "DELETE FROM public.invoices; DELETE FROM public.tasks; DELETE FROM public.contacts"
    )
    assert await admin.fetchval(query) is False
    # No publication metadata or ownership filter can hide a subsequent business write.
    await admin.execute(
        "INSERT INTO contacts(id,owner_id,name) VALUES(gen_random_uuid(),'new_actor','retained')"
    )
    assert await admin.fetchval(query) is True
    assert await admin.fetchval("SELECT name FROM contacts") == "retained"


async def test_reserved_looking_application_table_is_not_ignored(database):  # noqa: F811
    admin = database.admin
    await admin.execute(
        "DELETE FROM public.invoices; DELETE FROM public.tasks; DELETE FROM public.contacts"
    )
    await admin.execute(
        "CREATE TABLE public.omnia_orders (id integer); INSERT INTO public.omnia_orders VALUES(1)"
    )
    inventory = json.loads(await admin.fetchval(DATABASE_INVENTORY_SQL))
    assert "omnia_orders" in inventory["tables"]
    query = database_presence_query(inventory)
    assert query is not None and await admin.fetchval(query) is True


async def test_unknown_storage_in_database_cannot_be_called_empty(database):  # noqa: F811
    admin = database.admin
    await admin.execute(
        "CREATE SCHEMA unmanaged; CREATE TABLE unmanaged.business_files(id integer)"
    )
    inventory = json.loads(await admin.fetchval(DATABASE_INVENTORY_SQL))
    assert database_presence_query(inventory) is None

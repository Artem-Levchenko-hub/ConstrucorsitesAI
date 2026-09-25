"""AV03: row presence and counts are independent of schema analysis."""

from __future__ import annotations

import json

from tests._versioning_pg import pg  # noqa: F401
from yleum_orchestrator.services.restoration_catalog import CATALOG_SQL, describe_catalog
from yleum_orchestrator.services.versioning.inventory import (
    LIST_RELATIONS_SQL,
    aggregate,
    observe_inventory,
)

CUSTOMERS = """
CREATE TABLE clients (id serial PRIMARY KEY, name text NOT NULL, phone text,
  status text NOT NULL DEFAULT 'new',
  CONSTRAINT clients_status_check CHECK (status in ('new','vip')));
CREATE TABLE visits (id serial PRIMARY KEY,
  client_id integer NOT NULL REFERENCES clients(id),
  amount numeric(12,2) NOT NULL CHECK (amount >= 0));
INSERT INTO clients(name, phone) VALUES ('A','1'),('B','2'),('C','3'),('D','4');
INSERT INTO visits(client_id, amount) VALUES (1, 10),(2, 20),(3, 30);
"""


def by_object(report):
    return {item.object: item for item in report.objects}


def test_presence_survives_unsupported_schema_objects(pg):  # noqa: F811
    pg.run(CUSTOMERS)
    # A user trigger makes schema analysis partial; it must not hide the rows.
    pg.run(
        "CREATE FUNCTION public.normalize_phone() RETURNS trigger LANGUAGE plpgsql AS "
        "$$BEGIN NEW.phone := trim(NEW.phone); RETURN NEW; END$$; "
        "CREATE TRIGGER normalize_phone BEFORE INSERT ON clients "
        "FOR EACH ROW EXECUTE FUNCTION public.normalize_phone();"
    )
    _, blockers, unsupported = describe_catalog(json.loads(pg.run(CATALOG_SQL)))
    assert blockers == ["custom_triggers:clients"]
    assert unsupported == [{"kind": "trigger", "object": "public.clients.normalize_phone"}]
    report = observe_inventory(pg.run, observed_on="source", schema_analysis="partial")
    assert report.presence == "present"
    assert report.coverage == "complete"
    assert report.schema_analysis == "partial"
    objects = by_object(report)
    assert objects["public.clients"].row_count == 4
    assert objects["public.visits"].row_count == 3
    assert objects["public.clients"].count_kind == "exact"


def test_empty_requires_every_business_object_measured(pg):  # noqa: F811
    pg.run("CREATE TABLE clients (id serial PRIMARY KEY, name text);")
    assert observe_inventory(pg.run, observed_on="source").presence == "empty"
    pg.run("INSERT INTO clients(name) VALUES ('first');")
    assert observe_inventory(pg.run, observed_on="source").presence == "present"


def test_partitions_are_counted_once_through_the_root(pg):  # noqa: F811
    pg.run(
        "CREATE TABLE events (id int, at date) PARTITION BY RANGE (at); "
        "CREATE TABLE events_2026 PARTITION OF events FOR VALUES FROM ('2026-01-01') "
        "TO ('2027-01-01'); INSERT INTO events VALUES (1,'2026-02-01'),(2,'2026-03-01');"
    )
    objects = by_object(observe_inventory(pg.run, observed_on="source"))
    assert objects["public.events"].row_count == 2
    assert "public.events_2026" not in objects


def test_views_are_not_executed_and_do_not_decide_presence(pg):  # noqa: F811
    pg.run(
        "CREATE TABLE clients (id serial PRIMARY KEY); "
        "CREATE VIEW client_view AS SELECT * FROM clients;"
    )
    report = observe_inventory(pg.run, observed_on="source")
    view = by_object(report)["public.client_view"]
    assert view.count_kind == "not_measured" and view.classification == "derived"
    assert report.presence == "empty" and report.coverage == "complete"


def test_unusual_identifiers_and_technical_tables(pg):  # noqa: F811
    pg.run(
        'CREATE SCHEMA "Odd Schema"; CREATE TABLE "Odd Schema"."Client ""Book""" (id int); '
        'INSERT INTO "Odd Schema"."Client ""Book""" VALUES (1); '
        "CREATE SCHEMA drizzle; CREATE TABLE drizzle.__drizzle_migrations (id serial, hash text); "
        "INSERT INTO drizzle.__drizzle_migrations(hash) VALUES ('x');"
    )
    report = observe_inventory(pg.run, observed_on="source")
    objects = by_object(report)
    assert objects['Odd Schema.Client "Book"'].row_count == 1
    assert objects["drizzle.__drizzle_migrations"].classification == "technical"
    assert report.presence == "present"
    pg.run('DELETE FROM "Odd Schema"."Client ""Book""";')
    # Migration bookkeeping alone does not make a business app non-empty.
    assert observe_inventory(pg.run, observed_on="source").presence == "empty"


def test_one_failing_count_does_not_zero_the_others(pg):  # noqa: F811
    pg.run(CUSTOMERS)
    listing = pg.run(LIST_RELATIONS_SQL)

    def flaky(sql: str) -> bytes:
        if sql == LIST_RELATIONS_SQL:
            return listing
        if "json_build_array" in sql or '"visits"' in sql:
            raise RuntimeError("timeout")
        return pg.run(sql)

    report = observe_inventory(flaky, observed_on="source")
    objects = by_object(report)
    assert objects["public.clients"].row_count == 4
    assert objects["public.visits"].row_count is None
    assert objects["public.visits"].diagnostic == "count_failed"
    # Observed rows keep "present"; the gap is reported as partial coverage.
    assert report.presence == "present" and report.coverage == "partial"


def test_unreadable_catalog_is_unavailable_never_empty():
    def broken(_sql: str) -> bytes:
        raise RuntimeError("database is not running")

    report = observe_inventory(broken, observed_on="source")
    assert (report.presence, report.coverage) == ("unknown", "unavailable")


def test_unknown_parts_never_turn_into_empty():
    from yleum_orchestrator.services.versioning.contracts import InventoryObject

    empty = InventoryObject(object="public.a", kind="table", classification="business",
                            presence="empty", row_count=0, count_kind="exact")
    unmeasured = InventoryObject(object="public.b", kind="foreign_table",
                                 classification="unknown", presence="unknown",
                                 count_kind="not_measured")
    assert aggregate([empty, unmeasured]) == ("unknown", "partial")


def test_fallback_is_bounded_and_materialized_views_are_not_scanned(pg):  # noqa: F811
    from yleum_orchestrator.services.versioning import inventory as module

    pg.run(
        "CREATE TABLE a (id int); INSERT INTO a VALUES (1); CREATE TABLE b (id int); "
        "CREATE MATERIALIZED VIEW mv AS SELECT * FROM a WITH NO DATA;"
    )
    calls: list[str] = []

    def batch_fails(sql: str) -> bytes:
        calls.append(sql)
        if "json_build_array" in sql:
            raise RuntimeError("timeout")
        return pg.run(sql)

    original = module.FALLBACK_LIMIT
    module.FALLBACK_LIMIT = 1
    try:
        report = observe_inventory(batch_fails, observed_on="source")
    finally:
        module.FALLBACK_LIMIT = original
    objects = by_object(report)
    assert objects["public.a"].row_count == 1
    assert objects["public.b"].diagnostic == "count_skipped"
    assert objects["public.mv"].count_kind == "not_measured"
    assert not any('"mv"' in sql for sql in calls)
    assert report.presence == "present" and report.coverage == "partial"

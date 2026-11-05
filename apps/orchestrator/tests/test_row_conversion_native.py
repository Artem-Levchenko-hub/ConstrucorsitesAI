"""Normal enum/array observations retain metadata locks and read-only execution."""
import json

import pytest

from tests._versioning_pg import pg  # noqa: F401
from yleum_orchestrator.services.restoration_row_conversion import row_conversion_preamble


def test_ordinary_enum_array_values_are_preserved_under_read_only_catalog_lease(pg):  # noqa: F811
    pg.run("""
      CREATE TYPE public.audit_state AS ENUM ('draft', 'ready');
      CREATE TABLE public.audit_items (
        id integer PRIMARY KEY,
        state public.audit_state NOT NULL,
        history public.audit_state[] NOT NULL,
        title text NOT NULL,
        details jsonb NOT NULL
      );
      INSERT INTO public.audit_items VALUES
        (1, 'ready', ARRAY['draft','ready']::public.audit_state[], 'Preserved data', '{"n":3}');
    """)
    script = row_conversion_preamble([("public", "audit_items")]) + """
      SELECT pg_catalog.json_build_object(
        'read_only', pg_catalog.current_setting('transaction_read_only'),
        'metadata_locks', (SELECT pg_catalog.count(*) FROM pg_catalog.pg_locks
          WHERE pid = pg_catalog.pg_backend_pid() AND mode = 'ShareLock' AND granted
          AND relation IN ('pg_catalog.pg_attribute'::pg_catalog.regclass,
            'pg_catalog.pg_cast'::pg_catalog.regclass, 'pg_catalog.pg_class'::pg_catalog.regclass,
            'pg_catalog.pg_namespace'::pg_catalog.regclass,
            'pg_catalog.pg_proc'::pg_catalog.regclass,
            'pg_catalog.pg_type'::pg_catalog.regclass)),
        'value', (SELECT pg_catalog.to_jsonb(t) FROM public.audit_items AS t WHERE id=1));
      COMMIT;
    """
    observed = json.loads(pg.run(script))
    assert observed["read_only"] == "on"
    assert observed["metadata_locks"] == 6
    assert observed["value"] == {
        "id": 1, "state": "ready", "history": ["draft", "ready"],
        "title": "Preserved data", "details": {"n": 3},
    }
    assert pg.run("SELECT title FROM public.audit_items WHERE id=1;").strip() == b"Preserved data"


def test_nonstored_relation_is_refused_before_value_observation(pg):  # noqa: F811
    pg.run("CREATE VIEW public.audit_view AS SELECT 1 AS id;")
    with pytest.raises(RuntimeError, match="controller database operation failed"):
        pg.run(row_conversion_preamble([("public", "audit_view")]) + "SELECT 1; COMMIT;")


def test_optional_absent_table_does_not_prevent_empty_observation(pg):  # noqa: F811
    observed = pg.run(
        row_conversion_preamble([("public", "absent_audit_table")], allow_missing=True)
        + "SELECT pg_catalog.current_setting('transaction_read_only'); COMMIT;"
    )
    assert observed.strip() == b"on"

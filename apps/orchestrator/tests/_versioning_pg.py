"""Disposable PostgreSQL for versioning tests, driven through ``psql -qAt`` exactly
like the controller's ``admin_sql`` (ON_ERROR_STOP, stdin script, raw stdout)."""

from __future__ import annotations

import os
import shutil
import subprocess
from collections.abc import Iterator
from urllib.parse import urlparse

import pytest

RESET_DISPOSABLE_DATABASE_SQL = """
DROP EVENT TRIGGER IF EXISTS fixture_event;
DO $reset$
DECLARE
    schema_name text;
BEGIN
    FOR schema_name IN
        SELECT nspname
        FROM pg_namespace
        WHERE nspname <> 'public'
          AND nspname <> 'information_schema'
          AND nspname !~ '^pg_'
    LOOP
        EXECUTE format('DROP SCHEMA %I CASCADE', schema_name);
    END LOOP;
END
$reset$;
DROP SCHEMA IF EXISTS public CASCADE;
CREATE SCHEMA public;
DO $reset$
DECLARE
    object_oid oid;
BEGIN
    FOR object_oid IN SELECT oid FROM pg_largeobject_metadata
    LOOP
        PERFORM lo_unlink(object_oid);
    END LOOP;
END
$reset$;
"""


class Pg:
    def __init__(self, dsn: str) -> None:
        self.dsn = dsn

    def run(self, sql: str) -> bytes:
        outcome = subprocess.run(
            ["psql", "-X", "-qAt", "-v", "ON_ERROR_STOP=1", self.dsn],
            input=sql.encode(), capture_output=True, timeout=60, check=False,
        )
        if outcome.returncode != 0:
            raise RuntimeError("controller database operation failed")
        return outcome.stdout


@pytest.fixture
def pg() -> Iterator[Pg]:
    dsn = os.environ.get("RESTORATION_TEST_DATABASE_URL")
    if not dsn:
        pytest.skip("requires explicit disposable RESTORATION_TEST_DATABASE_URL")
    if shutil.which("psql") is None:
        pytest.skip("requires the psql client")
    if urlparse(dsn).path != "/restoration_policy_test":
        pytest.fail("RESTORATION_TEST_DATABASE_URL must name restoration_policy_test")
    db = Pg(dsn)
    if db.run("SELECT current_database();").strip() != b"restoration_policy_test":
        pytest.fail("connected database must be restoration_policy_test")
    db.run(RESET_DISPOSABLE_DATABASE_SQL)
    try:
        yield db
    finally:
        db.run(RESET_DISPOSABLE_DATABASE_SQL)

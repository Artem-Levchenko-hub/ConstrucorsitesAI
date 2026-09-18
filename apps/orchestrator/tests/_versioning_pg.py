"""Disposable PostgreSQL for versioning tests, driven through ``psql -qAt`` exactly
like the controller's ``admin_sql`` (ON_ERROR_STOP, stdin script, raw stdout)."""

from __future__ import annotations

import os
import shutil
import subprocess
from urllib.parse import urlparse

import pytest


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
def pg() -> Pg:
    dsn = os.environ.get("RESTORATION_TEST_DATABASE_URL")
    if not dsn:
        pytest.skip("requires explicit disposable RESTORATION_TEST_DATABASE_URL")
    if shutil.which("psql") is None:
        pytest.skip("requires the psql client")
    if urlparse(dsn).path != "/restoration_policy_test":
        pytest.fail("RESTORATION_TEST_DATABASE_URL must name restoration_policy_test")
    db = Pg(dsn)
    assert db.run("SELECT current_database();").strip() == b"restoration_policy_test"
    db.run(
        "DROP SCHEMA IF EXISTS omnia_guard CASCADE; DROP SCHEMA IF EXISTS unmanaged CASCADE; "
        "DROP SCHEMA IF EXISTS drizzle CASCADE; DROP SCHEMA IF EXISTS \"Odd Schema\" CASCADE; "
        "DROP EVENT TRIGGER IF EXISTS fixture_event; "
        "DROP SCHEMA public CASCADE; CREATE SCHEMA public;"
    )
    return db

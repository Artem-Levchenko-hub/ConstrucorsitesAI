"""Unit tests for `core.postgres_admin` pure-function surface.

Integration tests against a real Postgres are intentionally out of scope here
— they need a live `omnia-postgres-users` instance (or testcontainers), which
this file doesn't try to spin up. The pure pieces (DSN assembly, identifier
quoting, host rewriting) are what regresses in code review, so they're what
we cover.
"""

from __future__ import annotations

from uuid import UUID

import pytest

from omnia_orchestrator.core import postgres_admin
from omnia_orchestrator.core.errors import OrchestratorError

PROJECT_ID = UUID("01234567-89ab-cdef-0123-456789abcdef")


@pytest.fixture(autouse=True)
def _admin_dsn(monkeypatch: pytest.MonkeyPatch) -> None:
    """Force a deterministic admin DSN so DSN-building tests don't depend on
    the test runner's local .env."""
    monkeypatch.setenv(
        "DATABASE_URL",
        "postgresql+asyncpg://omnia_root:rootpw@localhost:5433/omnia_users",
    )
    monkeypatch.setenv("INTERNAL_TOKEN", "test-token-test-token-test-token")
    # Clear the settings cache so the monkeypatched env actually takes effect.
    from omnia_orchestrator.core.config import get_settings

    get_settings.cache_clear()  # type: ignore[attr-defined]


def test_project_short_id_first_8_hex() -> None:
    assert postgres_admin._project_short_id(PROJECT_ID) == "01234567"


def test_quote_ident_accepts_valid() -> None:
    assert postgres_admin._quote_ident("proj_01234567") == '"proj_01234567"'
    assert postgres_admin._quote_ident("_x") == '"_x"'


@pytest.mark.parametrize(
    "bad",
    [
        "1leading_digit",
        "has space",
        "has-dash",
        "has;injection",
        "has'quote",
        "",
    ],
)
def test_quote_ident_rejects_invalid(bad: str) -> None:
    with pytest.raises(OrchestratorError) as excinfo:
        postgres_admin._quote_ident(bad)
    assert excinfo.value.code == "invalid_identifier"


def test_user_facing_host_rewrites_loopback() -> None:
    assert postgres_admin._user_facing_host("localhost") == "host.docker.internal"
    assert postgres_admin._user_facing_host("127.0.0.1") == "host.docker.internal"
    assert postgres_admin._user_facing_host("::1") == "host.docker.internal"


def test_user_facing_host_preserves_external() -> None:
    assert postgres_admin._user_facing_host("omnia-postgres-users") == "omnia-postgres-users"
    assert postgres_admin._user_facing_host("10.0.0.5") == "10.0.0.5"


def test_build_dsn_shape() -> None:
    dsn = postgres_admin.build_dsn(
        "proj_01234567_user", "p@ss/w:rd!", "proj_01234567"
    )
    # Host must be rewritten away from localhost to the container-to-container
    # name on the runtime network (NOT the 127.0.0.1:5433 host bind, which is
    # unreachable from a container); password URL-encoded.
    assert dsn.startswith("postgresql://proj_01234567_user:")
    assert "@omnia-postgres-users:5432/omnia_users" in dsn
    assert "p%40ss%2Fw%3Ard%21" in dsn  # @ / : ! all percent-encoded
    assert "?options=-c+search_path%3Dproj_01234567" in dsn


def test_build_dsn_default_port_when_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(
        "DATABASE_URL",
        "postgresql+asyncpg://omnia_root:rootpw@localhost/omnia_users",
    )
    from omnia_orchestrator.core.config import get_settings

    get_settings.cache_clear()  # type: ignore[attr-defined]
    dsn = postgres_admin.build_dsn("role", "pw", "schema")
    assert ":5432/" in dsn  # falls back to default Postgres port


def test_normalize_admin_dsn_strips_asyncpg_dialect() -> None:
    assert (
        postgres_admin._normalize_admin_dsn(
            "postgresql+asyncpg://u:p@h:1/d"
        )
        == "postgresql://u:p@h:1/d"
    )
    # Already plain — passthrough.
    assert (
        postgres_admin._normalize_admin_dsn("postgresql://u:p@h:1/d")
        == "postgresql://u:p@h:1/d"
    )


def test_escape_sql_literal_doubles_single_quotes() -> None:
    assert postgres_admin._escape_sql_literal("a'b") == "a''b"
    assert postgres_admin._escape_sql_literal("no quotes") == "no quotes"


class _FakeConn:
    def __init__(self, schema_present: bool) -> None:
        self.executed: list[str] = []
        self._present = schema_present

    async def fetchrow(self, query: str, *args: object) -> object:
        return (1,) if self._present else None

    async def execute(self, query: str, *args: object) -> None:
        self.executed.append(query)


class _FakePool:
    def __init__(self, conn: _FakeConn) -> None:
        self._conn = conn

    def acquire(self):  # type: ignore[no-untyped-def]
        conn = self._conn

        class _CM:
            async def __aenter__(self_inner) -> _FakeConn:
                return conn

            async def __aexit__(self_inner, *exc: object) -> bool:
                return False

        return _CM()


@pytest.mark.asyncio
async def test_archive_schema_renames_live_schema(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    conn = _FakeConn(schema_present=True)

    async def _pool() -> _FakePool:
        return _FakePool(conn)

    monkeypatch.setattr(postgres_admin, "_get_admin_pool", _pool)
    await postgres_admin.archive_schema(PROJECT_ID)

    joined = " | ".join(conn.executed)
    # Stale archive cleared, then live schema renamed aside (soft-delete).
    assert 'DROP SCHEMA IF EXISTS "zdel_proj_01234567" CASCADE' in joined
    assert 'ALTER SCHEMA "proj_01234567" RENAME TO "zdel_proj_01234567"' in joined


@pytest.mark.asyncio
async def test_archive_schema_noop_when_absent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    conn = _FakeConn(schema_present=False)

    async def _pool() -> _FakePool:
        return _FakePool(conn)

    monkeypatch.setattr(postgres_admin, "_get_admin_pool", _pool)
    await postgres_admin.archive_schema(PROJECT_ID)

    # Nothing to archive → no DDL executed (idempotent re-run).
    assert conn.executed == []

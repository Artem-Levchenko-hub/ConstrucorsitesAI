"""Per-project Postgres schema + role provisioning.

R-01 (deep module): callers see `create_schema(project_id)` /
`drop_schema(project_id)` / `load_existing_dsn(project_id)`. SQL identifier
quoting, password generation, DSN assembly, and secret persistence are all
private.

R-04 (separation by layer): orchestrator owns `omnia-postgres-users` (the
*user-project* Postgres at :5433). The admin DSN comes from
`settings.database_url` and is never exposed to user containers.

Per-project isolation: each project gets schema `proj_<id8>` and role
`proj_<id8>_user` with privileges scoped to that schema. The role's password
is fresh on every provision (idempotent rotation) and rendered into the
container's `DATABASE_URL` only; persisted to `secrets_root/<project_id>/db.env`
with mode 0600 so a restart can recover state without re-rotating.
"""

from __future__ import annotations

import asyncio
import os
import re
import secrets
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote, urlparse
from uuid import UUID

import asyncpg  # type: ignore[import-untyped]
import structlog

from omnia_orchestrator.core.config import get_settings
from omnia_orchestrator.core.errors import OrchestratorError

log = structlog.get_logger("omnia_orchestrator.postgres_admin")


@dataclass(frozen=True, slots=True)
class SchemaCredentials:
    """Result of provisioning a per-project schema + role."""

    schema_name: str
    role_name: str
    dsn: str


_VALID_IDENT = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]*$")
_admin_pool: asyncpg.Pool | None = None
_pool_lock = asyncio.Lock()


def _project_short_id(project_id: UUID) -> str:
    return project_id.hex[:8]


def _quote_ident(ident: str) -> str:
    if not _VALID_IDENT.match(ident):
        raise OrchestratorError(
            code="invalid_identifier",
            message=f"refusing to quote suspicious SQL identifier: {ident!r}",
            status_code=500,
        )
    return f'"{ident}"'


def _normalize_admin_dsn(raw: str) -> str:
    """asyncpg.connect does not accept the SQLAlchemy `+asyncpg` dialect tag."""
    return raw.replace("postgresql+asyncpg://", "postgresql://", 1)


async def _get_admin_pool() -> asyncpg.Pool:
    global _admin_pool
    async with _pool_lock:
        if _admin_pool is None or _admin_pool.is_closing():
            _admin_pool = await asyncpg.create_pool(
                dsn=_normalize_admin_dsn(get_settings().database_url),
                min_size=1,
                max_size=4,
                command_timeout=15,
            )
    return _admin_pool


def _user_facing_host(admin_host: str) -> str:
    """The hostname used inside the user-project container.

    Orchestrator runs on the VPS host network alongside `omnia-postgres-users`;
    user containers are isolated and reach the host via `host.docker.internal`.
    On Linux this only resolves when the container is started with
    `extra_hosts={"host.docker.internal": "host-gateway"}` — see
    `core.docker_client.start_container`.
    """
    if admin_host in {"localhost", "127.0.0.1", "::1"}:
        return "host.docker.internal"
    return admin_host


def build_dsn(role: str, password: str, schema: str) -> str:
    """Assemble the per-container DATABASE_URL pointing at the user's schema.

    Format matches the template's `.env.example`: shared db, isolation via
    role + `search_path`. The `options=-c+search_path%3D<schema>` query param
    is honoured by both node-postgres (`pg`) and drizzle-kit.
    """
    raw = get_settings().database_url
    parsed = urlparse(_normalize_admin_dsn(raw))
    db = (parsed.path or "/").lstrip("/") or "postgres"
    # User containers reach Postgres CONTAINER-TO-CONTAINER over the shared
    # runtime network (omnia-runtime_default) by its container name + internal
    # port 5432 — NOT via the host bind (127.0.0.1:5433), which lives in the
    # host's loopback and is unreachable from a container's network namespace
    # (host.docker.internal → bridge gateway, where Postgres isn't listening).
    # The container must be attached to that network (see docker_client
    # start_container). Overridable via env if the infra names change.
    host = os.getenv("OMNIA_RUNTIME_DB_HOST", "omnia-postgres-users")
    port = int(os.getenv("OMNIA_RUNTIME_DB_PORT") or 5432)
    encoded_password = quote(password, safe="")
    return (
        f"postgresql://{role}:{encoded_password}@{host}:{port}/{db}"
        f"?options=-c+search_path%3D{schema}"
    )


def _persist_secret(project_id: UUID, dsn: str) -> None:
    secrets_dir = Path(get_settings().secrets_root) / str(project_id)
    secrets_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    secret_file = secrets_dir / "db.env"
    secret_file.write_text(f"DATABASE_URL={dsn}\n", encoding="utf-8")
    try:
        secret_file.chmod(0o600)
    except OSError:
        # Windows dev paths don't honour chmod fully; prod is Linux.
        pass


def _escape_sql_literal(value: str) -> str:
    return value.replace("'", "''")


async def create_schema(project_id: UUID) -> SchemaCredentials:
    """Create per-project schema + role with privileges scoped to that schema.

    Idempotent: re-running for the same project_id reuses the schema and
    rotates the role password. Rotation is intentional — the orchestrator is
    the only place holding the password, so a fresh provision can start clean
    without keeping stale credentials alive.
    """
    short = _project_short_id(project_id)
    schema_name = f"proj_{short}"
    role_name = f"proj_{short}_user"
    password = secrets.token_urlsafe(32)

    schema_q = _quote_ident(schema_name)
    role_q = _quote_ident(role_name)
    password_sql = _escape_sql_literal(password)

    pool = await _get_admin_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            existing = await conn.fetchrow(
                "SELECT 1 FROM pg_roles WHERE rolname = $1", role_name
            )
            if existing is None:
                await conn.execute(
                    f"CREATE ROLE {role_q} LOGIN PASSWORD '{password_sql}'"
                )
            else:
                await conn.execute(
                    f"ALTER ROLE {role_q} WITH PASSWORD '{password_sql}'"
                )

            await conn.execute(
                f"CREATE SCHEMA IF NOT EXISTS {schema_q} AUTHORIZATION {role_q}"
            )
            await conn.execute(
                f"GRANT USAGE, CREATE ON SCHEMA {schema_q} TO {role_q}"
            )
            admin_path = urlparse(
                _normalize_admin_dsn(get_settings().database_url)
            ).path or "/"
            db_name = admin_path.lstrip("/") or "postgres"
            await conn.execute(
                f"GRANT CONNECT ON DATABASE {_quote_ident(db_name)} TO {role_q}"
            )

    dsn = build_dsn(role_name, password, schema_name)
    _persist_secret(project_id, dsn)
    log.info(
        "postgres_admin.created",
        project_id=str(project_id),
        schema=schema_name,
        role=role_name,
    )
    return SchemaCredentials(schema_name=schema_name, role_name=role_name, dsn=dsn)


async def drop_schema(project_id: UUID) -> None:
    """Tear down the project's schema + role. Idempotent: missing is success."""
    short = _project_short_id(project_id)
    schema_name = f"proj_{short}"
    role_name = f"proj_{short}_user"
    schema_q = _quote_ident(schema_name)
    role_q = _quote_ident(role_name)

    pool = await _get_admin_pool()
    async with pool.acquire() as conn:
        await conn.execute(f"DROP SCHEMA IF EXISTS {schema_q} CASCADE")
        await conn.execute(f"DROP ROLE IF EXISTS {role_q}")

    secret_file = Path(get_settings().secrets_root) / str(project_id) / "db.env"
    secret_file.unlink(missing_ok=True)
    log.info(
        "postgres_admin.dropped",
        project_id=str(project_id),
        schema=schema_name,
    )


def load_existing_dsn(project_id: UUID) -> str | None:
    """Read back a previously persisted DSN from `secrets_root`.

    Used by the deploy path so the prod container gets the same credentials
    as its dev sibling. Returns None if the project hasn't been provisioned.
    """
    secret_file = Path(get_settings().secrets_root) / str(project_id) / "db.env"
    if not secret_file.exists():
        return None
    for line in secret_file.read_text(encoding="utf-8").splitlines():
        if line.startswith("DATABASE_URL="):
            return line[len("DATABASE_URL=") :]
    return None

"""Migration-chain safety guard (G008 — durable week-long builds).

Four agents push to `main`; the classic failure is two migrations declaring the
SAME `down_revision` (a fork) or a duplicate `revision` id — `alembic upgrade
head` then errors with "multiple heads" and a deploy can silently ship against a
half-migrated DB. This test makes that impossible to merge unnoticed: it parses
the migration chain statically (no DB needed) and asserts it is a single linear
line with exactly one head and one root.
"""

from __future__ import annotations

import asyncio
import importlib.util
import os
import re
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from unittest.mock import Mock, call
from uuid import uuid4

import asyncpg  # type: ignore[import-untyped]
import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy.engine import make_url

from omnia_api.core.config import get_settings

_VERSIONS = Path(__file__).resolve().parent.parent / "migrations" / "versions"
_API_ROOT = Path(__file__).resolve().parents[1]
# Migrations use either bare (`revision = "x"`) or annotated
# (`revision: str = "x"`, `down_revision: Union[str, None] = "x"`) declarations —
# the optional `(?::[^=\n]+)?` swallows a type annotation. A root's
# `down_revision = None` (unquoted) intentionally does NOT match, so it maps to
# None — without that, the `None` inside `Union[str, None]` would mislead us.
_REV = re.compile(r'^revision\s*(?::[^=\n]+)?=\s*["\']([^"\']+)["\']', re.M)
_DOWN = re.compile(r'^down_revision\s*(?::[^=\n]+)?=\s*["\']([^"\']+)["\']', re.M)


def _asyncpg_dsn(url: str) -> str:
    return url.replace("postgresql+asyncpg://", "postgresql://", 1)


def _normalized_catalog_sql(value: str) -> str:
    return " ".join(value.replace("::text", "").split())


async def _execute(dsn: str, statement: str) -> str:
    connection = await asyncpg.connect(dsn)
    try:
        return str(await connection.execute(statement))
    finally:
        await connection.close()


async def _fetch(dsn: str, statement: str) -> list[asyncpg.Record]:
    connection = await asyncpg.connect(dsn)
    try:
        return list(await connection.fetch(statement))
    finally:
        await connection.close()


async def _fetchval(dsn: str, statement: str) -> object:
    connection = await asyncpg.connect(dsn)
    try:
        return await connection.fetchval(statement)
    finally:
        await connection.close()


@dataclass(frozen=True)
class ProjectCellMigrationDatabase:
    config: Config
    dsn: str

    def upgrade(self, revision: str) -> None:
        command.upgrade(self.config, revision)

    def fetch(self, statement: str) -> list[asyncpg.Record]:
        return asyncio.run(_fetch(self.dsn, statement))

    def fetchval(self, statement: str) -> object:
        return asyncio.run(_fetchval(self.dsn, statement))


@pytest.fixture
def project_cell_migration_database(
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[ProjectCellMigrationDatabase]:
    configured_url = os.environ.get("DATABASE_TEST_URL") or os.environ["DATABASE_URL"]
    parsed = make_url(configured_url)
    database_name = f"omnia_project_cell_{uuid4().hex}"
    target_url = parsed.set(database=database_name).render_as_string(hide_password=False)
    admin_url = parsed.set(database="postgres").render_as_string(hide_password=False)
    admin_dsn = _asyncpg_dsn(admin_url)
    target_dsn = _asyncpg_dsn(target_url)

    asyncio.run(_execute(admin_dsn, f'CREATE DATABASE "{database_name}"'))
    monkeypatch.setenv("DATABASE_URL", target_url)
    monkeypatch.setenv("DATABASE_TEST_URL", target_url)
    get_settings.cache_clear()
    config = Config(str(_API_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(_API_ROOT / "migrations"))
    config.set_main_option("path_separator", "os")
    try:
        yield ProjectCellMigrationDatabase(config=config, dsn=target_dsn)
    finally:
        get_settings.cache_clear()
        asyncio.run(_execute(admin_dsn, f'DROP DATABASE "{database_name}" WITH (FORCE)'))


def _chain() -> dict[str, str | None]:
    """Map each revision id -> its down_revision (None for the root)."""
    chain: dict[str, str | None] = {}
    for path in _VERSIONS.glob("*.py"):
        text = path.read_text(encoding="utf-8")
        rev = _REV.search(text)
        if not rev:
            continue
        down = _DOWN.search(text)
        chain[rev.group(1)] = down.group(1) if down else None
    return chain


def _load_capacity_queue_migration() -> ModuleType:
    path = _VERSIONS / "0055_project_cell_capacity_queue.py"
    spec = importlib.util.spec_from_file_location("migration_0055_capacity_queue", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_capacity_queue_migration_uses_actual_generation_constraint_names() -> None:
    migration = _load_capacity_queue_migration()
    recorder = Mock()
    recorder.f.side_effect = lambda name: name
    migration.op = recorder

    migration.upgrade()

    recorder.drop_constraint.assert_any_call(
        "ck_generation_runs_ck_generation_runs_status_allowed",
        "generation_runs",
        type_="check",
    )
    upgrade_create = next(
        item
        for item in recorder.create_check_constraint.call_args_list
        if item.args[1] == "generation_runs"
    )
    assert upgrade_create.args[0] == "ck_generation_runs_status_allowed"
    assert "queued_for_capacity" in str(upgrade_create.args[2])

    recorder.reset_mock()
    recorder.f.side_effect = lambda name: name
    migration.downgrade()

    assert call(
        "ck_generation_runs_status_allowed", "generation_runs", type_="check"
    ) in recorder.drop_constraint.call_args_list
    downgrade_create = next(
        item
        for item in recorder.create_check_constraint.call_args_list
        if item.args[1] == "generation_runs"
    )
    assert downgrade_create.args[0] == (
        "ck_generation_runs_ck_generation_runs_status_allowed"
    )
    assert "queued_for_capacity" not in str(downgrade_create.args[2])


def test_revision_ids_are_unique() -> None:
    # A duplicate revision id is two files claiming the same node — ambiguous.
    files = list(_VERSIONS.glob("*.py"))
    revs: list[str] = []
    for path in files:
        m = _REV.search(path.read_text(encoding="utf-8"))
        if m:
            revs.append(m.group(1))
    assert len(revs) == len(set(revs)), "duplicate revision id(s) in migrations/"


def test_no_two_migrations_share_a_parent() -> None:
    # Two migrations with the same down_revision = a fork = "multiple heads".
    chain = _chain()
    parents = [d for d in chain.values() if d is not None]
    dupes = {p for p in parents if parents.count(p) > 1}
    assert not dupes, f"forked migration chain — these down_revisions are claimed twice: {dupes}"


def test_exactly_one_head() -> None:
    chain = _chain()
    assert chain, "no migrations found"
    downs = {d for d in chain.values() if d is not None}
    heads = [rev for rev in chain if rev not in downs]
    assert len(heads) == 1, f"expected exactly one head, found {sorted(heads)}"


def test_project_cell_finalization_is_the_only_head() -> None:
    # Mutation caught: placing 0054 on the wrong parent or introducing another branch.
    chain = _chain()
    downs = {down for down in chain.values() if down is not None}
    heads = sorted(revision for revision in chain if revision not in downs)
    assert heads == ["0056_project_cell_finalization"]
    assert chain["0056_project_cell_finalization"] == "0055_project_cell_capacity_queue"


def test_project_cell_candidates_migration_upgrade_and_rollback(
    project_cell_migration_database: ProjectCellMigrationDatabase,
) -> None:
    database = project_cell_migration_database
    database.upgrade("0054_project_cell_candidates")
    assert database.fetchval("SELECT version_num FROM alembic_version") == (
        "0054_project_cell_candidates"
    )
    checks = [str(row["definition"]) for row in database.fetch("""
        SELECT pg_get_constraintdef(oid) AS definition FROM pg_constraint
        WHERE conrelid = 'project_cell_candidates'::regclass AND contype = 'c'
    """)]
    assert len(checks) == 8
    for prefix in ("database-backup", "build", "verification"):
        assert any(f"{prefix}/sha256/[0-9a-f]{{64}}" in check for check in checks)
    assert database.fetchval("""
        SELECT count(*) FROM pg_constraint
        WHERE conrelid = 'project_cell_candidates'::regclass AND contype = 'u'
    """) == 0  # Retrying the same source after rejection/cancellation is allowed.
    assert database.fetchval("""
        SELECT count(*) FROM pg_indexes
        WHERE tablename = 'project_cell_candidates'
          AND indexname = 'uq_project_cell_candidates_one_accepted'
          AND indexdef LIKE '%UNIQUE%WHERE%accepted%'
    """) == 1
    command.downgrade(database.config, "0053_project_cell_operation_fencing")
    assert database.fetchval("SELECT to_regclass('project_cell_candidates')") is None
    assert database.fetchval("SELECT to_regclass('project_cell_workspaces')") is not None


def test_project_cell_migrated_schema_matches_durable_contract(
    project_cell_migration_database: ProjectCellMigrationDatabase,
) -> None:
    database = project_cell_migration_database
    database.upgrade("0053_project_cell_operation_fencing")

    assert database.fetchval("SELECT version_num FROM alembic_version") == (
        "0053_project_cell_operation_fencing"
    )

    checks = {
        str(row["conname"]): _normalized_catalog_sql(str(row["definition"]))
        for row in database.fetch(
            """
            SELECT conname, pg_get_constraintdef(oid) AS definition
            FROM pg_constraint
            WHERE conrelid IN (
                'project_cell_workspaces'::regclass,
                'project_cell_operations'::regclass
            ) AND contype = 'c'
            ORDER BY conname
            """
        )
    }
    assert checks == {
        "ck_project_cell_operations_kind_allowed": (
            "CHECK ((kind = ANY (ARRAY['ensure', 'wake', 'pause', 'stop', 'destroy', "
            "'status', 'restore', 'reconcile'])))"
        ),
        "ck_project_cell_operations_status_allowed": (
            "CHECK ((status = ANY (ARRAY['pending', 'running', 'completed', 'failed', "
            "'cancelled', 'indeterminate'])))"
        ),
        "ck_project_cell_workspaces_state_allowed": (
            "CHECK ((state = ANY (ARRAY['provisioning', 'ready', 'stopped', 'failed', "
            "'deleting', 'deleted'])))"
        ),
    }

    uniques = {
        str(row["conname"]): _normalized_catalog_sql(str(row["definition"]))
        for row in database.fetch(
            """
            SELECT conname, pg_get_constraintdef(oid) AS definition
            FROM pg_constraint
            WHERE conrelid IN (
                'project_cell_workspaces'::regclass,
                'project_cell_operations'::regclass
            ) AND contype = 'u'
            ORDER BY conname
            """
        )
    }
    assert uniques == {
        "uq_project_cell_operations_workspace_id_idempotency_key": (
            "UNIQUE (workspace_id, idempotency_key)"
        ),
        "uq_project_cell_workspaces_project_id": "UNIQUE (project_id)",
    }

    foreign_keys = {
        str(row["conname"]): _normalized_catalog_sql(str(row["definition"]))
        for row in database.fetch(
            """
            SELECT conname, pg_get_constraintdef(oid) AS definition
            FROM pg_constraint
            WHERE conrelid IN (
                'project_cell_workspaces'::regclass,
                'project_cell_operations'::regclass
            ) AND contype = 'f'
            ORDER BY conname
            """
        )
    }
    assert foreign_keys == {
        "fk_project_cell_operations_generation_run_id_generation_runs": (
            "FOREIGN KEY (generation_run_id) REFERENCES generation_runs(id) ON DELETE SET NULL"
        ),
        "fk_project_cell_operations_workspace_id_project_cell_workspaces": (
            "FOREIGN KEY (workspace_id) REFERENCES project_cell_workspaces(id) ON DELETE CASCADE"
        ),
        "fk_project_cell_workspaces_generation_run_id_generation_runs": (
            "FOREIGN KEY (generation_run_id) REFERENCES generation_runs(id) ON DELETE SET NULL"
        ),
        "fk_project_cell_workspaces_owner_id_users": (
            "FOREIGN KEY (owner_id) REFERENCES users(id) ON DELETE CASCADE"
        ),
        "fk_project_cell_workspaces_project_id_projects": (
            "FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE"
        ),
    }

    indexes = database.fetch(
        """
        SELECT index_definition.indexdef,
               index_data.indisunique AS is_unique,
               pg_get_expr(index_data.indpred, index_data.indrelid) AS predicate
        FROM pg_indexes AS index_definition
        JOIN pg_class AS index_class ON index_class.relname = index_definition.indexname
        JOIN pg_index AS index_data ON index_data.indexrelid = index_class.oid
        WHERE index_definition.schemaname = 'public'
          AND index_definition.indexname =
              'uq_project_cell_operations_one_active_per_workspace'
        """
    )
    assert len(indexes) == 1
    active_index = indexes[0]
    assert active_index["is_unique"] is True
    assert "USING btree (workspace_id)" in str(active_index["indexdef"])
    assert _normalized_catalog_sql(str(active_index["predicate"])) == (
        "(status = ANY (ARRAY['pending', 'running']))"
    )

    server_defaults = {
        (str(row["table_name"]), str(row["column_name"])): (
            None
            if row["column_default"] is None
            else _normalized_catalog_sql(str(row["column_default"]))
        )
        for row in database.fetch(
            """
            SELECT table_name, column_name, column_default
            FROM information_schema.columns
            WHERE table_schema = 'public'
              AND (table_name, column_name) IN (
                  ('project_cell_workspaces', 'provider_metadata'),
                  ('project_cell_workspaces', 'fencing_epoch'),
                  ('project_cell_workspaces', 'version'),
                  ('project_cell_operations', 'request_payload'),
                  ('project_cell_operations', 'status'),
                  ('project_cell_operations', 'fencing_epoch')
              )
            ORDER BY table_name, column_name
            """
        )
    }
    assert server_defaults == {
        ("project_cell_operations", "request_payload"): "'{}'::jsonb",
        ("project_cell_operations", "fencing_epoch"): None,
        ("project_cell_operations", "status"): "'pending'",
        ("project_cell_workspaces", "fencing_epoch"): "0",
        ("project_cell_workspaces", "provider_metadata"): "'{}'::jsonb",
        ("project_cell_workspaces", "version"): "1",
    }

    columns = {
        (str(row["table_name"]), str(row["column_name"])): (
            str(row["is_nullable"]),
            _normalized_catalog_sql(str(row["data_type"])),
        )
        for row in database.fetch(
            """
            SELECT table_name, column_name, is_nullable, data_type
            FROM information_schema.columns
            WHERE table_schema = 'public'
              AND (table_name, column_name) IN (
                  ('project_cell_operations', 'fencing_epoch'),
                  ('project_cell_workspaces', 'fencing_epoch')
              )
            ORDER BY table_name, column_name
            """
        )
    }
    assert columns == {
        ("project_cell_operations", "fencing_epoch"): ("YES", "integer"),
        ("project_cell_workspaces", "fencing_epoch"): ("NO", "integer"),
    }


def test_exactly_one_root() -> None:
    chain = _chain()
    roots = [rev for rev, down in chain.items() if down is None]
    assert len(roots) == 1, f"expected exactly one root, found {sorted(roots)}"


def test_chain_is_fully_connected() -> None:
    # Walk from the head down; every node must be reachable (no dangling parent
    # pointing at a revision that doesn't exist).
    chain = _chain()
    downs = {d for d in chain.values() if d is not None}
    head = next(rev for rev in chain if rev not in downs)
    seen: set[str] = set()
    node: str | None = head
    while node is not None:
        assert node in chain, f"down_revision points at unknown revision {node!r}"
        assert node not in seen, "cycle detected in migration chain"
        seen.add(node)
        node = chain[node]
    assert len(seen) == len(chain), (
        f"unreachable migrations: {sorted(set(chain) - seen)}"
    )

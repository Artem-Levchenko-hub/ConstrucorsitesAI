"""Unit tests for the demo-seed policy layer + the postgres_admin writer.

Two surfaces are covered without a live Postgres:
  * ``demo_seed_writer._build_batches`` — the model-independent policy: which
    entities get seeded (PUBLIC only), how many rows, malformed-input safety.
  * ``demo_seed_writer.seed_demo_data`` — fail-soft + delegation contract.
  * ``postgres_admin.seed_public_records`` — the SQL flow (SET ROLE, demo user,
    skip-if-non-empty, insert) exercised against a scripted fake connection.
"""

from __future__ import annotations

import json
from typing import Any
from uuid import UUID

import pytest

from omnia_orchestrator.core import postgres_admin
from omnia_orchestrator.services import demo_seed_writer, demo_seeder

PROJECT_ID = UUID("01234567-89ab-cdef-0123-456789abcdef")


def _entity_json(name: str, access: str, fields: dict[str, Any]) -> str:
    return json.dumps({"name": name, "access": access, "fields": fields})


# ── _build_batches: the seeding policy ───────────────────────────────────────


def test_build_batches_seeds_public_entity_with_floor_rows() -> None:
    files = {
        "entities/Product.json": _entity_json(
            "Product",
            "public",
            {"title": {"type": "string"}, "price": {"type": "number"}},
        )
    }
    batches = demo_seed_writer._build_batches(PROJECT_ID, files)
    assert set(batches) == {"Product"}
    # Every seeded catalog clears the gate floor so the browse screen is full.
    assert len(batches["Product"]) >= demo_seeder.MIN_ROWS
    assert all("title" in row and "price" in row for row in batches["Product"])


def test_build_batches_seeds_public_and_admin_but_skips_owner() -> None:
    """Owner entities filter per-viewer — an empty "my items" is the correct
    first state, so they stay unseeded. Admin entities feed the operator's
    back-office dashboard, and the FIRST signup is now the admin operator
    (auth.roleForNewUser excludes the password-less demo owner) → admin rows
    DO show on first paint and must be seeded, or the dashboard hero reads 0."""
    files = {
        "entities/Task.json": _entity_json(
            "Task", "owner", {"title": {"type": "string"}}
        ),
        "entities/Order.json": _entity_json(
            "Order", "admin", {"amount": {"type": "number"}}
        ),
        "entities/Course.json": _entity_json(
            "Course", "public", {"title": {"type": "string"}}
        ),
    }
    batches = demo_seed_writer._build_batches(PROJECT_ID, files)
    assert set(batches) == {"Course", "Order"}


def test_build_batches_ignores_non_entity_and_fieldless_files() -> None:
    files = {
        "src/app/page.tsx": "export default function Page() {}",
        "entities/Empty.json": _entity_json("Empty", "public", {}),
        "package.json": "{}",
    }
    assert demo_seed_writer._build_batches(PROJECT_ID, files) == {}


def test_build_batches_survives_malformed_json() -> None:
    files = {
        "entities/Broken.json": "{ this is not json",
        "entities/Good.json": _entity_json(
            "Good", "public", {"name": {"type": "string"}}
        ),
    }
    batches = demo_seed_writer._build_batches(PROJECT_ID, files)
    assert set(batches) == {"Good"}


def test_build_batches_is_deterministic() -> None:
    files = {
        "entities/Item.json": _entity_json(
            "Item", "public", {"title": {"type": "string"}, "qty": {"type": "number"}}
        )
    }
    a = demo_seed_writer._build_batches(PROJECT_ID, files)
    b = demo_seed_writer._build_batches(PROJECT_ID, files)
    assert a == b


# ── seed_demo_data: delegation + fail-soft ───────────────────────────────────


async def test_seed_demo_data_delegates_public_batches(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    async def fake_seed(project_id: UUID, batches: dict[str, Any]) -> dict[str, int]:
        captured["project_id"] = project_id
        captured["batches"] = batches
        return {name: len(rows) for name, rows in batches.items()}

    monkeypatch.setattr(postgres_admin, "seed_public_records", fake_seed)
    files = {
        "entities/Listing.json": _entity_json(
            "Listing", "public", {"title": {"type": "string"}}
        )
    }
    result = await demo_seed_writer.seed_demo_data(PROJECT_ID, files)
    assert captured["project_id"] == PROJECT_ID
    assert set(captured["batches"]) == {"Listing"}
    assert result["Listing"] >= demo_seeder.MIN_ROWS


async def test_seed_demo_data_noop_without_public_entities(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    called = False

    async def fake_seed(*_a: Any, **_k: Any) -> dict[str, int]:
        nonlocal called
        called = True
        return {}

    monkeypatch.setattr(postgres_admin, "seed_public_records", fake_seed)
    files = {"entities/Task.json": _entity_json("Task", "owner", {"t": {"type": "string"}})}
    assert await demo_seed_writer.seed_demo_data(PROJECT_ID, files) == {}
    assert called is False  # writer is never hit when there's nothing to seed


async def test_seed_demo_data_swallows_writer_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def boom(*_a: Any, **_k: Any) -> dict[str, int]:
        raise RuntimeError("db down")

    monkeypatch.setattr(postgres_admin, "seed_public_records", boom)
    files = {
        "entities/P.json": _entity_json("P", "public", {"title": {"type": "string"}})
    }
    # Fail-soft: a writer error must not propagate into the hot-reload.
    assert await demo_seed_writer.seed_demo_data(PROJECT_ID, files) == {}


# ── postgres_admin.seed_public_records: SQL flow on a fake connection ─────────


class _FakeConn:
    """Records SQL calls and replays scripted return values for one txn."""

    def __init__(self, counts: dict[str, int]) -> None:
        self.counts = counts  # entity -> existing row count
        self.executed: list[str] = []
        self.inserted: dict[str, list[tuple[Any, ...]]] = {}
        self.role_set = False

    def transaction(self) -> _FakeConn:
        return self

    async def __aenter__(self) -> _FakeConn:
        return self

    async def __aexit__(self, *_exc: Any) -> bool:
        return False

    async def execute(self, sql: str, *_args: Any) -> str:
        self.executed.append(sql)
        if "SET LOCAL ROLE" in sql:
            self.role_set = True
        return "OK"

    async def fetchrow(self, sql: str, *args: Any) -> dict[str, Any]:
        # Demo-user upsert returns a fresh id on first insert.
        return {"id": UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")}

    async def fetchval(self, sql: str, *args: Any) -> int:
        return self.counts.get(args[0], 0)

    async def executemany(self, sql: str, rows: list[tuple[Any, ...]]) -> None:
        entity = rows[0][0]
        self.inserted[entity] = rows


class _FakePool:
    def __init__(self, conn: _FakeConn) -> None:
        self._conn = conn

    def acquire(self) -> _FakePool:
        return self

    async def __aenter__(self) -> _FakeConn:
        return self._conn

    async def __aexit__(self, *_exc: Any) -> bool:
        return False


async def test_seed_public_records_inserts_empty_catalog_as_project_role(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    conn = _FakeConn(counts={})  # all catalogs empty

    async def fake_pool() -> _FakePool:
        return _FakePool(conn)

    monkeypatch.setattr(postgres_admin, "_get_admin_pool", fake_pool)
    rows = [{"title": "Альфа 1"}, {"title": "Альфа 2"}]
    result = await postgres_admin.seed_public_records(PROJECT_ID, {"Product": rows})

    assert result == {"Product": 2}
    assert conn.role_set, "must assume the project role before writing"
    assert conn.role_set
    # The schema is qualified onto every statement (no search_path mutation).
    assert any('"proj_01234567_user"' in s for s in conn.executed)
    inserted = conn.inserted["Product"]
    assert len(inserted) == 2
    # Each insert carries (entity, json-string-data, owner-id).
    assert inserted[0][0] == "Product"
    assert json.loads(inserted[0][1]) == {"title": "Альфа 1"}


async def test_seed_public_records_skips_non_empty_catalog(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    conn = _FakeConn(counts={"Product": 4})  # user already has rows

    async def fake_pool() -> _FakePool:
        return _FakePool(conn)

    monkeypatch.setattr(postgres_admin, "_get_admin_pool", fake_pool)
    result = await postgres_admin.seed_public_records(
        PROJECT_ID, {"Product": [{"title": "x"}]}
    )
    assert result == {"Product": 0}
    assert "Product" not in conn.inserted  # idempotent: no clobber


async def test_seed_public_records_empty_batches_is_noop() -> None:
    assert await postgres_admin.seed_public_records(PROJECT_ID, {}) == {}

"""Read-only baseline: actual route handlers/models; external effects stay in memory."""

import importlib
import socket
import traceback
from datetime import UTC, datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import UUID

import pytest

PROJECT = UUID("00000000-0000-0000-0000-000000000001")
OWNER = UUID("00000000-0000-0000-0000-000000000002")
OLD = UUID("00000000-0000-0000-0000-000000000003")
NEW = UUID("00000000-0000-0000-0000-000000000004")
FIELDS = (
    "id",
    "project_id",
    "commit_sha",
    "prompt_text",
    "model_id",
    "parent_id",
    "preview_url",
    "is_rollback_target",
    "created_at",
)
CASES = [
    (None, None, None, None, datetime(2026, 9, 13, 12, tzinfo=UTC)),
    ("", "", "", None, datetime(2026, 9, 13, 12)),
    (
        "project/revision/index.html",
        "Привет",
        "example-model",
        OLD,
        datetime(2026, 9, 13, 12, 34, 56, 123456, tzinfo=timezone(timedelta(hours=3))),
    ),
    (
        "path with space/страница.html",
        "test",
        None,
        OLD,
        datetime(2026, 9, 13, 12, 34, 56, 123456, tzinfo=UTC),
    ),
]


@pytest.fixture
def modules(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://qa:qa@127.0.0.1:1/qa")
    monkeypatch.setenv("JWT_SECRET", "disposable-characterization-secret")
    original_connect = socket.socket.connect

    def connect(sock, address):
        # Windows asyncio implements its internal socketpair through loopback TCP.
        if any(
            frame.name in {"socketpair", "_fallback_socketpair"}
            and frame.filename.endswith("socket.py")
            for frame in traceback.extract_stack()
        ):
            return original_connect(sock, address)
        pytest.fail("External network is forbidden")

    monkeypatch.setattr(socket.socket, "connect", connect)
    result = {
        name: importlib.import_module("omnia_api.routers." + name)
        for name in ("rollback", "snapshots")
    }
    result["generation_publication"] = importlib.import_module(
        "omnia_api.services.generation.publication"
    )
    from omnia_api.core import minio

    settings = SimpleNamespace(
        minio_public_url="https://objects.example.test/", minio_bucket_previews="previews"
    )
    monkeypatch.setattr(minio, "get_settings", lambda: settings)
    return result


def snapshot(case):
    from omnia_api.models.snapshot import Snapshot

    preview, prompt, model, parent, created = case
    return Snapshot(
        id=NEW,
        project_id=PROJECT,
        commit_sha="a" * 40,
        prompt_text=prompt,
        model_id=model,
        parent_id=parent,
        preview_key=preview,
        is_rollback_target=False,
        created_at=created,
    )


def expected(row):
    return {
        "id": row.id,
        "project_id": row.project_id,
        "commit_sha": row.commit_sha,
        "prompt_text": row.prompt_text,
        "model_id": row.model_id,
        "parent_id": row.parent_id,
        "preview_url": (
            "https://objects.example.test/previews/" + row.preview_key if row.preview_key else None
        ),
        "is_rollback_target": row.is_rollback_target,
        "created_at": row.created_at,
    }


@pytest.mark.parametrize(
    "case", CASES, ids=["null-utc", "empty-naive", "full-offset", "unicode-utc"]
)
def test_existing_serializers_and_json_contract(modules, case):
    from omnia_api.schemas.snapshot import SnapshotPublic

    row = snapshot(case)
    value = expected(row)
    reference = SnapshotPublic.model_validate(value)
    for name in ("rollback", "snapshots"):
        function = getattr(
            modules[name], "_public_dict" if name == "snapshots" else "_snapshot_dict"
        )
        raw = function(row)
        assert tuple(raw) == FIELDS
        assert raw == value
        assert raw["id"] is row.id and raw["created_at"] is row.created_at
        assert SnapshotPublic.model_validate(raw).model_dump_json() == reference.model_dump_json()
    event = modules["generation_publication"]._snapshot_payload(row)
    assert event == {
        **value,
        "id": str(NEW),
        "project_id": str(PROJECT),
        "parent_id": str(row.parent_id) if row.parent_id else None,
        "created_at": row.created_at.isoformat(),
    }
    if row.created_at.tzinfo == UTC:
        assert event["created_at"].endswith("+00:00")
        assert reference.model_dump(mode="json")["created_at"].endswith("Z")


@pytest.mark.parametrize("name", ["rollback"])
@pytest.mark.parametrize("case", [CASES[0], CASES[2]], ids=["null-preview-utc", "preview-offset"])
async def test_actual_mutation_consumers_preserve_response_and_event(
    modules, monkeypatch, name, case
):
    from omnia_api.schemas.snapshot import RollbackRequest, SnapshotPublic

    module = modules[name]
    row = snapshot(case)
    row.id = OLD
    project = SimpleNamespace(
        id=PROJECT, owner_id=OWNER, template="blank", slug="qa", current_snapshot_id=OLD
    )
    added = []

    async def get(_model, identity):
        return project if identity == PROJECT else row

    async def flush():
        for new in added:
            new.id, new.created_at, new.preview_key = NEW, case[4], case[0]
            new.is_rollback_target = False

    session = SimpleNamespace(
        get=get, add=added.append, flush=flush, commit=AsyncMock(), refresh=AsyncMock()
    )
    events = AsyncMock()
    monkeypatch.setattr(module, "publish_event", events)
    monkeypatch.setattr(module, "enqueue_preview", Mock())
    monkeypatch.setattr(
        module.repo_svc,
        "read_files",
        Mock(
            return_value={
                "index.html": '<html><head></head><body><img src="old.png"></body></html>',
            }
        ),
    )
    monkeypatch.setattr(module.repo_svc, "commit_files", Mock(return_value="b" * 40))
    monkeypatch.setattr(module.repo_svc, "checkout", Mock(return_value="b" * 40))
    monkeypatch.setattr(module, "record_restored_version", AsyncMock())
    response = await module.post_rollback(
        PROJECT, RollbackRequest(snapshot_id=OLD), session, SimpleNamespace(id=OWNER)
    )
    assert len(added) == 1
    assert (
        response.model_dump_json()
        == SnapshotPublic.model_validate(expected(added[0])).model_dump_json()
    )
    assert events.await_count == 1
    event_project, event_type, envelope = events.await_args.args
    assert event_project == PROJECT and event_type == "snapshot.created"
    assert envelope["snapshot"]["created_at"] == case[4].isoformat()
    assert envelope["snapshot"]["preview_url"] == expected(added[0])["preview_url"]
    assert envelope["snapshot"]["parent_id"] == str(OLD)


@pytest.mark.parametrize("case", [CASES[0], CASES[2]], ids=["null-preview-utc", "preview-offset"])
async def test_actual_list_and_get_consumers(modules, monkeypatch, case):
    module = modules["snapshots"]
    row = snapshot(case)
    project = SimpleNamespace(id=PROJECT, owner_id=OWNER)

    async def get(_model, identity):
        return project if identity == PROJECT else row

    session = SimpleNamespace(
        get=get,
        execute=AsyncMock(
            return_value=SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: [row]))
        ),
    )
    monkeypatch.setattr(module.repo_svc, "read_files", Mock(return_value={"index.html": "fixture"}))
    listed = await module.list_snapshots(PROJECT, session, SimpleNamespace(id=OWNER))
    detailed = await module.get_snapshot(PROJECT, NEW, session, SimpleNamespace(id=OWNER))
    assert listed[0].model_dump() == expected(row)
    assert detailed.model_dump() == {**expected(row), "files": {"index.html": "fixture"}}


@pytest.mark.parametrize("case", [CASES[0], CASES[2]])
async def test_restoration_consumer_keeps_snapshot_response(modules, case):
    from omnia_api.routers import restorations
    from omnia_api.schemas.restoration import RestoreOperation
    from omnia_api.schemas.snapshot import SnapshotPublic

    row = snapshot(case)
    operation = RestoreOperation(
        id=OLD,
        project_id=PROJECT,
        source_version_id=OLD,
        source_snapshot_id=OLD,
        base_draft_snapshot_id=OLD,
        state="completed",
        phase="completed",
        updated_at=row.created_at,
        revision=1,
        candidate_id=NEW,
        execution_policy="manual",
        selected_branch=None,
        adaptation_run_id=None,
        report=None,
        can_apply=False,
        can_cancel=False,
        applied_version=NEW,
        applied_snapshot_id=NEW,
        error=None,
    )
    session = SimpleNamespace(get=AsyncMock(return_value=row))
    result = await restorations._with_snapshot(session, operation)
    assert result.applied_snapshot.model_dump_json() == (
        SnapshotPublic.model_validate(expected(row)).model_dump_json()
    )
    assert operation.applied_snapshot is None

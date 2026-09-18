"""Characterisation of the four no-LLM page edits in ``routers/uploads.py``.

Frozen BEFORE their shared "read the current page" / "commit the edit as a
snapshot" halves got one owner, unchanged AFTER. Actual route handlers and
models; git, queue and pub/sub stay in memory.
"""

from __future__ import annotations

import threading
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, Mock
from uuid import UUID

import pytest

from omnia_api.core.errors import ApiError
from omnia_api.routers import uploads
from omnia_api.schemas.snapshot import SnapshotPublic
from omnia_api.schemas.upload import (
    ElementDeleteRequest,
    ElementMoveRequest,
    ImagePatchRequest,
    TextPatchRequest,
)

PROJECT = UUID("00000000-0000-0000-0000-000000000001")
OWNER = UUID("00000000-0000-0000-0000-000000000002")
OLD = UUID("00000000-0000-0000-0000-000000000003")
NEW = UUID("00000000-0000-0000-0000-000000000004")
PARENT_SHA = "a" * 40
NEW_SHA = "b" * 40
CREATED = datetime(2026, 9, 19, 12, 34, 56, tzinfo=UTC)
ASSET = "https://objects.example.test/uploads/new.png"
PAGE = '<html><body><h1> Привет </h1><img src="old.png"><p>A</p><p>B</p><p>A</p></body></html>'

EDITS = {
    "image": (
        uploads.image_patch,
        ImagePatchRequest(old_src="old.png", new_src=ASSET),
        PAGE.replace("old.png", ASSET),
        "image: своя картинка",
        "(своя картинка)",
    ),
    "text": (
        uploads.text_patch,
        TextPatchRequest(old_text="Привет", new_text="Добрый день & <b>"),
        PAGE.replace("<h1> Привет </h1>", "<h1> Добрый день &amp; &lt;b&gt; </h1>"),
        "text: правка текста",
        "(правка текста)",
    ),
    "text-second-occurrence": (
        uploads.text_patch,
        TextPatchRequest(old_text="A", new_text="C", index=1),
        PAGE.replace("<p>B</p><p>A</p>", "<p>B</p><p>C</p>"),
        "text: правка текста",
        "(правка текста)",
    ),
    "delete-second-occurrence": (
        uploads.element_delete,
        ElementDeleteRequest(outer_html="<p>A</p>", index=1),
        PAGE.replace("<p>B</p><p>A</p>", "<p>B</p>"),
        "element: жёсткое удаление",
        "(удаление элемента)",
    ),
    "move": (
        uploads.element_move,
        ElementMoveRequest(a_html="<p>B</p>", b_html='<img src="old.png">'),
        PAGE.replace(
            '<img src="old.png"><p>A</p><p>B</p>', '<p>B</p><p>A</p><img src="old.png">'
        ),
        "element: перемещение",
        "(перемещение элемента)",
    ),
}
ENDPOINTS = {
    name: EDITS[name][:2] for name in ("image", "text", "delete-second-occurrence", "move")
}


class World:
    """In-memory project, session and side-effect recorders for one request."""

    def __init__(
        self,
        monkeypatch: pytest.MonkeyPatch,
        files: dict[str, str],
        *,
        preview_key: str | None = None,
    ) -> None:
        self.order: list[str] = []
        self.threads: dict[str, int] = {}
        self.pointer_at_commit: UUID | None = None
        self.preview_key = preview_key
        self.added: list[Any] = []
        self.project = SimpleNamespace(
            id=PROJECT, owner_id=OWNER, template="blank", slug="qa", current_snapshot_id=OLD
        )
        self.current: Any = SimpleNamespace(id=OLD, commit_sha=PARENT_SHA)

        settings = SimpleNamespace(
            minio_public_url="https://objects.example.test/", minio_bucket_previews="previews"
        )
        from omnia_api.core import minio

        monkeypatch.setattr(minio, "get_settings", lambda: settings)
        monkeypatch.setattr(uploads, "get_settings", lambda: settings)

        self.read_files = Mock(side_effect=self._step("read", files))
        self.commit_files = Mock(side_effect=self._step("git-commit", NEW_SHA))
        self.enqueue = Mock(side_effect=self._step("preview", None))
        self.events = AsyncMock(side_effect=self._step("event", None))
        monkeypatch.setattr(uploads.repo_svc, "read_files", self.read_files)
        monkeypatch.setattr(uploads.repo_svc, "commit_files", self.commit_files)
        monkeypatch.setattr(uploads, "enqueue_preview", self.enqueue)
        monkeypatch.setattr(uploads, "publish_event", self.events)

        self.session = SimpleNamespace(
            get=self._get,
            add=self._add,
            flush=self._flush,
            commit=AsyncMock(side_effect=self._commit),
            refresh=AsyncMock(side_effect=self._step("refresh", None)),
        )

    def _step(self, name: str, value: Any):
        def record(*_args: Any, **_kwargs: Any) -> Any:
            self.order.append(name)
            self.threads[name] = threading.get_ident()
            return value

        return record

    def _commit(self) -> None:
        self.order.append("db-commit")
        self.pointer_at_commit = self.project.current_snapshot_id

    async def _get(self, _model: Any, identity: UUID) -> Any:
        return self.project if identity == PROJECT else self.current

    def _add(self, row: Any) -> None:
        self.order.append("add")
        self.added.append(row)

    async def _flush(self) -> None:
        self.order.append("flush")
        for row in self.added:
            row.id, row.created_at, row.preview_key = NEW, CREATED, self.preview_key
            row.is_rollback_target = False

    async def call(self, handler: Any, payload: Any, user: UUID = OWNER) -> SnapshotPublic:
        return await handler(PROJECT, payload, self.session, SimpleNamespace(id=user))


@pytest.mark.parametrize("preview_key", [None, "previews/new.png"], ids=["fresh", "rendered"])
@pytest.mark.parametrize("name", list(EDITS))
async def test_edit_commits_the_page_as_a_snapshot_and_announces_it(
    name, preview_key, monkeypatch
):
    handler, payload, new_page, message, prompt_text = EDITS[name]
    world = World(
        monkeypatch,
        {"index.html": PAGE, "about.html": "<p>о нас</p>"},
        preview_key=preview_key,
    )

    response = await world.call(handler, payload)

    world.read_files.assert_called_once_with(PROJECT, PARENT_SHA)
    world.commit_files.assert_called_once_with(
        PROJECT, {"index.html": new_page}, message, PARENT_SHA
    )
    (row,) = world.added
    assert (row.project_id, row.commit_sha, row.prompt_text, row.model_id, row.parent_id) == (
        PROJECT,
        NEW_SHA,
        prompt_text,
        None,
        OLD,
    )
    assert world.project.current_snapshot_id == NEW
    assert world.pointer_at_commit == NEW, "the new pointer must be part of the transaction"
    world.session.refresh.assert_awaited_once_with(row)
    world.enqueue.assert_called_once_with(NEW)
    assert world.order == [
        "read", "git-commit", "add", "flush", "db-commit", "refresh", "preview", "event"
    ]
    # git, object storage and the queue are blocking: they stay off the event loop.
    loop_thread = threading.get_ident()
    assert all(world.threads[step] != loop_thread for step in ("read", "git-commit", "preview"))

    preview_url = (
        None if preview_key is None else "https://objects.example.test/previews/" + preview_key
    )
    world.events.assert_awaited_once_with(
        PROJECT,
        "snapshot.created",
        {
            "snapshot": {
                "id": str(NEW),
                "project_id": str(PROJECT),
                "commit_sha": NEW_SHA,
                "prompt_text": prompt_text,
                "model_id": None,
                "parent_id": str(OLD),
                "preview_url": preview_url,
                "is_rollback_target": False,
                "created_at": CREATED.isoformat(),
            }
        },
    )
    assert response == SnapshotPublic(
        id=NEW,
        project_id=PROJECT,
        commit_sha=NEW_SHA,
        prompt_text=prompt_text,
        model_id=None,
        parent_id=OLD,
        preview_url=preview_url,
        is_rollback_target=False,
        created_at=CREATED,
    )


@pytest.mark.parametrize("name", list(ENDPOINTS))
async def test_edit_targets_the_first_index_candidate(name, monkeypatch):
    handler, payload = ENDPOINTS[name]
    files = {"dist/index.html": "<p>сборка</p>", "public/index.html": PAGE}
    world = World(monkeypatch, files)

    await world.call(handler, payload)

    (_project, written, _message, _parent), _ = world.commit_files.call_args
    assert list(written) == ["public/index.html"]


@pytest.mark.parametrize("name", list(ENDPOINTS))
async def test_a_failed_preview_job_is_not_swallowed(name, monkeypatch):
    handler, payload = ENDPOINTS[name]
    world = World(monkeypatch, {"index.html": PAGE})
    world.enqueue.side_effect = RuntimeError("queue is down")

    with pytest.raises(RuntimeError, match="queue is down"):
        await world.call(handler, payload)

    # The edit itself is already durable; only the announcement did not happen.
    assert world.project.current_snapshot_id == NEW and world.pointer_at_commit == NEW
    assert world.events.await_count == 0


async def _rejected(world: World, handler: Any, payload: Any, **kwargs: Any) -> ApiError:
    with pytest.raises(ApiError) as caught:
        await world.call(handler, payload, **kwargs)
    assert world.added == [] and world.commit_files.call_count == 0
    assert world.events.await_count == 0 and world.enqueue.call_count == 0
    assert world.project.current_snapshot_id in (OLD, None)
    return caught.value


@pytest.mark.parametrize("name", list(ENDPOINTS))
async def test_edit_is_refused_for_a_foreign_project(name, monkeypatch):
    handler, payload = ENDPOINTS[name]
    world = World(monkeypatch, {"index.html": PAGE})

    error = await _rejected(world, handler, payload, user=UUID(int=99))

    assert (error.code, error.message, error.status_code) == ("not_found", "project not found", 404)
    assert world.read_files.call_count == 0


@pytest.mark.parametrize("name", list(ENDPOINTS))
async def test_edit_needs_a_current_snapshot(name, monkeypatch):
    handler, payload = ENDPOINTS[name]
    world = World(monkeypatch, {"index.html": PAGE})
    world.project.current_snapshot_id = None

    error = await _rejected(world, handler, payload)

    assert (error.code, error.message, error.status_code) == (
        "no_snapshot",
        "project has no snapshot to edit",
        400,
    )
    assert world.read_files.call_count == 0


@pytest.mark.parametrize("name", list(ENDPOINTS))
async def test_edit_needs_the_snapshot_row(name, monkeypatch):
    handler, payload = ENDPOINTS[name]
    world = World(monkeypatch, {"index.html": PAGE})
    world.current = None

    error = await _rejected(world, handler, payload)

    assert (error.code, error.message, error.status_code) == (
        "no_snapshot",
        "current snapshot missing",
        400,
    )
    assert world.read_files.call_count == 0


@pytest.mark.parametrize("name", list(ENDPOINTS))
async def test_edit_needs_a_static_index(name, monkeypatch):
    handler, payload = ENDPOINTS[name]
    world = World(monkeypatch, {"app/page.tsx": "export default function Page() {}"})

    error = await _rejected(world, handler, payload)

    assert (error.code, error.message, error.status_code) == (
        "no_index",
        "this project has no static index.html to edit",
        400,
    )


@pytest.mark.parametrize(
    ("name", "payload", "code"),
    [
        ("image", ImagePatchRequest(old_src="old.png", new_src="https://evil.test/x"), "bad_src"),
        ("image", ImagePatchRequest(old_src=ASSET, new_src=ASSET), "empty_patch"),
        ("text", TextPatchRequest(old_text="Привет", new_text="Привет"), "empty_patch"),
    ],
)
async def test_request_checks_come_before_the_snapshot_lookup(name, payload, code, monkeypatch):
    world = World(monkeypatch, {"index.html": PAGE})
    world.project.current_snapshot_id = None

    error = await _rejected(world, EDITS[name][0], payload)

    assert error.code == code and error.status_code == 400


@pytest.mark.parametrize(
    ("name", "payload", "code"),
    [
        ("image", ImagePatchRequest(old_src="missing.png", new_src=ASSET), "src_not_found"),
        ("text", TextPatchRequest(old_text="нет такого", new_text="x"), "text_not_found"),
        (
            "delete-second-occurrence",
            ElementDeleteRequest(outer_html="<p>Z</p>"),
            "element_not_found",
        ),
        ("move", ElementMoveRequest(a_html="<p>Z</p>", b_html="<p>B</p>"), "element_not_found"),
        ("move", ElementMoveRequest(a_html="<p>B</p>", b_html="<p>B</p>"), "empty_patch"),
        (
            "move",
            ElementMoveRequest(a_html="<body><h1> Привет </h1>", b_html="<h1> Привет </h1>"),
            "overlap",
        ),
    ],
)
async def test_edit_that_does_not_apply_changes_nothing(name, payload, code, monkeypatch):
    world = World(monkeypatch, {"index.html": PAGE})

    error = await _rejected(world, EDITS[name][0], payload)

    assert error.code == code and error.status_code == 400

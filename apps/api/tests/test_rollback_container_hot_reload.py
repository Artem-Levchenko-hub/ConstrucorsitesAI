"""Rollback admission: apply before Git, explicit errors, durable restored versions."""

from __future__ import annotations

import asyncio
import datetime as _dt
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from omnia_api.core.errors import ApiError
from omnia_api.models.snapshot import Snapshot
from omnia_api.routers import rollback as rollback_mod
from omnia_api.schemas.snapshot import RollbackRequest
from omnia_api.services import snapshot_restore as restore_mod

_OWNER = uuid.uuid4()
_PROJECT_ID = uuid.uuid4()
_TARGET_SNAP = uuid.uuid4()
_REVERTED_FILES = {
    "src/app/page.tsx": 'title="Управляйте ремзоной без хаоса"',
    "src/app/(app)/layout.tsx": "export default function Layout(){return null}",
}


class _FakeSession:
    """Minimal async session: get() dispatches by model, mutations are no-ops,
    refresh() stamps created_at so the response/publish path doesn't crash."""

    def __init__(self, project, target_snap):
        self._project = project
        self._target = target_snap
        self._added: list = []
        self.committed = False

    async def get(self, model, ident):
        if model.__name__ == "Project":
            return self._project
        if model.__name__ == "Snapshot":
            return self._target if ident == _TARGET_SNAP else None
        return None

    def add(self, obj):  # sync in SQLAlchemy
        self._added.append(obj)

    async def flush(self):
        # Mimic the DB applying column defaults on flush (id default=uuid4,
        # is_rollback_target default=False) — the handler reads them afterwards.
        for obj in self._added:
            if getattr(obj, "id", None) is None:
                obj.id = uuid.uuid4()
            if getattr(obj, "is_rollback_target", None) is None:
                obj.is_rollback_target = False

    async def commit(self):
        self.committed = True

    async def refresh(self, obj):
        if getattr(obj, "created_at", None) is None:
            obj.created_at = _dt.datetime(2026, 6, 17, 1, 0, 0)


def _make_project(template: str):
    return SimpleNamespace(
        id=_PROJECT_ID,
        owner_id=_OWNER,
        slug="dogfood-rollback-crm-2af92e",
        template=template,
        current_snapshot_id=uuid.uuid4(),
    )


def _make_target():
    snap = Snapshot(
        project_id=_PROJECT_ID,
        commit_sha="9747759c" * 5,
        prompt_text="CRM для записи клиентов автосервиса",
    )
    snap.id = _TARGET_SNAP
    snap.created_at = _dt.datetime(2026, 6, 17, 0, 59, 0)
    snap.is_rollback_target = False
    return snap


def _patch_common(monkeypatch, hot_calls, *, hot_reload_raises=False):
    monkeypatch.setattr(rollback_mod.repo_svc, "checkout", lambda pid, sha: "rolledbacksha")
    monkeypatch.setattr(rollback_mod.repo_svc, "read_files", lambda pid, sha: dict(_REVERTED_FILES))

    async def _hot_reload(*, project_id, slug, files, empty_files=()):
        if hot_reload_raises:
            raise RuntimeError("orchestrator down")
        hot_calls.append({"project_id": project_id, "slug": slug, "files": files})
        return {"written": len(files)}

    monkeypatch.setattr(restore_mod.orchestrator_client, "hot_reload", _hot_reload)
    monkeypatch.setattr(rollback_mod, "enqueue_preview", lambda sid: None)
    monkeypatch.setattr(rollback_mod, "record_restored_version", AsyncMock())

    async def _publish(*a, **k):
        return None

    monkeypatch.setattr(rollback_mod, "publish_event", _publish)
    monkeypatch.setattr(rollback_mod, "preview_public_url", lambda key: None)


def _run_rollback(project):
    session = _FakeSession(project, _make_target())
    user = SimpleNamespace(id=_OWNER)
    payload = RollbackRequest(snapshot_id=_TARGET_SNAP)
    result = asyncio.run(rollback_mod.post_rollback(_PROJECT_ID, payload, session, user))
    return session, result


@pytest.mark.parametrize("template", ["nextjs_entities", "fullstack", "spa"])
def test_container_rollback_hot_reloads_reverted_tree(monkeypatch, template):
    """A container rollback MUST push the rolled-back tree into the live dev
    container, else the preview keeps serving the post-edit code (the BS-42 bug)."""
    hot_calls: list = []
    _patch_common(monkeypatch, hot_calls)

    session, _ = _run_rollback(_make_project(template))

    assert session.committed is True
    assert len(hot_calls) == 1, "rollback did not hot_reload the container"
    call = hot_calls[0]
    assert call["project_id"] == _PROJECT_ID
    assert call["slug"] == "dogfood-rollback-crm-2af92e"
    # The reverted (state A) tree, not the post-edit one.
    assert call["files"]["src/app/page.tsx"] == _REVERTED_FILES["src/app/page.tsx"]


@pytest.mark.parametrize("template", ["blank", "landing", "portfolio", "blog"])
def test_static_rollback_does_not_hot_reload(monkeypatch, template):
    """Static templates have no persistent container — their preview re-renders
    from repo files, so rollback must NOT attempt a hot_reload."""
    hot_calls: list = []
    _patch_common(monkeypatch, hot_calls)

    session, _ = _run_rollback(_make_project(template))

    assert session.committed is True
    assert hot_calls == [], "static rollback should not touch any container"


def test_hot_reload_failure_blocks_head_and_snapshot(monkeypatch):
    hot_calls: list = []
    _patch_common(monkeypatch, hot_calls, hot_reload_raises=True)
    checkout = Mock()
    monkeypatch.setattr(rollback_mod.repo_svc, "checkout", checkout)
    project = _make_project("nextjs_entities")
    previous = project.current_snapshot_id
    session = _FakeSession(project, _make_target())
    with pytest.raises(ApiError):
        asyncio.run(
            rollback_mod.post_rollback(
                _PROJECT_ID,
                RollbackRequest(snapshot_id=_TARGET_SNAP),
                session,
                SimpleNamespace(id=_OWNER),
            )
        )
    assert not session.committed and not session._added
    assert project.current_snapshot_id == previous
    checkout.assert_not_called()
    rollback_mod.record_restored_version.assert_not_called()


def test_rollback_snapshot_is_a_user_visible_version(monkeypatch):
    """A deliberate restore is version history; technical snapshots are not."""
    hot_calls: list = []
    _patch_common(monkeypatch, hot_calls)

    _, result = _run_rollback(_make_project("spa"))

    assert result.prompt_text == "Восстановление версии"


def test_with_rollback_deletions_pure() -> None:
    """Orphans (old-tree-only paths) become delete-intents (""); target content
    always wins over a same-path delete; no old tree → target unchanged."""
    target = {"a.ts": "A", "b.ts": "B"}
    old = {"a.ts": "old-A", "b.ts": "old-B", "phantom.ts": "junk"}
    out = rollback_mod.with_rollback_deletions(target, old)
    assert out == {"a.ts": "A", "b.ts": "B", "phantom.ts": ""}
    assert rollback_mod.with_rollback_deletions(target, {}) == target


def test_rollback_deletes_files_absent_from_reverted_tree(monkeypatch):
    """BS-42 follow-up LOCKED (was strict-xfail): the rollback push now carries
    delete-intents (empty content → write_files does `rm -f`) for files present
    in the PRE-rollback tree but absent from the target tree. Without this, a
    file created after the target snapshot survives the rollback inside the
    container (2026-07-08 live: a failed build's phantom modules outlived a
    rollback and re-poisoned the retry build exactly this way)."""
    current_snap_id = uuid.uuid4()
    project = SimpleNamespace(
        id=_PROJECT_ID,
        owner_id=_OWNER,
        slug="dogfood-rollback-crm-2af92e",
        template="nextjs_entities",
        current_snapshot_id=current_snap_id,
    )
    current_snap = SimpleNamespace(commit_sha="oldsha1234")

    class _Session(_FakeSession):
        async def get(self, model, ident):
            if model.__name__ == "Snapshot" and ident == current_snap_id:
                return current_snap
            return await super().get(model, ident)

    def _read(pid, sha):
        if sha == "9747759c" * 5:
            return dict(_REVERTED_FILES)  # target tree
        # pre-rollback tree: same files + an orphan the failed edit created
        return {**_REVERTED_FILES, "src/lib/items.ts": "broken phantom module"}

    monkeypatch.setattr(rollback_mod.repo_svc, "checkout", lambda pid, sha: "rolledbacksha")
    monkeypatch.setattr(rollback_mod.repo_svc, "read_files", _read)

    captured: dict = {}

    async def _hot_reload(*, project_id, slug, files, empty_files=()):
        captured.update(files)
        return {"written": len(files)}

    monkeypatch.setattr(restore_mod.orchestrator_client, "hot_reload", _hot_reload)
    monkeypatch.setattr(rollback_mod, "enqueue_preview", lambda sid: None)
    monkeypatch.setattr(rollback_mod, "record_restored_version", AsyncMock())

    async def _publish(*a, **k):
        return None

    monkeypatch.setattr(rollback_mod, "publish_event", _publish)
    monkeypatch.setattr(rollback_mod, "preview_public_url", lambda key: None)

    session = _Session(project, _make_target())
    user = SimpleNamespace(id=_OWNER)
    result = asyncio.run(
        rollback_mod.post_rollback(
            _PROJECT_ID, RollbackRequest(snapshot_id=_TARGET_SNAP), session, user
        )
    )
    assert result is not None
    assert captured["src/lib/items.ts"] == ""  # orphan → delete-intent
    # target content always wins — never overwritten by a delete
    assert "src/app/page.tsx" not in captured  # unchanged files need no migration-triggering replay


def test_max_restore_never_mutates_repo_runtime_or_versions(monkeypatch):
    _patch_common(monkeypatch, [])
    checkout, read = Mock(), Mock()
    apply = AsyncMock()
    monkeypatch.setattr(rollback_mod.repo_svc, "checkout", checkout)
    monkeypatch.setattr(rollback_mod.repo_svc, "read_files", read)
    monkeypatch.setattr(restore_mod.orchestrator_client, "hot_reload", apply)
    project = _make_project("max_miniapp")
    previous = project.current_snapshot_id
    session = _FakeSession(project, _make_target())
    with pytest.raises(ApiError) as caught:
        asyncio.run(
            rollback_mod.post_rollback(
                _PROJECT_ID,
                RollbackRequest(snapshot_id=_TARGET_SNAP),
                session,
                SimpleNamespace(id=_OWNER),
            )
        )
    assert caught.value.status_code == 409
    assert not session.committed and not session._added
    assert project.current_snapshot_id == previous
    checkout.assert_not_called()
    read.assert_not_called()
    apply.assert_not_called()
    rollback_mod.record_restored_version.assert_not_called()


def test_static_restore_records_a_durable_version(monkeypatch):
    _patch_common(monkeypatch, [])
    session, result = _run_rollback(_make_project("blank"))
    rollback_mod.record_restored_version.assert_awaited_once()
    assert session.committed and result.prompt_text == "Восстановление версии"

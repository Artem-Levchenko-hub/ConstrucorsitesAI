"""Допуск к восстановлению версии: приложение MAX отказывает до любых действий."""

from __future__ import annotations

import asyncio
import datetime as _dt
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from yleum_api.core.errors import ApiError
from yleum_api.models.snapshot import Snapshot
from yleum_api.routers import rollback as rollback_mod
from yleum_api.schemas.snapshot import RollbackRequest

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


def _patch_common(monkeypatch):
    monkeypatch.setattr(rollback_mod.repo_svc, "checkout", lambda pid, sha: "rolledbacksha")
    monkeypatch.setattr(rollback_mod.repo_svc, "read_files", lambda pid, sha: dict(_REVERTED_FILES))
    monkeypatch.setattr(rollback_mod, "record_restored_version", AsyncMock())

    async def _publish(*a, **k):
        return None

    monkeypatch.setattr(rollback_mod, "publish_event", _publish)


def test_max_restore_never_mutates_repo_runtime_or_versions(monkeypatch):
    _patch_common(monkeypatch)
    checkout, read = Mock(), Mock()
    monkeypatch.setattr(rollback_mod.repo_svc, "checkout", checkout)
    monkeypatch.setattr(rollback_mod.repo_svc, "read_files", read)
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
    rollback_mod.record_restored_version.assert_not_called()


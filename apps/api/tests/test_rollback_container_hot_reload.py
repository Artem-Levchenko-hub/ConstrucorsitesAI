"""Acceptance-lock for BS-42 (run #42, 2026-06-17).

**Blind spot:** the flagship "вернуться назад" / "откат за 1 sec" feature
(`POST /api/projects/{id}/rollback`, routers/rollback.py) is a **no-op on the
live preview for container-backed apps**. The handler reverts the git repo
(`repo_svc.checkout`) and enqueues a fresh screenshot (`enqueue_preview`) but —
unlike the build (messages.py:3683), edit, and style-patch (style_patch.py:209)
paths — it never pushed the reverted files into the running dev container via
`orchestrator_client.hot_reload`. Container apps (nextjs_entities / fullstack /
spa) serve their preview from `omnia-dev-<slug>` (ingress.py:43), so after a
rollback the git repo holds the OLD version while the container keeps serving the
post-edit code. Static templates (blank/landing/portfolio/blog) re-render the
preview from repo files and roll back correctly — the gap is container-only.

**Live repro (run #42, prod, dogfood-rollback-crm-2af92e):** blank → nextjs_entities
(BS-4). State A hero `title="Управляйте ремзоной без хаоса"`. A follow-up edit
hot-reloaded state B (`title="ДОГФУД-Б-МАРКЕР сервис на потоке"`) into the live
container. `POST /rollback` to snapshot A returned 200 OK; the rollback snapshot's
git tree held state A ("Управляйте ремзоной без хаоса"), but the live container's
`src/app/page.tsx` STILL served state B ("ДОГФУД-Б-МАРКЕР") — git and the running
app diverged. No `hot_reload` line in the api log for the rollback.

**Fix (shipped this run):** rollback.py now, for `_CONTAINER_NEXT` templates,
reads the rolled-back tree (`repo_svc.read_files(project_id, new_sha)`) and pushes
it into the dev container via `orchestrator_client.hot_reload` — best-effort
(R-10), exact parity with the style-patch path. Static templates are untouched.

These tests LOCK: (1) a container rollback calls hot_reload with the reverted
tree; (2) a static rollback does NOT; (3) a hot_reload failure never blocks the
rollback (best-effort); (4) deletion-aware sync (was strict-xfail, SHIPPED
2026-07-08): orphans — files in the pre-rollback tree but not the target tree —
ride along as empty-content delete-intents, which write_files turns into `rm -f`
in the container, so phantom files can't survive a rollback anymore.
"""
from __future__ import annotations

import asyncio
import datetime as _dt
import uuid
from types import SimpleNamespace

import pytest

from omnia_api.models.snapshot import Snapshot
from omnia_api.routers import rollback as rollback_mod
from omnia_api.schemas.snapshot import RollbackRequest

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
    monkeypatch.setattr(
        rollback_mod.repo_svc, "checkout", lambda pid, sha: "rolledbacksha"
    )
    monkeypatch.setattr(
        rollback_mod.repo_svc, "read_files", lambda pid, sha: dict(_REVERTED_FILES)
    )

    async def _hot_reload(*, project_id, slug, files):
        if hot_reload_raises:
            raise RuntimeError("orchestrator down")
        hot_calls.append({"project_id": project_id, "slug": slug, "files": files})
        return {"written": len(files)}

    monkeypatch.setattr(rollback_mod.orchestrator_client, "hot_reload", _hot_reload)
    monkeypatch.setattr(rollback_mod, "enqueue_preview", lambda sid: None)

    async def _publish(*a, **k):
        return None

    monkeypatch.setattr(rollback_mod, "publish_event", _publish)
    monkeypatch.setattr(rollback_mod, "preview_public_url", lambda key: None)


def _run_rollback(project):
    session = _FakeSession(project, _make_target())
    user = SimpleNamespace(id=_OWNER)
    payload = RollbackRequest(snapshot_id=_TARGET_SNAP)
    result = asyncio.run(
        rollback_mod.post_rollback(_PROJECT_ID, payload, session, user)
    )
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


def test_hot_reload_failure_never_blocks_rollback(monkeypatch):
    """Best-effort (R-10): a down orchestrator must not fail the rollback — git +
    snapshot are already the canonical state."""
    hot_calls: list = []
    _patch_common(monkeypatch, hot_calls, hot_reload_raises=True)

    session, result = _run_rollback(_make_project("nextjs_entities"))

    assert session.committed is True, "rollback was blocked by a hot_reload failure"
    assert result is not None


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
        if sha == "rolledbacksha":
            return dict(_REVERTED_FILES)  # target tree
        # pre-rollback tree: same files + an orphan the failed edit created
        return {**_REVERTED_FILES, "src/lib/items.ts": "broken phantom module"}

    monkeypatch.setattr(
        rollback_mod.repo_svc, "checkout", lambda pid, sha: "rolledbacksha"
    )
    monkeypatch.setattr(rollback_mod.repo_svc, "read_files", _read)

    captured: dict = {}

    async def _hot_reload(*, project_id, slug, files):
        captured.update(files)
        return {"written": len(files)}

    monkeypatch.setattr(rollback_mod.orchestrator_client, "hot_reload", _hot_reload)
    monkeypatch.setattr(rollback_mod, "enqueue_preview", lambda sid: None)

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
    assert captured["src/app/page.tsx"] == _REVERTED_FILES["src/app/page.tsx"]

from __future__ import annotations

import asyncio
import os
import time
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4, uuid5

import pytest
from pydantic import ValidationError

from yleum_orchestrator.core.cell_resources import CellIdentityConflict
from yleum_orchestrator.core.errors import OrchestratorError
from yleum_orchestrator.routers.runtime import _workspace_revision
from yleum_orchestrator.schemas.workspace import (
    WorkspaceAgentExecRequest,
    WorkspaceCheckpointPreviewRequest,
)
from yleum_orchestrator.services import checkpoint_preview as slots
from yleum_orchestrator.services import checkpoint_preview_runtime as driver
from yleum_orchestrator.services.project_machine import MachineOperationResult


@pytest.fixture
def setup(tmp_path, monkeypatch):
    canonical, project, owner, run = (uuid4() for _ in range(4))
    files = {"src/app/page.tsx": "export default function Page() {}"}
    payload = dict(project_id=project, owner_id=owner, run_id=run, checkpoint_digest="c" * 64)
    source = WorkspaceCheckpointPreviewRequest(
        **payload, files=files, source_revision=_workspace_revision(files)
    )
    lookup = WorkspaceCheckpointPreviewRequest(**payload)
    state = SimpleNamespace(
        workspace_id=canonical,
        project_id=project,
        owner_id=owner,
        active_generation_run_id=None,
        phase="completed",
    )

    @asynccontextmanager
    async def named(name):
        assert name == f"checkpoint-preview-{canonical}"
        yield

    manager = SimpleNamespace(
        state_store=SimpleNamespace(root=tmp_path / "cells", load=lambda _: state),
        operation_lock=SimpleNamespace(hold_named=named),
    )
    monkeypatch.setattr(slots, "_manager", lambda _: manager)
    monkeypatch.setattr(
        slots, "get_settings", lambda: SimpleNamespace(cell_state_path=tmp_path / "cells")
    )
    monkeypatch.setattr(slots.readers, "require_if_configured", lambda: None)
    # Native directory fsync is a separate release/CI contract on Linux.
    # These request/workflow tests do not emulate Project Cell Docker effects.
    if os.name == "nt":
        monkeypatch.setattr(
            "yleum_orchestrator.services.project_machine.sync_controller_parent_chain",
            lambda _: None,
        )
    return SimpleNamespace(
        canonical=canonical,
        project=project,
        owner=owner,
        source=source,
        lookup=lookup,
        state=state,
        manager=manager,
    )


def test_request_requires_all_or_none_of_source(setup):
    req = setup.lookup.model_dump(exclude_none=True)
    for kwargs in (
        {"source_revision": "a" * 64},
        {"files": {"x": "y"}},
        {"source_revision": "a" * 64, "files": {}},
    ):
        with pytest.raises(ValidationError):
            WorkspaceCheckpointPreviewRequest(**req, **kwargs)


async def test_cold_probe_owner_and_busy_guards(setup):
    s = setup
    with pytest.raises(OrchestratorError) as absent:
        await slots.checkpoint_session(s.canonical, s.lookup)
    assert absent.value.details == {"reason": "draft_preview_source_required"}
    for field in ("project_id", "owner_id"):
        with pytest.raises(OrchestratorError) as denied:
            await slots.checkpoint_session(
                s.canonical, s.source.model_copy(update={field: uuid4()})
            )
        assert denied.value.status_code == 404
    s.state.active_generation_run_id = uuid4()
    with pytest.raises(OrchestratorError) as busy:
        await slots.checkpoint_session(s.canonical, s.source)
    assert busy.value.details == {"reason": "draft_preview_workspace_busy"}
    assert not slots._slot_path(s.canonical).exists()


async def test_concurrent_start_and_reopen_keep_one_cell_and_canonical_identity(setup, monkeypatch):
    s = setup
    candidate = uuid5(
        s.canonical, f"owner-draft-preview:{s.source.run_id}:{s.source.checkpoint_digest}"
    )
    building, release = asyncio.Event(), asyncio.Event()

    async def materialize(_manager, actual, logical_project, owner, lease, revision, files):
        assert (actual, logical_project, owner) == (candidate, s.project, s.owner)
        assert lease not in {s.source.run_id, candidate} and revision == _workspace_revision(files)
        building.set()
        await release.wait()

    build = AsyncMock(side_effect=materialize)
    monkeypatch.setattr(driver, "materialize", build)
    signed = AsyncMock(return_value=object())
    monkeypatch.setattr(driver, "signed_session", signed)
    with pytest.raises(OrchestratorError) as pending:
        await slots.checkpoint_session(s.canonical, s.source)
    assert pending.value.details == {"reason": "draft_preview_preparing"}
    await asyncio.wait_for(building.wait(), timeout=3)
    with pytest.raises(OrchestratorError):
        await slots.checkpoint_session(s.canonical, s.lookup)
    task = slots._jobs[s.canonical]
    release.set()
    await task
    slot = slots._read(s.canonical)
    assert slot.phase == "ready" and slot.candidate == candidate
    assert signed.return_value is await slots.checkpoint_session(s.canonical, s.lookup)
    signed.assert_awaited_once_with(s.manager, candidate, s.project, s.owner)
    assert build.await_count == 1 and s.state.active_generation_run_id is None
    with pytest.raises(OrchestratorError) as changed:
        await slots.checkpoint_session(
            s.canonical, s.source.model_copy(update={"source_revision": "b" * 64})
        )
    assert changed.value.details == {"reason": "draft_preview_source_changed"}


async def test_reader_inhibit_before_effect_preserves_retry(setup, monkeypatch):
    s = setup
    monkeypatch.setattr(
        slots.readers,
        "require_if_configured",
        Mock(side_effect=slots.readers.ReaderInventoryUnavailable("inhibited")),
    )
    build = AsyncMock()
    monkeypatch.setattr(driver, "materialize", build)
    with pytest.raises(slots.readers.ReaderInventoryUnavailable):
        await slots._run(s.canonical, s.source, restart=False)
    assert slots._read(s.canonical) is None
    build.assert_not_awaited()


async def test_failed_reopen_becomes_terminal_until_retry(setup, monkeypatch):
    s = setup
    slot = slots.Slot(
        canonical=s.canonical,
        project=s.project,
        owner=s.owner,
        run=s.lookup.run_id,
        checkpoint=s.lookup.checkpoint_digest,
        revision=s.source.source_revision,
        lease=uuid4(),
        phase="ready",
    )
    slots._write(slot)
    monkeypatch.setattr(
        driver, "signed_session", AsyncMock(side_effect=driver.PreviewProcessStopped())
    )
    resume = AsyncMock(side_effect=driver.CellResourceError("cannot start app"))
    monkeypatch.setattr(driver, "resume_candidate", resume)
    cleanup = AsyncMock()
    monkeypatch.setattr(slots, "_clean", cleanup)
    with pytest.raises(OrchestratorError) as pending:
        await slots.checkpoint_session(s.canonical, s.lookup)
    assert pending.value.details == {"reason": "draft_preview_preparing"}
    await slots._jobs[s.canonical]
    with pytest.raises(OrchestratorError) as failed:
        await slots.checkpoint_session(s.canonical, s.lookup)
    assert failed.value.details == {"reason": "draft_preview_build_failed"}
    assert cleanup.await_count == 1 and resume.await_count == 1


def test_lookup_only_key_does_not_mint_credentials(setup, tmp_path):
    state = setup.state
    runtime = Mock(root=tmp_path)
    with pytest.raises(driver.PreviewProcessStopped):
        driver._serving_key(runtime, state)
    runtime.secret.assert_not_called()
    runtime.preview.assert_not_called()
    path = tmp_path / "boundary-secrets" / f"{state.workspace_id}.json"
    assert not path.exists()
    driver.write_controller_json(path, {"postgres_password": "existing"})
    runtime.preview.return_value = ("stopped", "")
    with pytest.raises(driver.PreviewProcessStopped):
        driver._serving_key(runtime, state)
    runtime.preview.assert_called_once_with(state, signing_secret="existing")


async def test_sweeper_cleans_abandoned_or_expired_candidate_only(setup, monkeypatch):
    s = setup
    slot = slots.Slot(
        canonical=s.canonical,
        project=s.project,
        owner=s.owner,
        run=s.lookup.run_id,
        checkpoint=s.lookup.checkpoint_digest,
        revision=s.source.source_revision,
        lease=uuid4(),
        phase="ready",
    )
    slots._write(slot)
    retire = AsyncMock()
    monkeypatch.setattr(driver, "retire_candidate", retire)
    await slots.sweep_previews()
    retire.assert_not_awaited()
    old = time.time() - slots._IDLE_SECONDS - 10
    os.utime(slots._slot_path(s.canonical), (old, old))
    await slots.sweep_previews()
    retire.assert_awaited_once_with(
        s.manager, slot.candidate, s.project, s.owner, slot.lease, slot.revision
    )
    assert slots._read(s.canonical) is None


async def test_source_identity_checked_before_workspace_or_runtime_effect(setup):
    with pytest.raises(CellIdentityConflict):
        await driver.materialize(
            None, uuid4(), setup.project, setup.owner, uuid4(), "0" * 64, setup.source.files
        )


def test_uses_explicit_build_but_skips_final_acceptance():
    def task(name):
        return SimpleNamespace(role="full_build", name=name, cwd=".", argv=["pnpm", name])

    manifest = SimpleNamespace(tasks=[task("build"), task("final-test")])
    assert driver._build_commands(manifest, {}) == ["cd . && pnpm build"]
    assert driver._build_commands(SimpleNamespace(tasks=[]), {}) == []


async def test_empty_database_migration_owns_a_separate_request_without_bootstrap(setup):
    s = setup
    machine = SimpleNamespace(
        ensure=AsyncMock(),
        request_start=AsyncMock(return_value=None),
        request_finish=AsyncMock(side_effect=lambda _, result: result),
    )
    runtime = SimpleNamespace(
        parts=lambda _: (machine, None),
        _request_digest=lambda *_: "1" * 64,
        _project_migrations=AsyncMock(),
    )
    req = WorkspaceAgentExecRequest(
        generation_run_id=uuid4(),
        operation_id=uuid4(),
        fencing_epoch=1,
        expected_revision="2" * 64,
        cmd="omnia:owner-preview-migrations",
    )
    await driver._migrate_empty_database(runtime, s.state, SimpleNamespace(tasks=[]), req)
    assert machine.request_start.await_args.args[0].operation_id == req.operation_id
    runtime._project_migrations.assert_awaited_once_with(s.state, req, verify_applied=False)
    assert machine.request_finish.await_args.args[1].exit_code == 0
    machine.request_start.return_value = MachineOperationResult(
        operation_id=str(req.operation_id), state="completed", exit_code=0
    )
    await driver._migrate_empty_database(runtime, s.state, SimpleNamespace(tasks=[]), req)
    assert runtime._project_migrations.await_count == 1


def test_signed_session_uses_real_product_id_in_private_host(setup):
    from urllib.parse import parse_qs, urlsplit

    from yleum_orchestrator.routers import workspace as ws

    s = setup
    candidate = uuid4()
    result = ws._sign_draft_preview_session(
        candidate, s.project, "https://candidate.preview.example", "secret"
    )
    query = parse_qs(urlsplit(result.bootstrap_url).query)
    expires = int(query["expires"][0])
    signature = ws._max_preview_bootstrap_signature("secret", str(s.project), expires)
    assert result.workspace_id == candidate and signature == query["signature"][0]
    assert signature != ws._max_preview_bootstrap_signature("secret", str(candidate), expires)


async def test_interrupted_destroy_reconciles_then_discards_at_new_fence_without_capture(
    setup,
    tmp_path,
    monkeypatch,
):
    s = setup
    candidate, lease = uuid4(), uuid4()
    member = tmp_path / "owner-checkpoint-members" / f"{candidate}.json"
    member.parent.mkdir(mode=0o700)
    member.write_text("{}")
    state = SimpleNamespace(
        workspace_id=candidate,
        project_id=s.project,
        owner_id=s.owner,
        active_generation_run_id=lease,
        phase="indeterminate",
        fencing_epoch=3,
        last_operation_id=uuid5(lease, "retire-3"),
    )
    operations = {state.last_operation_id: SimpleNamespace(kind="destroy", status="indeterminate")}
    state.operation = operations.get

    async def reconcile(identity, mutation):
        assert identity == candidate and mutation.fencing_epoch == 4
        state.fencing_epoch = mutation.fencing_epoch
        state.last_operation_id = mutation.operation_id
        state.phase = "completed"
        operations[mutation.operation_id] = SimpleNamespace(kind="reconcile", status="completed")
        return SimpleNamespace(identity_valid=True)

    @asynccontextmanager
    async def hold(identity):
        assert identity == candidate
        yield

    manager = SimpleNamespace(
        state_store=SimpleNamespace(root=tmp_path / "cells", load=lambda _: state),
        operation_lock=SimpleNamespace(hold=hold),
        reconcile=AsyncMock(side_effect=reconcile),
        destroy_compute_without_lock=AsyncMock(),
        assert_uncreated_workspace=AsyncMock(),
        release_generation=AsyncMock(side_effect=AssertionError("failed preview cannot capture")),
        docker=SimpleNamespace(
            list_workspace_volumes=AsyncMock(return_value=[]),
            list_workspace_networks=AsyncMock(return_value=[]),
            list_workspace_containers=AsyncMock(return_value=[]),
        ),
    )
    monkeypatch.setattr(driver.nginx_writer, "unpublish", AsyncMock())
    monkeypatch.setattr(driver.ws, "_draft_preview_host", lambda _: "private-preview")
    monkeypatch.setattr(
        driver.ws,
        "_require_portable_runtime",
        lambda _: SimpleNamespace(root=tmp_path, exists=lambda _: False),
    )
    monkeypatch.setattr(driver, "_retire_controller_files", Mock())
    await driver.retire_candidate(
        manager, candidate, s.project, s.owner, lease, s.source.source_revision
    )
    call = manager.destroy_compute_without_lock.await_args
    assert call.args[0] == candidate and call.args[1].operation_id == uuid5(lease, "retire-5")
    assert call.args[1].fencing_epoch == 5 and call.kwargs["capture"] is False
    assert manager.release_generation.await_count == 0 and not member.exists()


def test_retire_removes_only_candidate_artifacts_and_credentials(tmp_path):
    candidate, canonical = uuid4(), uuid4()
    runtime = tmp_path / "project-machines"
    credential = tmp_path / "credentials"
    states = tmp_path / "states"
    manager = SimpleNamespace(
        credential_store=SimpleNamespace(root=credential),
        state_store=SimpleNamespace(workspace_path=lambda key: states / f"{key}.json"),
    )
    for key in (candidate, canonical):
        for folder in (runtime / str(key), runtime / "artifacts" / str(key)):
            folder.mkdir(mode=0o700, parents=True)
            (folder / "payload").write_bytes(b"protected")
        for folder in (
            runtime / "project-db-credentials",
            runtime / "core-runtime-credentials",
            credential,
            states,
        ):
            folder.mkdir(mode=0o700, exist_ok=True)
            (folder / f"{key}.json").write_text("private")
    driver._retire_controller_files(manager, runtime, candidate)
    for folder in (runtime, runtime / "artifacts"):
        assert (
            not (folder / str(candidate)).exists()
            and (folder / str(canonical) / "payload").is_file()
        )
    for folder in (
        runtime / "project-db-credentials",
        runtime / "core-runtime-credentials",
        credential,
        states,
    ):
        assert (
            not (folder / f"{candidate}.json").exists() and (folder / f"{canonical}.json").is_file()
        )

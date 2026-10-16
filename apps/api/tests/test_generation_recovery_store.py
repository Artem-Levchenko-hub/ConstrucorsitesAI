"""Real ORM queries at the session boundary; PostgreSQL proves row locking separately."""
from __future__ import annotations

import asyncio
import copy
import hashlib
import importlib
import importlib.util
from types import SimpleNamespace
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy import JSON, Column, MetaData, Table, create_engine, select, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import async_sessionmaker
from sqlalchemy.orm import Session

from tests.test_generation_recovery_contract import contract
from yleum_api.models.generation_run import GenerationRun
from yleum_api.models.project import Project
from yleum_api.models.project_cell import ProjectCellWorkspace
from yleum_api.models.snapshot import Snapshot
from yleum_api.models.user import User
from yleum_api.services.generation import source_checkpoint as sc
from yleum_api.services.generation_runs import GenerationDispatch, prompt_hash
from yleum_api.services.orchestrator_client import ProjectCellWorkspaceIdentity
from yleum_api.services.promotion_permit import workspace_revision_digest


def store():
    name = "yleum_api.services.generation.recovery_store"
    assert importlib.util.find_spec(name) is not None, "missing recovery checkpoint CAS writer"
    return importlib.import_module(name)


@pytest.fixture
def checkpoint_case(monkeypatch):
    from yleum_api.core.config import get_settings

    monkeypatch.setenv("GENERATION_RECOVERY_MODE", "on")
    get_settings.cache_clear()
    rc, rs = contract(), store()
    engine = create_engine("sqlite://")
    metadata = MetaData()
    for model in (GenerationRun, Project, ProjectCellWorkspace, Snapshot):
        Table(model.__tablename__, metadata, *(
            Column(c.name, JSON() if isinstance(c.type, JSONB) else c.type,
                   primary_key=c.primary_key) for c in model.__table__.columns
        ))
    metadata.create_all(engine)
    sync = Session(engine, expire_on_commit=False)

    class BoundarySession:
        @property
        def no_autoflush(self):
            return sync.no_autoflush

        async def get(self, model, key, **kwargs):
            return sync.get(model, key, **kwargs)

        async def scalar(self, statement):
            return sync.scalar(statement)

        async def commit(self):
            sync.commit()

        async def rollback(self):
            sync.rollback()

    owner, project_id, run_id, workspace_id = (uuid4() for _ in range(4))
    project = Project(id=project_id, owner_id=owner, current_snapshot_id=None)
    run = GenerationRun(id=run_id, project_id=project_id, user_id=owner, status="running",
                        prompt_hash=prompt_hash("Original scope"), idempotency_key="one")
    dispatch = GenerationDispatch(1, project_id, owner, uuid4(), uuid4(), None,
                                  "Original scope", "test-model", None, False, True, None)
    run.agent_state = {"dispatch": dispatch.to_json(), "unrelated": {"kept": [1, 2]}}
    workspace = ProjectCellWorkspace(id=workspace_id, project_id=project_id, owner_id=owner,
                                     generation_run_id=run_id, fencing_epoch=7, state="ready")
    sync.add_all([project, run, workspace])
    sync.commit()
    files = {"src/page.tsx": "original candidate", ".env": "excluded secret"}
    context_data = b'{"original_scope":"Original scope"}'
    context_digest = hashlib.sha256(context_data).hexdigest()
    objects = {context_digest: context_data}
    uploads = []

    def put(project_arg, run_arg, data):
        assert (project_arg, run_arg) == (project_id, run_id)
        digest = hashlib.sha256(data).hexdigest()
        objects[digest] = data
        uploads.append(digest)
        return digest

    monkeypatch.setattr(sc, "_put_checkpoint", put)
    monkeypatch.setattr(sc, "_get_checkpoint", lambda p, r, d: objects.get(d))
    identity = ProjectCellWorkspaceIdentity(workspace_revision_digest(files), "d" * 64,
                                           "b" * 64, "c" * 64, "e" * 64, "f" * 64)

    async def current_identity(workspace_arg, *, generation_run_id, fencing_epoch):
        assert (workspace_arg, generation_run_id, fencing_epoch) == (workspace_id, run_id, 7)
        return case.physical_identity

    monkeypatch.setattr(rs, "project_cell_agent_identity", current_identity)
    state = rc.RecoveryState(identity=rc.RecoveryIdentity(
        run_id=run_id, project_id=project_id, user_id=owner, prompt_hash=run.prompt_hash,
        accepted_snapshot_id=None, supersedes_run_id=None,
    ), binding=rc.RecoveryBinding(source_sha256=identity.workspace_revision,
                                 schema_sha256=identity.schema_data_digest,
                                 environment_sha256=identity.environment_digest,
                                 workspace_id=str(workspace_id), fencing_epoch=7),
        context_ref=rc.CheckpointRef(object_key=sc._object_key(project_id, run_id, context_digest),
                                     sha256=context_digest))
    case = SimpleNamespace(rc=rc, rs=rs, session=BoundarySession(), sync=sync, run=run,
                           project=project, workspace=workspace, files=files, state=state,
                           objects=objects, uploads=uploads, physical_identity=identity)
    yield case
    get_settings.cache_clear()
    sync.close()
    engine.dispose()


async def save(case, state=None, *, attempt=0, revision=0, files=None, deleted=()):
    return await case.rs.save_recovery_checkpoint(
        case.session, run_id=case.run.id, expected_attempt=attempt, expected_revision=revision,
        state=state or case.state, files=case.files if files is None else files,
        deleted_paths=deleted,
    )


async def test_checkpoint_preserves_other_state_and_never_publishes(checkpoint_case):
    c = checkpoint_case
    ref = await save(c)
    saved = c.rc.decode_recovery(c.run.agent_state["recovery_v1"])
    assert saved.checkpoint == ref and saved.state_revision == 1
    assert c.run.agent_state["unrelated"] == {"kept": [1, 2]}
    assert c.project.current_snapshot_id is None
    assert list(c.sync.scalars(select(Snapshot))) == []
    payload = sc.decode_checkpoint(c.objects[ref.sha256], project_id=c.project.id,
                                   user_id=c.project.owner_id, run_id=c.run.id, baseline_sha=None)
    assert payload == {"files": {"src/page.tsx": "original candidate"}, "deleted_paths": []}
    assert saved.binding.source_sha256 != ref.sha256


@pytest.mark.parametrize("problem", [
    "foreign_owner", "foreign_run", "workspace_owner", "workspace_run", "stale_fence",
    "stale_attempt", "stale_revision", "source", "schema", "environment", "baseline",
    "missing_context", "foreign_context", "missing_workspace", "terminal_run", "unsafe_path",
    "unknown_contract", "corrupt_contract", "forged_model_copy",
])
async def test_invalid_checkpoint_has_no_upload_or_state_change(checkpoint_case, problem):
    c = checkpoint_case
    state, attempt, revision, files = c.state, 0, 0, c.files
    raw = state.model_dump(mode="json")
    if problem == "foreign_owner":
        c.project.owner_id = uuid4()
    elif problem == "foreign_run":
        raw["identity"]["user_id"] = str(uuid4())
    elif problem == "workspace_owner":
        c.workspace.owner_id = uuid4()
    elif problem == "workspace_run":
        c.workspace.generation_run_id = uuid4()
    elif problem == "stale_fence":
        raw["binding"]["fencing_epoch"] = 6
    elif problem == "stale_attempt":
        attempt = 1
    elif problem == "stale_revision":
        revision = 1
    elif problem in {"source", "schema", "environment"}:
        raw["binding"][f"{problem}_sha256"] = "9" * 64
    elif problem == "baseline":
        raw["identity"]["accepted_snapshot_id"] = str(uuid4())
    elif problem == "missing_context":
        c.objects.clear()
    elif problem == "foreign_context":
        raw["context_ref"]["object_key"] = sc._object_key(uuid4(), c.run.id,
                                                          raw["context_ref"]["sha256"])
    elif problem == "missing_workspace":
        raw["binding"]["workspace_id"] = str(uuid4())
    elif problem == "terminal_run":
        c.run.status = "completed"
    elif problem == "unsafe_path":
        files = {"../escape.ts": "unsafe"}
    elif problem == "unknown_contract":
        c.run.agent_state = c.run.agent_state | {"recovery_v1": {"version": 99, "keep": True}}
    elif problem == "corrupt_contract":
        c.run.agent_state = c.run.agent_state | {"recovery_v1": None}
    if problem == "forged_model_copy":
        state = state.model_copy(update={"active_work_ms": -1})
    else:
        state = c.rc.decode_recovery(raw)
    c.sync.commit()
    previous = copy.deepcopy(c.run.agent_state)
    with pytest.raises((c.rc.RecoveryContractError, ValueError)):
        await save(c, state, attempt=attempt, revision=revision, files=files)
    assert c.uploads == []
    assert c.run.agent_state == previous
    assert c.project.current_snapshot_id is None
    assert list(c.sync.scalars(select(Snapshot))) == []


async def test_authorized_source_evolution_uses_current_binding_and_cas(checkpoint_case):
    c = checkpoint_case
    old_ref = await save(c)
    previous = c.rc.decode_recovery(c.run.agent_state["recovery_v1"])
    c.files = {"src/page.tsx": "new feature", ".env": "excluded secret"}
    identity = c.physical_identity
    c.physical_identity = ProjectCellWorkspaceIdentity(
        workspace_revision_digest(c.files), identity.dependency_digest,
        identity.schema_data_digest, identity.cell_manifest_digest, identity.environment_digest,
        identity.build_config_digest,
    )
    raw = previous.model_dump(mode="json")
    raw["binding"]["source_sha256"] = c.physical_identity.workspace_revision
    raw["cursor"] = "agent"
    new_ref = await save(c, c.rc.decode_recovery(raw), revision=1)
    assert new_ref != old_ref and old_ref.sha256 in c.objects
    assert c.run.agent_state["recovery_v1"]["state_revision"] == 2
    before = copy.deepcopy(c.run.agent_state)
    with pytest.raises(c.rc.RecoveryContractError):
        await save(c, previous, revision=1)
    assert c.run.agent_state == before and len(c.uploads) == 2


async def test_storage_failure_retains_the_only_source_and_original_identity(checkpoint_case,
                                                                          monkeypatch):
    c = checkpoint_case
    ref = await save(c)
    before = copy.deepcopy(c.run.agent_state)
    previous = c.rc.decode_recovery(before["recovery_v1"])

    def unavailable(*_args):
        raise OSError("private storage unavailable")

    monkeypatch.setattr(sc, "_put_checkpoint", unavailable)
    with pytest.raises(OSError, match="private storage"):
        await save(c, previous, revision=1)
    assert c.run.agent_state == before
    assert ref.sha256 in c.objects
    assert c.project.current_snapshot_id is None


async def test_shadow_observations_are_separate_and_default_off(checkpoint_case, monkeypatch):
    from yleum_api.core.config import get_settings

    c = checkpoint_case
    observation = c.rc.RecoveryObservation(kind="source", binding=c.state.binding,
        requirement_id=None, action_signature=None, diagnostic_signatures=(), witness=None)
    assert await c.rs.save_recovery_shadow(c.session, run_id=c.run.id,
                                         observation=observation) is False
    monkeypatch.setenv("GENERATION_RECOVERY_MODE", "shadow")
    get_settings.cache_clear()
    try:
        assert await c.rs.save_recovery_shadow(c.session, run_id=c.run.id,
                                              observation=observation) is True
        assert "recovery_v1" not in c.run.agent_state
        assert c.run.agent_state["recovery_shadow_v1"]["observation"]["kind"] == "source"
        assert c.run.agent_state["unrelated"] == {"kept": [1, 2]}
        assert c.uploads == [] and c.run.status == "running"
    finally:
        get_settings.cache_clear()


@pytest.mark.parametrize("field", ["identity", "requirements"])
async def test_scope_cannot_change_at_a_current_revision(checkpoint_case, field):
    c = checkpoint_case
    await save(c)
    raw = copy.deepcopy(c.run.agent_state["recovery_v1"])
    if field == "identity":
        raw["identity"]["supersedes_run_id"] = str(uuid4())
    else:
        raw["requirements"] = [{"id": "new", "text": "Expanded scope",
            "origin_prompt_sha256": c.run.prompt_hash, "check_kind": "manual", "required": True}]
    before = copy.deepcopy(c.run.agent_state)
    with pytest.raises(c.rc.RecoveryContractError, match="immutable scope"):
        await save(c, c.rc.decode_recovery(raw), revision=1)
    assert c.run.agent_state == before and len(c.uploads) == 1


@pytest.mark.parametrize("mode,allowlist,allowed", [
    ("off", "self", False), ("shadow", "self", False),
    ("canary", "", False), ("canary", "other", False),
    ("canary", "substring", False), ("canary", "self", True), ("on", "", True),
])
async def test_active_writer_rollout_gate(checkpoint_case, monkeypatch, mode, allowlist, allowed):
    from yleum_api.core.config import get_settings

    c = checkpoint_case
    lists = {"": "", "other": str(uuid4()), "substring": f"x{c.project.id}x",
             "self": f" {uuid4()}, {c.project.id} "}
    monkeypatch.setenv("GENERATION_RECOVERY_MODE", mode)
    monkeypatch.setenv("GENERATION_RECOVERY_CANARY_PROJECT_IDS", lists[allowlist])
    get_settings.cache_clear()
    before = copy.deepcopy(c.run.agent_state)
    if allowed:
        await save(c)
        assert "recovery_v1" in c.run.agent_state
    else:
        with pytest.raises(c.rc.RecoveryContractError, match="disabled"):
            await save(c)
        assert c.run.agent_state == before and c.uploads == []


async def test_authoritative_reader_failure_keeps_prior_source(checkpoint_case, monkeypatch):
    c = checkpoint_case
    await save(c)
    before = copy.deepcopy(c.run.agent_state)

    async def unavailable(*_args, **_kwargs):
        raise OSError("fenced reader unavailable")

    monkeypatch.setattr(c.rs, "project_cell_agent_identity", unavailable)
    with pytest.raises(OSError, match="fenced reader"):
        await save(c, c.rc.decode_recovery(before["recovery_v1"]), revision=1)
    assert c.run.agent_state == before and len(c.uploads) == 1


async def test_failed_commit_keeps_previous_reference(checkpoint_case, monkeypatch):
    c = checkpoint_case
    ref = await save(c)
    before = copy.deepcopy(c.run.agent_state)

    async def commit_failure():
        raise OSError("checkpoint commit unavailable")

    monkeypatch.setattr(c.session, "commit", commit_failure)
    with pytest.raises(OSError, match="commit unavailable"):
        await save(c, c.rc.decode_recovery(before["recovery_v1"]), revision=1)
    assert c.run.agent_state == before and ref.sha256 in c.objects


async def test_physical_drift_during_upload_never_installs_new_reference(checkpoint_case,
                                                                       monkeypatch):
    c = checkpoint_case
    ref = await save(c)
    before = copy.deepcopy(c.run.agent_state)
    original_put = sc._put_checkpoint

    def drift(*args):
        digest = original_put(*args)
        identity = c.physical_identity
        c.physical_identity = ProjectCellWorkspaceIdentity(
            "9" * 64, identity.dependency_digest, identity.schema_data_digest,
            identity.cell_manifest_digest, identity.environment_digest,
            identity.build_config_digest,
        )
        return digest

    monkeypatch.setattr(sc, "_put_checkpoint", drift)
    with pytest.raises(c.rc.RecoveryContractError, match="candidate"):
        await save(c, c.rc.decode_recovery(before["recovery_v1"]), revision=1)
    assert c.run.agent_state == before and ref.sha256 in c.objects


async def test_checkpoint_records_owned_original_snapshot_and_deletions(checkpoint_case):
    c = checkpoint_case
    snapshot = Snapshot(id=uuid4(), project_id=c.project.id, commit_sha="a" * 40)
    c.sync.add(snapshot)
    c.project.current_snapshot_id = snapshot.id
    dispatch = copy.deepcopy(c.run.agent_state["dispatch"])
    dispatch["current_snapshot_id"] = str(snapshot.id)
    c.run.agent_state = c.run.agent_state | {"dispatch": dispatch}
    c.sync.commit()
    raw = c.state.model_dump(mode="json")
    raw["identity"]["accepted_snapshot_id"] = str(snapshot.id)
    ref = await save(c, c.rc.decode_recovery(raw), deleted=("src/old.ts",))
    payload = sc.decode_checkpoint(c.objects[ref.sha256], project_id=c.project.id,
        user_id=c.project.owner_id, run_id=c.run.id, baseline_sha="a" * 40)
    assert payload["deleted_paths"] == ["src/old.ts"]
    assert c.project.current_snapshot_id == snapshot.id
    assert len(list(c.sync.scalars(select(Snapshot)))) == 1


async def test_saved_checkpoint_rejects_changed_original_baseline_sha(checkpoint_case):
    c = checkpoint_case
    snapshot = Snapshot(id=uuid4(), project_id=c.project.id, commit_sha="a" * 40)
    c.sync.add(snapshot)
    c.project.current_snapshot_id = snapshot.id
    dispatch = copy.deepcopy(c.run.agent_state["dispatch"])
    dispatch["current_snapshot_id"] = str(snapshot.id)
    c.run.agent_state = c.run.agent_state | {"dispatch": dispatch}
    c.sync.commit()
    raw = c.state.model_dump(mode="json")
    raw["identity"]["accepted_snapshot_id"] = str(snapshot.id)
    await save(c, c.rc.decode_recovery(raw))
    before = copy.deepcopy(c.run.agent_state)
    snapshot.commit_sha = "b" * 40
    c.sync.commit()
    with pytest.raises(c.rc.RecoveryContractError, match="retained checkpoint"):
        await save(c, c.rc.decode_recovery(before["recovery_v1"]), revision=1)
    assert c.run.agent_state == before and len(c.uploads) == 1


async def test_unavailable_retained_checkpoint_is_not_replaced(checkpoint_case):
    c = checkpoint_case
    ref = await save(c)
    before = copy.deepcopy(c.run.agent_state)
    c.objects.pop(ref.sha256)
    with pytest.raises(c.rc.RecoveryContractError, match="retained checkpoint"):
        await save(c, c.rc.decode_recovery(before["recovery_v1"]), revision=1)
    assert c.run.agent_state == before and len(c.uploads) == 1


@pytest.mark.parametrize("corruption", ["digest", "foreign_baseline", "changed_prompt"])
async def test_claimed_context_and_baseline_facts_are_validated_before_upload(checkpoint_case,
                                                                           corruption):
    c = checkpoint_case
    if corruption == "digest":
        c.objects[c.state.context_ref.sha256] = b"changed immutable context"
    elif corruption == "foreign_baseline":
        snapshot = Snapshot(id=uuid4(), project_id=uuid4(), commit_sha="a" * 40)
        c.sync.add(snapshot)
        c.project.current_snapshot_id = snapshot.id
        dispatch = copy.deepcopy(c.run.agent_state["dispatch"])
        dispatch["current_snapshot_id"] = str(snapshot.id)
        c.run.agent_state = c.run.agent_state | {"dispatch": dispatch}
        raw = c.state.model_dump(mode="json")
        raw["identity"]["accepted_snapshot_id"] = str(snapshot.id)
        c.state = c.rc.decode_recovery(raw)
    else:
        dispatch = copy.deepcopy(c.run.agent_state["dispatch"])
        dispatch["prompt_text"] = "Replacement scope"
        c.run.agent_state = c.run.agent_state | {"dispatch": dispatch}
    c.sync.commit()
    before = copy.deepcopy(c.run.agent_state)
    with pytest.raises(c.rc.RecoveryContractError):
        await save(c)
    assert c.uploads == [] and c.run.agent_state == before


@pytest.mark.parametrize("unknown", [{"version": 99}, {"version": 1, "observation": {}}])
async def test_unknown_shadow_contract_is_preserved(checkpoint_case, monkeypatch, unknown):
    from yleum_api.core.config import get_settings

    c = checkpoint_case
    c.run.agent_state = c.run.agent_state | {"recovery_shadow_v1": unknown}
    c.sync.commit()
    before = copy.deepcopy(c.run.agent_state)
    observation = c.rc.RecoveryObservation(kind="source", binding=c.state.binding,
        requirement_id=None, action_signature=None, diagnostic_signatures=(), witness=None)
    monkeypatch.setenv("GENERATION_RECOVERY_MODE", "shadow")
    get_settings.cache_clear()
    try:
        with pytest.raises(c.rc.RecoveryContractError):
            await c.rs.save_recovery_shadow(c.session, run_id=c.run.id, observation=observation)
        assert c.run.agent_state == before
    finally:
        get_settings.cache_clear()


@pytest_asyncio.fixture
async def postgres_case(checkpoint_case, db_session):
    """Real PostgreSQL schema/constraints; only fenced HTTP/private object I/O is replaced."""
    c = checkpoint_case
    user = User(id=c.project.owner_id, email=f"recovery-{uuid4().hex}@example.com",
                password_hash="synthetic")
    db_session.add(user)
    await db_session.flush()
    project = Project(id=c.project.id, owner_id=user.id, name="Recovery QA",
                      slug=f"recovery-{uuid4().hex}", template="blank")
    db_session.add(project)
    await db_session.flush()
    run = GenerationRun(id=c.run.id, project_id=project.id, user_id=user.id,
                        idempotency_key="recovery-qa", prompt_hash=c.run.prompt_hash,
                        status="running", agent_state=copy.deepcopy(c.run.agent_state))
    db_session.add(run)
    await db_session.flush()
    workspace = ProjectCellWorkspace(id=c.workspace.id, project_id=project.id, owner_id=user.id,
                                     provider="docker", state="ready", generation_run_id=run.id,
                                     fencing_epoch=7)
    db_session.add(workspace)
    await db_session.commit()
    c.run, c.project, c.workspace, c.session = run, project, workspace, db_session
    return c


async def _assert_postgres_roundtrip_and_unknown_json(c):
    await c.session.refresh(c.run)
    ref = c.rc.decode_recovery(c.run.agent_state["recovery_v1"]).checkpoint
    assert ref.sha256 in c.objects
    assert c.run.agent_state["unrelated"] == {"kept": [1, 2]}
    c.run.agent_state = c.run.agent_state | {"recovery_v1": {"version": 99, "opaque": [1, 2]}}
    await c.session.commit()
    before = copy.deepcopy(c.run.agent_state)
    with pytest.raises(c.rc.RecoveryContractError):
        await save(c)
    await c.session.refresh(c.run)
    assert c.run.agent_state == before
    assert len(c.uploads) == 1
    assert await c.session.scalar(select(Snapshot.id)) is None


async def _assert_postgres_owner_and_fence(c, conflict):
    owner_id, run_id = c.run.user_id, c.run.id
    if conflict == "owner":
        other = User(email=f"other-{uuid4().hex}@example.com", password_hash="synthetic")
        c.session.add(other)
        await c.session.flush()
        c.workspace.owner_id = other.id
    elif conflict == "fence":
        c.workspace.fencing_epoch = 8
    else:
        c.workspace.generation_run_id = None
    await c.session.commit()
    before = copy.deepcopy(c.run.agent_state)
    with pytest.raises(c.rc.RecoveryContractError):
        await save(c)
    run = await c.session.get(GenerationRun, run_id)
    assert run.agent_state == before and c.uploads == []
    assert await c.session.scalar(select(Snapshot.id)) is None
    await c.session.refresh(c.workspace)
    c.workspace.owner_id = owner_id
    c.workspace.generation_run_id = run_id
    c.workspace.fencing_epoch = 7
    await c.session.commit()


async def _assert_postgres_concurrent_cas(c, test_engine, monkeypatch):
    entered, release = asyncio.Event(), asyncio.Event()
    original_reader = c.rs.project_cell_agent_identity

    async def blocked_reader(*args, **kwargs):
        entered.set()
        await release.wait()
        return await original_reader(*args, **kwargs)

    monkeypatch.setattr(c.rs, "project_cell_agent_identity", blocked_reader)
    factory = async_sessionmaker(test_engine, expire_on_commit=False)
    async with factory() as first, factory() as second:
        task = asyncio.create_task(c.rs.save_recovery_checkpoint(
            first, run_id=c.run.id, expected_attempt=0, expected_revision=0, state=c.state,
            files=c.files, deleted_paths=(),
        ))
        try:
            await asyncio.wait_for(entered.wait(), timeout=10)
            await second.execute(text("SET LOCAL lock_timeout = '250ms'"))
            with pytest.raises(DBAPIError, match="lock timeout"):
                await c.rs.save_recovery_checkpoint(
                    second, run_id=c.run.id, expected_attempt=0, expected_revision=0,
                    state=c.state, files=c.files, deleted_paths=(),
                )
            assert c.uploads == []
        finally:
            release.set()
            await asyncio.wait_for(task, timeout=10)
        with pytest.raises(c.rc.RecoveryContractError, match="revision"):
            await c.rs.save_recovery_checkpoint(
                second, run_id=c.run.id, expected_attempt=0, expected_revision=0,
                state=c.state, files=c.files, deleted_paths=(),
            )
        assert len(c.uploads) == 1
        saved = await second.get(GenerationRun, c.run.id, populate_existing=True)
        assert saved.agent_state["recovery_v1"]["state_revision"] == 1


async def test_postgres_recovery_ownership_concurrent_cas_and_json_preservation(
    postgres_case, test_engine, monkeypatch,
):
    # One full real schema setup. Every assertion still uses real constraints,
    # transactions, JSONB and independent sessions for the contested row locks.
    c = postgres_case
    for conflict in ("owner", "fence", "run"):
        await _assert_postgres_owner_and_fence(c, conflict)
    await _assert_postgres_concurrent_cas(c, test_engine, monkeypatch)
    await _assert_postgres_roundtrip_and_unknown_json(c)

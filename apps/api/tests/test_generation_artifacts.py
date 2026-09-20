"""Actual publication owners: frozen Git/SQL/quota order and failure contracts."""

from __future__ import annotations

import copy
from contextlib import asynccontextmanager
from dataclasses import replace
from functools import partial
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from omnia_api.core.config import get_settings
from omnia_api.models.account import BusinessEntitlement
from omnia_api.models.generation_run import GenerationRun
from omnia_api.models.message import Message
from omnia_api.models.project import Project
from omnia_api.models.snapshot import Snapshot
from omnia_api.models.user import User
from omnia_api.services import repo
from omnia_api.services.generation import agent_publication
from omnia_api.services.generation.contracts import (
    GenerationIds,
    GenerationRuntime,
    ProjectGenerationFacts,
    SourceBaseline,
)
from omnia_api.services.generation.progress import GenerationProgress
from omnia_api.services.generation.publication import consume_free_generation


def records():
    owner = User(
        id=uuid4(),
        email=f"artifact-{uuid4().hex}@example.test",
        password_hash="fixture",
        free_generations_used=3,
    )
    project = Project(
        id=uuid4(),
        owner_id=owner.id,
        name="Publication fixture",
        slug=f"artifact-{uuid4().hex}",
        template="blank",
    )
    parent = Snapshot(
        id=uuid4(),
        project_id=project.id,
        commit_sha="a" * 40,
        prompt_text="original",
        model_id="original",
    )
    project.current_snapshot_id = parent.id
    message = Message(
        id=uuid4(),
        project_id=project.id,
        role="assistant",
        content="before",
        tokens_in=7,
        tokens_out=None,
    )
    run = GenerationRun(
        id=uuid4(),
        project_id=project.id,
        user_id=owner.id,
        assistant_message_id=message.id,
        idempotency_key="fixture",
        prompt_hash="b" * 64,
        status="running",
        response_mode="build",
        agent_state={"changed_files": ["prior.txt"], "keep": "yes"},
    )
    return [owner, project, parent, message, run]


class OfflineSession:
    """Transaction double; durable copies change only at successful commit."""

    def __init__(self, rows, trace, fault=None):
        self.rows = rows
        self.trace = trace
        self.fault = fault
        self.durable = copy.deepcopy(rows)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return False

    def add(self, row):
        self.trace.append("add")
        self.rows.append(row)

    async def flush(self):
        self.trace.append("flush")
        if self.fault == "flush":
            raise RuntimeError("fixture flush failure")
        for row in self.rows:
            if hasattr(row, "id") and row.id is None:
                row.id = uuid4()

    async def get(self, model, key, **_kwargs):
        self.trace.append(f"get:{model.__name__}")
        return next(
            (
                row
                for row in self.rows
                if isinstance(row, model)
                and getattr(row, "id", getattr(row, "business_id", None)) == key
            ),
            None,
        )

    async def commit(self):
        self.trace.append("commit")
        if self.fault == "commit":
            raise RuntimeError("fixture commit failure")
        self.durable = copy.deepcopy(self.rows)

    async def refresh(self, _row):
        self.trace.append("refresh")
        if self.fault == "refresh":
            raise RuntimeError("fixture refresh failure")


def context(rows, factory, trace, monkeypatch, **overrides):
    owner, project, parent, message, run = rows[:5]
    original = {"old.txt": "preserve", "page.txt": "old"}
    parent.commit_sha = repo.init_from_files(project.id, original, "seed")
    real_commit = repo.commit_files
    calls = []

    def commit(*args, **kwargs):
        trace.append("git")
        calls.append((args, kwargs))
        return real_commit(*args, **kwargs)

    monkeypatch.setattr(repo, "commit_files", commit)
    env = {"rows": rows, "monkeypatch": monkeypatch}
    settings = get_settings().model_copy(update={"use_clean_chat_content": True})
    env.update(
        factory=factory,
        project_id=project.id,
        run_id=run.id,
        get_settings=lambda: settings,
        user_id=owner.id,
        assistant_message_id=message.id,
        current_snapshot_id=parent.id,
        current_sha=parent.commit_sha,
        files={"page.txt": "new", "empty.txt": ""},
        prompt_text="P" * 65,
        model_id="routing",
        routing_model="routing",
        force_model=None,
        orchestrate=True,
        surgical=False,
        accumulated="A useful change.",
        usage_data={"tokens_in": 11, "tokens_out": 23},
        _agent_step_log=[{"action": "write", "path": "page.txt"}],
        _max_finalization_proof=None,
        _promotion_permit=None,
        template="blank",
        is_free=True,
        free_business_id=None,
    )
    env.update(overrides)
    return env, calls, original


async def execute(path, env):
    """Invoke actual publication and quota owners, preserving frozen row expectations."""
    rows = env["rows"]
    owner, project, _parent, message, run = rows[:5]
    ids = GenerationIds(run.id, project.id, owner.id, uuid4(), message.id)
    facts = ProjectGenerationFacts(
        env["template"], project.slug, project.name, None, None, False, "ru", False, "", ""
    )
    baseline = SourceBaseline(
        env["current_snapshot_id"], env["current_sha"], {"old.txt": "preserve", "page.txt": "old"}
    )
    captured = {}

    async def capture_snapshot(*args, **kwargs):
        result = await real_create(*args, **kwargs)
        captured.update(snapshot=result[0], project=result[1], new_sha=kwargs["commit_sha"])
        return result

    async def noop(*args, **kwargs):
        pass

    assert path == "agent"  # the one-shot publisher left with the site builder
    target = agent_publication
    # Nested scope restores imported collaborators between repeated executions.
    with env["monkeypatch"].context() as patch:
        real_create = target.create_generation_snapshot
        patch.setattr(target, "create_generation_snapshot", capture_snapshot)
        patch.setattr(target, "get_settings", env["get_settings"])
        patch.setattr(target, "enqueue_preview", lambda *_: None)
        patch.setattr(target, "publish_event", noop)
        patch.setattr(target, "_snapshot_payload", lambda row: {"id": str(row.id)})
        common = dict(
            _consume_free_generation=partial(
                consume_free_generation,
                is_free=env["is_free"],
                free_business_id=env["free_business_id"],
                user_id=owner.id,
            ),
            accumulated=env["accumulated"],
            baseline=baseline,
            factory=env["factory"],
            files=env["files"],
            ids=ids,
            model_id=env["model_id"],
            project_info=facts,
            prompt_text=env["prompt_text"],
        )
        await target.publish_agent_candidate(
            **common,
            _att_capture=None,
            _attestation_stack="",
            _orch_name=None,
            _max_finalization_proof=env["_max_finalization_proof"],
            _promotion_permit=env["_promotion_permit"],
            progress=GenerationProgress(
                env["factory"], run.id, project.id, message.id, env["_agent_step_log"]
            ),
            runtime=env.get("runtime", GenerationRuntime()),
        )
    return captured


@pytest.mark.parametrize("path", ["agent"])
async def test_caller_publication_rows_order_and_real_git(path, monkeypatch):
    rows, trace = records(), []
    session = OfflineSession(rows, trace)
    env, calls, original = context(rows, lambda: session, trace, monkeypatch)
    result = await execute(path, env)
    snapshot = result["snapshot"]
    owner, project, parent, message, run = rows[:5]
    # Later one-shot hot-reload/repair gates use this exact caller-local row.
    assert result["project"] is project
    assert snapshot.parent_id == parent.id
    assert snapshot.project_id == project.id and snapshot.prompt_text == "P" * 65
    assert snapshot.model_id == "routing"
    assert project.current_snapshot_id == message.snapshot_id == snapshot.id
    assert run.agent_state == {
        "keep": "yes",
        "snapshot_id": str(snapshot.id),
        "commit_sha": snapshot.commit_sha,
        "changed_files": ["empty.txt", "page.txt", "prior.txt"],
    }
    assert owner.free_generations_used == 4
    assert message.content == "A useful change."
    assert (message.tokens_in, message.tokens_out) == (7, 0)
    assert message.agent_steps == env["_agent_step_log"]
    assert trace == [
        "git",
        "add",
        "flush",
        "get:Project",
        "get:GenerationRun",
        "get:Message",
        "get:User",
        "commit",
        "refresh",
    ]
    assert calls[0][0][2] == "AI(agent): " + "P" * 50
    assert calls[0][0][3] == parent.commit_sha
    assert calls[0][1] == {"exact_tree": False}
    assert repo.read_files(project.id, parent.commit_sha) == original
    # Ordinary commits interpret empty strings as deletion, unlike exact_tree.
    assert repo.read_files(project.id, snapshot.commit_sha) == {
        "old.txt": "preserve",
        "page.txt": "new",
    }
    assert len(session.durable) == 6


@pytest.mark.parametrize("path", ["agent"])
@pytest.mark.parametrize("fault", ["git", "upload", "flush", "commit"])
async def test_caller_failure_does_not_publish_durable_rows(path, fault, monkeypatch):
    rows, trace = records(), []
    session = OfflineSession(rows, trace, fault)
    env, _calls, original = context(rows, lambda: session, trace, monkeypatch)

    def fail(*_args, **_kwargs):
        raise RuntimeError(f"fixture {fault} failure")

    if fault == "git":
        monkeypatch.setattr(repo, "commit_files", fail)
    elif fault == "upload":
        monkeypatch.setattr(repo, "_upload", fail)
    with pytest.raises(RuntimeError, match=f"fixture {fault} failure"):
        await execute(path, env)
    assert len(session.durable) == 5
    assert session.durable[0].free_generations_used == 3
    assert session.durable[3].snapshot_id is None
    assert "refresh" not in trace
    if fault in {"git", "upload"}:
        assert "add" not in trace
    if fault == "flush":
        assert "get:Project" not in trace
    assert repo.read_files(rows[1].id, env["current_sha"]) == original


@pytest.mark.parametrize("path", ["agent"])
@pytest.mark.parametrize("missing", [Project, GenerationRun, Message])
async def test_caller_preserves_optional_row_behavior(path, missing, monkeypatch):
    rows, trace = records(), []
    session = OfflineSession(rows, trace)
    env, _calls, _original = context(rows, lambda: session, trace, monkeypatch)
    session.rows = [row for row in rows if not isinstance(row, missing)]
    result = await execute(path, env)
    assert result["project"] is (None if missing is Project else rows[1])
    assert trace[-2:] == ["commit", "refresh"]


@pytest.mark.parametrize("path", ["agent"])
async def test_caller_reexecution_preserves_existing_non_idempotent_semantics(path, monkeypatch):
    rows, trace = records(), []
    session = OfflineSession(rows, trace)
    env, _calls, _original = context(rows, lambda: session, trace, monkeypatch)
    first = (await execute(path, env))["snapshot"]
    second = (await execute(path, env))["snapshot"]
    assert first.id != second.id
    assert first.parent_id == second.parent_id == env["current_snapshot_id"]
    assert rows[0].free_generations_used == 5
    assert len([row for row in session.durable if isinstance(row, Snapshot)]) == 3


@pytest.mark.parametrize("path", ["agent"])
@pytest.mark.parametrize("is_free", [False, True])
async def test_caller_free_business_counter_precedes_user(path, is_free, monkeypatch):
    rows, trace = records(), []
    business = BusinessEntitlement(business_id=uuid4(), free_generations_used=8)
    session = OfflineSession(rows, trace)
    env, _calls, _original = context(
        rows,
        lambda: session,
        trace,
        monkeypatch,
        is_free=is_free,
        free_business_id=business.business_id,
    )
    rows.append(business)
    await execute(path, env)
    assert rows[0].free_generations_used == 3
    assert business.free_generations_used == (9 if is_free else 8)


@pytest.mark.parametrize("path", ["agent"])
@pytest.mark.parametrize("fault", [None, "git", "upload", "flush", "commit"])
async def test_disposable_db_caller_publication(path, fault, test_engine, monkeypatch):
    """CI PostgreSQL only: actual transactions and FK constraints, no model calls."""
    rows, trace = records(), []
    factory = async_sessionmaker(test_engine, expire_on_commit=False)
    env, _calls, original = context(rows, factory, trace, monkeypatch)
    owner, project, parent, message, run = rows
    # Resolve the current-snapshot cycle in the same order as application setup.
    project.current_snapshot_id = None
    async with factory() as session:
        session.add(owner)
        await session.flush()
        session.add(project)
        await session.flush()
        session.add_all([parent, message])
        await session.flush()
        project.current_snapshot_id = parent.id
        session.add(run)
        await session.commit()

    class FaultSession:
        def __init__(self, session):
            self.session = session

        def __getattr__(self, name):
            return getattr(self.session, name)

        async def flush(self):
            if fault == "flush":
                raise RuntimeError("fixture flush failure")
            await self.session.flush()

        async def commit(self):
            if fault == "commit":
                raise RuntimeError("fixture commit failure")
            await self.session.commit()

    @asynccontextmanager
    async def publication_session():
        async with factory() as session:
            yield FaultSession(session)

    env["factory"] = publication_session
    if fault == "git":

        def fail_commit(*_args, **_kwargs):
            raise RuntimeError("fixture git failure")

        monkeypatch.setattr(repo, "commit_files", fail_commit)
    elif fault == "upload":

        def fail_upload(*_args, **_kwargs):
            raise RuntimeError("fixture upload failure")

        monkeypatch.setattr(repo, "_upload", fail_upload)
    if fault:
        with pytest.raises(RuntimeError, match=f"fixture {fault} failure"):
            await execute(path, env)
        async with factory() as session:
            assert len((await session.scalars(select(Snapshot))).all()) == 1
            assert (await session.get(Project, project.id)).current_snapshot_id == parent.id
            saved_message = await session.get(Message, message.id)
            assert saved_message.snapshot_id is None and saved_message.content == "before"
            assert (await session.get(User, owner.id)).free_generations_used == 3
            saved_run = await session.get(GenerationRun, run.id)
            assert saved_run.agent_state == {"changed_files": ["prior.txt"], "keep": "yes"}
        assert repo.read_files(project.id, parent.commit_sha) == original
        return
    result = await execute(path, env)
    async with factory() as session:
        saved = await session.get(Snapshot, result["snapshot"].id)
        assert saved.parent_id == parent.id and saved.commit_sha == result["new_sha"]
        assert (await session.get(Project, project.id)).current_snapshot_id == saved.id
        assert (await session.get(Message, message.id)).snapshot_id == saved.id
        saved_run = await session.get(GenerationRun, run.id)
        assert saved_run.agent_state["snapshot_id"] == str(saved.id)
        assert (await session.get(User, owner.id)).free_generations_used == 4
        assert len((await session.scalars(select(Snapshot))).all()) == 2
    assert repo.read_files(project.id, parent.commit_sha) == original
    repeated = await execute(path, env)
    assert repeated["snapshot"].id != result["snapshot"].id
    async with factory() as session:
        snapshots = (await session.scalars(select(Snapshot))).all()
        assert len(snapshots) == 3  # original + two executions; outer dispatch owns deduplication
        assert (await session.get(User, owner.id)).free_generations_used == 5
        saved_project = await session.get(Project, project.id)
        assert saved_project.current_snapshot_id == repeated["snapshot"].id
    assert repo.read_files(project.id, parent.commit_sha) == original


async def test_agent_proof_exact_tree_deletes_absent_and_preserves_empty(monkeypatch):
    rows, trace = records(), []
    session = OfflineSession(rows, trace)
    env, calls, original = context(
        rows, lambda: session, trace, monkeypatch, _max_finalization_proof=object()
    )
    snapshot = (await execute("agent", env))["snapshot"]
    assert calls[0][1] == {"exact_tree": True}
    assert repo.read_files(rows[1].id, snapshot.commit_sha) == {"page.txt": "new", "empty.txt": ""}
    assert repo.read_files(rows[1].id, env["current_sha"]) == original


async def test_max_publication_requires_green_promotion_permit_before_git(monkeypatch):
    from omnia_api.services.promotion_permit import PromotionPermitError

    rows, trace = records(), []
    session = OfflineSession(rows, trace)
    env, calls, original = context(
        rows,
        lambda: session,
        trace,
        monkeypatch,
        template="max_miniapp",
        _max_finalization_proof=object(),
    )

    with pytest.raises(PromotionPermitError) as raised:
        await execute("agent", env)

    assert raised.value.code == "PROMOTION_PERMIT_MISSING"
    assert calls == [] and trace == []
    assert session.durable[0].free_generations_used == 3
    assert session.durable[1].current_snapshot_id == rows[2].id
    assert session.durable[3].content == "before"
    assert repo.read_files(rows[1].id, env["current_sha"]) == original


async def test_tampered_promotion_permit_fails_before_git(monkeypatch):
    from types import SimpleNamespace

    from omnia_api.services.max_finalization import ProofBundle
    from omnia_api.services.promotion_permit import (
        PromotionPermitError,
        issue_promotion_permit,
        release_receipt_digest,
        release_receipt_ref,
    )

    rows, trace = records(), []
    session = OfflineSession(rows, trace)
    identity = SimpleNamespace(
        id=uuid4(),
        workspace_id=uuid4(),
        generation_run_id=rows[4].id,
        fencing_epoch=7,
        proof_key="1" * 64,
        workspace_revision="2" * 64,
    )
    from omnia_api.services.promotion_permit import canonical_files_digest

    build = SimpleNamespace(
        proof_id=identity.id,
        workspace_id=identity.workspace_id,
        outcome="green",
        artifact_ref="build/sha256/"
        + canonical_files_digest({"page.txt": "new", "empty.txt": ""}),
        redacted_detail="green",
    )
    behavior = SimpleNamespace(
        proof_id=identity.id,
        workspace_id=identity.workspace_id,
        outcome="green",
        artifact_ref="verification/sha256/" + "4" * 64,
        redacted_detail="green",
    )
    release = SimpleNamespace(
        proof_id=identity.id,
        workspace_id=identity.workspace_id,
        outcome="green",
        artifact_ref=release_receipt_ref(
            artifact_digest=build.artifact_ref.rsplit("/", 1)[-1],
            receipt_digest=release_receipt_digest(
                proof_key=identity.proof_key,
                artifact_digest=build.artifact_ref.rsplit("/", 1)[-1],
                detail="green",
            ),
        ),
        redacted_detail="green",
    )
    proof = ProofBundle(
        identity=identity,
        full_build=build,
        runtime=behavior,
        release=release,
    )
    permit = issue_promotion_permit(proof)
    tampered = replace(permit, workspace_revision="9" * 64)
    env, calls, _original = context(
        rows,
        lambda: session,
        trace,
        monkeypatch,
        template="max_miniapp",
        _max_finalization_proof=proof,
        _promotion_permit=tampered,
    )

    with pytest.raises(PromotionPermitError) as raised:
        await execute("agent", env)

    assert raised.value.code == "PROMOTION_PERMIT_TAMPERED"
    assert calls == [] and trace == []
    assert session.durable[0].free_generations_used == 3
    assert session.durable[1].current_snapshot_id == rows[2].id

    async def current_identity():
        return identity

    async def snapshot_files():
        return {"page.txt": "new", "empty.txt": ""}

    env["_promotion_permit"] = permit
    env["runtime"] = GenerationRuntime(
        handle=SimpleNamespace(
            workspace_id=identity.workspace_id,
            current_identity=current_identity,
            refresh_snapshot_files=None,
            snapshot_files=snapshot_files,
        )
    )
    snapshot = (await execute("agent", env))["snapshot"]
    assert calls and snapshot.parent_id == rows[2].id

    stale_identity = SimpleNamespace(
        proof_key=permit.proof_key,
        workspace_id=permit.workspace_id,
        generation_run_id=permit.generation_run_id,
        fencing_epoch=permit.fencing_epoch,
        workspace_revision="9" * 64,
    )
    from omnia_api.services.promotion_permit import require_promotion_permit

    with pytest.raises(PromotionPermitError) as stale:
        require_promotion_permit(permit, current_identity=stale_identity)
    assert stale.value.code == "PROMOTION_PERMIT_STALE"


async def test_max_publication_rejects_foreign_run_changed_tree_and_stale_fence_before_git(
    monkeypatch,
) -> None:
    from types import SimpleNamespace

    from omnia_api.services.max_finalization import ProofBundle
    from omnia_api.services.promotion_permit import (
        PromotionPermitError,
        canonical_files_digest,
        issue_promotion_permit,
        release_receipt_digest,
        release_receipt_ref,
    )

    scenarios = ("foreign_run", "foreign_workspace", "changed_tree", "stale_fence")
    for scenario in scenarios:
        rows, trace = records(), []
        session = OfflineSession(rows, trace)
        run_id = uuid4() if scenario == "foreign_run" else rows[4].id
        identity = SimpleNamespace(
            id=uuid4(),
            workspace_id=uuid4(),
            generation_run_id=run_id,
            fencing_epoch=7,
            proof_key="1" * 64,
            workspace_revision="2" * 64,
        )
        build_digest = canonical_files_digest({"page.txt": "new", "empty.txt": ""})
        build = SimpleNamespace(
            proof_id=identity.id,
            workspace_id=identity.workspace_id,
            outcome="green",
            artifact_ref=f"build/sha256/{build_digest}",
            redacted_detail="green",
        )
        behavior = SimpleNamespace(
            proof_id=identity.id,
            workspace_id=identity.workspace_id,
            outcome="green",
            artifact_ref="verification/sha256/" + "4" * 64,
            redacted_detail="green",
        )
        release = SimpleNamespace(
            proof_id=identity.id,
            workspace_id=identity.workspace_id,
            outcome="green",
            artifact_ref=release_receipt_ref(
                artifact_digest=build_digest,
                receipt_digest=release_receipt_digest(
                    proof_key=identity.proof_key,
                    artifact_digest=build_digest,
                    detail="green",
                ),
            ),
            redacted_detail="green",
        )
        proof = ProofBundle(
            identity=identity,
            full_build=build,
            runtime=behavior,
            release=release,
        )
        permit = issue_promotion_permit(proof)
        current = SimpleNamespace(
            proof_key=identity.proof_key,
            workspace_id=identity.workspace_id,
            generation_run_id=identity.generation_run_id,
            fencing_epoch=8 if scenario == "stale_fence" else identity.fencing_epoch,
            workspace_revision=identity.workspace_revision,
        )

        async def current_identity(current=current):
            return current

        async def snapshot_files():
            return {"page.txt": "new", "empty.txt": ""}

        env, calls, original = context(
            rows,
            lambda: session,
            trace,
            monkeypatch,
            template="max_miniapp",
            _max_finalization_proof=proof,
            _promotion_permit=permit,
            runtime=GenerationRuntime(
                handle=SimpleNamespace(
                    workspace_id=(
                        uuid4() if scenario == "foreign_workspace" else identity.workspace_id
                    ),
                    current_identity=current_identity,
                    refresh_snapshot_files=None,
                    snapshot_files=snapshot_files,
                )
            ),
        )
        if scenario == "changed_tree":
            env["files"] = {"page.txt": "changed after proof", "empty.txt": ""}

        with pytest.raises(PromotionPermitError) as raised:
            await execute("agent", env)

        assert raised.value.code == "PROMOTION_PERMIT_STALE"
        assert calls == [] and trace == []
        assert session.durable[0].free_generations_used == 3
        assert session.durable[1].current_snapshot_id == rows[2].id
        assert repo.read_files(rows[1].id, env["current_sha"]) == original


def test_promotion_permit_rejects_missing_behavior_receipt() -> None:
    from types import SimpleNamespace

    from omnia_api.services.max_finalization import ProofBundle
    from omnia_api.services.promotion_permit import PromotionPermitError, issue_promotion_permit

    identity = SimpleNamespace(
        id=uuid4(),
        workspace_id=uuid4(),
        generation_run_id=uuid4(),
        fencing_epoch=7,
        proof_key="1" * 64,
        workspace_revision="2" * 64,
    )
    green = SimpleNamespace(
        proof_id=identity.id,
        workspace_id=identity.workspace_id,
        outcome="green",
        artifact_ref="build/sha256/" + "3" * 64,
        redacted_detail="green",
    )
    release = SimpleNamespace(
        proof_id=identity.id,
        workspace_id=identity.workspace_id,
        outcome="green",
        artifact_ref="verification/sha256/" + "5" * 64,
        redacted_detail="green",
    )

    with pytest.raises(PromotionPermitError) as raised:
        issue_promotion_permit(
            ProofBundle(identity=identity, full_build=green, runtime=None, release=release)
        )

    assert raised.value.code == "PROMOTION_EVIDENCE_MISSING"


@pytest.mark.parametrize("path", ["agent"])
async def test_missing_business_entitlement_falls_back_to_user(path, monkeypatch):
    rows, trace = records(), []
    session = OfflineSession(rows, trace)
    env, _calls, _original = context(
        rows, lambda: session, trace, monkeypatch, free_business_id=uuid4()
    )
    await execute(path, env)
    assert rows[0].free_generations_used == 4
    assert trace.index("get:BusinessEntitlement") < trace.index("get:User")


@pytest.mark.parametrize("path", ["agent"])
async def test_refresh_failure_happens_after_durable_publication(path, monkeypatch):
    rows, trace = records(), []
    session = OfflineSession(rows, trace, fault="refresh")
    env, _calls, _original = context(rows, lambda: session, trace, monkeypatch)
    with pytest.raises(RuntimeError, match="fixture refresh failure"):
        await execute(path, env)
    assert trace[-2:] == ["commit", "refresh"]
    assert len(session.durable) == 6
    assert session.durable[0].free_generations_used == 4
    assert session.durable[3].snapshot_id == session.durable[-1].id

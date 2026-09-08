"""Characterize real caller publication regions, not a full model/generation run.

Compile the original statements from Git commit through snapshot refresh. This
keeps both real callers in scope before/after extraction without invoking models,
runtime services or thousands of unrelated postprocessing lines. Expected rows
and ordering below are declared independently of the extracted source.
"""

from __future__ import annotations

import ast
import copy
from contextlib import asynccontextmanager
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from omnia_api.models.generation_run import GenerationRun
from omnia_api.models.message import Message
from omnia_api.models.project import Project
from omnia_api.models.snapshot import Snapshot
from omnia_api.models.user import User
from omnia_api.routers import messages
from omnia_api.services import repo


def caller_region(path):
    tree = ast.parse(Path(messages.__file__).read_text(encoding="utf-8"))
    process = next(
        n for n in tree.body if isinstance(n, ast.AsyncFunctionDef) and n.name == "_process_prompt"
    )
    quota = next(
        n
        for n in process.body
        if isinstance(n, ast.AsyncFunctionDef) and n.name == "_consume_free_generation"
    )
    prefix = "AI(agent): " if path == "agent" else "AI: "
    matches = []
    for parent in ast.walk(process):
        body = getattr(parent, "body", None)
        if not isinstance(body, list):
            continue
        for index, node in enumerate(body):
            if (
                isinstance(node, ast.Assign)
                and any(isinstance(t, ast.Name) and t.id == "new_sha" for t in node.targets)
                and any(isinstance(n, ast.Constant) and n.value == prefix for n in ast.walk(node))
            ):
                following = body[index + 1]
                assert isinstance(following, ast.AsyncWith)
                assert "await session.refresh(snapshot)" in ast.unparse(following)
                matches.append([node, following])
    assert len(matches) == 1, "Publication seam changed; review scope explicitly"
    wrapper = ast.AsyncFunctionDef(
        name="characterize",
        args=ast.arguments(posonlyargs=[], args=[], kwonlyargs=[], kw_defaults=[], defaults=[]),
        body=[
            quota,
            *matches[0],
            ast.Return(ast.Call(func=ast.Name(id="locals", ctx=ast.Load()), args=[], keywords=[])),
        ],
        decorator_list=[],
    )
    module = ast.fix_missing_locations(ast.Module(body=[wrapper], type_ignores=[]))
    return compile(module, str(messages.__file__), "exec")


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
    env = dict(vars(messages))
    settings = messages.get_settings().model_copy(update={"use_clean_chat_content": True})
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
        is_free=True,
        free_business_id=None,
    )
    env.update(overrides)
    return env, calls, original


async def execute(path, env):
    exec(caller_region(path), env)
    return await env["characterize"]()


@pytest.mark.parametrize("path", ["agent", "oneshot"])
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
    assert snapshot.model_id == ("routing" if path == "agent" else "Оркестратор Sonnet+DeepSeek")
    assert project.current_snapshot_id == message.snapshot_id == snapshot.id
    assert run.agent_state == {
        "keep": "yes",
        "snapshot_id": str(snapshot.id),
        "commit_sha": snapshot.commit_sha,
        "changed_files": ["empty.txt", "page.txt", "prior.txt"],
    }
    assert owner.free_generations_used == 4
    assert message.content == (
        "A useful change."
        if path == "agent"
        else 'A useful change.\n\n<file path="page.txt">\nnew\n</file>'
        '\n\n<file path="empty.txt">\n\n</file>'
    )
    assert (message.tokens_in, message.tokens_out) == ((7, 0) if path == "agent" else (11, 23))
    assert message.agent_steps == (env["_agent_step_log"] if path == "agent" else None)
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
    assert calls[0][0][2] == ("AI(agent): " if path == "agent" else "AI: ") + "P" * 50
    assert calls[0][0][3] == parent.commit_sha
    assert calls[0][1] == ({"exact_tree": False} if path == "agent" else {})
    assert repo.read_files(project.id, parent.commit_sha) == original
    # Ordinary commits interpret empty strings as deletion, unlike exact_tree.
    assert repo.read_files(project.id, snapshot.commit_sha) == {
        "old.txt": "preserve",
        "page.txt": "new",
    }
    assert len(session.durable) == 6


@pytest.mark.parametrize("path", ["agent", "oneshot"])
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


@pytest.mark.parametrize("path", ["agent", "oneshot"])
@pytest.mark.parametrize("missing", [Project, GenerationRun, Message])
async def test_caller_preserves_optional_row_behavior(path, missing, monkeypatch):
    rows, trace = records(), []
    session = OfflineSession(rows, trace)
    env, _calls, _original = context(rows, lambda: session, trace, monkeypatch)
    session.rows = [row for row in rows if not isinstance(row, missing)]
    result = await execute(path, env)
    assert result["project"] is (None if missing is Project else rows[1])
    assert trace[-2:] == ["commit", "refresh"]


@pytest.mark.parametrize("path", ["agent", "oneshot"])
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


@pytest.mark.parametrize("path", ["agent", "oneshot"])
@pytest.mark.parametrize("is_free", [False, True])
async def test_caller_free_business_counter_precedes_user(path, is_free, monkeypatch):
    rows, trace = records(), []
    business = messages.BusinessEntitlement(business_id=uuid4(), free_generations_used=8)
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


@pytest.mark.parametrize(
    "model,forced,orchestrate,expected",
    [
        ("fallback", "forced", True, "fallback"),
        ("routing", "forced", True, "forced"),
        ("routing", None, False, "routing"),
    ],
)
async def test_oneshot_effective_model_label(model, forced, orchestrate, expected, monkeypatch):
    rows, trace = records(), []
    session = OfflineSession(rows, trace)
    env, _calls, _original = context(
        rows,
        lambda: session,
        trace,
        monkeypatch,
        model_id=model,
        force_model=forced,
        orchestrate=orchestrate,
    )
    assert (await execute("oneshot", env))["snapshot"].model_id == expected


@pytest.mark.parametrize("path", ["agent", "oneshot"])
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


async def test_oneshot_without_usage_keeps_existing_tokens(monkeypatch):
    rows, trace = records(), []
    session = OfflineSession(rows, trace)
    env, _calls, _original = context(rows, lambda: session, trace, monkeypatch, usage_data=None)
    await execute("oneshot", env)
    assert (rows[3].tokens_in, rows[3].tokens_out) == (7, None)


@pytest.mark.parametrize("path", ["agent", "oneshot"])
async def test_missing_business_entitlement_falls_back_to_user(path, monkeypatch):
    rows, trace = records(), []
    session = OfflineSession(rows, trace)
    env, _calls, _original = context(
        rows, lambda: session, trace, monkeypatch, free_business_id=uuid4()
    )
    await execute(path, env)
    assert rows[0].free_generations_used == 4
    assert trace.index("get:BusinessEntitlement") < trace.index("get:User")


@pytest.mark.parametrize("path", ["agent", "oneshot"])
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

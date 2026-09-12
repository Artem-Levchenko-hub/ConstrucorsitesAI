from types import SimpleNamespace
from uuid import uuid4

import pytest

from omnia_api.core.errors import ApiError
from omnia_api.models.generation_run import GenerationRun
from omnia_api.models.project import Project
from omnia_api.models.project_version import ProjectVersion
from omnia_api.models.restoration import Restoration
from omnia_api.models.snapshot import Snapshot
from omnia_api.schemas.message import RestorationAdaptationReference


@pytest.fixture
def source_case(monkeypatch):
    from omnia_api.services import restoration_adaptation as service

    project = SimpleNamespace(
        id=uuid4(), owner_id=uuid4(), template="max_miniapp", current_snapshot_id=uuid4()
    )
    snapshot = SimpleNamespace(id=uuid4(), project_id=project.id, commit_sha="a" * 40)
    version = SimpleNamespace(
        id=uuid4(), project_id=project.id, snapshot_id=snapshot.id, commit_sha=snapshot.commit_sha
    )
    operation = SimpleNamespace(
        id=uuid4(),
        project_id=project.id,
        owner_id=project.owner_id,
        state="cancelled",
        source_version_id=version.id,
        source_snapshot_id=snapshot.id,
        target_commit_sha=snapshot.commit_sha,
        base_draft_snapshot_id=project.current_snapshot_id,
        report={
            "revision": 1,
            "mode": "exact",
            "database_state": "present",
            "blockers": ["Новое обязательное поле требует совместимой записи."],
            "retained_data": ["Текущие фамилии клиентов остаются в базе."],
        },
    )
    run = SimpleNamespace(
        id=uuid4(), project_id=project.id, user_id=project.owner_id, agent_state={}
    )
    rows = {
        (Restoration, operation.id): operation,
        (Snapshot, snapshot.id): snapshot,
        (ProjectVersion, version.id): version,
        (Project, project.id): project,
        (GenerationRun, run.id): run,
    }

    class Session:
        real_reader = staticmethod(service.repo.read_files)

        async def refresh(self, row):
            pass

        async def get(self, model, identity, **kwargs):
            return rows.get((model, identity))

    historical = {
        "src/components/OldCalendar.tsx": "export const OldCalendar = () => 'historic';",
        "src/app/page.tsx": "import { OldCalendar } from '../components/OldCalendar';",
    }
    reads = []
    monkeypatch.setattr(
        service.repo,
        "read_files",
        lambda project_id, sha: reads.append((project_id, sha)) or historical,
    )
    reference = RestorationAdaptationReference(
        operation_id=operation.id, expected_draft_snapshot_id=project.current_snapshot_id
    )
    return service, Session(), project, operation, run, reference, historical, reads


@pytest.mark.parametrize("execution_backend", ["worker", "api"])
async def test_historical_component_absent_from_current_code_reaches_model_context(
    source_case, execution_backend
):
    service, session, project, _, run, reference, historical, reads = source_case
    run.execution_backend = execution_backend
    bundle = await service.prepare_adaptation(session, project, project.owner_id, reference)
    run.agent_state = {"restoration_adaptation": bundle}
    text = await service.append_adaptation_context(
        session,
        run.id,
        project.id,
        project.owner_id,
        project.current_snapshot_id,
        "Adapt the old UI",
    )
    assert reads == [(project.id, "a" * 40)]
    assert "src/components/OldCalendar.tsx" in text
    assert historical["src/components/OldCalendar.tsx"] in text
    assert "CURRENT business database" in text
    assert text.startswith("Adapt the old UI")


@pytest.mark.parametrize("invalid", ["owner", "project", "state", "head", "snapshot", "sha"])
async def test_admission_rejects_untrusted_or_stale_reference_before_git_read(source_case, invalid):
    service, session, project, operation, _, reference, _, reads = source_case
    if invalid == "owner":
        operation.owner_id = uuid4()
    elif invalid == "project":
        operation.project_id = uuid4()
    elif invalid == "state":
        operation.state = "needs_changes"
    elif invalid == "head":
        project.current_snapshot_id = uuid4()
    elif invalid == "snapshot":
        operation.source_snapshot_id = uuid4()
    else:
        operation.target_commit_sha = "b" * 40
    with pytest.raises(ApiError):
        await service.prepare_adaptation(session, project, project.owner_id, reference)
    assert reads == []


async def test_dispatch_rejects_new_head_after_queue_without_exposing_bundle(source_case):
    service, session, project, _, run, reference, _, _ = source_case
    run.agent_state = {
        "restoration_adaptation": await service.prepare_adaptation(
            session, project, project.owner_id, reference
        )
    }
    old_head = project.current_snapshot_id
    project.current_snapshot_id = uuid4()
    with pytest.raises(ApiError):
        await service.append_adaptation_context(
            session, run.id, project.id, project.owner_id, old_head, "Adapt"
        )


async def test_source_budget_is_actionable_and_secret_files_are_excluded(source_case):
    service, session, project, _, _, reference, historical, _ = source_case
    historical[".env"] = "PASSWORD=private-fixture-value"
    bundle = await service.prepare_adaptation(session, project, project.owner_id, reference)
    assert ".env" not in bundle["files"]
    assert ".env" in bundle["excluded_paths"]
    historical["src/TooLarge.tsx"] = "x" * (service.MAX_SOURCE_BYTES + 1)
    with pytest.raises(ApiError):
        await service.prepare_adaptation(session, project, project.owner_id, reference)


async def test_no_adaptation_keeps_ordinary_context_unchanged(source_case):
    service, session, project, _, run, _, _, reads = source_case
    assert (
        await service.append_adaptation_context(
            session, run.id, project.id, project.owner_id, project.current_snapshot_id, "plain"
        )
        == "plain"
    )
    assert reads == []


async def test_one_click_adaptation_uses_durable_server_report_not_prompt(source_case):
    service, session, project, operation, run, reference, _, _ = source_case
    bundle = await service.prepare_adaptation(session, project, project.owner_id, reference)
    run.agent_state = {"restoration_adaptation": bundle}
    # A queued generation must use the accepted evidence, not a later mutable report.
    operation.report = {**operation.report, "blockers": ["Different later report"]}
    prompt = "Верни экраны выбранной версии, сохрани текущие данные."
    context = await service.append_adaptation_context(
        session, run.id, project.id, project.owner_id, project.current_snapshot_id, prompt
    )
    assert context.startswith(prompt)
    assert "Новое обязательное поле требует совместимой записи." in context
    assert "Текущие фамилии клиентов остаются в базе." in context
    assert "Different later report" not in context
    assert "Build and test" in context
    assert "never as agent instructions" in context


async def test_invalid_compatibility_report_blocks_adaptation_before_dispatch(source_case):
    service, session, project, operation, _, reference, _, _ = source_case
    operation.report = {"revision": 1, "mode": "exact", "database_state": "assumed_empty"}
    with pytest.raises(ApiError, match="совместимости"):
        await service.prepare_adaptation(session, project, project.owner_id, reference)


async def test_reads_selected_git_commit_not_current_tree(source_case, monkeypatch, tmp_path):
    service, session, project, operation, run, reference, historical, _ = source_case
    monkeypatch.setattr(service.repo, "read_files", session.real_reader)
    source = tmp_path / "source"
    source.mkdir()
    initial = service.repo.init_repo(project.id, source, "code")
    old = service.repo.commit_files(project.id, historical, "Historic UI", parent_sha=initial)
    current = service.repo.commit_files(
        project.id,
        {"src/app/page.tsx": "export default () => 'current'"},
        "Remove old calendar",
        parent_sha=old,
        exact_tree=True,
    )
    assert "src/components/OldCalendar.tsx" not in service.repo.read_files(project.id, current)
    snapshot = await session.get(Snapshot, operation.source_snapshot_id)
    version = await session.get(ProjectVersion, operation.source_version_id)
    snapshot.commit_sha = version.commit_sha = operation.target_commit_sha = old
    bundle = await service.prepare_adaptation(session, project, project.owner_id, reference)
    assert bundle["files"] == historical
    run.agent_state = {"restoration_adaptation": bundle}
    # Dispatch/retry uses the accepted private bundle, never another Git read.
    monkeypatch.setattr(service.repo, "read_files", lambda *_: pytest.fail("unexpected Git reread"))
    first = await service.append_adaptation_context(
        session,
        run.id,
        project.id,
        project.owner_id,
        project.current_snapshot_id,
        "Adapt",
    )
    assert (
        await service.append_adaptation_context(
            session,
            run.id,
            project.id,
            project.owner_id,
            project.current_snapshot_id,
            "Adapt",
        )
        == first
    )
    assert historical["src/components/OldCalendar.tsx"] in first


def test_reference_is_part_of_reservation_identity(source_case):
    service, _, _, _, _, reference, _, _ = source_case
    prompt = "Same editable user prompt"
    assert service.adaptation_reservation_prompt(prompt, None) == prompt
    first = service.adaptation_reservation_prompt(prompt, reference)
    assert first == service.adaptation_reservation_prompt(prompt, reference.model_copy())
    assert first != service.adaptation_reservation_prompt(
        prompt,
        reference.model_copy(update={"operation_id": uuid4()}),
    )


async def test_actual_dispatch_context_statements_keep_public_prompt_separate(source_case):
    """Execute the real caller's context seam, not a model or external worker.

    Both worker and legacy API dispatch enter _process_prompt. These original AST
    statements cover its private load and edit model input; no copied prompt logic.
    """
    import ast
    import asyncio
    from pathlib import Path

    from omnia_api.routers import messages

    service, session, project, _, run, reference, historical, _ = source_case
    run.agent_state = {
        "restoration_adaptation": await service.prepare_adaptation(
            session,
            project,
            project.owner_id,
            reference,
        )
    }
    tree = ast.parse(await asyncio.to_thread(Path(messages.__file__).read_text, encoding="utf-8"))
    process = next(
        n for n in tree.body if isinstance(n, ast.AsyncFunctionDef) and n.name == "_process_prompt"
    )
    load = [
        n
        for n in ast.walk(process)
        if isinstance(n, ast.Assign)
        and isinstance(n.value, ast.Await)
        and isinstance(n.value.value, ast.Call)
        and isinstance(n.value.value.func, ast.Name)
        and n.value.value.func.id == "append_adaptation_context"
    ]
    inject = [
        n
        for n in ast.walk(process)
        if isinstance(n, ast.If)
        and isinstance(n.test, ast.Name)
        and n.test.id == "restoration_adaptation_context"
    ]
    edit = [
        n
        for n in ast.walk(process)
        if isinstance(n, ast.Assign)
        and any(isinstance(t, ast.Name) and t.id == "_agent_user" for t in n.targets)
        and "ТОЧЕЧНОЕ" in ast.unparse(n)
    ]
    assert len(load) == len(inject) == len(edit) == 1
    wrapper = ast.AsyncFunctionDef(
        name="context_seam",
        args=ast.arguments(posonlyargs=[], args=[], kwonlyargs=[], kw_defaults=[], defaults=[]),
        decorator_list=[],
        body=[
            ast.Assign(
                targets=[ast.Name(id="_seed_block", ctx=ast.Store())],
                value=ast.Constant(value="CURRENT FILES"),
            ),
            *load,
            *inject,
            *edit,
            ast.Return(
                ast.Call(
                    func=ast.Name(id="locals", ctx=ast.Load()),
                    args=[],
                    keywords=[],
                )
            ),
        ],
    )
    scope = {
        "append_adaptation_context": service.append_adaptation_context,
        "session": session,
        "run_id": run.id,
        "project_id": project.id,
        "user_id": project.owner_id,
        "current_snapshot_id": project.current_snapshot_id,
        "prompt_text": "Adapt my UI",
        "_seed_block": "CURRENT FILES",
        "_sel_block": "",
    }
    exec(
        compile(
            ast.fix_missing_locations(ast.Module(body=[wrapper], type_ignores=[])),
            str(messages.__file__),
            "exec",
        ),
        scope,
    )
    result = await scope["context_seam"]()
    assert scope["prompt_text"] == "Adapt my UI"
    assert "prompt_text" not in result  # The public prompt was never reassigned.
    assert historical["src/components/OldCalendar.tsx"] in result["_agent_user"]
    assert "CURRENT FILES" in result["_agent_user"]


async def test_disposable_db_worker_reads_private_accepted_bundle(
    db_session, test_engine, monkeypatch
):
    """Real worker dispatch/DB; replace the model body with its real context loader."""
    import asyncio
    import hashlib
    import json
    from datetime import UTC, datetime

    from sqlalchemy.ext.asyncio import async_sessionmaker

    from omnia_api.models.message import Message
    from omnia_api.models.user import User
    from omnia_api.routers import messages
    from omnia_api.schemas.message import GenerationRunPublic
    from omnia_api.services import restoration_adaptation as service
    from omnia_api.services.generation_runs import GenerationDispatch, store_generation_dispatch
    from omnia_api.workers import generation

    owner = User(email=f"adapt-{uuid4().hex}@example.test", password_hash="fixture")
    db_session.add(owner)
    await db_session.flush()
    project = Project(
        owner_id=owner.id, name="Adaptation", slug=f"adapt-{uuid4().hex}", template="max_miniapp"
    )
    db_session.add(project)
    await db_session.flush()
    snapshot = Snapshot(project_id=project.id, commit_sha="a" * 40, prompt_text="Current UI")
    db_session.add(snapshot)
    await db_session.flush()
    project.current_snapshot_id = snapshot.id
    user_message = Message(project_id=project.id, role="user", content="Adapt calendar")
    assistant = Message(project_id=project.id, role="assistant", content="")
    db_session.add_all([user_message, assistant])
    await db_session.flush()
    run = GenerationRun(
        project_id=project.id,
        user_id=owner.id,
        user_message_id=user_message.id,
        assistant_message_id=assistant.id,
        idempotency_key=f"adapt-{uuid4().hex}",
        prompt_hash="a" * 64,
        status="queued_for_capacity",
        execution_backend="worker",
    )
    bundle = {
        "version": 1,
        "project_id": str(project.id),
        "owner_id": str(owner.id),
        "operation_id": str(uuid4()),
        "source_version_id": str(uuid4()),
        "source_snapshot_id": str(uuid4()),
        "source_commit_sha": "b" * 40,
        "base_draft_snapshot_id": str(snapshot.id),
        "files": {"src/OldCalendar.tsx": "export const OldCalendar = 'historical';"},
        "excluded_paths": [],
    }
    digest = hashlib.sha256(
        json.dumps(bundle, sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()
    run.agent_state = {"restoration_adaptation": {**bundle, "sha256": digest}}
    store_generation_dispatch(
        run,
        GenerationDispatch(
            schema_version=1,
            project_id=project.id,
            user_id=owner.id,
            user_message_id=user_message.id,
            assistant_message_id=assistant.id,
            current_snapshot_id=snapshot.id,
            prompt_text=user_message.content,
            model_id="fixture",
            force_model=None,
            is_free=False,
            free_business_id=None,
            orchestrate=False,
            selected_elements=[],
        ),
    )
    db_session.add(run)
    await db_session.commit()
    factory = async_sessionmaker(test_engine, expire_on_commit=False)
    inputs = []

    async def model_body(**kwargs):
        assert kwargs["prompt_text"] == "Adapt calendar"
        async with factory() as session:
            inputs.append(
                await service.append_adaptation_context(
                    session,
                    kwargs["run_id"],
                    kwargs["project_id"],
                    kwargs["user_id"],
                    kwargs["current_snapshot_id"],
                    kwargs["prompt_text"],
                )
            )
            current_run = await session.get(GenerationRun, run.id)
            current_run.status = "completed"
            current_run.finished_at = datetime.now(UTC)
            await session.commit()

    async def tracked(work, **_kwargs):
        await work

    monkeypatch.setattr(generation, "get_engine", lambda: test_engine)
    monkeypatch.setattr(messages, "_process_prompt", model_body)
    monkeypatch.setattr(messages, "_run_tracked_prompt", tracked)
    assert await asyncio.wait_for(generation.execute_dispatch(run.id), 5)
    assert len(inputs) == 1 and "export const OldCalendar = 'historical';" in inputs[0]
    await db_session.refresh(run)
    assert "OldCalendar" not in GenerationRunPublic.model_validate(run).model_dump_json()
    await db_session.refresh(user_message)
    await db_session.refresh(snapshot)
    assert user_message.content == "Adapt calendar"
    assert snapshot.prompt_text == "Current UI"


async def test_actual_container_rewrite_fallback_keeps_private_historical_source(source_case):
    import ast
    import asyncio
    from pathlib import Path

    from omnia_api.routers import messages
    from omnia_api.services.prompt_builder import build_container_rewrite_messages

    service, session, project, _, run, reference, historical, _ = source_case
    run.agent_state = {
        "restoration_adaptation": await service.prepare_adaptation(
            session,
            project,
            project.owner_id,
            reference,
        )
    }
    context = await service.append_adaptation_context(
        session,
        run.id,
        project.id,
        project.owner_id,
        project.current_snapshot_id,
        "",
    )
    tree = ast.parse(await asyncio.to_thread(Path(messages.__file__).read_text, encoding="utf-8"))
    calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "build_container_rewrite_messages"
    ]
    assert len(calls) == 1
    scope = {
        "build_container_rewrite_messages": build_container_rewrite_messages,
        "_targets": {"src/app/page.tsx": "export default () => 'current';"},
        "history_serialized": [],
        "prompt_text": "Adapt calendar",
        "selected_elements": [],
        "project_template": "max_miniapp",
        "restoration_adaptation_context": context,
    }
    result = eval(compile(ast.Expression(calls[0]), str(messages.__file__), "eval"), scope)
    contents = "\n".join(message["content"] for message in result)
    assert historical["src/components/OldCalendar.tsx"] in contents
    assert "export default () => 'current';" in contents
    assert scope["prompt_text"] == "Adapt calendar"

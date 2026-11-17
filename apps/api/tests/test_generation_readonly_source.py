import asyncio
import json
from uuid import UUID, uuid4

import httpx
import pytest
from sqlalchemy import func, select

from yleum_api.core import db
from yleum_api.core.config import get_settings
from yleum_api.core.deps import get_current_user
from yleum_api.main import app
from yleum_api.models.generation_credits import GenerationCreditReservation
from yleum_api.models.generation_run import GenerationRun
from yleum_api.models.message import Message
from yleum_api.models.project import Project
from yleum_api.models.project_memory import ProjectMemoryRevision
from yleum_api.models.project_version import ProjectVersion
from yleum_api.models.snapshot import Snapshot
from yleum_api.services import repo
from yleum_api.services.accounts import create_account
from yleum_api.services.generation import (
    acceptance,
    lightweight_turns,
    provider_attempts,
    supervisor,
)
from yleum_api.services.generation.source_intent import (
    readonly_source_request,
    source_change_required,
)
from yleum_api.services.generation_runs import load_generation_dispatch


@pytest.mark.parametrize(
    ("prompt", "requires_change"),
    [
        ("Расскажи, как работает каталог. Ничего не меняй.", False),
        ("Do not change anything, just explain how the catalog works.", False),
        ("Как работает каталог?", False),
        ("How does the catalog work?", False),
        ("Explain how to remove History and add sorting.", False),
        ("Explain how the add and remove buttons work.", False),
        ("Explain how the catalog works and remove the History tab.", True),
        ("Расскажи, как работает каталог и убери вкладку История.", True),
        ("Расскажи, как работает каталог. Исправь сортировку. Данные не меняй.", True),
    ],
)
def test_analysis_intent_preserves_a_separately_requested_edit(prompt, requires_change):
    assert source_change_required(prompt) is requires_change
    assert readonly_source_request(prompt) is (not requires_change)


def test_bare_continuation_remains_eligible_for_generation():
    assert not readonly_source_request("Продолжи")


@pytest.fixture
async def existing_catalog(db_session, monkeypatch):
    owner = await create_account(
        db_session, email=f"readonly-{uuid4().hex[:8]}@example.com",
        password_hash="unused", email_verified_at=None, consent=None,
    )
    project = Project(
        owner_id=owner.id, name="Catalog", slug=f"readonly-{uuid4().hex[:8]}",
        template="max_miniapp",
    )
    db_session.add(project)
    await db_session.flush()
    path = "src/app/api/catalog/route.ts"
    starter_sha = repo.init_from_files(project.id, {path: "// initial catalog"}, "Starter")
    files = {
        path: "export function GET() { return Response.json({sortedBy: 'title'}); }",
        ".env": "PRIVATE_RUNTIME_MARKER=private",
        "src/lib/legacy.ts": "const apiKey = 'sk-synthetic-fixture-key-0000000';",
    }
    commit_sha = repo.commit_files(project.id, files, "Build catalog", parent_sha=starter_sha)
    snapshot = Snapshot(project_id=project.id, commit_sha=commit_sha, prompt_text="Build catalog")
    db_session.add(snapshot)
    await db_session.flush()
    project.current_snapshot_id = snapshot.id
    await db_session.commit()

    class Redis:
        async def publish(self, *_args):
            pass

    async def current_user():
        return owner

    app.dependency_overrides[get_current_user] = current_user
    monkeypatch.setattr(acceptance, "get_redis", Redis)
    monkeypatch.setenv("PHONE_VERIFICATION_ENABLED", "false")
    get_settings.cache_clear()
    yield project, snapshot, files
    get_settings.cache_clear()


async def test_readonly_source_answer_leaves_the_catalog_and_version_history_intact(
    client, db_session, test_engine, existing_catalog, monkeypatch,
):
    project, snapshot, files = existing_catalog
    monkeypatch.setenv("MOCK_LLM", "false")
    monkeypatch.setenv("UNLIMITED_GENERATIONS", "false")
    monkeypatch.setenv("GENERATION_PROVIDER_ATTEMPTS_ENABLED", "true")
    monkeypatch.setenv("USE_PROJECT_MEMORY", "true")
    monkeypatch.setenv("USE_GENERATION_WORKER", "true")
    get_settings.cache_clear()
    monkeypatch.setattr(db, "get_engine", lambda: test_engine)
    monkeypatch.setattr(supervisor, "get_engine", lambda: test_engine)
    monkeypatch.setattr(lightweight_turns, "get_engine", lambda: test_engine)
    monkeypatch.setattr(provider_attempts, "get_engine", lambda: test_engine)
    received = []
    build_dispatches = []
    events = []
    finish = asyncio.Event()

    async def respond(request):
        received.append(json.loads(request.content))
        await finish.wait()
        return httpx.Response(200, json={
            "choices": [{"finish_reason": "stop", "message": {
                "content": "Каталог сортируется по названию."
            }}],
        })

    async def wait_for_stop(_run_id):
        await asyncio.Future()

    async def clear_stop(_run_id):
        pass

    async def emit(_project_id, event, payload):
        events.append((event, payload))

    original_client = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: original_client(
        transport=httpx.MockTransport(respond), **kwargs,
    ))
    monkeypatch.setattr(supervisor, "_wait_for_generation_cancel", wait_for_stop)
    monkeypatch.setattr(supervisor, "clear_generation_cancel", clear_stop)
    monkeypatch.setattr(lightweight_turns, "publish_event", emit)
    monkeypatch.setattr(
        acceptance, "_spawn_process_prompt", lambda **kwargs: build_dispatches.append(kwargs),
    )

    payload = {
        "prompt": "Расскажи, как работает каталог. Ничего не меняй.",
        "idempotency_key": "catalog-explanation",
        "design_preset_id": "editorial-trust",
    }
    url = f"/api/projects/{project.id}/prompt"
    response = await client.post(url, json=payload)
    assert response.status_code == 202
    result = response.json()
    assert result["mode"] == "clarify"
    assert not build_dispatches
    run_id = UUID(result["run_id"])
    task = supervisor._PROMPT_TASKS[run_id]
    finish.set()
    await asyncio.wait_for(task, timeout=10)

    assert len(received) == 1
    request = received[0]
    assert "tools" not in request and "tool_choice" not in request
    assert request["metadata"]["run_id"] == str(run_id)
    assert request["metadata"]["stage"] == "discovery"
    assert request["metadata"]["generation_provider_identity"]["run_id"] == str(run_id)
    context = request["messages"][-1]["content"]
    assert "src/app/api/catalog/route.ts" in context and "sortedBy" in context
    assert "initial catalog" not in context
    assert "PRIVATE_RUNTIME_MARKER" not in context
    assert "synthetic-fixture-key" not in context

    await db_session.refresh(project)
    assert project.current_snapshot_id == snapshot.id
    assert project.design_preset_id is None
    assert repo.read_files(project.id, snapshot.commit_sha) == files
    assert await db_session.scalar(select(func.count()).select_from(Snapshot)) == 1
    assert await db_session.scalar(select(func.count()).select_from(ProjectVersion)) == 0
    assert await db_session.scalar(select(func.count()).select_from(ProjectMemoryRevision)) == 0
    run = await db_session.get(GenerationRun, run_id, populate_existing=True)
    assert run is not None and run.execution_backend == "api"
    assert "dispatch" not in run.agent_state
    hold = await db_session.get(GenerationCreditReservation, run_id, populate_existing=True)
    assert hold is not None and hold.state == "released"
    message = await db_session.get(Message, UUID(result["message_id"]), populate_existing=True)
    assert message is not None and message.content == "Каталог сортируется по названию."
    assert message.snapshot_id is None
    assert events[-1] == ("llm.done", {"message_id": result["message_id"], "snapshot_id": None})
    replay = await client.post(url, json=payload)
    assert replay.status_code == 202
    assert replay.json()["run_status"] == "completed"
    assert replay.json()["replayed"] is True
    assert replay.json()["message_id"] == result["message_id"]
    assert len(received) == 1


async def test_explanation_with_requested_edit_still_dispatches_the_edit(
    client, db_session, existing_catalog, monkeypatch,
):
    project, snapshot, _ = existing_catalog
    monkeypatch.setenv("USE_GENERATION_WORKER", "false")
    get_settings.cache_clear()
    build_dispatches = []
    monkeypatch.setattr(
        acceptance, "_spawn_process_prompt", lambda **kwargs: build_dispatches.append(kwargs),
    )
    prompt = "Explain how the catalog works and remove the History tab."
    response = await client.post(
        f"/api/projects/{project.id}/prompt",
        json={"prompt": prompt, "idempotency_key": "catalog-edit"},
    )
    assert response.status_code == 202
    assert response.json()["mode"] in {"edit", "build"}
    assert len(build_dispatches) == 1
    run = await db_session.get(GenerationRun, UUID(response.json()["run_id"]))
    dispatch = load_generation_dispatch(run)
    assert dispatch.prompt_text == prompt
    assert dispatch.current_snapshot_id == snapshot.id

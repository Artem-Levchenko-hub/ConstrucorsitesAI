"""Real owner/history/object bindings; no provider or workspace mutation."""

import hashlib
from datetime import timedelta
from uuid import uuid4

import pytest

from tests.test_agent_retry_context import history as history_fixture  # noqa: F401
from yleum_api.core.errors import ApiError
from yleum_api.models.message import Message
from yleum_api.schemas.message import GenerationContinuationReference, PromptRequest
from yleum_api.services.generation import draft_continuation as dc
from yleum_api.services.generation import source_checkpoint as sc
from yleum_api.services.generation.recovery_contract import RecoveryContractError


@pytest.fixture
def draft(history_fixture, monkeypatch):  # noqa: F811
    history = history_fixture
    project = history.ensure_project()
    previous = history.add("Клиентский дневник питания с оливковым оформлением")
    previous.finished_at = previous.created_at + timedelta(milliseconds=50)
    raw = sc.encode_checkpoint(
        project_id=project.id,
        user_id=project.owner_id,
        run_id=previous.id,
        baseline_sha=history.baseline.sha,
        files={
            "src/app/page.tsx": "export const savedFeature = 'ready'",
            "src/lib/menu.ts": "saved menu",
        },
        deleted_paths=["src/app/old.tsx"],
    )
    digest = hashlib.sha256(raw).hexdigest()
    previous.agent_state = {
        **previous.agent_state,
        "source_checkpoint": {
            "digest": digest,
            "baseline_sha": history.baseline.sha,
            "original_prompt": "Клиентский дневник питания с оливковым оформлением",
        },
    }
    history.session.commit()
    calls = []

    async def load(project_id, run_id, *, object_key, digest):
        calls.append((project_id, run_id, object_key, digest))
        assert project_id == project.id and run_id == previous.id
        assert object_key == sc._object_key(project_id, run_id, digest)
        return raw

    async def quiesced(session, **values):
        assert values == {
            "project_id": project.id,
            "user_id": project.owner_id,
            "predecessor_id": previous.id,
            "allow_current_lease_observation": True,
        }

    monkeypatch.setattr(sc, "load_checkpoint_object", load)
    monkeypatch.setattr(
        "yleum_api.services.generation.recovery_requirements.require_quiesced_predecessor",
        quiesced,
    )
    reference = GenerationContinuationReference(
        run_id=previous.id,
        assistant_message_id=previous.assistant_message_id,
        expected_snapshot_id=project.current_snapshot_id,
    )
    return history, project, previous, reference, calls


async def prepare(case, reference=None, **changes):
    history, project, _, original, _ = case
    payload = PromptRequest(prompt="Продолжи сборку", continuation=reference or original, **changes)
    return await dc.prepare(
        history.factory(), project=project, user_id=project.owner_id, payload=payload, existing=None
    )


async def child(case):
    history, _, _, _, _ = case
    value = await prepare(case)
    current = history.add("Продолжи сборку", status="running")
    current.agent_state = {
        **current.agent_state,
        dc.KEY: value.model_dump(mode="json"),
        "source_checkpoint_resume": value.source_reference(),
    }
    history.session.commit()
    return current


async def test_prepare_pins_one_private_object_without_modifying_previous(draft):
    history, project, previous, reference, calls = draft
    before = dict(previous.agent_state)
    value = await prepare(draft)
    assert value.reference() == reference
    assert value.source_run_id == previous.id and len(calls) == 1
    assert calls[0][0] == project.id and calls[0][1] == previous.id
    assert previous.status == "failed" and previous.agent_state == before
    assert history.session.get(Message, previous.user_message_id).content.startswith("Клиентский")


@pytest.mark.parametrize("field", ["run_id", "assistant_message_id", "expected_snapshot_id"])
async def test_another_target_cannot_be_used_with_the_same_replay_key(draft, field):
    history, project, _, reference, calls = draft
    current = await child(draft)
    calls.clear()
    wrong = reference.model_copy(update={field: uuid4()})
    with pytest.raises(ApiError) as error:
        await dc.prepare(
            history.factory(),
            project=project,
            user_id=project.owner_id,
            payload=PromptRequest(prompt="Продолжи сборку", continuation=wrong),
            existing=current,
        )
    assert error.value.code == "idempotency_conflict"
    assert not calls


async def test_replay_keeps_accepted_target_without_reloading_storage(draft):
    history, project, _, reference, calls = draft
    current = await child(draft)
    calls.clear()
    value = await dc.prepare(
        history.factory(),
        project=project,
        user_id=project.owner_id,
        payload=PromptRequest(prompt="Продолжи сборку", continuation=reference),
        existing=current,
    )
    assert value.reference() == reference and calls == []
    with pytest.raises(ApiError):
        await dc.prepare(
            history.factory(),
            project=project,
            user_id=project.owner_id,
            payload=PromptRequest(prompt="Продолжи сборку"),
            existing=current,
        )


@pytest.mark.parametrize(
    "change", ["owner", "assistant", "snapshot", "latest", "running", "unfinished"]
)
async def test_stale_or_foreign_predecessor_is_rejected_before_storage(draft, change):
    history, _project, previous, reference, calls = draft
    if change == "owner":
        previous.user_id = uuid4()
    elif change == "assistant":
        reference = reference.model_copy(update={"assistant_message_id": uuid4()})
    elif change == "snapshot":
        reference = reference.model_copy(update={"expected_snapshot_id": uuid4()})
    elif change == "latest":
        history.add("Другая работа")
    elif change == "running":
        previous.status = "running"
    else:
        previous.finished_at = None
    history.session.commit()
    with pytest.raises(ApiError) as error:
        await prepare(draft, reference)
    assert error.value.details is not None and error.value.details["retryable"] is False
    assert calls == []


async def test_cleanup_uncertainty_stops_before_source_or_credit_work(draft, monkeypatch):
    async def pending(*args, **kwargs):
        raise RecoveryContractError("release outcome unknown")

    monkeypatch.setattr(
        "yleum_api.services.generation.recovery_requirements.require_quiesced_predecessor",
        pending,
    )
    with pytest.raises(ApiError) as error:
        await prepare(draft)
    assert error.value.status_code == 409 and draft[-1] == []
    assert error.value.code == "source_changed"
    assert error.value.details == {"reason": "continuation_cleanup_pending", "retryable": True}
    assert error.value.message == (
        "Среда пока не готова. Попробуйте продолжить сборку из сохранённого черновика чуть позже."
    )


@pytest.mark.parametrize("unavailable", ["missing", "exception", "corrupt"])
async def test_unavailable_draft_never_becomes_a_fresh_build(draft, monkeypatch, unavailable):
    async def load(*args, **kwargs):
        if unavailable == "exception":
            raise OSError("temporary storage outage")
        return None if unavailable == "missing" else b'{"files":{}}'

    monkeypatch.setattr(sc, "load_checkpoint_object", load)
    with pytest.raises(ApiError) as error:
        await prepare(draft)
    assert error.value.status_code == 503
    assert error.value.code == "unavailable"
    reason = (
        "continuation_storage_unavailable" if unavailable == "exception"
        else "continuation_checkpoint_unavailable"
    )
    assert error.value.details == {"reason": reason, "retryable": True}
    assert draft[2].status == "failed"


async def test_addressed_restore_uses_saved_files_deletions_and_real_brief(draft):
    history, _, previous, _, _ = draft
    previous.error = "MAX_FINALIZATION_FAILED: AssertionError in tests/nutrition-http.mjs:36"
    history.session.commit()
    current = await child(draft)
    saved, task = await sc.adjacent_checkpoint(
        factory=history.factory,
        ids=history.ids(current),
        baseline=history.baseline,
    )
    assert saved["files"]["src/lib/menu.ts"] == "saved menu"
    assert saved["deleted_paths"] == ["src/app/old.tsx"]
    assert task.startswith("Клиентский дневник") and previous.status == "failed"
    classification, prompt, _ = await history.classify(current)
    assert classification.is_continue is True and classification.is_edit is False
    assert "сохранённых исходников" in prompt and task in prompt
    feedback = await dc.continuation_feedback(history.factory, history.ids(current))
    assert "tests/nutrition-http.mjs:36" in feedback
    assert "remaining_diagnostic" in feedback


@pytest.mark.parametrize(
    "change",
    [
        "project_head",
        "object_pin",
        "source_request",
        "child_message",
        "child_status",
        "child_owner",
        "child_project",
        "parent_assistant",
    ],
)
async def test_cold_restore_reproves_source_and_never_falls_through(draft, change):
    history, project, previous, _, _ = draft
    current = await child(draft)
    if change == "project_head":
        project.current_snapshot_id = uuid4()
    elif change == "object_pin":
        current.agent_state = {**current.agent_state, "source_checkpoint_resume": {}}
    elif change == "source_request":
        previous.prompt_hash = "0" * 64
    elif change == "child_message":
        history.session.get(Message, current.user_message_id).content = "Подмени задачу"
    elif change == "child_status":
        current.status = "cancelled"
    elif change == "child_owner":
        current.user_id = uuid4()
    elif change == "child_project":
        current.project_id = uuid4()
    else:
        previous.assistant_message_id = uuid4()
    history.session.commit()
    with pytest.raises(ApiError):
        await sc.adjacent_checkpoint(
            factory=history.factory,
            ids=history.ids(current),
            baseline=history.baseline,
        )


async def test_public_affordance_binds_exact_failed_run_and_current_snapshot(draft):
    history, project, previous, reference, _ = draft
    assert dc.public_reference(previous, project, history.snapshot) == reference
    previous.status = "running"
    assert dc.public_reference(previous, project, history.snapshot) is None
    previous.status = "completed"
    assert dc.public_reference(previous, project, history.snapshot) is None


def test_request_reference_rejects_private_object_fields():
    with pytest.raises(ValueError):
        GenerationContinuationReference(
            run_id=uuid4(),
            assistant_message_id=uuid4(),
            expected_snapshot_id=None,
            source_run_id=uuid4(),
            checkpoint_sha256="0" * 64,
        )


async def test_postgres_admission_pins_original_engine_and_replays_one_child(
    db_session,
    test_engine,
    monkeypatch,
):
    from datetime import UTC, datetime

    from sqlalchemy import func, select
    from sqlalchemy.ext.asyncio import async_sessionmaker

    from tests.test_generation_runs import _owner_and_project
    from yleum_api.models.generation_run import GenerationRun
    from yleum_api.services.generation.acceptance import PromptAcceptance
    from yleum_api.services.generation.contracts import GenerationIds, SourceBaseline
    from yleum_api.services.generation_engines import engine_selection, selected_engine
    from yleum_api.services.generation_runs import (
        GenerationDispatch,
        prompt_hash,
        store_generation_dispatch,
    )

    owner, project = await _owner_and_project(db_session)
    project.template = "max_miniapp"
    original = Message(project_id=project.id, role="user", content="Сохрани мой дневник питания")
    assistant = Message(project_id=project.id, role="assistant", content="", model_id="gpt-6.2-sol")
    db_session.add_all([original, assistant])
    await db_session.flush()
    parent = GenerationRun(
        project_id=project.id,
        user_id=owner.id,
        user_message_id=original.id,
        assistant_message_id=assistant.id,
        status="running",
        response_mode="build",
        idempotency_key=str(uuid4()),
        prompt_hash=prompt_hash(original.content),
        agent_state=engine_selection("claude"),
    )
    store_generation_dispatch(
        parent,
        GenerationDispatch(
            schema_version=1,
            project_id=project.id,
            user_id=owner.id,
            user_message_id=original.id,
            assistant_message_id=assistant.id,
            current_snapshot_id=None,
            prompt_text=original.content,
            model_id="test-no-provider",
            force_model=None,
            is_free=False,
            orchestrate=True,
            selected_elements=None,
        ),
    )
    db_session.add(parent)
    await db_session.commit()
    await sc.persist_checkpoint(
        factory=async_sessionmaker(test_engine, expire_on_commit=False),
        ids=GenerationIds(parent.id, project.id, owner.id, original.id, assistant.id),
        baseline=SourceBaseline(None, None, {}),
        files={"src/app/page.tsx": "saved product"},
        original_prompt=original.content,
    )
    # The checkpoint writer commits in its own session; admission is a new HTTP
    # request in production, so refresh this reused fixture before continuing.
    await db_session.refresh(parent)
    parent.status = "failed"
    parent.finished_at = datetime.now(UTC)
    await db_session.commit()
    cleanup = []

    async def quiesced(session, **values):
        cleanup.append(values["predecessor_id"])

    monkeypatch.setattr(
        "yleum_api.services.generation.recovery_requirements.require_quiesced_predecessor",
        quiesced,
    )
    reference = GenerationContinuationReference(
        run_id=parent.id,
        assistant_message_id=assistant.id,
        expected_snapshot_id=None,
    )
    payload = PromptRequest(
        prompt="Продолжи сборку", idempotency_key="continue-db-fixture", continuation=reference
    )
    admission = PromptAcceptance(
        project_id=project.id,
        project=project,
        payload=payload,
        session=db_session,
        current_user=owner,
    )
    assert await admission.reserve_or_replay() is None
    current = admission.generation_run
    assert current.id != parent.id and selected_engine(current.agent_state) == "claude"
    assert dc.marker(current).source_run_id == parent.id
    assert (
        current.agent_state["source_checkpoint_resume"]["digest"]
        == (parent.agent_state["source_checkpoint"]["digest"])
    )
    # Complete the accepted response identity, with no model/dispatch operation.
    request = Message(project_id=project.id, role="user", content=payload.prompt)
    reply = Message(project_id=project.id, role="assistant", content="")
    db_session.add_all([request, reply])
    await db_session.flush()
    current.user_message_id, current.assistant_message_id = request.id, reply.id
    current.response_payload = {
        "run_id": str(current.id),
        "message_id": str(reply.id),
        "snapshot_id": None,
        "mode": "build",
    }
    await db_session.commit()
    retry = PromptAcceptance(
        project_id=project.id,
        project=project,
        payload=payload,
        session=db_session,
        current_user=owner,
    )
    response = await retry.reserve_or_replay()
    assert response.replayed and response.run_id == current.id
    assert cleanup == [parent.id]
    assert (
        await db_session.scalar(
            select(func.count())
            .select_from(GenerationRun)
            .where(
                GenerationRun.project_id == project.id,
            )
        )
        == 2
    )
    changed = payload.model_copy(
        update={
            "continuation": reference.model_copy(
                update={"assistant_message_id": uuid4()},
            )
        }
    )
    with pytest.raises(ApiError) as error:
        await PromptAcceptance(
            project_id=project.id,
            project=project,
            payload=changed,
            session=db_session,
            current_user=owner,
        ).reserve_or_replay()
    assert error.value.code == "idempotency_conflict"

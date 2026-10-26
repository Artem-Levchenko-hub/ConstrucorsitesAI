"""Real admitted history and attachment loaders; storage bytes stay local."""

import base64
import copy
import hashlib
import json
from dataclasses import replace
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import JSON, Column, MetaData, Table
from sqlalchemy.dialects.postgresql import JSONB

from tests.test_agent_retry_context import history  # noqa: F401 - real SQLite history
from tests.test_reference_images import png_bytes
from yleum_api.models.font_asset import FontAsset
from yleum_api.models.message import Message
from yleum_api.models.reference_image import ReferenceImage
from yleum_api.services import agent_builder, agent_native
from yleum_api.services import reference_images as images
from yleum_api.services.generation.agent_pipeline import _load_turn_reference_images
from yleum_api.services.generation.agent_seed import stage_owner_fonts
from yleum_api.services.generation.retry_request import resolve_request_source


@pytest.fixture
def visual_retry(history, monkeypatch):  # noqa: F811
    project = history.ensure_project()
    metadata = MetaData()
    for model in (ReferenceImage, FontAsset):
        Table(
            model.__tablename__,
            metadata,
            *(
                Column(
                    column.name,
                    JSON() if isinstance(column.type, JSONB) else column.type,
                    primary_key=column.primary_key,
                )
                for column in model.__table__.columns
            ),
        )
    metadata.create_all(history.session.get_bind())
    raw = png_bytes()
    row = ReferenceImage(
        id=uuid4(),
        project_id=project.id,
        uploader_id=project.owner_id,
        filename="reference.png",
        mime_type="image/png",
        sha256=hashlib.sha256(raw).hexdigest(),
        object_key="local-reference",
        size_bytes=len(raw),
        width=1,
        height=1,
        status="ready",
        is_bound=True,
    )
    history.session.add(row)
    history.session.commit()
    monkeypatch.setattr(images, "_load", lambda loaded: raw)
    original = history.add(
        "Используй референс для дизайна этот",
        references=[images.public_image(row)],
    )
    return SimpleNamespace(history=history, original=original, image=row, raw=raw)


@pytest.mark.parametrize("native", [False, True])
async def test_manual_retry_before_checkpoint_keeps_image_bytes_for_two_retries(
    visual_retry,
    monkeypatch,
    native,
):
    case = visual_retry
    for _ in range(2):
        current = case.history.add("Попробуй ещё раз", status="running", mode="edit")
        result, prompt, _ = await case.history.classify(current)
        references = await _load_turn_reference_images(
            case.history.factory,
            case.history.ids(current),
            case.history.baseline,
        )
        seen = []

        async def execute(action):
            return {"ok": True}

        if native:

            async def complete(*args, **kwargs):
                seen.extend(args[2][0]["content"])
                return {
                    "content": [{"type": "text", "text": "unfinished"}],
                    "stop_reason": "end_turn",
                }

            monkeypatch.setattr(agent_native, "_call_messages", complete)
            await agent_native.run_native_build(
                system="local",
                task=prompt,
                execute=execute,
                reference_images=references,
                max_steps=1,
            )
            block = next(
                (item for item in seen if isinstance(item, dict) and item.get("type") == "image"),
                None,
            )
            assert block is not None, "plain retry dropped the original visual request"
            actual = base64.b64decode(block["source"]["data"])
        else:

            async def complete(messages, model, **kwargs):
                seen.extend(messages[1]["content"])
                return '<omnia:action name="done">{"summary":"local"}</omnia:action>'

            await agent_builder.run_agent_build(
                system_prompt="local",
                user_prompt=prompt,
                model="local",
                execute=execute,
                complete=complete,
                reference_images=references,
                max_steps=1,
            )
            block = next(
                (
                    item
                    for item in seen
                    if isinstance(item, dict) and item.get("type") == "image_url"
                ),
                None,
            )
            assert block is not None, "plain retry dropped the original visual request"
            actual = base64.b64decode(block["image_url"]["url"].split(",", 1)[1])
        assert actual == case.raw
        assert result.prompt == "Используй референс для дизайна этот"
        assert "source_checkpoint_resume" not in current.agent_state
        current.status = "failed"
        case.history.session.commit()


async def test_manual_retry_keeps_original_fonts_and_images_on_cold_load(visual_retry, monkeypatch):
    from yleum_api.services import font_assets

    case, records = visual_retry, visual_retry.history
    project = records.ensure_project()
    bundle = {
        "family": "Owner Reference",
        "license_text": "test fixture license",
        "faces": [
            {
                "style": "normal",
                "weight": "400",
                "unicode_range": "U+0000-00FF",
                "data": base64.b64encode(b"local-font-fixture").decode(),
            }
        ],
    }
    raw = json.dumps(bundle).encode()
    font = FontAsset(
        id=uuid4(),
        project_id=project.id,
        uploader_id=project.owner_id,
        family=bundle["family"],
        filename="reference.woff2",
        object_key="local-font",
        sha256=hashlib.sha256(raw).hexdigest(),
        size_bytes=len(raw),
        metadata_json={"source": "upload"},
        status="ready",
        is_bound=True,
    )
    records.session.add(font)
    records.session.commit()
    binding = {**font_assets.public_font(font), "role": "heading"}
    original = records.add(
        "Используй изображение и выбранный шрифт",
        fonts=[binding],
        references=[images.public_image(case.image)],
    )
    before = copy.deepcopy(original.agent_state)
    monkeypatch.setattr(font_assets, "_load_bundle", lambda row: dict(bundle))
    files = {"src/app/globals.css": "body { color: black; }"}
    writes = []

    async def snapshot():
        return dict(files)

    async def stage(patch, deletes):
        writes.append(dict(patch))
        files.update(patch)

    runtime = SimpleNamespace(handle=SimpleNamespace(snapshot_files=snapshot, stage_patch=stage))
    for _ in range(2):
        current = records.add("Попробуй ещё раз", status="running", mode="edit")
        current_ids = records.ids(current)
        records.session.expire_all()  # Reload durable identities rather than reuse Python state.
        prepared = await stage_owner_fonts(
            factory=records.factory,
            ids=current_ids,
            baseline=records.baseline,
            runtime=runtime,
        )
        refs = await _load_turn_reference_images(records.factory, current_ids, records.baseline)
        source = await resolve_request_source(
            records.factory(),
            ids=current_ids,
            baseline=records.baseline,
        )
        assert source.message_id == original.user_message_id
        assert source.prompt == "Используй изображение и выбранный шрифт"
        assert base64.b64decode(refs[0]["source"]["data"]) == case.raw
        assert json.loads(prepared["typography.json"])["heading"] == "Owner Reference"
        assert any("data:font/woff2;base64," in value for value in prepared.values())
        assert current.agent_state.keys() == {"dispatch"}
        current.status = "failed"
        records.session.commit()
    assert len(writes) == 1  # Materialization is idempotent across manual retries.
    assert original.agent_state == before


@pytest.mark.parametrize(
    "barrier",
    [
        "completed",
        "running",
        "chat",
        "clarify",
        "foreign_owner",
        "foreign_message",
        "changed_baseline",
        "source_admission",
        "current_admission",
        "source_hash",
        "image_hash",
        "source_role",
        "equal_previous",
        "equal_current",
        "project_owner",
    ],
)
async def test_manual_retry_never_crosses_untrusted_history(visual_retry, barrier):
    records, original = visual_retry.history, visual_retry.original
    if barrier in {"completed", "running", "chat", "clarify", "foreign_owner"}:
        records.add(
            "Другой запрос",
            status=barrier if barrier in {"completed", "running"} else "failed",
            mode=barrier if barrier in {"chat", "clarify"} else "edit",
            user=uuid4() if barrier == "foreign_owner" else None,
        )
    if barrier == "equal_previous":
        records.add("Другая задача с тем же временем", at=original.created_at)
    current = records.add("Попробуй ещё раз", status="running", mode="edit")
    if barrier == "equal_current":
        current.created_at = original.created_at
    elif barrier == "foreign_message":
        foreign = records.add("Чужое сообщение", project=uuid4())
        original.user_message_id = foreign.user_message_id
    elif barrier == "changed_baseline":
        records.snapshot.commit_sha = "b" * 40
    elif barrier in {"source_admission", "current_admission"}:
        target = original if barrier == "source_admission" else current
        state = copy.deepcopy(target.agent_state)
        state["dispatch"]["current_snapshot_id"] = str(uuid4())
        target.agent_state = state
    elif barrier == "source_hash":
        original.prompt_hash = "0" * 64
    elif barrier in {"image_hash", "source_role"}:
        message = records.session.get(Message, original.user_message_id)
        if barrier == "image_hash":
            message.reference_images = [{**message.reference_images[0], "sha256": "f" * 64}]
        else:
            message.role = "assistant"
    elif barrier == "project_owner":
        records.ensure_project().owner_id = uuid4()
    records.session.commit()
    source = await resolve_request_source(
        records.factory(),
        ids=records.ids(current),
        baseline=records.baseline,
    )
    assert source.message_id == current.user_message_id
    assert source.prompt == "Попробуй ещё раз"


@pytest.mark.parametrize(
    "prompt,own_image",
    [
        ("Сделай другой экран", False),
        ("Попробуй ещё раз и добавь поиск", False),
        ("Попробуй ещё раз", True),
    ],
)
async def test_new_task_and_replacement_assets_use_only_their_own_request(
    visual_retry,
    prompt,
    own_image,
):
    records = visual_retry.history
    current = records.add(
        prompt,
        status="running",
        mode="edit",
        references=[images.public_image(visual_retry.image)] if own_image else None,
    )
    source = await resolve_request_source(
        records.factory(),
        ids=records.ids(current),
        baseline=records.baseline,
    )
    assert source.message_id == current.user_message_id and source.prompt == prompt


async def test_nearest_substantive_failed_request_replaces_older_visual_task(visual_retry):
    records = visual_retry.history
    newer = records.add("Добавь поиск без изменения дизайна")
    current = records.add("Попробуй ещё раз", status="running", mode="edit")
    source = await resolve_request_source(
        records.factory(),
        ids=records.ids(current),
        baseline=records.baseline,
    )
    assert source.message_id == newer.user_message_id
    assert await _load_turn_reference_images(records.factory, records.ids(current)) == []


@pytest.mark.parametrize(
    "field", ["run_id", "project_id", "user_id", "user_message_id", "assistant_message_id"]
)
async def test_current_request_identity_is_checked_before_loading_assets(visual_retry, field):
    records = visual_retry.history
    current = records.add("Попробуй ещё раз", status="running", mode="edit")
    ids = replace(records.ids(current), **{field: uuid4()})
    with pytest.raises(ValueError, match="owner conflicts"):
        await resolve_request_source(records.factory(), ids=ids, baseline=records.baseline)


async def test_current_request_hash_conflict_is_terminal(visual_retry):
    records = visual_retry.history
    current = records.add("Попробуй ещё раз", status="running", mode="edit")
    current.prompt_hash = "0" * 64
    records.session.commit()
    with pytest.raises(ValueError, match="identity conflicts"):
        await _load_turn_reference_images(records.factory, records.ids(current), records.baseline)


async def test_invalid_checkpoint_marker_cannot_fall_back_to_manual_history(visual_retry):
    records = visual_retry.history
    current = records.add("Попробуй ещё раз", status="running", mode="edit")
    current.agent_state = {**current.agent_state, "source_checkpoint_resume": {"invalid": True}}
    records.session.commit()
    with pytest.raises(ValueError, match="provenance conflicts"):
        await _load_turn_reference_images(records.factory, records.ids(current), records.baseline)


async def test_manual_retry_does_not_search_beyond_twenty_admissions(visual_retry):
    records = visual_retry.history
    for _ in range(20):
        records.add("Попробуй ещё раз")
    current = records.add("Попробуй ещё раз", status="running", mode="edit")
    source = await resolve_request_source(
        records.factory(),
        ids=records.ids(current),
        baseline=records.baseline,
    )
    assert source.message_id == current.user_message_id


async def test_new_font_selection_is_an_explicit_request_not_older_visual_context(visual_retry):
    records = visual_retry.history
    current = records.add(
        "Попробуй ещё раз",
        status="running",
        mode="edit",
        fonts=[{"id": str(uuid4()), "sha256": "d" * 64, "role": "heading"}],
    )
    source = await resolve_request_source(
        records.factory(),
        ids=records.ids(current),
        baseline=records.baseline,
    )
    assert source.message_id == current.user_message_id
    assert await _load_turn_reference_images(records.factory, records.ids(current)) == []


async def test_cold_load_rechecks_current_accepted_baseline(visual_retry):
    records = visual_retry.history
    current = records.add("Попробуй ещё раз", status="running", mode="edit")
    source = await resolve_request_source(
        records.factory(),
        ids=records.ids(current),
        baseline=records.baseline,
    )
    assert source.message_id == visual_retry.original.user_message_id
    records.ensure_project().current_snapshot_id = uuid4()
    records.session.commit()
    records.session.expire_all()
    source = await resolve_request_source(
        records.factory(),
        ids=records.ids(current),
        baseline=records.baseline,
    )
    assert source.message_id == current.user_message_id


async def test_expanded_dispatch_prompt_uses_same_manual_source_as_attachments(visual_retry):
    from yleum_api.services.generation.agent_preparation import classify_agent_turn
    from yleum_api.services.generation.contracts import ProjectGenerationFacts

    records = visual_retry.history
    current = records.add("Попробуй ещё раз", status="running", mode="edit")
    classified = await classify_agent_turn(
        baseline=records.baseline,
        factory=records.factory,
        ids=records.ids(current),
        orchestrate=False,
        project_info=ProjectGenerationFacts(
            "max_miniapp",
            "fixture",
            "Fixture",
            None,
            None,
            False,
            "ru",
            False,
            "",
            "",
        ),
        prompt_text="Expanded admission context; this is not the original user task.",
    )
    assert classified.prompt == "Используй референс для дизайна этот"


async def test_first_build_retry_preserves_references_with_no_accepted_snapshot(visual_retry):
    from yleum_api.services.generation.contracts import SourceBaseline

    records = visual_retry.history
    current = records.add("Попробуй ещё раз", status="running", mode="build")
    for run in (visual_retry.original, current):
        state = copy.deepcopy(run.agent_state)
        state["dispatch"]["current_snapshot_id"] = None
        run.agent_state = state
    records.ensure_project().current_snapshot_id = None
    records.session.commit()
    loaded = await _load_turn_reference_images(
        records.factory,
        records.ids(current),
        SourceBaseline(None, None, {}),
    )
    assert base64.b64decode(loaded[0]["source"]["data"]) == visual_retry.raw


async def test_explicit_checkpoint_source_stays_authoritative(visual_retry):
    from yleum_api.services.generation import source_checkpoint

    records, original = visual_retry.history, visual_retry.original
    original.status = "running"
    records.session.commit()
    await source_checkpoint.persist_checkpoint(
        factory=records.factory,
        ids=records.ids(original),
        baseline=records.baseline,
        files={"src/app/page.tsx": "saved unaccepted draft"},
        original_prompt="Используй референс для дизайна этот",
    )
    original.status = "failed"
    records.session.commit()
    current = records.add("Попробуй ещё раз", status="running", mode="edit")
    assert await source_checkpoint.adjacent_checkpoint(
        factory=records.factory,
        ids=records.ids(current),
        baseline=records.baseline,
    )
    before = copy.deepcopy(current.agent_state)
    loaded = await _load_turn_reference_images(
        records.factory, records.ids(current), records.baseline
    )
    assert base64.b64decode(loaded[0]["source"]["data"]) == visual_retry.raw
    assert current.agent_state == before


async def test_postgres_manual_retry_loads_original_image_before_any_checkpoint(
    db_session,
    monkeypatch,
):
    from datetime import UTC, datetime, timedelta

    from sqlalchemy.ext.asyncio import async_sessionmaker

    from tests.test_reference_images import reference_owner
    from yleum_api.models.generation_run import GenerationRun
    from yleum_api.models.snapshot import Snapshot
    from yleum_api.services.generation.contracts import GenerationIds, SourceBaseline
    from yleum_api.services.generation_runs import GenerationDispatch, store_generation_dispatch

    user, project = await reference_owner(db_session)
    snapshot = Snapshot(project_id=project.id, commit_sha="a" * 40, prompt_text="Existing app")
    db_session.add(snapshot)
    await db_session.flush()
    project.current_snapshot_id = snapshot.id
    await db_session.commit()
    objects = {}
    monkeypatch.setattr(images, "_store", lambda key, raw, mime: objects.__setitem__(key, raw))
    monkeypatch.setattr(images, "_load", lambda row: objects[row.object_key])
    raw = png_bytes()
    image = await images.create_image(db_session, project.id, user.id, raw, "image/png", "ref.png")
    reference = images.public_image(image)
    baseline = SourceBaseline(snapshot.id, snapshot.commit_sha, {})
    factory = async_sessionmaker(db_session.bind, expire_on_commit=False)
    moment = datetime.now(UTC)

    async def admitted(prompt, references, ordinal, status):
        message = Message(
            project_id=project.id, role="user", content=prompt, reference_images=references
        )
        assistant = Message(project_id=project.id, role="assistant", content="")
        db_session.add_all([message, assistant])
        await db_session.flush()
        run = GenerationRun(
            id=uuid4(),
            project_id=project.id,
            user_id=user.id,
            user_message_id=message.id,
            assistant_message_id=assistant.id,
            status=status,
            response_mode="edit",
            idempotency_key=str(uuid4()),
            created_at=moment + timedelta(seconds=ordinal),
            prompt_hash=hashlib.sha256(
                images.request_identity(prompt, references).encode()
            ).hexdigest(),
        )
        store_generation_dispatch(
            run,
            GenerationDispatch(
                schema_version=1,
                project_id=project.id,
                user_id=user.id,
                user_message_id=message.id,
                assistant_message_id=assistant.id,
                current_snapshot_id=snapshot.id,
                prompt_text=prompt,
                model_id="test-no-provider",
                force_model=None,
                is_free=False,
                orchestrate=False,
                selected_elements=None,
                reference_images=[
                    {"id": str(item["id"]), "sha256": str(item["sha256"])} for item in references
                ]
                or None,
            ),
        )
        db_session.add(run)
        await db_session.commit()
        return run, GenerationIds(run.id, project.id, user.id, message.id, assistant.id)

    original, _ = await admitted("Use this reference", [reference], 0, "failed")
    for ordinal in (1, 2):
        retry, ids = await admitted("Попробуй ещё раз", [], ordinal, "running")
        loaded = await _load_turn_reference_images(factory, ids, baseline)
        assert base64.b64decode(loaded[0]["source"]["data"]) == raw
        assert retry.agent_state.keys() == {"dispatch"}
        retry.status = "failed"
        await db_session.commit()
    assert original.agent_state.keys() == {"dispatch"}

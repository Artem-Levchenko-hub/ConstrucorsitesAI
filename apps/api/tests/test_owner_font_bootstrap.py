"""Materialized fonts survive a lost write acknowledgement and text-only snapshots."""

import hashlib
import json
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker

from tests.test_font_assets_api import fixture_font
from tests.test_font_assets_api import font_storage as font_storage
from tests.test_reference_images import reference_owner
from yleum_api.models.generation_run import GenerationRun
from yleum_api.models.message import Message
from yleum_api.schemas.message import FontSelection
from yleum_api.services import font_assets, repo
from yleum_api.services.generation.agent_seed import stage_owner_fonts
from yleum_api.services.generation.contracts import GenerationIds, SourceBaseline
from yleum_api.services.reference_images import request_identity


async def test_font_apply_lost_ack_cold_retry_snapshot_and_ordinary_edit(
    db_session,
    monkeypatch,
    font_storage,
):
    user, project = await reference_owner(db_session)
    asset = await font_assets.create_font(
        db_session, project.id, user.id, fixture_font(), "owner.ttf"
    )
    bindings = await font_assets.resolve_selections(
        db_session, project.id, user.id, [FontSelection(asset_id=asset.id, role="all")], bind=True
    )
    await font_assets.bind_message_fonts(db_session, project.id, user.id, bindings)
    message = Message(
        project_id=project.id, role="user", content="Apply font", font_selections=bindings
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
        status="running",
        prompt_hash=hashlib.sha256(
            request_identity(message.content, [], bindings).encode()
        ).hexdigest(),
        idempotency_key="font-bootstrap-1",
    )
    db_session.add(run)
    await db_session.commit()
    ids = GenerationIds(run.id, project.id, user.id, message.id, assistant.id)
    factory = async_sessionmaker(db_session.bind, expire_on_commit=False)
    source = {"src/app/globals.css": '@import "tailwindcss";\n'}
    baseline = SourceBaseline(None, None, dict(source))
    writes = []

    async def snapshot():
        return dict(source)

    async def lose_ack(files, deletes):
        writes.append(files)
        source.update(files)
        raise RuntimeError("synthetic acknowledgement lost after committed font write")

    runtime = SimpleNamespace(handle=SimpleNamespace(snapshot_files=snapshot, stage_patch=lose_ack))
    with pytest.raises(RuntimeError, match="acknowledgement lost"):
        await stage_owner_fonts(factory=factory, ids=ids, baseline=baseline, runtime=runtime)
    assert len(writes) == 1
    # A fresh invocation resolves only the accepted immutable binding, never the
    # browser's upload or today's catalogue version. Physical retry is a no-op.
    monkeypatch.setattr(font_assets, "fetch_family", lambda *a: pytest.fail("new font resolve"))
    prepared = await stage_owner_fonts(factory=factory, ids=ids, baseline=baseline, runtime=runtime)
    assert len(writes) == 1
    assert json.loads(prepared["typography.json"])["heading"] == "Yleum Test Font"
    initial = repo.init_from_files(project.id, source, "font source")
    committed = repo.commit_files(
        project.id, source, "font exact snapshot", initial, exact_tree=True
    )
    restored = repo.read_files(project.id, committed)
    assert restored == source
    assert "data:font/woff2;base64," in "".join(restored.values())
    # A normally accepted text change neither resolves nor stages typography.
    ordinary = Message(project_id=project.id, role="user", content="Change button text")
    db_session.add(ordinary)
    await db_session.flush()
    run.user_message_id = ordinary.id
    run.prompt_hash = hashlib.sha256(ordinary.content.encode()).hexdigest()
    await db_session.commit()
    ordinary_ids = GenerationIds(run.id, project.id, user.id, ordinary.id, assistant.id)
    assert (
        await stage_owner_fonts(
            factory=factory, ids=ordinary_ids, baseline=baseline, runtime=runtime
        )
        == {}
    )
    assert source == restored and len(writes) == 1


async def test_font_bootstrap_fencing_reconciles_lost_ack_without_duplicate_write(
    monkeypatch,
    db_session,
    test_engine,
):
    from tests.test_project_cell_executor import (
        test_v2_write_ack_loss_reconciles_receipt_before_checkpoint_without_replay as exercise,
    )
    from yleum_api.services.generation import recovery_pipeline

    original = recovery_pipeline.run_bootstrap_stage

    async def font_stage(driver, name, invoke, *, handle):
        async def prepare():
            files, result = await invoke()
            assert result is None
            return files

        files = await original(driver, "font_apply", prepare, handle=handle)
        return files, None

    monkeypatch.setattr(recovery_pipeline, "run_bootstrap_stage", font_stage)
    await exercise(monkeypatch, db_session, test_engine, "stage_patch", "bootstrap")


async def test_plain_free_name_is_pinned_before_dispatch_and_replayed_without_resolution(
    client,
    db_session,
    monkeypatch,
    font_storage,
):
    from unittest.mock import AsyncMock

    from yleum_api.core.config import get_settings
    from yleum_api.core.deps import get_current_user
    from yleum_api.main import app
    from yleum_api.services.generation import acceptance
    from yleum_api.services.generation_runs import load_generation_dispatch

    user, project = await reference_owner(db_session)
    app.dependency_overrides[get_current_user] = lambda: user
    settings = get_settings().model_copy(
        update={
            "use_generation_worker": False,
            "use_progressive_discovery": False,
            "use_clarify_interview": False,
            "use_result_type_router": False,
            "use_followup_appification": False,
        }
    )
    monkeypatch.setattr(acceptance, "get_settings", lambda: settings)
    spawned = []
    monkeypatch.setattr(acceptance, "_spawn_process_prompt", lambda **kw: spawned.append(kw))
    monkeypatch.setattr(acceptance, "get_redis", lambda: SimpleNamespace(publish=AsyncMock()))
    raw = fixture_font("Montserrat")
    calls = []

    def fetch(name):
        calls.append(name)
        return {
            "family": "Montserrat",
            "package": "@fontsource/montserrat",
            "version": "5.3.0",
            "integrity": "sha512-synthetic",
            "license": "OFL-1.1",
            "license_text": "Synthetic fixture",
            "faces": [{"raw": raw, "unicode_range": "U+0-10FFFF"}],
        }

    monkeypatch.setattr(font_assets, "fetch_family", fetch)
    prompt = "Используй шрифт Montserrat"
    payload = {"prompt": prompt, "skip_clarify": True, "idempotency_key": "font-name-plain-1"}
    result = await client.post(f"/api/projects/{project.id}/prompt", json=payload)
    assert result.status_code == 202, result.text
    run = await db_session.get(GenerationRun, result.json()["run_id"])
    assert run.prompt_hash == hashlib.sha256(prompt.encode()).hexdigest()
    dispatch = load_generation_dispatch(run)
    assert dispatch.orchestrate is True and not dispatch.font_selections
    message = await db_session.get(Message, run.user_message_id)
    assert not message.font_selections
    assert message.resolved_fonts[0]["family"] == "Montserrat"
    assert message.resolved_fonts[0]["role"] == "all"
    assert calls == ["Montserrat"] and len(spawned) == 1

    async def forbidden(*args, **kwargs):
        pytest.fail("replay must not resolve current catalogue")

    monkeypatch.setattr(font_assets, "pin_named_request", forbidden)
    replay = await client.post(f"/api/projects/{project.id}/prompt", json=payload)
    assert replay.status_code == 202 and replay.json()["replayed"] is True
    assert calls == ["Montserrat"] and len(spawned) == 1

"""Real PostgreSQL CAS/JSONB evidence acceptance; Root runs the owned DB fixture."""
import asyncio
import hashlib
from copy import deepcopy
from dataclasses import replace
from uuid import uuid4

import pytest

from yleum_api.models.generation_run import GenerationRun
from yleum_api.services.generation.contracts import SourceBaseline
from yleum_api.services.generation.prompt_entry_evidence import KEY, record_coder_entry
from yleum_api.services.prompt_preparation import render_prepared_brief

from .test_prompt_preparation import _accepted


async def test_native_first_entry_is_owned_concurrent_immutable_and_preserves_other_state(
    client, db_session, test_engine, monkeypatch,
):
    factory, ids = await _accepted(client, db_session, test_engine, monkeypatch)
    baseline = SourceBaseline(uuid4(), "a" * 40, {"src/app/page.tsx": "private source"})
    retained = {
        "prompt_preparation_v1": {
            "version": 1, "state": "ready", "result": "private prepared plan",
        },
        "recovery_v1": {"version": 1, "private_fixture": "keep"},
        "progress_fixture": {"step": 12, "actions": 7},
    }
    run = await db_session.get(GenerationRun, ids.run_id, populate_existing=True)
    run.agent_state = {**run.agent_state, **retained}
    await db_session.commit()
    before = deepcopy(run.agent_state)
    original_hash = run.prompt_hash
    fragment = render_prepared_brief("private prepared plan")
    users = ["Owner request" + fragment, "Owner request" + fragment + "\nrestored context"]

    await asyncio.gather(*(record_coder_entry(
        factory, ids, baseline, input_prompt="Owner request", coder_user=user,
        entry_kind="legacy", rendered_brief=fragment,
    ) for user in users))
    await db_session.refresh(run)
    first = deepcopy(run.agent_state[KEY])
    assert first["run_id"] == str(ids.run_id) and first["project_id"] == str(ids.project_id)
    assert first["owner_id"] == str(ids.user_id)
    assert first["user_message_id"] == str(ids.user_message_id)
    assert first["assistant_message_id"] == str(ids.assistant_message_id)
    assert first["run_prompt_sha256"] == original_hash
    assert first["baseline_snapshot_id"] == str(baseline.snapshot_id)
    assert first["baseline_commit_sha"] == baseline.sha
    expected_hashes = {hashlib.sha256(user.encode()).hexdigest() for user in users}
    assert first["coder_user_sha256"] in expected_hashes
    assert first["rendered_brief_included"] is True
    assert first["full_http_captured"] is False and first["recorded_at"]
    assert {key: value for key, value in run.agent_state.items() if key != KEY} == before

    # Changed retries/resumes cannot replace the first exact entry receipt.
    await record_coder_entry(
        factory, ids, baseline, input_prompt="changed", coder_user="changed",
        entry_kind="native",
    )
    await db_session.refresh(run)
    assert run.agent_state[KEY] == first
    assert {key: value for key, value in run.agent_state.items() if key != KEY} == before


@pytest.mark.parametrize("field", [
    "run_id", "project_id", "user_id", "user_message_id", "assistant_message_id", "cancelled",
])
async def test_native_entry_refuses_wrong_identity_or_cancelled_run(
    client, db_session, test_engine, monkeypatch, field,
):
    factory, ids = await _accepted(client, db_session, test_engine, monkeypatch)
    run = await db_session.get(GenerationRun, ids.run_id, populate_existing=True)
    if field == "cancelled":
        run.status = "cancelled"
        await db_session.commit()
        attempted = ids
    else:
        attempted = replace(ids, **{field: uuid4()})
    before = deepcopy(run.agent_state)
    await record_coder_entry(
        factory, attempted, SourceBaseline(None, None, {}), input_prompt="owner",
        coder_user="user", entry_kind="legacy",
    )
    await db_session.refresh(run)
    assert KEY not in run.agent_state and run.agent_state == before

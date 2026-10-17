"""Typed durable cursor and evidence reconciliation regressions."""

from __future__ import annotations

from copy import deepcopy
from uuid import uuid4

import pytest

from yleum_api.services.generation.recovery_contract import RecoveryContractError, decode_recovery


def bootstrap():
    return {
        "version": 2,
        "identity": {
            "run_id": str(uuid4()),
            "project_id": str(uuid4()),
            "user_id": str(uuid4()),
            "prompt_hash": "a" * 64,
            "accepted_snapshot_id": None,
            "supersedes_run_id": None,
        },
        "execution": {
            "schema_version": 1,
            "stage": "bootstrap",
            "substep": "runtime_prepare",
            "logical_step_seq": 0,
            "next_tool_index": 0,
            "lease_cycle_seq": 0,
            "physical_state": "no_effects",
        },
        "incomplete_response_budget": {"total": 7, "since_progress": 2},
    }


def test_exact_v2_bootstrap_roundtrip_preserves_arithmetic_without_minting_authority():
    raw = bootstrap()
    before = deepcopy(raw)
    state = decode_recovery(raw)
    assert state.version == 2 and state.execution.substep == "runtime_prepare"
    assert state.incomplete_response_budget.total == 7
    assert state.incomplete_response_budget.since_progress == 2
    assert decode_recovery(state.model_dump(mode="json")) == state
    assert "_issuer" not in state.model_dump_json()
    assert raw == before


@pytest.mark.parametrize(
    "mutation",
    [
        {"schema_version": 2},
        {"schema_version": True},
        {"unknown": True},
        {"next_tool_index": True},
        {"stage": "agent", "substep": "runtime_prepare"},
        {"stage": "bootstrap", "substep": "tool_wait"},
        {"physical_state": "no_effects", "logical_step_seq": 1},
        {"control_workspace_id": str(uuid4())},
        {"control_fencing_epoch": 2},
    ],
)
def test_unknown_or_inconsistent_cursor_cannot_be_repaired_or_coerced(mutation):
    raw = bootstrap()
    raw["execution"].update(mutation)
    before = deepcopy(raw)
    with pytest.raises(RecoveryContractError):
        decode_recovery(raw)
    assert raw == before


def test_checkpoint_writer_cannot_downgrade_executable_cursor_to_shadow_v1():
    from yleum_api.services.generation.recovery_store import _validate_revision

    raw = bootstrap()
    previous = decode_recovery(raw)
    raw.pop("execution")
    raw["version"] = 1
    replacement = decode_recovery(raw)
    with pytest.raises(RecoveryContractError):
        _validate_revision(previous, replacement, expected_attempt=0, expected_revision=0)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "damage", [None, "missing", "foreign", "binding", "bytes", "cycle", "version"]
)
async def test_executable_context_chain_preserves_unicode_and_fails_closed(monkeypatch, damage):
    import hashlib
    import json
    from uuid import uuid4

    from yleum_api.services.generation import recovery_execution as execution
    from yleum_api.services.generation import source_checkpoint
    from yleum_api.services.generation.recovery_contract import RecoveryBinding, RecoveryIdentity

    project, run, user = uuid4(), uuid4(), uuid4()
    identity = RecoveryIdentity(
        run_id=run,
        project_id=project,
        user_id=user,
        prompt_hash="a" * 64,
        accepted_snapshot_id=None,
        supersedes_run_id=None,
    )
    objects = {}

    async def store(project_id, run_id, data):
        digest = hashlib.sha256(data).hexdigest()
        key = f"{project_id}/{run_id}/{digest}"
        objects[key] = data
        return key, digest

    async def load(project_id, run_id, *, object_key, digest):
        if object_key != f"{project_id}/{run_id}/{digest}":
            raise ValueError("foreign")
        data = objects.get(object_key)
        return data if data is not None and hashlib.sha256(data).hexdigest() == digest else None

    monkeypatch.setattr(source_checkpoint, "store_checkpoint_object", store)
    monkeypatch.setattr(source_checkpoint, "load_checkpoint_object", load)
    value = {
        "schema_version": 1,
        "entry": "native",
        "tool_arguments": "Привет 😀\n" * 100,
        "history": [{"id": "call-1", "result": {"done": False}}],
        "budget": {"steps": 9, "total": 7, "since_progress": 2},
        "deadline": "2026-10-17T12:00:00Z",
    }
    binding = RecoveryBinding(
        workspace_id=str(uuid4()),
        fencing_epoch=9,
        source_sha256="b" * 64,
        schema_sha256="c" * 64,
        environment_sha256="d" * 64,
    )
    ref = await execution.store_execution_context(identity, value, binding=binding, chunk_bytes=127)
    assert len(objects) > 3
    if damage is None:
        assert await execution.load_execution_context(identity, ref, binding=binding) == value
        return
    root = json.loads(objects[ref.object_key])
    if damage == "missing":
        objects.pop(root["tail"]["object_key"])
    elif damage == "bytes":
        objects[root["tail"]["object_key"]] += b"bad"
    elif damage == "foreign":
        identity = identity.model_copy(update={"run_id": uuid4()})
    elif damage == "binding":
        binding = binding.model_copy(update={"fencing_epoch": 10})
    else:
        root["version"] = 9 if damage == "version" else 1
        if damage == "cycle":
            root["tail"] = ref.model_dump(mode="json")
        key, digest = await store(project, run, json.dumps(root).encode())
        ref = ref.model_copy(update={"object_key": key, "sha256": digest})
    with pytest.raises(ValueError):
        await execution.load_execution_context(identity, ref, binding=binding)


@pytest.mark.parametrize(
    "resumed,outcome",
    [
        (False, "ok"),
        (True, "ok"),
        (True, "pending"),
        (False, "truncated"),
        (False, "outer_retry"),
        (True, "outer_retry"),
    ],
)
async def test_execution_driver_keeps_model_identity_and_budget_on_cold_read(
    monkeypatch, resumed, outcome
):
    from datetime import UTC, datetime, timedelta
    from unittest.mock import AsyncMock
    from uuid import uuid4

    from yleum_api.services import llm_client
    from yleum_api.services.generation import recovery_execution as ex
    from yleum_api.services.generation import recovery_reconciliation as rec
    from yleum_api.services.generation.recovery_contract import (
        CheckpointRef,
        ContinuationClaim,
        ExecutionBudget,
        ExecutionCursor,
        RecoveryBinding,
        RecoveryIdentity,
        RecoveryStateV2,
    )

    identity = RecoveryIdentity(
        run_id=uuid4(),
        project_id=uuid4(),
        user_id=uuid4(),
        prompt_hash="a" * 64,
        accepted_snapshot_id=None,
        supersedes_run_id=None,
    )
    claim = ContinuationClaim(
        run_id=identity.run_id,
        token=uuid4(),
        attempt_seq=1,
        worker_id="synthetic",
        expires_at=datetime.now(UTC) + timedelta(seconds=30),
    )
    ref = CheckpointRef(object_key="synthetic/context", sha256="b" * 64)
    binding = RecoveryBinding(
        workspace_id=str(uuid4()),
        fencing_epoch=1,
        source_sha256="a" * 64,
        schema_sha256="b" * 64,
        environment_sha256="c" * 64,
    )
    root = rec.ProviderEffectCursor(claim, 1, 5).root_effect_id
    state = RecoveryStateV2(
        identity=identity,
        continuation_claim=claim,
        attempt_seq=1,
        state_revision=1,
        cursor="agent",
        binding=binding,
        checkpoint=ref,
        context_ref=ref,
        execution=ExecutionCursor(
            stage="agent",
            substep="response_wait" if resumed else "request_ready",
            logical_step_seq=5,
            physical_state="active",
            execution_context_ref=ref,
            pending_effect_id=root if resumed else None,
            provider_effect_id=root if resumed else None,
            budget=ExecutionBudget(steps=5, provider_attempts=5),
        ),
    )
    frame = {
        "version": 1,
        "kind": "native",
        "value": {"step": 4, "convo": [{"role": "user", "content": "Привет"}]},
    }
    if outcome == "outer_retry":
        frame = {
            "version": 1,
            "kind": "legacy",
            "value": {
                "contract": "a" * 64,
                "step": 4,
                "attempt": 0,
                "retry_started_at": datetime.now(UTC).isoformat(),
                "call_deadline": (datetime.now(UTC) + timedelta(seconds=300)).isoformat(),
                "active_model": "model",
                "call_messages": [{"role": "user", "content": "Привет"}],
            },
        }
    context = {"schema_version": 1, "entry": "pipeline", "active_frame": frame if resumed else None}
    driver = ex.ExecutionDriver(None, state, context, None)
    saved = []

    async def save(execution, *, context, phase="working"):
        saved.append((execution, context, phase))
        driver.state = driver.state.model_copy(
            update={
                "execution": execution,
                "phase": phase,
                "state_revision": driver.state.state_revision + 1,
            }
        )
        driver.context = context

    monkeypatch.setattr(driver, "save", save)
    seen = []

    async def invoke():
        cursor = rec._PROVIDER_EFFECT.get()
        seen.append(cursor)
        if outcome == "outer_retry":
            assert getattr(cursor, "call_deadline", None) == datetime.fromisoformat(
                frame["value"]["call_deadline"]
            )
        if outcome == "outer_retry" and len(seen) == 1:
            raise llm_client.LLMError(
                "safe refusal", code="preconnect", retry_safe=True, provider_charge_ambiguous=False
            )
        if outcome == "pending":
            raise llm_client.LLMError(
                "pending",
                code="generation_response_pending",
                retry_safe=False,
                provider_charge_ambiguous=True,
            )
        if outcome == "truncated":
            raise llm_client.LLMOutputTruncated(requested_max_tokens=None, completion_tokens=1)
        return {
            "content": [
                {
                    "type": "tool_use",
                    "id": "owned-tool",
                    "name": "read_file",
                    "input": {"path": "src/привет.ts"},
                }
            ],
            "stop_reason": "tool_use",
        }

    async def wait():
        raise ex.GenerationYielded(
            run_id=identity.run_id, checkpoint=ref, attempt_seq=1, phase="waiting_dependency"
        )

    driver.wait = AsyncMock(side_effect=wait)
    if outcome == "outer_retry":
        with pytest.raises(llm_client.LLMError):
            await driver.provider(frame, invoke)
        retry = {**frame, "value": {**frame["value"], "attempt": 1}}
        result = await driver.provider(retry, invoke)
        assert result["content"][0]["input"]["path"] == "src/привет.ts"
        assert len(seen) == 2
        assert seen[1].root_effect_id == seen[0].root_effect_id
        assert seen[1].logical_step_seq == seen[0].logical_step_seq
        assert (seen[1].outer_attempt, seen[1].outer_allowance) == (1, 5)
        assert driver.state.execution.budget.steps == (5 if resumed else 6)
        assert rec._PROVIDER_EFFECT.get() is None
        return
    if outcome == "pending":
        with pytest.raises(ex.GenerationYielded):
            await driver.provider(frame, invoke)
        driver.wait.assert_awaited_once()
    elif outcome == "truncated":
        with pytest.raises(llm_client.LLMOutputTruncated):
            await driver.provider(frame, invoke)
        driver.wait.assert_not_awaited()
    else:
        result = await driver.provider(frame, invoke)
        assert result["content"][0]["input"]["path"] == "src/привет.ts"
    assert len(seen) == 1 and seen[0].reconcile_only is resumed
    assert seen[0].logical_step_seq == (5 if resumed else 6)
    assert driver.state.execution.budget.steps == (5 if resumed else 6)
    assert driver.state.execution.budget.provider_attempts == 5  # SQL attempts own this accounting.
    assert rec._PROVIDER_EFFECT.get() is None


def test_source_history_roundtrip_never_restores_receipt_authority_or_aba_credit():
    from yleum_api.services.generation.continuation import SourceNoveltyTracker

    tracker = SourceNoveltyTracker({"a.ts": "a"})
    assert tracker.observe({"a.ts": "b"}, verified_receipts=frozenset({"issued-once"}))
    history = tracker.source_history()
    assert "issued-once" not in repr(history)
    restored = SourceNoveltyTracker.from_source_history(history)
    assert not restored.observe({"a.ts": "a"})
    assert not restored.observe({"a.ts": "b"})
    assert restored.observe({"a.ts": "c"})
    with pytest.raises(ValueError):
        SourceNoveltyTracker.from_source_history(("unknown",))


async def test_driver_source_checkpoint_preserves_accepted_file_deletions(monkeypatch):
    from contextlib import asynccontextmanager

    from yleum_api.services.generation import recovery_execution as ex
    from yleum_api.services.generation import recovery_store
    from yleum_api.services.generation.recovery_contract import CheckpointRef, RecoveryBinding

    state = decode_recovery(bootstrap())
    ref = CheckpointRef(object_key="synthetic", sha256="a" * 64)
    binding = RecoveryBinding(
        workspace_id=str(uuid4()),
        fencing_epoch=1,
        source_sha256="a" * 64,
        schema_sha256="b" * 64,
        environment_sha256="c" * 64,
    )

    @asynccontextmanager
    async def factory():
        yield object()

    async def snapshot():
        return binding, {"src/kept.ts": "new"}

    async def store(*args, **kwargs):
        return ref

    captured = []

    async def save(session, **kwargs):
        captured.append(kwargs)
        return ref

    monkeypatch.setattr(ex, "store_execution_context", store)
    monkeypatch.setattr(recovery_store, "save_recovery_checkpoint", save)
    context = {
        "schema_version": 1,
        "entry": "pipeline",
        "pipeline": {"baseline": {"files": {"src/deleted.ts": "old", "src/kept.ts": "old"}}},
    }
    driver = ex.ExecutionDriver(factory, state, context, snapshot)
    await driver.save(state.execution, context=context)
    assert captured[0]["deleted_paths"] == ("src/deleted.ts",)
    assert captured[0]["files"] == {"src/kept.ts": "new"}

@pytest.mark.parametrize("boundary", [
    "tool_ready", "response_wait", "checkpoint_ready", "inside",
    "unknown_role", "foreign_provider", "pending",
])
async def test_autonomous_role_keeps_provider_cursor_and_arithmetic(monkeypatch, boundary):
    from datetime import UTC, datetime, timedelta
    from unittest.mock import AsyncMock

    from yleum_api.services.generation import recovery_execution as ex
    from yleum_api.services.generation import recovery_pipeline as pipeline
    from yleum_api.services.generation import recovery_reconciliation as rec
    from yleum_api.services.generation.recovery_contract import (
        CheckpointRef,
        ContinuationClaim,
        RecoveryBinding,
    )

    state = decode_recovery(bootstrap())
    claim = ContinuationClaim(run_id=state.identity.run_id, token=uuid4(), attempt_seq=0,
                              worker_id="owned", expires_at=datetime.now(UTC)+timedelta(minutes=1))
    root = rec.ProviderEffectCursor(claim, 0, 7).root_effect_id
    ref = CheckpointRef(object_key="synthetic/context", sha256="f"*64)
    active = boundary != "checkpoint_ready"
    execution = state.execution.model_copy(update={
        "stage": "agent" if active else "finalization",
        "substep": "response_wait" if boundary == "response_wait" else
                   "tool_ready" if active else "checkpoint_ready",
        "physical_state": "active", "logical_step_seq": 7,
        "provider_effect_id": uuid4() if boundary == "foreign_provider" else root,
        "pending_effect_id": root, "execution_context_ref": ref,
        "budget": state.execution.budget.model_copy(update={"steps": 7, "provider_attempts": 7}),
    })
    state = state.model_copy(update={"cursor": execution.stage, "execution": execution,
        "continuation_claim": claim, "phase": "working", "checkpoint": ref, "context_ref": ref,
        "binding": RecoveryBinding(workspace_id=str(uuid4()), fencing_epoch=1,
            source_sha256="a"*64, schema_sha256="b"*64, environment_sha256="c"*64)})
    context = {"completed": {}, "active_frame": {"version": 1, "kind": "native", "value": {}}}
    driver = ex.ExecutionDriver(None, state, context, None)
    writes, calls = [], []

    async def save(cursor, *, context, **kwargs):
        driver.state = decode_recovery(driver.state.model_copy(update={
            "execution": cursor, "state_revision": driver.state.state_revision+1,
        }).model_dump(mode="json"))
        driver.context = context
        writes.append(cursor)

    monkeypatch.setattr(driver, "save", save)
    monkeypatch.setattr(driver, "wait", AsyncMock(side_effect=AssertionError("unexpected wait")))
    if boundary == "pending":
        driver.context["completed"]["pending_role"] = pipeline.PendingRole(
            operation_id=uuid4(), role="fast_check", return_substep="tool_ready",
        ).model_dump(mode="json")
    for _ in range(2):
        operation_id = uuid4()

        async def invoke():
            calls.append(operation_id)
            return "checked"

        kwargs = dict(role="unknown" if boundary == "unknown_role" else "fast_check",
                      operation_id=operation_id, invoke=invoke)
        if boundary in {"foreign_provider", "pending", "unknown_role"}:
            with pytest.raises((ValueError, RecoveryContractError)):
                await pipeline.run_durable_role(driver, **kwargs)
            assert calls == writes == []
            return
        if boundary == "inside":
            cursor = rec.ToolEffectCursor(claim, 0, 7, "model-tool")
            with rec.tool_effect_scope(cursor):
                assert await pipeline.run_durable_role(driver, **kwargs) == "checked"
            assert writes == []
        else:
            assert await pipeline.run_durable_role(driver, **kwargs) == "checked"
            await pipeline.finish_recorded_role(driver, operation_id)
        assert driver.state.execution.budget == state.execution.budget
        assert driver.state.incomplete_response_budget == state.incomplete_response_budget
        expected_seq = 7 if active else 7+len(calls)
        assert driver.state.execution.logical_step_seq == expected_seq
        assert driver.state.execution.provider_effect_id == root
        if active:
            assert driver.state.execution.pending_effect_id == root
    assert len(calls) == len(set(calls)) == 2

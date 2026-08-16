from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import pytest

from omnia_api.services import agent_native
from omnia_api.services.generation_continuity import (
    _segment_progress,
    classify_stop,
    workspace_digest,
)
from omnia_api.services.max_environment_manifest import (
    build_max_environment_manifest,
    manifest_prompt_block,
)
from omnia_api.services.max_project_kit import (
    default_max_project_config,
    render_max_managed_files,
)


def test_progressing_internal_red_stays_recoverable() -> None:
    for reason in (
        "generation_deadline_red",
        "max_steps_red",
        "runtime_check_failed",
        "missing_dependency",
        "managed_api_signature_mismatch",
    ):
        decision = classify_stop(reason, attempt=2, started_at=datetime.now(UTC))
        assert decision.continue_run is True
        assert decision.classification == "environment_rediscovery"


@pytest.mark.parametrize(
    "reason",
    ("kernel_verification_pending", "generation_time_slice", "internal_exception:TimeoutError"),
)
def test_internal_continuations_have_a_global_attempt_limit(reason: str) -> None:
    decision = classify_stop(reason, attempt=3, started_at=datetime.now(UTC))

    assert decision.continue_run is False
    assert decision.classification == "internal_budget_exhausted"
    assert decision.delay_seconds == 0


def test_internal_continuations_have_an_elapsed_deadline() -> None:
    decision = classify_stop(
        "kernel_verification_pending",
        attempt=1,
        started_at=datetime.now(UTC) - timedelta(minutes=31),
    )

    assert decision.continue_run is False
    assert decision.classification == "internal_budget_exhausted"


def test_exhausted_proof_states_do_not_restart_the_same_checkpoint() -> None:
    for reason in ("visual_quality_unmet", "max_release_proof_red"):
        decision = classify_stop(reason, attempt=0, started_at=datetime.now(UTC))

        assert decision.continue_run is False
        assert decision.classification == "internal_proof_blocked"


def test_owner_dependency_stops_without_model_repair_or_retry() -> None:
    decision = classify_stop(
        "kernel_owner_dependency",
        attempt=0,
        started_at=datetime.now(UTC),
    )

    assert decision.continue_run is False
    assert decision.classification == "external_owner_dependency"
    assert "не будет переписываться" in decision.action


def test_recurring_unchanged_segment_breaks_the_automatic_loop() -> None:
    decision = classify_stop(
        "visual_proof_unavailable",
        attempt=2,
        started_at=datetime.now(UTC),
        repeated_segment_count=3,
    )

    assert decision.continue_run is False
    assert decision.classification == "internal_no_progress"
    assert "три сегмента" in decision.action


def test_semantic_loop_is_terminal_internal_no_progress() -> None:
    decision = classify_stop(
        "semantic_loop_red",
        attempt=0,
        started_at=datetime.now(UTC),
    )

    assert decision.continue_run is False
    assert decision.classification == "internal_no_progress"


def test_workspace_digest_is_stable_and_content_sensitive() -> None:
    first = workspace_digest({"b.ts": "two", "a.ts": "one"})

    assert first == workspace_digest({"a.ts": "one", "b.ts": "two"})
    assert first != workspace_digest({"a.ts": "changed", "b.ts": "two"})


def test_segment_progress_detects_non_consecutive_cycles() -> None:
    digest = workspace_digest({"src/app.tsx": "same"})
    state = {
        "last_segment": {
            "stop_reason": "visual_proof_unavailable",
            "workspace_digest": digest,
        }
    }

    history, first_count = _segment_progress(state, {}, "visual_proof_unavailable")
    other_history, _ = _segment_progress(
        {
            "last_segment": {
                "stop_reason": "runtime_check_failed",
                "workspace_digest": digest,
            }
        },
        {"recent_segment_fingerprints": history},
        "runtime_check_failed",
    )
    history, second_count = _segment_progress(
        state,
        {"recent_segment_fingerprints": other_history},
        "visual_proof_unavailable",
    )

    assert first_count == 1
    assert second_count == 2


def test_only_true_external_provider_block_terminalizes() -> None:
    rejection = classify_stop("provider_rejected_401", attempt=0, started_at=datetime.now(UTC))
    outage = classify_stop(
        "provider_stopped_red",
        attempt=20,
        started_at=datetime.now(UTC) - timedelta(days=2),
    )

    assert rejection.continue_run is False
    assert rejection.classification == "external_provider_access"
    assert rejection.action
    assert outage.continue_run is False
    assert outage.classification == "external_provider_outage"


def test_environment_manifest_is_source_derived_locked_and_secret_free() -> None:
    manifest = build_max_environment_manifest()
    rendered = json.dumps(manifest, ensure_ascii=False).casefold()
    managed = render_max_managed_files(
        default_max_project_config("Manifest"),
        UUID(int=0),
    )["src/lib/omnia/integration-client.ts"]
    signatures = "\n".join(manifest["managed_signatures"]["integration_client"])

    assert manifest["runtime"]["framework"].startswith("next@")
    assert "src/components/MaxAppProvider.tsx" in manifest["locked_paths"]
    assert "requestOmniaAI" in str(manifest["managed_signatures"])
    assert ("trackMaxEvent" in signatures) is ("export async function trackMaxEvent" in managed)
    assert "trackOmniaGoal" in signatures
    assert "pnpm typecheck" in manifest["proof_commands"]
    assert "api_key=" not in rendered
    assert "password=" not in rendered


def test_kernel_environment_manifest_has_one_automatic_proof_owner() -> None:
    manifest = build_max_environment_manifest(profile="agent")
    prompt = manifest_prompt_block(profile="agent")

    assert "skill_index" not in manifest
    assert all("automatically" in item for item in manifest["proof_commands"])
    assert "plan_task" not in prompt
    assert "continuation milestones" not in prompt
    assert "Read relevant locked contracts" not in prompt
    assert "Start with a usable vertical slice" not in prompt
    assert "do not call planning, build" in prompt


@pytest.mark.asyncio
async def test_worker_death_replays_same_logical_provider_turn(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    checkpoints: list[dict[str, object]] = []
    turn_ids: list[str] = []
    systems: list[str] = []

    async def save_checkpoint(value: Any) -> None:
        checkpoints.append(dict(value))

    async def killed_call(
        client: Any, url: str, convo: Any, system: str, **kwargs: Any
    ) -> dict[str, Any]:
        turn_ids.append(kwargs["turn_id"])
        systems.append(system)
        raise asyncio.CancelledError

    async def execute(action: Any) -> dict[str, Any]:
        if action.name == "build":
            return {"ok": True, "detail": "clean"}
        return {"ok": True, "content": "export default 1", "detail": "written"}

    monkeypatch.setattr(agent_native, "_call_messages", killed_call)
    with pytest.raises(asyncio.CancelledError):
        await agent_native.run_native_build(
            system="system",
            task="task",
            execute=execute,
            run_id="same-run",
            max_steps=1,
            checkpoint=save_checkpoint,
        )

    assert checkpoints[-1]["provider_turn_index"] == 0
    assert checkpoints[-1]["version"] == 3
    assert checkpoints[-1]["no_write_turns"] == 0

    async def resumed_call(
        client: Any, url: str, convo: Any, system: str, **kwargs: Any
    ) -> dict[str, Any]:
        turn_ids.append(kwargs["turn_id"])
        systems.append(system)
        return {
            "stop_reason": "tool_use",
            "content": [
                {
                    "type": "tool_use",
                    "id": "write-1",
                    "name": "write_file",
                    "input": {"path": "src/x.ts", "content": "export default 1"},
                }
            ],
        }

    monkeypatch.setattr(agent_native, "_call_messages", resumed_call)
    result = await agent_native.run_native_build(
        system="changed-after-restart",
        task="task",
        execute=execute,
        run_id="same-run",
        max_steps=1,
        resume_checkpoint=checkpoints[-1],
        checkpoint=save_checkpoint,
    )

    assert turn_ids == ["same-run:0", "same-run:0"]
    assert systems == ["system", "system"]
    assert result.files == {"src/x.ts": "export default 1"}
    assert checkpoints[-1]["provider_turn_index"] == 1
    assert checkpoints[-1]["workspace_revision"] == 1
    assert checkpoints[-1]["recent_mutation_paths"] == ["src/x.ts"]


@pytest.mark.asyncio
async def test_worker_death_after_tool_use_replays_action_not_bare_assistant_turn(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    checkpoints: list[dict[str, object]] = []
    turn_ids: list[str] = []
    execution_attempts = 0
    executed_actions: list[str] = []

    async def save_checkpoint(value: Any) -> None:
        # Match the production JSONB boundary instead of retaining mutable aliases.
        checkpoints.append(json.loads(json.dumps(value)))

    async def settled_turn(
        client: Any, url: str, convo: Any, system: str, **kwargs: Any
    ) -> dict[str, Any]:
        turn_ids.append(kwargs["turn_id"])
        return {
            "stop_reason": "tool_use",
            "content": [
                {
                    "type": "tool_use",
                    "id": "write_1",
                    "name": "write_file",
                    "input": {"path": "src/x.ts", "content": "export default 1"},
                }
            ],
        }

    async def execute(action: Any) -> dict[str, Any]:
        nonlocal execution_attempts
        execution_attempts += 1
        executed_actions.append(action.name)
        if execution_attempts == 1:
            raise asyncio.CancelledError
        return {"ok": True, "content": action.args["content"], "detail": "written"}

    monkeypatch.setattr(agent_native, "_call_messages", settled_turn)
    with pytest.raises(asyncio.CancelledError):
        await agent_native.run_native_build(
            system="system",
            task="task",
            execute=execute,
            run_id="same-run",
            max_steps=1,
            checkpoint=save_checkpoint,
        )

    crash_checkpoint = checkpoints[-1]
    assert crash_checkpoint["provider_turn_index"] == 1
    assert crash_checkpoint["convo"][-1]["role"] == "assistant"
    assert crash_checkpoint["pending_tool_index"] == 0

    result = await agent_native.run_native_build(
        system="changed-after-restart",
        task="task",
        execute=execute,
        run_id="same-run",
        max_steps=1,
        resume_checkpoint=crash_checkpoint,
        checkpoint=save_checkpoint,
    )

    assert turn_ids == ["same-run:0"]
    assert executed_actions[:2] == ["write_file", "write_file"]
    assert result.files == {"src/x.ts": "export default 1"}


@pytest.mark.asyncio
async def test_worker_death_mid_tool_batch_resumes_after_completed_mutation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    checkpoints: list[dict[str, object]] = []
    executed_paths: list[str] = []
    reconcile_paths: list[str] = []
    cancel_second_once = True

    async def save_checkpoint(value: Any) -> None:
        checkpoints.append(json.loads(json.dumps(value)))

    assistant_turn = {
        "stop_reason": "tool_use",
        "content": [
            {
                "type": "tool_use",
                "id": "write_1",
                "name": "write_file",
                "input": {"path": "src/a.ts", "content": "export const a = 1"},
            },
            {
                "type": "tool_use",
                "id": "write_2",
                "name": "write_file",
                "input": {"path": "src/b.ts", "content": "export const b = 1"},
            },
            {
                "type": "tool_use",
                "id": "write_3",
                "name": "write_file",
                "input": {"path": "src/c.ts", "content": "export const c = 1"},
            },
        ],
    }

    async def settled_turn(*args: Any, **kwargs: Any) -> dict[str, Any]:
        return assistant_turn

    async def execute(action: Any) -> dict[str, Any]:
        nonlocal cancel_second_once
        if action.name == "build":
            return {"ok": True, "detail": "clean"}
        executed_paths.append(action.path)
        if action.args.get("_resume_reconcile"):
            reconcile_paths.append(action.path)
        if action.path == "src/b.ts" and cancel_second_once:
            cancel_second_once = False
            raise asyncio.CancelledError
        return {"ok": True, "content": action.args["content"], "detail": "written"}

    monkeypatch.setattr(agent_native, "_call_messages", settled_turn)
    with pytest.raises(asyncio.CancelledError):
        await agent_native.run_native_build(
            system="system",
            task="task",
            execute=execute,
            run_id="batch-run",
            max_steps=1,
            checkpoint=save_checkpoint,
        )

    crash_checkpoint = checkpoints[-1]
    assert crash_checkpoint["pending_tool_index"] == 1
    assert crash_checkpoint["written"] == {"src/a.ts": "export const a = 1"}
    assert crash_checkpoint["pending_turn_state"]["wrote"] is True

    result = await agent_native.run_native_build(
        system="system",
        task="task",
        execute=execute,
        run_id="batch-run",
        max_steps=1,
        resume_checkpoint=crash_checkpoint,
        checkpoint=save_checkpoint,
    )

    assert executed_paths == ["src/a.ts", "src/b.ts", "src/b.ts", "src/c.ts"]
    assert reconcile_paths == ["src/b.ts"]
    assert result.files == {
        "src/a.ts": "export const a = 1",
        "src/b.ts": "export const b = 1",
        "src/c.ts": "export const c = 1",
    }
    assert checkpoints[-1]["no_write_turns"] == 0


@pytest.mark.asyncio
async def test_uncertain_bash_uses_durable_preparation_without_repeating_effect(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    checkpoints: list[dict[str, object]] = []
    effects = 0
    cancel_once = True

    async def save_checkpoint(value: Any) -> None:
        checkpoints.append(json.loads(json.dumps(value)))

    async def settled_turn(*args: Any, **kwargs: Any) -> dict[str, Any]:
        return {
            "stop_reason": "tool_use",
            "content": [
                {
                    "type": "tool_use",
                    "id": "bash_1",
                    "name": "bash",
                    "input": {"cmd": "generate", "mutation_paths": ["src/a.ts"]},
                }
            ],
        }

    async def prepare(_action: Any) -> dict[str, object]:
        return {"bash_before_tree": {"src/a.ts": "before"}}

    async def execute(action: Any) -> dict[str, Any]:
        nonlocal effects, cancel_once
        assert action.args["_durable_preparation"]["bash_before_tree"] == {"src/a.ts": "before"}
        if action.args.get("_resume_reconcile"):
            return {"ok": False, "status": "uncertain", "retry": "never"}
        effects += 1
        if cancel_once:
            cancel_once = False
            raise asyncio.CancelledError
        return {"ok": True, "files": {"src/a.ts": "after"}}

    monkeypatch.setattr(agent_native, "_call_messages", settled_turn)
    with pytest.raises(asyncio.CancelledError):
        await agent_native.run_native_build(
            system="system",
            task="task",
            execute=execute,
            prepare_execute=prepare,
            run_id="bash-run",
            max_steps=1,
            checkpoint=save_checkpoint,
        )

    crash_checkpoint = checkpoints[-1]
    assert crash_checkpoint["pending_tool_started_index"] == 0
    assert crash_checkpoint["pending_tool_preparations"]["0"] == {
        "bash_before_tree": {"src/a.ts": "before"}
    }

    await agent_native.run_native_build(
        system="system",
        task="task",
        execute=execute,
        prepare_execute=prepare,
        run_id="bash-run",
        max_steps=1,
        resume_checkpoint=crash_checkpoint,
        checkpoint=save_checkpoint,
    )

    assert effects == 1


@pytest.mark.asyncio
async def test_pending_bash_reconciliation_keeps_started_checkpoint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    checkpoints: list[dict[str, object]] = []

    async def save_checkpoint(value: Any) -> None:
        checkpoints.append(json.loads(json.dumps(value)))

    async def settled_turn(*args: Any, **kwargs: Any) -> dict[str, Any]:
        return {
            "stop_reason": "tool_use",
            "content": [
                {
                    "type": "tool_use",
                    "id": "bash_rollback",
                    "name": "bash",
                    "input": {"cmd": "generate", "mutation_paths": ["src/a.ts"]},
                }
            ],
        }

    async def prepare(_action: Any) -> dict[str, object]:
        return {"bash_before_tree": {"src/a.ts": "before"}}

    async def execute(_action: Any) -> dict[str, Any]:
        return {
            "ok": False,
            "infra_dead": True,
            "reconciliation_pending": True,
            "error": "rollback unavailable",
        }

    monkeypatch.setattr(agent_native, "_call_messages", settled_turn)
    result = await agent_native.run_native_build(
        system="system",
        task="task",
        execute=execute,
        prepare_execute=prepare,
        run_id="bash-reconciliation-run",
        max_steps=1,
        checkpoint=save_checkpoint,
    )

    assert result.stop_reason == "tool_reconciliation_pending"
    assert checkpoints[-1]["pending_tool_started_index"] == 0
    assert checkpoints[-1]["pending_tool_index"] == 0
    assert checkpoints[-1]["pending_tool_preparations"]["0"] == {
        "bash_before_tree": {"src/a.ts": "before"}
    }


@pytest.mark.asyncio
async def test_owner_canary_project_brain_survives_worker_restart(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = agent_native.get_settings()
    monkeypatch.setattr(settings, "agent_kernel_v2_enabled", False, raising=False)
    monkeypatch.setattr(
        settings,
        "agent_kernel_v2_canary_users",
        "owner-fixture",
        raising=False,
    )
    checkpoints: list[dict[str, object]] = []
    working_notes: list[str] = []

    async def save_checkpoint(value: Any) -> None:
        checkpoints.append(dict(value))

    async def killed_call(
        client: Any, url: str, convo: Any, system: str, **kwargs: Any
    ) -> dict[str, Any]:
        working_notes.append(str(kwargs.get("working_memory") or ""))
        raise asyncio.CancelledError

    async def execute(_action: Any) -> dict[str, Any]:
        return {"ok": True, "detail": "clean"}

    monkeypatch.setattr(agent_native, "_call_messages", killed_call)
    with pytest.raises(asyncio.CancelledError):
        await agent_native.run_native_build(
            system="MAX system",
            task="Build owner fitness app",
            execute=execute,
            user_id="owner-fixture",
            run_id="brain-run",
            stable_max_loop=True,
            stable_max_product_first=False,
            checkpoint=save_checkpoint,
            acceptance_criteria=["typecheck clean"],
        )

    owner_checkpoint = checkpoints[-1]
    assert owner_checkpoint["version"] == 4
    assert owner_checkpoint["brain_v2"]["version"] == 2
    assert owner_checkpoint["brain_v2"]["objective"] == "Build owner fitness app"
    assert owner_checkpoint["brain_v2"]["acceptance"] == [
        {"criterion": "typecheck clean", "status": "open", "evidence": []}
    ]

    with pytest.raises(asyncio.CancelledError):
        await agent_native.run_native_build(
            system="changed system",
            task="changed task",
            execute=execute,
            user_id="owner-fixture",
            run_id="brain-run",
            stable_max_loop=True,
            stable_max_product_first=False,
            resume_checkpoint=owner_checkpoint,
            checkpoint=save_checkpoint,
        )

    assert "PROJECT BRAIN v2" in working_notes[-1]
    assert "Build owner fitness app" in working_notes[-1]

    other_checkpoints: list[dict[str, object]] = []

    async def save_other(value: Any) -> None:
        other_checkpoints.append(dict(value))

    with pytest.raises(asyncio.CancelledError):
        await agent_native.run_native_build(
            system="MAX system",
            task="Build other app",
            execute=execute,
            user_id="other-fixture",
            run_id="other-run",
            stable_max_loop=True,
            stable_max_product_first=False,
            checkpoint=save_other,
        )

    assert other_checkpoints[-1]["version"] == 3
    assert "brain_v2" not in other_checkpoints[-1]

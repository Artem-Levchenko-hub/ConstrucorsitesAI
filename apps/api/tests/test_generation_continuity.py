from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from omnia_api.services import agent_native
from omnia_api.services.generation_continuity import classify_stop
from omnia_api.services.max_environment_manifest import build_max_environment_manifest


def test_internal_red_never_becomes_terminal() -> None:
    for reason in (
        "generation_deadline_red",
        "max_steps_red",
        "visual_quality_unmet",
        "runtime_check_failed",
        "missing_dependency",
        "managed_api_signature_mismatch",
    ):
        decision = classify_stop(reason, attempt=99, started_at=datetime.now(UTC))
        assert decision.continue_run is True
        assert decision.classification == "environment_rediscovery"


def test_only_true_external_provider_block_terminalizes() -> None:
    rejection = classify_stop(
        "provider_rejected_401", attempt=0, started_at=datetime.now(UTC)
    )
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

    assert manifest["runtime"]["framework"].startswith("next@")
    assert "src/components/MaxAppProvider.tsx" in manifest["locked_paths"]
    assert "requestOmniaAI" in str(manifest["managed_signatures"])
    assert "pnpm typecheck" in manifest["proof_commands"]
    assert "api_key=" not in rendered
    assert "password=" not in rendered


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

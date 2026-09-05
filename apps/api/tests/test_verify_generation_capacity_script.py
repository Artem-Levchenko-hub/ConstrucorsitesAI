from __future__ import annotations

import asyncio
import json
from uuid import UUID

import pytest
from scripts import verify_generation_capacity as script


def test_execute_guard_is_inert(capsys) -> None:
    assert script.main([]) == 2

    payload = json.loads(capsys.readouterr().out)
    assert payload == {"status": "required", "step": "execute_guard"}


def test_capacity_progress_keeps_only_bounded_public_fields() -> None:
    sanitized = script._sanitize_progress(
        {
            "status": "unknown",
            "queue_position": -10,
            "capacity_reason": "secret=value",
            "detail": "postgresql://user:password@example.test/private",
            "token": "private",
        }
    )

    assert sanitized == {
        "status": "running",
        "queue_position": 0,
        "capacity_reason": None,
    }


def test_output_ids_exclude_existing_owner_identity() -> None:
    context = script.AcceptanceContext(
        label="capacity-acceptance-test",
        owner_id=UUID("22222222-2222-2222-2222-222222222222"),
    )

    assert "owner_id" not in context.ids()


def test_portable_probe_is_parameterized_and_leaves_owned_evidence() -> None:
    probe_id = UUID("11111111-1111-1111-1111-111111111111")
    source = script._portable_probe_source(probe_id)

    assert str(probe_id) in source
    assert "process.env.DATABASE_URL" in source
    assert "VALUES ($1, $2)" in source
    assert "WHERE probe_id = $1" in source
    assert "DELETE" not in source
    assert script._PORTABLE_MARKER in source


@pytest.mark.asyncio
async def test_executor_forwards_claim_token_and_drains_heartbeat_on_admission(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context = script.AcceptanceContext(
        label="capacity-acceptance-token",
        capacity_dispatch_token=UUID("11111111-1111-1111-1111-111111111111"),
        owner_id=UUID("22222222-2222-2222-2222-222222222222"),
        project_id=UUID("33333333-3333-3333-3333-333333333333"),
        run_id=UUID("44444444-4444-4444-4444-444444444444"),
    )
    heartbeat_started = asyncio.Event()
    heartbeat_drained = asyncio.Event()
    captured: dict[str, object] = {}
    cleared: list[UUID] = []
    handle = object()
    real_clear = script.clear_capacity_admission_event

    async def fake_heartbeat(run_id: UUID, token: UUID) -> str:
        assert run_id == context.run_id
        assert token == context.capacity_dispatch_token
        heartbeat_started.set()
        try:
            await asyncio.Future()
        finally:
            heartbeat_drained.set()
        raise AssertionError("heartbeat was not cancelled")

    async def fake_create_executor(**kwargs):
        captured.update(kwargs)
        await heartbeat_started.wait()
        assert context.run_id is not None
        script.capacity_admission_event(context.run_id).set()
        return handle

    async def emit(_payload: dict[str, object]) -> None:
        return None

    def track_clear(run_id: UUID) -> None:
        cleared.append(run_id)
        real_clear(run_id)

    monkeypatch.setattr(script, "_wait_for_capacity_dispatch_lease_loss", fake_heartbeat)
    monkeypatch.setattr(script, "maybe_create_project_cell_executor", fake_create_executor)
    monkeypatch.setattr(script, "clear_capacity_admission_event", track_clear)

    result = await script._create_executor_with_capacity_claim(context, agent_emit=emit)

    assert result is handle
    assert captured["capacity_dispatch_token"] == context.capacity_dispatch_token
    assert heartbeat_drained.is_set()
    assert cleared == [context.run_id]


@pytest.mark.asyncio
async def test_dispatch_lease_loss_cancels_and_drains_executor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context = script.AcceptanceContext(
        label="capacity-acceptance-lost",
        owner_id=UUID("22222222-2222-2222-2222-222222222222"),
        project_id=UUID("33333333-3333-3333-3333-333333333333"),
        run_id=UUID("44444444-4444-4444-4444-444444444444"),
    )
    executor_started = asyncio.Event()
    executor_drained = asyncio.Event()
    cleared: list[UUID] = []
    real_clear = script.clear_capacity_admission_event

    async def fake_heartbeat(_run_id: UUID, _token: UUID) -> str:
        await executor_started.wait()
        return "lost"

    async def fake_create_executor(**_kwargs):
        executor_started.set()
        try:
            await asyncio.Future()
        finally:
            executor_drained.set()
        raise AssertionError("executor was not cancelled")

    async def emit(_payload: dict[str, object]) -> None:
        return None

    def track_clear(run_id: UUID) -> None:
        cleared.append(run_id)
        real_clear(run_id)

    monkeypatch.setattr(script, "_wait_for_capacity_dispatch_lease_loss", fake_heartbeat)
    monkeypatch.setattr(script, "maybe_create_project_cell_executor", fake_create_executor)
    monkeypatch.setattr(script, "clear_capacity_admission_event", track_clear)

    with pytest.raises(script.AcceptanceFailure, match="capacity_dispatch_lease_lost"):
        await script._create_executor_with_capacity_claim(context, agent_emit=emit)

    assert executor_drained.is_set()
    assert cleared == [context.run_id]


def test_terminal_error_preserves_existing_diagnostic() -> None:
    assert (
        script._terminal_error(
            "invalid queued dispatch: generation dispatch is unavailable",
            status="failed",
            stage="ensure_and_bootstrap",
        )
        == "invalid queued dispatch: generation dispatch is unavailable"
    )
    assert (
        script._terminal_error(None, status="failed", stage="ensure_and_bootstrap")
        == "acceptance_failed:ensure_and_bootstrap"
    )
    assert script._terminal_error("old", status="completed", stage="completed") is None

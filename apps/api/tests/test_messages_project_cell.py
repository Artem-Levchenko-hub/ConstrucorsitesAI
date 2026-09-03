from __future__ import annotations

import asyncio
import os
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

os.environ.setdefault("DATABASE_URL", "postgresql://test:test@127.0.0.1:5432/test")
os.environ.setdefault("JWT_SECRET", "test-jwt-secret-at-least-32-bytes")

from omnia_api.core.errors import ApiError
from omnia_api.routers import messages
from omnia_api.services.agent_builder import Action
from omnia_api.services.generation_runs import promote_generation_after_admission


def test_cancel_protocol_signals_only_genuinely_running_generation() -> None:
    assert messages._generation_cancel_protocol("pending") == "terminal_without_signal"
    assert (
        messages._generation_cancel_protocol("queued_for_capacity")
        == "terminal_without_signal"
    )
    assert messages._generation_cancel_protocol("running") == "signal_running"
    assert messages._generation_cancel_protocol("cancel_requested") == "already_requested"


class _PromotionSession:
    def __init__(self, run: SimpleNamespace) -> None:
        self.run = run
        self.commits = 0

    async def __aenter__(self) -> _PromotionSession:
        return self

    async def __aexit__(self, *_args: object) -> None:
        return None

    async def scalar(self, _statement: object) -> SimpleNamespace:
        return self.run

    async def commit(self) -> None:
        self.commits += 1


class _PromotionSessionFactory:
    def __init__(self, session: _PromotionSession) -> None:
        self.session = session

    def __call__(self) -> _PromotionSession:
        return self.session


@pytest.mark.asyncio
async def test_immediate_pending_dispatch_token_wins_without_queueing() -> None:
    winner_token = uuid4()
    stale_token = uuid4()
    run = SimpleNamespace(status="pending", agent_state={}, started_at=None)
    session = _PromotionSession(run)

    winner_result = await promote_generation_after_admission(  # type: ignore[arg-type]
        _PromotionSessionFactory(session),  # type: ignore[arg-type]
        run_id=uuid4(),
        dispatch_token=winner_token,
    )
    stale_result = await promote_generation_after_admission(  # type: ignore[arg-type]
        _PromotionSessionFactory(session),  # type: ignore[arg-type]
        run_id=uuid4(),
        dispatch_token=stale_token,
    )

    assert winner_result == "admitted"
    assert stale_result == "lost"
    assert run.status == "running"
    assert run.agent_state == {"capacity_admitted_dispatch_token": str(winner_token)}
    assert session.commits == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "agent_state",
    [
        {"capacity_dispatch_claim": {"token": "malformed"}},
        {"capacity_admitted_dispatch_token": "malformed"},
    ],
)
async def test_pending_dispatch_with_existing_ownership_marker_fails_closed(
    agent_state: dict[str, object],
) -> None:
    run = SimpleNamespace(status="pending", agent_state=agent_state, started_at=None)
    session = _PromotionSession(run)

    result = await promote_generation_after_admission(  # type: ignore[arg-type]
        _PromotionSessionFactory(session),  # type: ignore[arg-type]
        run_id=uuid4(),
        dispatch_token=uuid4(),
    )

    assert result == "lost"
    assert run.status == "pending"
    assert session.commits == 0


@pytest.mark.asyncio
async def test_stale_dispatch_token_cannot_reenter_run_promoted_by_new_winner() -> None:
    winner_token = uuid4()
    stale_token = uuid4()
    run = SimpleNamespace(
        status="queued_for_capacity",
        agent_state={
            "capacity_dispatch_claim": {
                "token": str(winner_token),
                "expires_at": "2026-09-02T00:00:30+00:00",
            }
        },
        started_at=None,
    )
    session = _PromotionSession(run)

    winner_result = await promote_generation_after_admission(  # type: ignore[arg-type]
        _PromotionSessionFactory(session),  # type: ignore[arg-type]
        run_id=uuid4(),
        dispatch_token=winner_token,
    )
    stale_result = await promote_generation_after_admission(  # type: ignore[arg-type]
        _PromotionSessionFactory(session),  # type: ignore[arg-type]
        run_id=uuid4(),
        dispatch_token=stale_token,
    )

    assert winner_result == "admitted"
    assert stale_result == "lost"
    assert run.agent_state == {"capacity_admitted_dispatch_token": str(winner_token)}
    assert session.commits == 1


@pytest.mark.asyncio
async def test_winning_dispatch_token_may_replay_its_admission() -> None:
    winner_token = uuid4()
    run = SimpleNamespace(
        status="running",
        agent_state={"capacity_admitted_dispatch_token": str(winner_token)},
        started_at=None,
    )
    session = _PromotionSession(run)

    result = await promote_generation_after_admission(  # type: ignore[arg-type]
        _PromotionSessionFactory(session),  # type: ignore[arg-type]
        run_id=uuid4(),
        dispatch_token=winner_token,
    )

    assert result == "admitted"
    assert session.commits == 0


@pytest.mark.asyncio
async def test_capacity_dispatch_uses_a_database_session_lock() -> None:
    run_id = uuid4()
    session = SimpleNamespace(execute=AsyncMock())
    session.execute.return_value = SimpleNamespace(scalar_one=lambda: True)

    claimed = await messages._try_claim_capacity_dispatch(session, run_id)

    assert claimed is True
    statement = session.execute.await_args.args[0]
    assert "pg_try_advisory_xact_lock" in str(statement)


def test_capacity_dispatch_lock_key_is_stable_and_signed_bigint() -> None:
    run_id = uuid4()

    key = messages._capacity_dispatch_lock_key(run_id)

    assert key == messages._capacity_dispatch_lock_key(run_id)
    assert -(2**63) <= key < 2**63


@pytest.mark.asyncio
async def test_capacity_dispatch_watcher_closes_after_queue_is_admitted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    states = iter(("prequeue", "renewed", "admitted"))
    original_sleep = asyncio.sleep

    async def no_sleep(_seconds: float) -> None:
        await original_sleep(0)

    async def renew(_run_id, _token) -> str:
        return next(states)

    monkeypatch.setattr(messages.asyncio, "sleep", no_sleep)
    monkeypatch.setattr(messages, "_renew_capacity_dispatch_claim", renew)

    result = await asyncio.wait_for(
        messages._wait_for_capacity_dispatch_lease_loss(uuid4(), uuid4()),
        timeout=0.1,
    )

    assert result == "closed"


@pytest.mark.asyncio
async def test_capacity_dispatch_watcher_reports_loss_before_admission(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_sleep = asyncio.sleep

    async def no_sleep(_seconds: float) -> None:
        await original_sleep(0)

    async def lost(_run_id, _token) -> str:
        return "lost"

    monkeypatch.setattr(messages.asyncio, "sleep", no_sleep)
    monkeypatch.setattr(messages, "_renew_capacity_dispatch_claim", lost)

    result = await messages._wait_for_capacity_dispatch_lease_loss(uuid4(), uuid4())

    assert result == "lost"


@pytest.mark.asyncio
async def test_closed_capacity_watcher_does_not_cancel_admitted_work(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    completed = asyncio.Event()

    async def work() -> None:
        await asyncio.sleep(0)
        completed.set()

    async def never_cancel(_run_id):
        await asyncio.Future()

    async def closed(_run_id, _token) -> str:
        return "closed"

    finalize = AsyncMock()
    cancelled = AsyncMock()
    monkeypatch.setattr(messages, "_wait_for_generation_cancel", never_cancel)
    monkeypatch.setattr(messages, "_wait_for_capacity_dispatch_lease_loss", closed)
    monkeypatch.setattr(messages, "finalize_generation_run", finalize)
    monkeypatch.setattr(messages, "_finalize_cancelled_generation", cancelled)
    monkeypatch.setattr(messages, "clear_generation_cancel", AsyncMock())

    await messages._run_tracked_prompt(
        work(),
        run_id=uuid4(),
        project_id=uuid4(),
        assistant_message_id=uuid4(),
        label="test",
        capacity_dispatch_token=uuid4(),
    )

    assert completed.is_set()
    finalize.assert_awaited_once()
    cancelled.assert_not_awaited()


@pytest.mark.asyncio
async def test_local_admission_signal_wins_if_database_heartbeat_fails_after_admission(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_id = uuid4()
    heartbeat_failed = asyncio.Event()
    work_completed = asyncio.Event()

    async def work() -> None:
        messages.capacity_admission_event(run_id).set()
        heartbeat_failed.set()
        await asyncio.sleep(0)
        work_completed.set()

    async def never_cancel(_run_id):
        await asyncio.Future()

    async def failed_heartbeat(_run_id, _token) -> str:
        await heartbeat_failed.wait()
        return "lost"

    finalize = AsyncMock()
    cancelled = AsyncMock()
    monkeypatch.setattr(messages, "_wait_for_generation_cancel", never_cancel)
    monkeypatch.setattr(
        messages,
        "_wait_for_capacity_dispatch_lease_loss",
        failed_heartbeat,
    )
    monkeypatch.setattr(messages, "finalize_generation_run", finalize)
    monkeypatch.setattr(messages, "_finalize_cancelled_generation", cancelled)
    monkeypatch.setattr(messages, "clear_generation_cancel", AsyncMock())

    await messages._run_tracked_prompt(
        work(),
        run_id=run_id,
        project_id=uuid4(),
        assistant_message_id=uuid4(),
        label="test",
        capacity_dispatch_token=uuid4(),
    )

    assert work_completed.is_set()
    finalize.assert_awaited_once()
    cancelled.assert_not_awaited()


@pytest.mark.asyncio
async def test_queued_terminal_without_cancel_signal_allows_inflight_cleanup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_id = uuid4()
    ensure_running = asyncio.Event()
    terminal_committed = asyncio.Event()
    cleanup_completed = asyncio.Event()
    cancel_signal = asyncio.Event()

    async def work() -> None:
        ensure_running.set()
        await terminal_committed.wait()
        await asyncio.sleep(0)
        cleanup_completed.set()
        raise RuntimeError("cancelled during admission")

    async def wait_for_cancel(_run_id) -> None:
        await cancel_signal.wait()

    async def never_lose_lease(_run_id, _token) -> str:
        await asyncio.Future()
        return "lost"

    terminalize = AsyncMock()
    monkeypatch.setattr(messages, "_wait_for_generation_cancel", wait_for_cancel)
    monkeypatch.setattr(
        messages,
        "_wait_for_capacity_dispatch_lease_loss",
        never_lose_lease,
    )
    monkeypatch.setattr(messages, "_finalize_cancelled_generation", terminalize)
    monkeypatch.setattr(messages, "set_generation_run_status", AsyncMock())
    monkeypatch.setattr(messages, "_emergency_error", AsyncMock())
    monkeypatch.setattr(messages, "clear_generation_cancel", AsyncMock())

    tracked = asyncio.create_task(
        messages._run_tracked_prompt(
            work(),
            run_id=run_id,
            project_id=uuid4(),
            assistant_message_id=uuid4(),
            label="queued-cancel-cleanup",
            capacity_dispatch_token=uuid4(),
        )
    )
    await ensure_running.wait()
    terminal_committed.set()
    await tracked

    assert cleanup_completed.is_set()
    assert not cancel_signal.is_set()
    terminalize.assert_not_awaited()


@pytest.mark.asyncio
async def test_lost_capacity_watcher_cancels_only_waiter_without_terminalizing_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    waiter_cancelled = asyncio.Event()

    async def waiting_work() -> None:
        try:
            await asyncio.Future()
        except asyncio.CancelledError:
            waiter_cancelled.set()
            raise

    async def never_cancel(_run_id):
        await asyncio.Future()

    async def lost(_run_id, _token) -> str:
        return "lost"

    terminalize = AsyncMock()
    finalize = AsyncMock()
    monkeypatch.setattr(messages, "_wait_for_generation_cancel", never_cancel)
    monkeypatch.setattr(messages, "_wait_for_capacity_dispatch_lease_loss", lost)
    monkeypatch.setattr(messages, "_finalize_cancelled_generation", terminalize)
    monkeypatch.setattr(messages, "finalize_generation_run", finalize)
    monkeypatch.setattr(messages, "clear_generation_cancel", AsyncMock())

    await messages._run_tracked_prompt(
        waiting_work(),
        run_id=uuid4(),
        project_id=uuid4(),
        assistant_message_id=uuid4(),
        label="test",
        capacity_dispatch_token=uuid4(),
    )

    assert waiter_cancelled.is_set()
    terminalize.assert_not_awaited()
    finalize.assert_not_awaited()


@pytest.mark.asyncio
async def test_prepare_max_runtime_context_selects_project_cell_once_without_legacy_runtime(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, str]] = []
    emitted: list[tuple[str, dict[str, object]]] = []

    async def fake_execute(action: Action) -> dict[str, object]:
        return {"ok": True, "detail": action.name}

    async def fake_maybe_create(**kwargs):
        calls.append((str(kwargs["project_id"]), str(kwargs["project_slug"])))
        return SimpleNamespace(execute=fake_execute)

    async def fake_emit(event: str, data: dict[str, object]) -> None:
        emitted.append((event, dict(data)))

    async def fake_ensure() -> None:
        pytest.fail("legacy ensure_provisioned path must stay unused for selected cell")

    monkeypatch.setattr(
        messages.project_cell_executor,
        "maybe_create_project_cell_executor",
        fake_maybe_create,
    )
    monkeypatch.setattr(
        messages.orchestrator_client,
        "agent_sandbox_capabilities",
        lambda *args, **kwargs: pytest.fail("legacy sandbox attestation must stay unused"),
    )

    result = await messages._prepare_max_runtime_context(
        project_id=uuid4(),
        project_slug="max-cell",
        user_id=uuid4(),
        generation_run_id=uuid4(),
        vision_context="ctx",
        legacy_execute=lambda _action: pytest.fail("legacy executor must stay unused"),
        max_shell_requested=True,
        ensure_legacy_runtime_ready=fake_ensure,
        agent_emit=fake_emit,
        max_model_locked_files=frozenset({"src/app/page.tsx"}),
        max_security_locked_files=frozenset({"src/app/api/max/route.ts"}),
    )

    assert len(calls) == 1
    assert result["project_cell_handle"] is not None
    assert result["base_agent_executor"] is result["project_cell_handle"].execute
    assert result["max_shell_enabled"] is True
    assert result["active_max_locked_files"] == frozenset({"src/app/api/max/route.ts"})
    assert result["agent_result"] is None
    assert emitted == [
        (
            "agent.step",
            {
                "step": 0,
                "action": "project_cell",
                "human": "Подключаю owner-only Project Cell",
                "path": "",
                "detail": (
                    "Кодовая генерация идёт в изолированном workspace; "
                    "preview/runtime синхронизируются только для проверки."
                ),
                "ok": True,
            },
        )
    ]


@pytest.mark.asyncio
async def test_prepare_max_runtime_context_re_raises_project_cell_failure_without_legacy_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    emitted: list[tuple[str, dict[str, object]]] = []

    async def fake_emit(event: str, data: dict[str, object]) -> None:
        emitted.append((event, dict(data)))

    async def fake_maybe_create(**_kwargs):
        raise messages.project_cell_executor.ProjectCellExecutorUnavailable("bootstrap failed")

    async def fake_ensure() -> None:
        pytest.fail("legacy ensure_provisioned path must stay unused after cell failure")

    monkeypatch.setattr(
        messages.project_cell_executor,
        "maybe_create_project_cell_executor",
        fake_maybe_create,
    )
    monkeypatch.setattr(
        messages.orchestrator_client,
        "agent_sandbox_capabilities",
        lambda *args, **kwargs: pytest.fail("legacy sandbox attestation must stay unused"),
    )

    with pytest.raises(
        messages.project_cell_executor.ProjectCellExecutorUnavailable,
        match="bootstrap failed",
    ):
        await messages._prepare_max_runtime_context(
            project_id=uuid4(),
            project_slug="max-cell",
            user_id=uuid4(),
            generation_run_id=uuid4(),
            vision_context="ctx",
            legacy_execute=lambda _action: pytest.fail("legacy executor must stay unused"),
            max_shell_requested=True,
            ensure_legacy_runtime_ready=fake_ensure,
            agent_emit=fake_emit,
            max_model_locked_files=frozenset({"src/app/page.tsx"}),
            max_security_locked_files=frozenset({"src/app/api/max/route.ts"}),
        )

    assert emitted == [
        (
            "agent.step",
            {
                "step": 0,
                "action": "project_cell",
                "human": "Project Cell не подготовился",
                "path": "",
                "detail": "bootstrap failed",
                "ok": False,
            },
        )
    ]


@pytest.mark.asyncio
async def test_execute_max_agent_action_routes_selected_runtime_check_without_legacy_bootstrap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: list[str] = []

    async def fake_execute(action: Action) -> dict[str, object]:
        seen.append(action.name)
        return {"ok": True, "detail": "cell runtime"}

    monkeypatch.setattr(
        messages.orchestrator_client,
        "create_max_preview_session",
        lambda *args, **kwargs: pytest.fail("legacy MAX preview bootstrap must stay unused"),
    )
    monkeypatch.setattr(
        messages.orchestrator_client,
        "get_status",
        lambda *args, **kwargs: pytest.fail("legacy status lookup must stay unused"),
    )

    result = await messages._execute_max_agent_action(
        Action(name="runtime_check", args={"path": "/"}),
        project_id=uuid4(),
        project_slug="max-cell",
        vision_context="ctx",
        base_agent_executor=lambda _action: pytest.fail("legacy executor must stay unused"),
        max_shell_enabled=False,
        project_cell_handle=SimpleNamespace(execute=fake_execute),
        active_max_locked_files=frozenset(),
        max_model_write_rejection=lambda _path, _content: None,
    )

    assert result == {"ok": True, "detail": "cell runtime"}
    assert seen == ["runtime_check"]


@pytest.mark.asyncio
async def test_portable_action_uses_provider_boundary_not_next_source_lock_or_autobuild():
    async def execute(action):
        return {"ok": True, "detail": action.name}

    handle = SimpleNamespace(is_portable=lambda: True, execute=execute)
    for action in (
        Action(
            name="write_file", args={"path": "src/app/layout.tsx", "content": "new product shell"}
        ),
        Action(name="bash", args={"cmd": "pip install flask"}),
    ):
        result = await messages._execute_max_agent_action(
            action,
            project_id=uuid4(),
            project_slug="portable",
            vision_context="",
            base_agent_executor=lambda action: pytest.fail("legacy executor"),
            max_shell_enabled=True,
            project_cell_handle=handle,
            active_max_locked_files=frozenset({"src/app/layout.tsx"}),
            max_model_write_rejection=lambda path, content: None,
        )
        assert result == {"ok": True, "detail": action.name}


@pytest.mark.asyncio
@pytest.mark.parametrize("use_cell", [False, True])
async def test_execute_max_agent_action_rejects_removed_see_before_any_dispatch(
    use_cell: bool,
) -> None:
    def forbidden_execute(_action: Action) -> None:
        pytest.fail("removed visual action must not reach any executor")

    result = await messages._execute_max_agent_action(
        Action(name="see", args={"path": "/"}),
        project_id=uuid4(),
        project_slug="max-no-see",
        vision_context="old context remains compatible",
        base_agent_executor=forbidden_execute,
        max_shell_enabled=False,
        project_cell_handle=SimpleNamespace(execute=forbidden_execute) if use_cell else None,
        active_max_locked_files=frozenset(),
        max_model_write_rejection=lambda _path, _content: None,
    )

    assert result == {"ok": False, "error": "unknown action see"}


@pytest.mark.asyncio
async def test_build_agent_seed_parts_reads_from_selected_cell_without_legacy_io(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, str]] = []

    async def fake_execute(action: Action) -> dict[str, object]:
        calls.append((action.name, action.path))
        if action.name == "list_dir" and action.path == "entities":
            return {"ok": True, "detail": "User.json"}
        if action.name == "list_dir" and action.path == "src/app/(app)/dashboard":
            return {"ok": True, "detail": "page.tsx"}
        if action.name == "read_file":
            return {"ok": True, "content": "export function CrudResource() {}"}
        raise AssertionError(f"unexpected action: {action}")

    monkeypatch.setattr(
        messages.orchestrator_client,
        "agent_list_dir",
        lambda *args, **kwargs: pytest.fail("legacy list_dir must stay unused"),
    )
    monkeypatch.setattr(
        messages.orchestrator_client,
        "agent_read_file",
        lambda *args, **kwargs: pytest.fail("legacy read_file must stay unused"),
    )

    parts = await messages._build_agent_seed_parts(
        uuid4(),
        "max-cell",
        project_cell_handle=SimpleNamespace(execute=fake_execute),
    )

    assert calls == [
        ("list_dir", "entities"),
        ("list_dir", "src/app/(app)/dashboard"),
        ("read_file", "src/components/omnia/crud-resource.tsx"),
    ]
    assert parts == [
        "entities/ contains:\nUser.json",
        "src/app/(app)/dashboard/ contains:\npage.tsx",
        (
            "src/components/omnia/crud-resource.tsx (the entity-page "
            'component — render <CrudResource entity="Name"/> in each '
            "page):\nexport function CrudResource() {}"
        ),
    ]


@pytest.mark.asyncio
async def test_project_cell_build_routes_through_handle_without_legacy_build(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    async def fake_execute(action: Action) -> dict[str, object]:
        calls.append(action.name)
        return {"ok": True, "detail": "build ok"}

    monkeypatch.setattr(
        messages.orchestrator_client,
        "agent_build",
        lambda *args, **kwargs: pytest.fail("legacy build must stay unused"),
    )

    result = await messages._project_cell_build(SimpleNamespace(execute=fake_execute))

    assert result == {"ok": True, "detail": "build ok"}
    assert calls == ["build"]


@pytest.mark.asyncio
async def test_project_cell_runtime_check_routes_through_handle_without_legacy_status(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, str]] = []

    async def fake_execute(action: Action) -> dict[str, object]:
        calls.append((action.name, action.path))
        return {"ok": True, "detail": "runtime ok"}

    monkeypatch.setattr(
        messages.orchestrator_client,
        "runtime_status",
        lambda *args, **kwargs: pytest.fail("legacy runtime_status must stay unused"),
    )
    monkeypatch.setattr(
        messages.orchestrator_client,
        "get_status",
        lambda *args, **kwargs: pytest.fail("legacy get_status must stay unused"),
    )

    result = await messages._project_cell_runtime_check(
        SimpleNamespace(execute=fake_execute),
        path="/",
    )

    assert result == {"ok": True, "detail": "runtime ok"}
    assert calls == [("runtime_check", "/")]


@pytest.mark.asyncio
async def test_apply_project_cell_preview_files_mirrors_cell_before_preview(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stage_calls: list[tuple[dict[str, str], tuple[str, ...]]] = []
    preview_calls: list[tuple[str, dict[str, str], tuple[str, ...]]] = []

    async def fake_stage_patch(
        writes: dict[str, str],
        deletes: tuple[str, ...] = (),
    ) -> None:
        stage_calls.append((dict(writes), tuple(deletes)))

    async def fake_sync_preview():
        return SimpleNamespace(generated_files={}, failure=None)

    async def fake_hot_reload(project_id, slug, files, *, empty_files=()):
        preview_calls.append((slug, dict(files), tuple(empty_files)))
        return {"state": "hot_reloaded"}

    monkeypatch.setattr(messages.orchestrator_client, "hot_reload", fake_hot_reload)

    await messages._apply_project_cell_preview_files(
        project_id=uuid4(),
        project_slug="max-cell",
        files={"src/app/page.tsx": "v2\n", "obsolete.txt": ""},
        project_cell_handle=SimpleNamespace(
            stage_patch=fake_stage_patch,
            sync_preview=fake_sync_preview,
        ),
    )

    assert stage_calls == [({"src/app/page.tsx": "v2\n"}, ("obsolete.txt",))]
    assert preview_calls == []


@pytest.mark.asyncio
async def test_apply_project_cell_preview_files_preserves_explicit_empty_files_in_legacy_preview(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    preview_calls: list[tuple[str, dict[str, str], tuple[str, ...]]] = []

    async def fake_hot_reload(project_id, slug, files, *, empty_files=()):
        preview_calls.append((slug, dict(files), tuple(empty_files)))
        return {"state": "hot_reloaded"}

    monkeypatch.setattr(messages.orchestrator_client, "hot_reload", fake_hot_reload)

    await messages._apply_project_cell_preview_files(
        project_id=uuid4(),
        project_slug="max-cell",
        files={"empty.txt": "", "deleted.txt": ""},
        empty_files=("empty.txt",),
    )

    assert preview_calls == [
        (
            "max-cell",
            {"empty.txt": "", "deleted.txt": ""},
            ("empty.txt",),
        )
    ]


@pytest.mark.asyncio
async def test_abort_unsafe_max_backend_restores_cell_and_preview(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stage_calls: list[tuple[dict[str, str], tuple[str, ...]]] = []
    preview_calls: list[str] = []
    files = {
        "src/app/page.tsx": "unsafe page\n",
        "src/app/api/max/route.ts": "unsafe route\n",
    }

    async def fake_stage_patch(
        writes: dict[str, str],
        deletes: tuple[str, ...] = (),
    ) -> None:
        stage_calls.append((dict(writes), tuple(deletes)))

    async def fake_sync_preview():
        preview_calls.append("sync")
        return SimpleNamespace(generated_files={}, failure=None)

    monkeypatch.setattr(
        messages.orchestrator_client,
        "hot_reload",
        lambda *args, **kwargs: pytest.fail("direct hot_reload should not run with a cell handle"),
    )

    with pytest.raises(ApiError) as caught:
        await messages._abort_unsafe_max_backend(
            project_id=uuid4(),
            project_slug="max-cell",
            current_files={"src/app/page.tsx": "safe page\n"},
            files=files,
            unsafe_paths=["src/app/api/max/route.ts"],
            project_cell_handle=SimpleNamespace(
                stage_patch=fake_stage_patch,
                sync_preview=fake_sync_preview,
            ),
        )

    assert caught.value.status_code == 422
    assert files == {
        "src/app/page.tsx": "safe page\n",
        "src/app/api/max/route.ts": "",
    }
    assert stage_calls == [({"src/app/page.tsx": "safe page\n"}, ("src/app/api/max/route.ts",))]
    assert preview_calls == ["sync"]


def test_max_shell_kill_switch_allows_owner_cell_without_attestation() -> None:
    assert (
        messages._resolve_max_shell_enabled(
            max_shell_requested=False,
            sandbox_attested=True,
            project_cell_handle=SimpleNamespace(),
        )
        is False
    )
    assert (
        messages._resolve_max_shell_enabled(
            max_shell_requested=True,
            sandbox_attested=False,
            project_cell_handle=SimpleNamespace(),
        )
        is True
    )


@pytest.mark.asyncio
async def test_run_max_shell_action_uses_project_cell_executor_without_sandbox(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    base_calls: list[str] = []
    sync_calls: list[str] = []

    async def fake_base_executor(action: Action) -> dict[str, object]:
        base_calls.append(str(action.args.get("cmd") or ""))
        return {
            "ok": True,
            "detail": "cell shell ok",
            "files": {"src/app/page.tsx": "v2\n"},
        }

    async def fake_snapshot_files() -> dict[str, str]:
        return {"src/app/page.tsx": "v1\n"}

    async def fake_sync_preview():
        sync_calls.append("sync")
        return SimpleNamespace(generated_files={}, failure=None)

    monkeypatch.setattr(
        messages.orchestrator_client,
        "agent_exec_sandbox",
        lambda *args, **kwargs: pytest.fail("sandbox path must stay unused for Project Cell"),
    )

    result = await messages._run_max_shell_action(
        action=Action(name="bash", args={"cmd": "pnpm test"}),
        project_id=uuid4(),
        project_slug="max-cell",
        max_shell_enabled=True,
        base_agent_executor=fake_base_executor,
        project_cell_handle=SimpleNamespace(
            snapshot_files=fake_snapshot_files,
            sync_preview=fake_sync_preview,
        ),
        active_max_locked_files=frozenset(),
        max_model_write_rejection=lambda _path, _content: None,
    )

    assert result == {
        "ok": True,
        "detail": "cell shell ok\n\nProject Cell synced files: src/app/page.tsx",
        "files": {"src/app/page.tsx": "v2\n"},
    }
    assert base_calls == ["pnpm test"]
    assert sync_calls == ["sync"]


@pytest.mark.asyncio
async def test_run_max_shell_action_keeps_sandbox_for_non_cell(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sandbox_calls: list[str] = []
    hot_reload_calls: list[dict[str, str]] = []

    async def fake_sandbox(_project_id, _project_slug, cmd: str) -> dict[str, object]:
        sandbox_calls.append(cmd)
        return {
            "ok": True,
            "detail": "sandbox ok",
            "files": {"src/app/page.tsx": "v2\n"},
            "base_workspace_revision": "a" * 64,
        }

    async def fake_hot_reload(_project_id, _slug, files, *, base_workspace_revision=None):
        hot_reload_calls.append(dict(files))
        assert base_workspace_revision == "a" * 64
        return {"state": "hot_reloaded"}

    monkeypatch.setattr(messages.orchestrator_client, "agent_exec_sandbox", fake_sandbox)
    monkeypatch.setattr(messages.orchestrator_client, "hot_reload", fake_hot_reload)

    result = await messages._run_max_shell_action(
        action=Action(name="bash", args={"cmd": "pnpm test"}),
        project_id=uuid4(),
        project_slug="max-sandbox",
        max_shell_enabled=True,
        base_agent_executor=lambda _action: pytest.fail("cell executor must stay unused"),
        project_cell_handle=None,
        active_max_locked_files=frozenset(),
        max_model_write_rejection=lambda _path, _content: None,
    )

    assert result == {
        "ok": True,
        "detail": "sandbox ok\n\nSandbox synced files: src/app/page.tsx",
        "files": {"src/app/page.tsx": "v2\n"},
    }
    assert sandbox_calls == ["pnpm test"]
    assert hot_reload_calls == [{"src/app/page.tsx": "v2\n"}]


@pytest.mark.asyncio
async def test_run_max_shell_action_rolls_back_rejected_cell_diff(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stage_calls: list[tuple[dict[str, str], tuple[str, ...]]] = []
    sync_calls: list[str] = []

    async def fake_base_executor(_action: Action) -> dict[str, object]:
        return {
            "ok": True,
            "detail": "cell shell ok",
            "files": {
                "src/app/page.tsx": "unsafe page\n",
                "src/app/api/max/route.ts": "unsafe route\n",
            },
        }

    async def fake_snapshot_files() -> dict[str, str]:
        return {"src/app/page.tsx": "safe page\n"}

    async def fake_stage_patch(
        writes: dict[str, str],
        deletes: tuple[str, ...] = (),
    ) -> None:
        stage_calls.append((dict(writes), tuple(deletes)))

    async def fake_sync_preview():
        sync_calls.append("sync")
        return SimpleNamespace(generated_files={}, failure=None)

    monkeypatch.setattr(
        messages.orchestrator_client,
        "agent_exec_sandbox",
        lambda *args, **kwargs: pytest.fail("sandbox path must stay unused for Project Cell"),
    )
    monkeypatch.setattr(
        messages.orchestrator_client,
        "hot_reload",
        lambda *args, **kwargs: pytest.fail("rollback must go through Project Cell helper"),
    )

    result = await messages._run_max_shell_action(
        action=Action(name="bash", args={"cmd": "pnpm test"}),
        project_id=uuid4(),
        project_slug="max-cell",
        max_shell_enabled=True,
        base_agent_executor=fake_base_executor,
        project_cell_handle=SimpleNamespace(
            snapshot_files=fake_snapshot_files,
            stage_patch=fake_stage_patch,
            sync_preview=fake_sync_preview,
        ),
        active_max_locked_files=frozenset(),
        max_model_write_rejection=lambda _path, _content: None,
    )

    assert result == {
        "ok": False,
        "error": (
            "MAX Studio owns /api/max and /api/omnia. "
            "Shell changes there are blocked; use the "
            "managed integration client instead."
        ),
    }
    assert stage_calls == [({"src/app/page.tsx": "safe page\n"}, ("src/app/api/max/route.ts",))]
    assert sync_calls == ["sync"]


@pytest.mark.asyncio
async def test_rollback_project_cell_shell_files_preserves_zero_byte_snapshot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stage_calls: list[tuple[dict[str, str], tuple[str, ...]]] = []
    sync_calls: list[str] = []

    async def fake_stage_patch(
        writes: dict[str, str],
        deletes: tuple[str, ...] = (),
    ) -> None:
        stage_calls.append((dict(writes), tuple(deletes)))

    async def fake_sync_preview():
        sync_calls.append("sync")
        return SimpleNamespace(generated_files={}, failure=None)

    monkeypatch.setattr(
        messages.orchestrator_client,
        "hot_reload",
        lambda *args, **kwargs: pytest.fail("rollback must stay on Project Cell path"),
    )

    rolled_back = await messages._rollback_project_cell_shell_files(
        project_id=uuid4(),
        project_slug="max-cell",
        snapshot_files={"empty.txt": ""},
        touched_paths=("empty.txt", "removed.txt"),
        project_cell_handle=SimpleNamespace(
            stage_patch=fake_stage_patch,
            sync_preview=fake_sync_preview,
        ),
    )

    assert rolled_back is False
    assert stage_calls == [({"empty.txt": ""}, ("removed.txt",))]
    assert sync_calls == ["sync"]


@pytest.mark.asyncio
async def test_run_app_self_repair_uses_project_cell_preview_apply_without_hot_reload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    apply_calls: list[dict[str, str]] = []
    probe_calls: list[object] = []

    async def fake_probe_app_error(*args, **kwargs):
        probe_calls.append(kwargs.get("project_cell_handle"))
        if len(probe_calls) == 1:
            return {"error": "boom", "file": "src/app/page.tsx"}, "compile"
        return None, ""

    async def fake_apply_preview_files(**kwargs) -> None:
        assert kwargs["project_cell_handle"] is not None
        apply_calls.append(dict(kwargs["files"]))

    async def fake_propose_fix(**kwargs) -> dict[str, str]:
        assert kwargs["file_path"] == "src/app/page.tsx"
        return {"src/app/page.tsx": "fixed\n"}

    monkeypatch.setattr(messages, "_probe_app_error", fake_probe_app_error)
    monkeypatch.setattr(messages, "_apply_project_cell_preview_files", fake_apply_preview_files)
    monkeypatch.setattr(messages.app_doctor, "propose_fix", fake_propose_fix)
    monkeypatch.setattr(
        messages.orchestrator_client,
        "hot_reload",
        lambda *args, **kwargs: pytest.fail("legacy hot_reload must stay unused"),
    )

    repaired, final_error, category = await messages._run_app_self_repair(
        project_id=uuid4(),
        slug="max-cell",
        files={"src/app/page.tsx": "broken\n"},
        passes=1,
        project_cell_handle=SimpleNamespace(),
    )

    assert repaired == {"src/app/page.tsx": "fixed\n"}
    assert final_error is None
    assert category == ""
    assert len(probe_calls) == 2
    assert apply_calls == [{"src/app/page.tsx": "fixed\n"}]


def test_hard_coverage_failure_is_captured_for_release_attestation() -> None:
    hard = SimpleNamespace(hard_missing=lambda: ["products"], passed=False, checks=[])
    soft = SimpleNamespace(hard_missing=lambda: [], passed=False, checks=[])

    capture = messages._capture_hard_coverage_attestation(None, hard, enabled=True)

    assert capture == [("coverage", hard)]
    assert messages._capture_hard_coverage_attestation(None, soft, enabled=True) is None
    assert messages._capture_hard_coverage_attestation(None, hard, enabled=False) is None

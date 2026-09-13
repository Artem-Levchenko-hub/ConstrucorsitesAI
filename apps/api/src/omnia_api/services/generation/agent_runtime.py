from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from omnia_api.core.config import get_settings
from omnia_api.models.generation_run import GenerationRun
from omnia_api.services import (
    agent_builder,
    orchestrator_client,
    project_cell_executor,
)
from omnia_api.services.generation.contracts import (
    AgentRuntimeBindings,
    GenerationIds,
    GenerationRuntime,
    ProjectGenerationFacts,
)
from omnia_api.services.generation.progress import GenerationProgress
from omnia_api.services.generation.runtime import (
    _execute_max_agent_action,
    _prepare_max_runtime_context,
    _project_cell_build,
    _project_cell_runtime_check,
    _resolve_max_shell_enabled,
)
from omnia_api.services.max_project_kit import MAX_SECURITY_LOCKED_FILES

_log = logging.getLogger("omnia_api.routers.messages")


async def prepare_agent_runtime(
    *,
    ids: GenerationIds,
    project_info: ProjectGenerationFacts,
    runtime: GenerationRuntime,
    progress: GenerationProgress,
    factory: async_sessionmaker[AsyncSession],
    prompt_text: str,
    _design_contract: Any,
    _agent_res: agent_builder.AgentResult | None,
    _defer_max_runtime_provision: bool,
    _provision_legacy_runtime_with_progress: Callable[[], Awaitable[None]],
    capacity_dispatch_token: UUID | None,
) -> tuple[AgentRuntimeBindings, agent_builder.AgentResult | None]:
    _agent_emit = progress.emit_agent_event
    _vision_context = _design_contract.vision_context if _design_contract else prompt_text
    _base_agent_executor = agent_builder.make_container_executor(
        project_id=ids.project_id,
        slug=project_info.slug,
        emit=_agent_emit,
        vision_context=_vision_context,
    )
    _agent_executor: Callable[[agent_builder.Action], Awaitable[dict[str, Any]]]
    _max_shell_requested = (
        project_info.template == "max_miniapp" and get_settings().max_project_shell_enabled
    )
    _max_sandbox_capabilities: dict[str, Any] = {}
    _max_sandbox_attested = False
    _max_shell_enabled = _resolve_max_shell_enabled(
        max_shell_requested=_max_shell_requested,
        sandbox_attested=False,
        project_cell_handle=None,
    )
    _active_max_locked_files: frozenset[str] = frozenset()
    _legacy_runtime_ready = not _defer_max_runtime_provision

    async def _ensure_legacy_runtime_ready() -> None:
        nonlocal _legacy_runtime_ready
        if _legacy_runtime_ready:
            return
        await _provision_legacy_runtime_with_progress()
        _legacy_runtime_ready = True

    async def _probe_runtime_status(path: str = "/") -> dict[str, Any]:
        if runtime.handle is not None:
            return await _project_cell_runtime_check(
                runtime.handle,
                path=path,
            )
        return await orchestrator_client.runtime_status(
            ids.project_id,
            slug=project_info.slug,
            path=path,
        )

    async def _probe_build_status() -> dict[str, Any]:
        if runtime.coordinator is not None:
            _proof_result = await runtime.coordinator.fast_check()
            return {
                "ok": _proof_result.outcome == "green",
                "detail": _proof_result.redacted_detail,
            }
        if runtime.handle is not None:
            return await _project_cell_build(runtime.handle)
        return await orchestrator_client.agent_build(ids.project_id, project_info.slug)

    async def _preview_base_url() -> str | None:
        if runtime.handle is not None:
            _preview = await runtime.handle.create_preview_session()
            return _preview.preview_url
        _status_payload = await orchestrator_client.get_status(ids.project_id)
        _raw_base = _status_payload.get("dev_url") if isinstance(_status_payload, dict) else None
        return str(_raw_base) if _raw_base else None

    bindings = AgentRuntimeBindings(
        base_executor=_base_agent_executor,
        execute=_base_agent_executor,
        vision_context=_vision_context,
        shell_requested=_max_shell_requested,
        shell_enabled=_max_shell_enabled,
        sandbox_attested=_max_sandbox_attested,
        sandbox_capabilities=_max_sandbox_capabilities,
        locked_files=_active_max_locked_files,
        probe_runtime=_probe_runtime_status,
        probe_build=_probe_build_status,
        preview_url=_preview_base_url,
    )
    if project_info.template == "max_miniapp":
        from omnia_api.services.max_project_kit import MAX_MODEL_LOCKED_FILES
        from omnia_api.services.secret_safety import max_model_write_rejection

        bindings.locked_files = MAX_MODEL_LOCKED_FILES

        async def _agent_executor(action: agent_builder.Action) -> dict[str, Any]:
            return await _execute_max_agent_action(
                action,
                project_id=ids.project_id,
                project_slug=project_info.slug,
                vision_context=bindings.vision_context,
                base_agent_executor=bindings.base_executor,
                max_shell_enabled=bindings.shell_enabled,
                project_cell_handle=runtime.handle,
                active_max_locked_files=bindings.locked_files,
                max_model_write_rejection=max_model_write_rejection,
            )

        bindings.execute = _agent_executor
    else:
        bindings.execute = bindings.base_executor

    if _agent_res is None and project_info.template == "max_miniapp":
        _max_runtime = await _prepare_max_runtime_context(
            project_id=ids.project_id,
            project_slug=project_info.slug,
            user_id=ids.user_id,
            generation_run_id=ids.run_id,
            vision_context=bindings.vision_context,
            legacy_execute=bindings.base_executor,
            max_shell_requested=bindings.shell_requested,
            ensure_legacy_runtime_ready=_ensure_legacy_runtime_ready,
            agent_emit=_agent_emit,
            max_model_locked_files=MAX_MODEL_LOCKED_FILES,
            max_security_locked_files=MAX_SECURITY_LOCKED_FILES,
            capacity_dispatch_token=capacity_dispatch_token,
        )
        runtime.handle = _max_runtime["project_cell_handle"]
        bindings.base_executor = _max_runtime["base_agent_executor"]
        bindings.sandbox_capabilities = _max_runtime["max_sandbox_capabilities"]
        bindings.sandbox_attested = _max_runtime["max_sandbox_attested"]
        bindings.shell_enabled = _max_runtime["max_shell_enabled"]
        bindings.locked_files = _max_runtime["active_max_locked_files"]
        _agent_res = _max_runtime["agent_result"]
        if (
            runtime.handle is not None
            and runtime.handle.is_portable()
            and get_settings().use_max_finalization_coordinator
        ):
            from omnia_api.services.max_finalization import (
                MaxFinalizationCoordinator,
                watch_generation_deadline,
            )

            runtime.coordinator = MaxFinalizationCoordinator(
                session_factory=factory,
                generation_run_id=ids.run_id,
                project_id=ids.project_id,
                project_slug=project_info.slug,
                executor=runtime.handle,
                emit=progress.record_generation_event,
            )

            async def _max_generation_deadline_watchdog() -> None:
                async with factory() as _deadline_session:
                    _deadline_run = await _deadline_session.get(
                        GenerationRun,
                        ids.run_id,
                    )
                    if _deadline_run is None:
                        return
                    _deadline_started = _deadline_run.started_at or _deadline_run.created_at
                _deadline_at = _deadline_started + timedelta(
                    seconds=get_settings().max_generation_deadline_seconds
                )
                await asyncio.sleep(max(0.0, (_deadline_at - datetime.now(UTC)).total_seconds()))
                await watch_generation_deadline(
                    session_factory=factory,
                    generation_run_id=ids.run_id,
                )

            if get_settings().use_project_cell_activity_watchdog:
                runtime.deadline_task = asyncio.create_task(_max_generation_deadline_watchdog())
            _direct_max_agent_executor = bindings.execute

            async def _agent_executor(
                action: agent_builder.Action,
            ) -> dict[str, Any]:
                if action.name == "build":
                    assert runtime.coordinator is not None
                    _proof_result = await runtime.coordinator.fast_check()
                    return {
                        "ok": _proof_result.outcome == "green",
                        "detail": _proof_result.redacted_detail,
                    }
                return await _direct_max_agent_executor(action)

            bindings.execute = _agent_executor

    # Seed the agent with the project layout + the CrudResource component
    # up-front so it does NOT burn steps re-discovering the fixed template
    # (the #1 latency sink observed in the first live runs). Fail-soft.
    if project_info.template == "max_miniapp" and runtime.handle is None:
        await _ensure_legacy_runtime_ready()

    return bindings, _agent_res


async def select_nonmax_runtime(
    *,
    _agent_emit: Callable[[str, dict[str, Any]], Awaitable[None]],
    _agent_res: agent_builder.AgentResult | None,
    bindings: AgentRuntimeBindings,
    ids: GenerationIds,
    project_info: ProjectGenerationFacts,
    runtime: GenerationRuntime,
) -> agent_builder.AgentResult | None:
    if _agent_res is None and project_info.template != "max_miniapp":
        try:
            runtime.handle = await project_cell_executor.maybe_create_project_cell_executor(
                project_id=ids.project_id,
                project_slug=project_info.slug,
                project_template=project_info.template,
                user_id=ids.user_id,
                generation_run_id=ids.run_id,
                legacy_execute=bindings.base_executor,
                vision_context=bindings.vision_context,
                agent_emit=lambda payload: _agent_emit(
                    "agent.step",
                    {
                        **payload,
                        "action": "capacity_wait",
                        "human": payload.get("action", "Ожидаю ресурсы сервера"),
                        "ok": True,
                    },
                ),
            )
        except project_cell_executor.ProjectCellExecutorUnavailable as _cell_exc:
            await _agent_emit(
                "agent.step",
                {
                    "step": 0,
                    "action": "project_cell",
                    "human": "Project Cell не подготовился",
                    "path": "",
                    "detail": str(_cell_exc),
                    "ok": False,
                },
            )
            _agent_res = agent_builder.AgentResult(
                done=False,
                summary=(
                    f"Генерация не запускалась: owner-only Project Cell недоступен ({_cell_exc})."
                ),
                files={},
                steps=0,
                stop_reason="project_cell_unavailable",
            )
        else:
            if runtime.handle is not None:
                bindings.base_executor = runtime.handle.execute
                bindings.execute = runtime.handle.execute
                bindings.shell_enabled = _resolve_max_shell_enabled(
                    max_shell_requested=bindings.shell_requested,
                    sandbox_attested=bindings.sandbox_attested,
                    project_cell_handle=runtime.handle,
                )
                bindings.locked_files = MAX_SECURITY_LOCKED_FILES
                await _agent_emit(
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

    return _agent_res

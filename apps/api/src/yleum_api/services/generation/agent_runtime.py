from __future__ import annotations

import asyncio
import logging
from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from yleum_api.core.config import get_settings
from yleum_api.services import (
    agent_builder,
)
from yleum_api.services.generation.contracts import (
    AgentRuntimeBindings,
    GenerationIds,
    GenerationRuntime,
    ProjectGenerationFacts,
)
from yleum_api.services.generation.progress import GenerationProgress
from yleum_api.services.generation.runtime import (
    _execute_max_agent_action,
    _prepare_max_runtime_context,
    _project_cell_build,
    _project_cell_runtime_check,
    _require_project_cell,
)
from yleum_api.services.max_project_kit import MAX_SECURITY_LOCKED_FILES

_log = logging.getLogger("yleum_api.routers.messages")


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
    capacity_dispatch_token: UUID | None,
) -> tuple[AgentRuntimeBindings, agent_builder.AgentResult | None]:
    _agent_emit = progress.emit_agent_event
    _vision_context = _design_contract.vision_context if _design_contract else prompt_text
    _base_agent_executor = agent_builder.make_docs_media_executor(
        project_id=ids.project_id,
        emit=_agent_emit,
    )
    _max_shell_requested = get_settings().max_project_shell_enabled
    _max_shell_enabled = False
    _active_max_locked_files: frozenset[str] = frozenset()

    async def _probe_runtime_status(path: str = "/") -> dict[str, Any]:
        return await _project_cell_runtime_check(
            _require_project_cell(runtime.handle),
            path=path,
        )

    async def _probe_build_status() -> dict[str, Any]:
        if runtime.coordinator is not None:
            _proof_result = await runtime.coordinator.fast_check()
            return {
                "ok": _proof_result.outcome == "green",
                "detail": _proof_result.redacted_detail,
            }
        return await _project_cell_build(_require_project_cell(runtime.handle))

    async def _preview_base_url() -> str | None:
        _preview = await _require_project_cell(runtime.handle).create_preview_session()
        return _preview.preview_url

    bindings = AgentRuntimeBindings(
        base_executor=_base_agent_executor,
        execute=_base_agent_executor,
        vision_context=_vision_context,
        shell_requested=_max_shell_requested,
        shell_enabled=_max_shell_enabled,
        locked_files=_active_max_locked_files,
        probe_runtime=_probe_runtime_status,
        probe_build=_probe_build_status,
        preview_url=_preview_base_url,
    )
    from yleum_api.services.max_project_kit import MAX_MODEL_LOCKED_FILES
    from yleum_api.services.secret_safety import max_model_write_rejection

    bindings.locked_files = MAX_MODEL_LOCKED_FILES

    async def _agent_executor(action: agent_builder.Action) -> dict[str, Any]:
        return await _execute_max_agent_action(
            action,
            project_id=ids.project_id,
            project_slug=project_info.slug,
            base_agent_executor=bindings.base_executor,
            max_shell_enabled=bindings.shell_enabled,
            project_cell_handle=_require_project_cell(runtime.handle),
            active_max_locked_files=bindings.locked_files,
            max_model_write_rejection=max_model_write_rejection,
        )

    bindings.execute = _agent_executor

    if _agent_res is None:
        _max_runtime = await _prepare_max_runtime_context(
            project_id=ids.project_id,
            project_slug=project_info.slug,
            user_id=ids.user_id,
            generation_run_id=ids.run_id,
            vision_context=bindings.vision_context,
            docs_media_execute=bindings.base_executor,
            max_shell_requested=bindings.shell_requested,
            agent_emit=_agent_emit,
            max_model_locked_files=MAX_MODEL_LOCKED_FILES,
            max_security_locked_files=MAX_SECURITY_LOCKED_FILES,
            capacity_dispatch_token=capacity_dispatch_token,
        )
        runtime.handle = _max_runtime["project_cell_handle"]
        bindings.base_executor = _max_runtime["base_agent_executor"]
        bindings.shell_enabled = _max_runtime["max_shell_enabled"]
        bindings.locked_files = _max_runtime["active_max_locked_files"]
        _agent_res = _max_runtime["agent_result"]
        if (
            runtime.handle is not None
            and runtime.handle.is_portable()
            and get_settings().use_max_finalization_coordinator
        ):
            from yleum_api.services.max_finalization import (
                MaxFinalizationCoordinator,
                run_generation_deadline_watchdog,
            )

            runtime.coordinator = MaxFinalizationCoordinator(
                session_factory=factory,
                generation_run_id=ids.run_id,
                project_id=ids.project_id,
                project_slug=project_info.slug,
                executor=runtime.handle,
                emit=progress.record_generation_event,
            )

            if get_settings().use_project_cell_activity_watchdog:
                runtime.deadline_task = asyncio.create_task(
                    run_generation_deadline_watchdog(
                        session_factory=factory,
                        generation_run_id=ids.run_id,
                    )
                )
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

    return bindings, _agent_res

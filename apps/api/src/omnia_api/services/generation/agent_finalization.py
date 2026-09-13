from __future__ import annotations

import asyncio
import logging

from omnia_api.services import repo as repo_svc
from omnia_api.services.generation.contracts import (
    AgentOperations,
    AgentPromptPlan,
    FinalizedAgentSource,
    GenerationIds,
    GenerationRuntime,
    SourceBaseline,
)
from omnia_api.services.generation.runtime import _restore_project_cell_source
from omnia_api.services.max_finalization import ProofBundle

_log = logging.getLogger("omnia_api.routers.messages")


async def finalize_max_candidate(
    *,
    _is_edit: bool,
    _max_has_generated_snapshot: bool,
    _max_shell_enabled: bool,
    accumulated: str,
    baseline: SourceBaseline,
    files: dict[str, str],
    ids: GenerationIds,
    is_free: bool,
    prompt_text: str,
    runtime: GenerationRuntime,
    plan: AgentPromptPlan,
    operations: AgentOperations,
) -> FinalizedAgentSource:
    _max_finalization_proof: ProofBundle | None = None
    if runtime.coordinator is not None and files:
        from omnia_api.services.max_finalization import MaxFinalizationStatus

        assert runtime.handle is not None

        async def _repair_finalization_source(detail: str) -> None:
            from omnia_api.services import agent_native
            from omnia_api.services.max_generation_contract import max_source_completion_gap

            assert runtime.handle is not None
            baseline = await runtime.handle.snapshot_files()
            await operations.emit(
                "agent.step",
                {
                    "action": "source_repair",
                    "human": "Дорабатываю по замечаниям проверки",
                    "detail": detail,
                    "ok": False,
                    "path": "",
                },
            )
            result = await agent_native.run_native_build(
                system=agent_native.native_system_prompt(plan.stack_guide or "", plan.skills),
                task=(
                    f"{plan.user}\n\nFINAL SOURCE CHECK FEEDBACK:\n{detail}\n"
                    "Fix the existing product in this same workspace. Preserve working "
                    "features and data. Do not repeat SQL effects already completed. "
                    "Make real source changes, run build, then done."
                ),
                execute=operations.execute,
                user_id=str(ids.user_id),
                project_id=str(ids.project_id),
                run_id=str(ids.run_id),
                message_id=str(ids.assistant_message_id),
                free=is_free,
                emit=operations.emit,
                max_steps=plan.steps,
                max_segments=1,
                allow_max_bash=_max_shell_enabled,
                portable_cell=True,
                initial_files=baseline,
                completion_check=lambda written, evidence: max_source_completion_gap(
                    prompt_text,
                    {**baseline, **written},
                    portable=True,
                ),
            )
            if result.stop_reason in {"provider_error", "infra_error", "error"}:
                raise RuntimeError(result.summary)

        try:
            _finalization = await runtime.coordinator.finalize_with_repair(
                prompt=prompt_text,
                repair=_repair_finalization_source,
            )
            if _finalization.status is not MaxFinalizationStatus.COMPLETE:
                raise RuntimeError("MAX_FINALIZATION_FAILED: " + _finalization.redacted_detail)
        except Exception:
            if baseline.sha and _max_has_generated_snapshot:
                try:
                    _published_source = await asyncio.to_thread(
                        repo_svc.read_files,
                        ids.project_id,
                        baseline.sha,
                    )
                    await _restore_project_cell_source(
                        runtime.handle,
                        _published_source,
                    )
                except Exception as _repair_rollback_exc:
                    _log.warning(
                        "MAX repair source restore failed",
                        exc_info=_repair_rollback_exc,
                    )
            raise
        if _finalization.status is MaxFinalizationStatus.COMPLETE:
            _max_finalization_proof = _finalization.proof
            files = await runtime.handle.snapshot_files()
            accumulated = (
                "Готово — правка применена и проверена."
                if _is_edit
                else "Готово — приложение собрано и проверено."
            )

    return FinalizedAgentSource(_max_finalization_proof, files, accumulated)

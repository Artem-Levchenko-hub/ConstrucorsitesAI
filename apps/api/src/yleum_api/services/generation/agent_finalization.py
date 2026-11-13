from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable, Mapping
from datetime import datetime
from pathlib import PurePosixPath
from typing import TYPE_CHECKING, NamedTuple, TypedDict
from uuid import UUID

from yleum_api.core.config import get_settings
from yleum_api.services import agent_builder
from yleum_api.services import repo as repo_svc
from yleum_api.services.generation.agent_recovery import _restore_max_core
from yleum_api.services.generation.contracts import (
    AgentOperations,
    AgentPromptPlan,
    FinalizedAgentSource,
    GenerationIds,
    GenerationRuntime,
    SourceBaseline,
    uses_single_max_source_repair,
)
from yleum_api.services.generation.recovery_trace import bounded_recovery_diagnostic
from yleum_api.services.generation.runtime import _restore_project_cell_source
from yleum_api.services.generation.source_error_classification import SourceFailure
from yleum_api.services.generation.source_error_classification import (
    source_check_is_repairable as source_check_is_repairable,
)
from yleum_api.services.generation.source_intent import (
    explicitly_readonly_request as explicitly_readonly_request,
)
from yleum_api.services.generation.source_intent import (
    source_change_required,
)
from yleum_api.services.generation.source_repair_context import (
    directed_repair_target,
    with_managed_source_context,
)
from yleum_api.services.max_finalization import ProofBundle
from yleum_api.services.max_project_kit import max_sast_source_view
from yleum_api.services.project_cell_errors import raise_if_terminal_cell_error

if TYPE_CHECKING:
    from yleum_api.services.generation.recovery_execution import ExecutionDriver

_log = logging.getLogger("yleum_api.routers.messages")

_NO_SOURCE_CHANGE_FAILURE = "edit produced no source changes"
_NO_SOURCE_CHANGE_MESSAGE = (
    "Не удалось применить правку: итоговый код не изменился. "
    "Повтори запрос или уточни, что именно нужно изменить."
)


class AdaptationActivationPending(RuntimeError):
    """Forward-only controller reconciliation owns the sealed candidate."""


def _canonical_operation_id(value: object) -> str | None:
    if isinstance(value, UUID):
        return str(value)
    if not isinstance(value, str):
        return None
    try:
        return value if str(UUID(value)) == value else None
    except ValueError:
        return None


class FinalizationRollbackUnverified(RuntimeError):
    """Preserve both bounded causes without retaining a raw exception chain."""

    def __init__(self, original: Exception, rollback: Exception, *, run_id: object,
                 operation_id: object = None) -> None:
        original_detail = bounded_recovery_diagnostic(str(original), max_bytes=2000)
        rollback_detail = bounded_recovery_diagnostic(
            str(rollback) or "restored runtime verification failed", max_bytes=1500,
        )
        self.diagnostics = {
            "original_error": original_detail, "rollback_error": rollback_detail,
            "run_id": _canonical_operation_id(run_id),
            "operation_id": _canonical_operation_id(operation_id),
        }
        identity = self.diagnostics["operation_id"]
        suffix = f" (operation_id={identity})" if identity else ""
        super().__init__(f"{original_detail}; rollback_unverified: {rollback_detail}{suffix}")


class EditSourceChangeVerdict(NamedTuple):
    files: dict[str, str]
    message: str
    failure: str | None


class _FinalizationOptions(TypedDict, total=False):
    continuation: ExecutionDriver
    source_preflight: Callable[[], Awaitable[str | None]]
    max_source_repairs: int
    interrupted_source_reason: str


def source_change_allows_partial_save(verdict: EditSourceChangeVerdict) -> bool:
    """Suppress the partial-save CTA after an explicit no-source-change failure."""
    return verdict.failure is None


def _is_edit_source_path(path: str) -> bool:
    """Auxiliary notes and generated metadata cannot prove a requested source edit.

    Keep source, assets, config and arbitrary existing-file deletions eligible;
    a restrictive extension allowlist would lose valid shell/backend edits.
    """
    from yleum_api.services.max_project_kit import MAX_SECURITY_LOCKED_FILES

    normalized = PurePosixPath(path.replace("\\", "/")).as_posix()
    source_path = PurePosixPath(normalized)
    documentation = source_path.suffix.lower() in {".md", ".rst"} and (
        len(source_path.parts) == 1
        or normalized.startswith(("docs/", "documentation/"))
        or source_path.stem.lower() in {"note", "notes"}
    )
    return (
        normalized not in MAX_SECURITY_LOCKED_FILES
        and not documentation
        and not normalized.lower().endswith(".tsbuildinfo")
        and source_path.name != "next-env.d.ts"
        and (not normalized.startswith(".omnia/") or normalized == ".omnia/cell.json")
    )


def _is_managed_catalog_protocol_upgrade(before: str, after: str) -> bool:
    """Recognize only the canonical legacy-to-catalog preparation migration."""
    from yleum_api.services.max_project_kit import enable_catalog_runtime_manifest

    try:
        previous = json.loads(before)
        current = json.loads(after)
        if (
            not isinstance(previous, dict)
            or previous.get("app_config_protocol") is not None
            or not isinstance(current, dict)
            or type(current.get("app_config_protocol")) is not int
            or current["app_config_protocol"] != 1
        ):
            return False
        upgraded = enable_catalog_runtime_manifest(before)
        if upgraded == before:
            return False
        # JSON encoding preserves true/1 and 1/1.0 distinctions in every field.
        return json.dumps(json.loads(upgraded), sort_keys=True, allow_nan=False) == json.dumps(
            current, sort_keys=True, allow_nan=False,
        )
    except (TypeError, ValueError, RecursionError):
        return False


def _edit_source_trees(
    baseline_files: Mapping[str, str], candidate_files: Mapping[str, str], *, catalog_runtime: bool,
) -> tuple[dict[str, str], dict[str, str]]:
    before = {p: value for p, value in baseline_files.items() if _is_edit_source_path(p)}
    after = {p: value for p, value in candidate_files.items() if _is_edit_source_path(p)}
    manifest_path = ".omnia/cell.json"
    if (
        catalog_runtime
        and manifest_path in before
        and manifest_path in after
        and _is_managed_catalog_protocol_upgrade(before[manifest_path], after[manifest_path])
    ):
        after[manifest_path] = before[manifest_path]
    return before, after


def unchanged_candidate_before_finalization(
    *, baseline_files: Mapping[str, str], workspace_files: Mapping[str, str],
    requires_source_change: bool, message: str, catalog_runtime: bool = False,
) -> EditSourceChangeVerdict | None:
    product_before, product_after = _edit_source_trees(
        baseline_files, workspace_files, catalog_runtime=catalog_runtime,
    )
    if product_after != product_before:
        return None
    return EditSourceChangeVerdict(
        {}, _NO_SOURCE_CHANGE_MESSAGE if requires_source_change else message,
        _NO_SOURCE_CHANGE_FAILURE if requires_source_change else None,
    )


def validate_edit_source_change(
    *,
    requires_source_change: bool,
    baseline_files: Mapping[str, str],
    candidate_files: Mapping[str, str],
    exact_tree: bool,
    message: str,
    catalog_runtime: bool = False,
) -> EditSourceChangeVerdict:
    """Reject a nonempty edit candidate without an attributable product delta.

    An empty candidate is the established explicit no-op contract. A nonempty
    candidate claims that source was written, so managed preparation alone
    cannot authorize a new version or a successful edit.
    """
    files = dict(candidate_files)
    if not requires_source_change or not files:
        return EditSourceChangeVerdict(files, message, None)
    product_before, product_after = _edit_source_trees(
        baseline_files, files, catalog_runtime=catalog_runtime,
    )
    if exact_tree:
        changed = product_after != product_before
    else:
        changed = any(
            (path in product_before if content == "" else product_before.get(path) != content)
            for path, content in product_after.items()
        )
    if changed:
        return EditSourceChangeVerdict(files, message, None)
    return EditSourceChangeVerdict({}, _NO_SOURCE_CHANGE_MESSAGE, _NO_SOURCE_CHANGE_FAILURE)


async def run_native_source_repair(
    *, task: str, prompt_text: str, baseline: Mapping[str, str],
    runtime: GenerationRuntime, ids: GenerationIds, plan: AgentPromptPlan,
    operations: AgentOperations, is_free: bool, shell_enabled: bool,
    edit_deadline: datetime | None,
    source_failure: SourceFailure | None = None,
    feedback: str = "",
) -> agent_builder.AgentResult:
    """Reuse the guarded editor's progress windows without accepting proof."""
    from yleum_api.services import agent_native
    from yleum_api.services.generation.agent_generation import (
        SourceCheckpointError,
        checkpoint_source_actions,
    )
    from yleum_api.services.generation.agent_runtime import guard_native_source_contract
    from yleum_api.services.max_generation_contract import max_source_completion_gap

    continuation = getattr(operations, "continuation", None)
    accounting = getattr(operations, "response_recovery", None)
    model_baseline = baseline
    single_repair = continuation is None and uses_single_max_source_repair(runtime)
    if continuation is not None:
        from yleum_api.services.generation.recovery_contract import IncompleteResponseBudget
        from yleum_api.services.generation.recovery_pipeline import (
            _result_from_data,
            pending_source_repair,
            prepare_source_repair,
        )
        # Persist exact context once. A resumed paid request keeps its original
        # task/baseline even when a later observation is larger or different.
        if pending_source_repair(continuation) is None:
            task = with_managed_source_context(task, baseline)
        entry = await prepare_source_repair(
            continuation, task=task, baseline=dict(baseline), edit_deadline=edit_deadline,
            targeted=source_failure is not None,
            response_budget=(accounting.budget if accounting is not None
                             else IncompleteResponseBudget()),
        )
        if accounting is not None:
            accounting.budget = entry.response_budget
        if entry.status == "complete":
            return _result_from_data(entry.result)
        task, model_baseline, edit_deadline = entry.task, entry.baseline, entry.edit_deadline
        edit_deadline = continuation.soft_edit_deadline(edit_deadline)
        if entry.targeted and source_failure is None:
            # A historical path list is not current source-repair authority.
            source_failure = SourceFailure()
    else:
        if single_repair and getattr(runtime, "source_repair_started", False):
            raise RuntimeError("MAX source repair already used")
        task = with_managed_source_context(task, baseline)
    if single_repair:
        runtime.source_repair_started = True
        _log.info("max_automatic_source_repair_started", extra={"run_id": str(ids.run_id)})
        await operations.emit("agent.step", {"action": "build", "human": "Проверяю приложение"})
    else:
        await operations.emit(
            "agent.step",
            {
                "action": "source_repair",
                "human": "Дорабатываю по замечаниям проверки",
                "detail": feedback,
                "ok": False,
                "path": "",
            },
        )
    execute = operations.execute
    guarded_write = getattr(runtime.handle, "capabilities", {}).get(
        "source_write_nofollow_v1",
    ) is True
    if source_failure is not None:
        allowed = frozenset(source_failure.allowed_paths)

        async def targeted_execute(action: agent_builder.Action) -> dict[str, object]:
            if action.name in {"write_file", "edit_file"}:
                if action.path not in allowed or (
                    action.path not in baseline and not guarded_write
                ):
                    return {"ok": False, "detail": "Source repair path is not authorized."}
            elif action.name not in {"read_file", "list_dir", "grep", "build", "done"}:
                return {"ok": False, "detail": "Source repair action is not authorized."}
            return await operations.execute(action)

        execute = targeted_execute
    repair_execute, repair_completion = await guard_native_source_contract(
        runtime, ids, execute,
        lambda written, evidence: max_source_completion_gap(
            prompt_text, {**baseline, **written}, portable=True,
        ),
    )
    checkpoint = getattr(operations, "checkpoint", None)
    snapshot = getattr(operations, "snapshot_files", None) or getattr(
        runtime.handle, "snapshot_files", None,
    )
    if checkpoint is not None and callable(snapshot):
        repair_execute = checkpoint_source_actions(
            execute=repair_execute, snapshot=snapshot, checkpoint=checkpoint,
        )
    directed_path = None
    if callable(snapshot):
        directed_path = directed_repair_target(
            model_baseline,
            importer=source_failure.importer if source_failure is not None else None,
            allowed=source_failure.allowed_paths if source_failure is not None else None,
        )
        if source_failure is not None and directed_path not in baseline and not guarded_write:
            directed_path = None
    try:
        result = await agent_native.run_native_build(
            continuation=continuation,
            response_recovery=getattr(operations, "response_recovery", None),
            take_verified_progress=getattr(operations, "take_verified_progress", None),
            pure_check=getattr(operations, "pure_check", None),
            model=plan.model,
            system=agent_native.native_system_prompt(plan.stack_guide or "", plan.skills),
            task=task, execute=repair_execute,
            user_id=str(ids.user_id), project_id=str(ids.project_id), run_id=str(ids.run_id),
            message_id=str(ids.assistant_message_id), free=is_free,
            emit=None if single_repair else operations.emit,
            max_steps=plan.steps,
            max_segments=(
                None
                if not single_repair and runtime.coordinator is not None
                and getattr(get_settings(), "agent_progressive_continuation", False)
                else 1
            ),
            snapshot_files=snapshot if callable(snapshot) else None,
            source_repair=True,
            directed_path=directed_path,
            allow_max_bash=shell_enabled, portable_cell=True, initial_files=model_baseline,
            edit_deadline=edit_deadline, completion_check=repair_completion,
        )
    except SourceCheckpointError as exc:
        raise RuntimeError(exc.candidate.summary) from None
    if result.stop_reason == "output_limit" and not result.needs_finalization:
        raise RuntimeError(
            "Provider response rejected (output_limit); finalization repair was not verified."
        )
    if result.stop_reason in {"provider_error", "infra_error", "error"}:
        raise RuntimeError(result.summary)
    if single_repair and get_settings().use_sast_gate and get_settings().sast_gate_blocking:
        from yleum_api.services.max_project_kit import MAX_SECURITY_LOCKED_FILES
        from yleum_api.services.sast_gate import check_sast

        assert runtime.handle is not None
        repaired_source = await runtime.handle.snapshot_files()
        sast = check_sast(max_sast_source_view({
            path: content for path, content in repaired_source.items()
            if path not in MAX_SECURITY_LOCKED_FILES
        }, is_max=True))
        if not sast.safe:
            raise RuntimeError("MAX_FINALIZATION_FAILED: repaired source failed security checks")
    if continuation is not None:
        from yleum_api.services.generation.recovery_pipeline import finish_source_repair
        await finish_source_repair(
            continuation, result,
            accounting.budget if accounting is not None else entry.response_budget,
        )
    return result


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
    catalog_runtime: bool = False,
    source_handoff: bool = False,
    interrupted_source_reason: str | None = None,
    render_core_on_rejection: Callable[[], Awaitable[dict[str, str]]] | None = None,
) -> FinalizedAgentSource:
    _max_finalization_proof: ProofBundle | None = None
    if runtime.coordinator is not None and files:
        from yleum_api.services.max_finalization import MaxFinalizationStatus

        assert runtime.handle is not None

        # A healthy unchanged tree cannot prove that the requested edit happened.
        # Check before spending minutes on a production build or running SQL.
        if (
            (_is_edit or _max_has_generated_snapshot)
            and runtime.handle.prove_restoration_adaptation is None
        ):
            unchanged = unchanged_candidate_before_finalization(
                baseline_files=baseline.files,
                workspace_files=await runtime.handle.snapshot_files(),
                requires_source_change=source_change_required(prompt_text),
                message=accumulated,
                catalog_runtime=catalog_runtime,
            )
            if unchanged is not None:
                if unchanged.failure:
                    raise RuntimeError(unchanged.failure)
                return FinalizedAgentSource(None, {}, unchanged.message)

        _repair_history: list[str] = []
        continuation = getattr(operations, "continuation", None)
        single_repair = continuation is None and uses_single_max_source_repair(runtime)

        async def _repair_finalization_source(
            detail: str, source_failure: SourceFailure | None = None,
        ) -> None:
            assert runtime.handle is not None
            assert runtime.coordinator is not None
            baseline = await runtime.handle.snapshot_files()
            history = _repair_history
            if continuation is not None:
                from yleum_api.services.generation.recovery_pipeline import FinalizationProgress
                progress = FinalizationProgress.model_validate_json(json.dumps(
                    continuation.context["completed"]["finalization_progress"],
                ))
                history = list(progress.feedback[:-1])
            # Each repair opens a fresh transcript, so the agent cannot see what it
            # already changed or what the earlier passes were told. Hand both over.
            _changed = sorted(set(baseline) - set(files)) + sorted(
                path for path in set(baseline) & set(files) if baseline[path] != files[path]
            )
            _carry = "".join(
                f"\n\nEARLIER CHECK FEEDBACK (pass {index}, already addressed or not):\n{text}"
                for index, text in enumerate(history, start=1)
            ) + (
                "\n\nFILES YOU ALREADY CHANGED IN THIS RUN: " + ", ".join(_changed[:40])
                if _changed
                else ""
            )
            if continuation is None:
                _repair_history.append(detail)
            repaired = await run_native_source_repair(
                task=(
                    f"{plan.user}\n\nFINAL SOURCE CHECK FEEDBACK:\n{detail}{_carry}\n"
                    "Fix the existing product in this same workspace. Preserve working "
                    "features and data. Do not repeat SQL effects already completed. "
                    "Make real source changes, run build, then done."
                ),
                prompt_text=prompt_text, baseline=baseline, runtime=runtime, ids=ids,
                plan=plan, operations=operations, is_free=is_free,
                shell_enabled=_max_shell_enabled,
                edit_deadline=await runtime.coordinator.source_edit_deadline(repair=True),
                source_failure=source_failure,
                feedback=detail,
            )
            if single_repair and not (
                repaired.done or repaired.needs_finalization or repaired.files
            ):
                raise RuntimeError("MAX_FINALIZATION_FAILED: source repair did not complete")

        try:
            options: _FinalizationOptions = {}
            if continuation is not None:
                options["continuation"] = continuation
            if single_repair:
                options["max_source_repairs"] = int(
                    not getattr(runtime, "source_repair_started", False)
                )
            if source_handoff:
                from yleum_api.services.generation.agent_runtime import migration_feedback

                options["source_preflight"] = lambda: migration_feedback(runtime, ids)
                if interrupted_source_reason is not None:
                    options["interrupted_source_reason"] = interrupted_source_reason
            _finalization = await runtime.coordinator.finalize_with_repair(
                prompt=prompt_text,
                repair=_repair_finalization_source,
                repair_source=_repair_finalization_source,
                **options,
            )
            if _finalization.status is MaxFinalizationStatus.ACTIVATING:
                raise AdaptationActivationPending(_finalization.redacted_detail)
            if _finalization.status is MaxFinalizationStatus.CANCELLED:
                raise asyncio.CancelledError
            if _finalization.status is not MaxFinalizationStatus.COMPLETE:
                raise RuntimeError("MAX_FINALIZATION_FAILED: " + _finalization.redacted_detail)
        except AdaptationActivationPending:
            raise
        except Exception as exc:
            from yleum_api.services.max_finalization import (
                AdaptationActivationRecoveryRequired,
            )

            if isinstance(exc, AdaptationActivationRecoveryRequired):
                raise AdaptationActivationPending(str(exc)) from exc
            from yleum_api.services.generation.source_checkpoint import checkpoint_before_rollback

            await checkpoint_before_rollback(operations)
            raise_if_terminal_cell_error(exc)
            from yleum_api.services.restorations import adaptation_activation_holds_generation_lease

            if await adaptation_activation_holds_generation_lease(
                runtime.coordinator.session_factory,
                ids.run_id,
            ):
                raise AdaptationActivationPending(
                    "sealed restoration adaptation activation requires recovery"
                ) from exc
            rollback_operation_id = None
            try:
                if baseline.sha and _max_has_generated_snapshot:
                    _published_source = await asyncio.to_thread(
                        repo_svc.read_files,
                        ids.project_id,
                        baseline.sha,
                    )
                    await _restore_project_cell_source(
                        runtime.handle,
                        _published_source,
                    )
                elif not _max_has_generated_snapshot and render_core_on_rejection is not None:
                    # Keep the checkpoint private and remove the rejected run's
                    # page and tests from the reusable workspace before a new prompt.
                    restored = await _restore_max_core(
                        handle=runtime.handle,
                        touched_files=await runtime.handle.export_files(),
                        render_core=render_core_on_rejection,
                        trusted_files=runtime.migration_baseline or baseline.files,
                        probe_build=operations.probe_build,
                        source_only=True,
                    )
                    rollback_operation_id = restored.get("operation_id")
                    if restored.get("ok") is not True:
                        raise RuntimeError(str(
                            restored.get("detail") or restored.get("error")
                            or "MAX core restore did not pass runtime verification"
                        ))
            except Exception as _repair_rollback_exc:
                if source_handoff or single_repair:
                    raise_if_terminal_cell_error(_repair_rollback_exc)
                    failure = FinalizationRollbackUnverified(
                        exc, _repair_rollback_exc, run_id=getattr(ids, "run_id", None),
                        operation_id=(
                            rollback_operation_id
                            or getattr(_repair_rollback_exc, "_rollback_operation_id", None)
                            or getattr(_repair_rollback_exc, "operation_id", None)
                        ),
                    )
                    _log.warning("MAX finalization rollback unverified", extra=failure.diagnostics)
                    emit = getattr(operations, "emit", None)
                    if callable(emit):
                        try:
                            await emit("agent.step", {
                                "action": "rollback", "human": "Восстановление не подтверждено",
                                "path": "", "detail": str(failure), "ok": False,
                                "recovery_trace": {
                                    "stage": "reconciling", "error_origin": "runtime",
                                    "retry_decision": "stop",
                                    **{key: value for key, value in failure.diagnostics.items()
                                       if key in {"run_id", "operation_id"} and value is not None},
                                },
                            })
                        except Exception as diagnostic_exc:
                            _log.warning("Rollback diagnostic publication failed: %s",
                                         type(diagnostic_exc).__name__)
                    raise failure from RuntimeError(failure.diagnostics["rollback_error"])
                _log.warning("MAX repair source restore failed", exc_info=_repair_rollback_exc)
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

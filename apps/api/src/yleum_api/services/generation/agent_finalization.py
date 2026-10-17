from __future__ import annotations

import asyncio
import json
import logging
import re
from collections.abc import Mapping
from datetime import datetime
from pathlib import PurePosixPath
from typing import NamedTuple

from yleum_api.core.config import get_settings
from yleum_api.services import agent_builder
from yleum_api.services import repo as repo_svc
from yleum_api.services.generation.contracts import (
    AgentOperations,
    AgentPromptPlan,
    FinalizedAgentSource,
    GenerationIds,
    GenerationRuntime,
    SourceBaseline,
)
from yleum_api.services.generation.runtime import _restore_project_cell_source
from yleum_api.services.generation.source_error_classification import SourceFailure
from yleum_api.services.max_finalization import ProofBundle
from yleum_api.services.project_cell_errors import raise_if_terminal_cell_error

_log = logging.getLogger("yleum_api.routers.messages")

_NO_SOURCE_CHANGE_FAILURE = "edit produced no source changes"
_NO_SOURCE_CHANGE_MESSAGE = (
    "Не удалось применить правку: итоговый код не изменился. "
    "Повтори запрос или уточни, что именно нужно изменить."
)


class AdaptationActivationPending(RuntimeError):
    """Forward-only controller reconciliation owns the sealed candidate."""


class EditSourceChangeVerdict(NamedTuple):
    files: dict[str, str]
    message: str
    failure: str | None


def source_change_allows_partial_save(verdict: EditSourceChangeVerdict) -> bool:
    """Suppress the partial-save CTA after an explicit no-source-change failure."""
    return verdict.failure is None


def explicitly_readonly_request(prompt: str) -> bool:
    return re.fullmatch(
        r"ничего\s+не\s+(?:меняй|изменяй|трогай)|"
        r"(?:do\s+not|don't)\s+(?:change|modify|edit)\s+(?:anything|files|code)",
        prompt.strip().rstrip(".!"), flags=re.IGNORECASE,
    ) is not None


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


def unchanged_candidate_before_finalization(
    *, baseline_files: Mapping[str, str], workspace_files: Mapping[str, str],
    requires_source_change: bool, message: str,
) -> EditSourceChangeVerdict | None:
    product_before = {
        p: value for p, value in baseline_files.items() if _is_edit_source_path(p)
    }
    product_after = {
        p: value for p, value in workspace_files.items() if _is_edit_source_path(p)
    }
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
) -> EditSourceChangeVerdict:
    """Reject a nonempty edit candidate that is byte-identical to its baseline.

    An empty candidate is the established explicit no-op contract. A nonempty
    candidate claims that source was written, so it must produce a semantic Git
    tree change before the run can create a version or report success.
    """
    files = dict(candidate_files)
    if not requires_source_change or not files:
        return EditSourceChangeVerdict(files, message, None)
    product_before = {p: value for p, value in baseline_files.items() if _is_edit_source_path(p)}
    product_after = {p: value for p, value in files.items() if _is_edit_source_path(p)}
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


def source_check_is_repairable(
    dimension: str, outcome: str, detail: str, source_failure: SourceFailure | None = None,
) -> bool:
    """Only trusted fast-check compiler/assertion diagnostics permit this repair."""
    if dimension != "fast_check" or outcome != "red" or (
        source_failure is not None and source_failure.kind == "infrastructure"
    ):
        return False
    compiler = source_failure is not None and bool(source_failure.allowed_paths)
    test = (
        "[targeted-test]" in detail
        and re.search(r"(?m)^not ok \d+ - ", detail)
        and "failureType: 'testCodeFailure'" in detail
        and "code: 'ERR_ASSERTION'" in detail
        and re.search(r"location: '/workspace/tests/[^'\r\n]+:\d+:\d+'", detail)
    )
    return bool(compiler or test)


async def run_native_source_repair(
    *, task: str, prompt_text: str, baseline: Mapping[str, str],
    runtime: GenerationRuntime, ids: GenerationIds, plan: AgentPromptPlan,
    operations: AgentOperations, is_free: bool, shell_enabled: bool,
    edit_deadline: datetime | None,
    source_failure: SourceFailure | None = None,
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
    if continuation is not None:
        from yleum_api.services.generation.recovery_contract import IncompleteResponseBudget
        from yleum_api.services.generation.recovery_pipeline import (
            _result_from_data,
            prepare_source_repair,
        )
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
        if entry.targeted and source_failure is None:
            # A historical path list is not current source-repair authority.
            source_failure = SourceFailure()
    execute = operations.execute
    if source_failure is not None:
        allowed = frozenset(source_failure.allowed_paths)
        capability = getattr(runtime.handle, "capabilities", {})
        guarded_write = capability.get("source_write_nofollow_v1") is True

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
            message_id=str(ids.assistant_message_id), free=is_free, emit=operations.emit,
            max_steps=plan.steps,
            max_segments=(
                None
                if runtime.coordinator is not None
                and getattr(get_settings(), "agent_progressive_continuation", False)
                else 1
            ),
            snapshot_files=snapshot if callable(snapshot) else None,
            source_repair=True,
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
                requires_source_change=not explicitly_readonly_request(prompt_text),
                message=accumulated,
            )
            if unchanged is not None:
                if unchanged.failure:
                    raise RuntimeError(unchanged.failure)
                return FinalizedAgentSource(None, {}, unchanged.message)

        _repair_history: list[str] = []
        continuation = getattr(operations, "continuation", None)

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
            await run_native_source_repair(
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
            )

        try:
            continuation_kw = {"continuation": continuation} if continuation is not None else {}
            _finalization = await runtime.coordinator.finalize_with_repair(
                prompt=prompt_text,
                repair=_repair_finalization_source,
                repair_source=_repair_finalization_source,
                **continuation_kw,
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
            if runtime.coordinator is not None:
                from yleum_api.services.restorations import (
                    adaptation_activation_holds_generation_lease,
                )

                if await adaptation_activation_holds_generation_lease(
                    runtime.coordinator.session_factory,
                    ids.run_id,
                ):
                    raise AdaptationActivationPending(
                        "sealed restoration adaptation activation requires recovery"
                    ) from exc
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

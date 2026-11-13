from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import replace
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from yleum_api.core.config import get_settings
from yleum_api.services import agent_builder
from yleum_api.services import repo as repo_svc
from yleum_api.services.generation.contracts import (
    AgentOperations,
    AgentPromptPlan,
    GenerationIds,
    GenerationRuntime,
    ProjectGenerationFacts,
    SourceBaseline,
    VerificationRecovery,
    uses_single_max_source_repair,
)
from yleum_api.services.generation.recovery_trace import bounded_recovery_diagnostic
from yleum_api.services.generation.runtime import (
    _apply_project_cell_preview_files,
    _require_project_cell,
)
from yleum_api.services.max_generation_contract import _is_product_source
from yleum_api.services.max_project_kit import include_portable_manifest
from yleum_api.services.portable_cell_contract import has_next_home
from yleum_api.services.project_cell_errors import raise_if_terminal_cell_error
from yleum_api.services.project_cell_executor import (
    ProjectCellCommandRole,
    ProjectCellExecutorHandle,
)

_log = logging.getLogger("yleum_api.routers.messages")

_REPAIRABLE_AGENT_STOPS = frozenset({
    "managed_source_contract_unresolved", "max_segments", "max_steps", "max_steps_red",
    "no_progress", "provider_stopped_red",
})


def _rollback_failure(detail: object) -> RuntimeError:
    """Retain a bounded, redacted restoration cause in both error and chain."""
    diagnostic = bounded_recovery_diagnostic(
        str(detail) or "restored runtime verification failed", max_bytes=1500,
    )
    return RuntimeError(diagnostic)


async def recover_stopped_candidate(
    *,
    _agent_res: agent_builder.AgentResult,
    _max_has_generated_snapshot: bool,
    _max_seed_files: dict[str, str],
    _provider_failure: str | None,
    _render_current_max_starter_files: Callable[[], Awaitable[dict[str, str]]],
    baseline: SourceBaseline,
    ids: GenerationIds,
    project_info: ProjectGenerationFacts,
    runtime: GenerationRuntime,
    operations: AgentOperations,
    allow_source_repair: bool = False,
) -> tuple[agent_builder.AgentResult, bool, dict[str, str], int]:
    _seg = _agent_res.segments
    # Остановленный прогон никогда не коммитится как наполовину сделанная
    # правка, даже если локальная проверка типов случайно зелёная: каждый
    # тронутый путь восстанавливается из последнего снимка, новые файлы
    # удаляются, а известно-рабочее приложение проверяется детерминированно.
    # Именно это делает «остановку по лимиту» совместимой с обещанием, что
    # Studio всегда оставляет после себя целое приложение.
    # Зелёная ограниченная остановка уже прошла и сборку исходников, и договор
    # о завершённости по брифу (включая локальное восстановление доказательства
    # выше). Выкатить её безопаснее, чем выбросить готовый продукт только
    # потому, что ход провайдера закончился.
    _must_restore_previous = not _agent_res.done and not _agent_res.needs_finalization
    _source_only = getattr(runtime, "coordinator", None) is not None
    _first_max_without_product = not _max_has_generated_snapshot
    # A settled length limit left no executable batch, but earlier writes are
    # still valid repair input. A new task can finish the missing home screen.
    repair_missing_home = (
        _first_max_without_product
        and _agent_res.stop_reason in {"output_limit", "exploring"}
        and project_info.template == "max_miniapp"
        and not has_next_home(_agent_res.files)
        and any(
            _is_product_source(path) and bool(source.strip())
            for path, source in _agent_res.files.items()
        )
    )
    if (
        allow_source_repair
        and _must_restore_previous
        and _agent_res.files
        and (
            (not _provider_failure and _agent_res.stop_reason in _REPAIRABLE_AGENT_STOPS)
            or repair_missing_home
        )
        and getattr(operations, "continuation", None) is None
        and uses_single_max_source_repair(runtime)
    ):
        # Keep the exact draft for the coordinator. A stopped turn still needs
        # a repair even when the incomplete product happens to compile.
        return (
            replace(
                _agent_res,
                summary="Проверяю приложение.",
                stop_reason=agent_builder.SOURCE_REPAIR_HANDOFF,
                needs_finalization=True,
                continuation_reason=_agent_res.stop_reason,
            ),
            _first_max_without_product,
            _max_seed_files,
            _seg,
        )
    _stopped_cause = bounded_recovery_diagnostic(
        f"generation stopped ({_agent_res.stop_reason or 'incomplete'}): {_agent_res.summary}",
        max_bytes=2000,
    )
    _rollback_error: Exception | None = None
    _rollback_verified = False
    if _must_restore_previous and baseline.sha and not _first_max_without_product:
        try:
            _unsafe_stop_reason = _agent_res.stop_reason
            _rollback_build = await _restore_touched_tree(
                project_id=ids.project_id,
                project_slug=project_info.slug,
                handle=_require_project_cell(runtime.handle),
                baseline_sha=baseline.sha,
                touched_files=_agent_res.files,
                probe_build=operations.probe_build,
                source_only=_source_only,
            )
            if _rollback_build.get("ok") is True:
                _rollback_verified = True
                _max_seed_files = {}
                _agent_res = agent_builder.AgentResult(
                    done=False,
                    summary=(
                        "Сборка не завершена. Незавершённые изменения не опубликованы; "
                        "в Studio оставлена последняя рабочая версия приложения."
                    ),
                    files={},
                    steps=_agent_res.steps,
                    transcript=_agent_res.transcript,
                    stop_reason=(
                        "max_steps_rolled_back"
                        if _unsafe_stop_reason in {"max_steps", "max_steps_green", "max_steps_red"}
                        else "provider_stopped_rolled_back"
                        if _unsafe_stop_reason in {"provider_stopped_green", "provider_stopped_red"}
                        else "unsafe_changes_rolled_back"
                    ),
                    evidence=_agent_res.evidence,
                    segments=_agent_res.segments,
                )
                await operations.emit(
                    "agent.step",
                    {
                        "step": _agent_res.steps,
                        "action": "rollback",
                        "human": "Сборка не завершена — сохраняю рабочую версию",
                        "path": "",
                        "detail": ("Лимит исчерпан; красные файлы отброшены, сборка снова чистая."),
                        "ok": False,
                    },
                )
            else:
                _rollback_error = _rollback_failure(
                    _rollback_build.get("detail") or _rollback_build.get("error")
                    or "restored runtime verification failed",
                )
        except Exception as _rollback_exc:
            raise_if_terminal_cell_error(_rollback_exc)
            _rollback_error = _rollback_failure(_rollback_exc)
            print(f"[PP] hard-limit rollback failed: {_rollback_error}", flush=True)
    elif _must_restore_previous and _first_max_without_product:
        # Config sync creates a legitimate snapshot before the first AI
        # build, but that snapshot may contain only managed files and no
        # product page. It is not a rollback target. Restore only the
        # buildable platform core, remove every generated product path,
        # and do not publish the core as a successful application.
        try:
            _rollback_build = await _restore_max_core(
                handle=_require_project_cell(runtime.handle),
                touched_files=_agent_res.files,
                render_core=_render_current_max_starter_files,
                trusted_files=runtime.migration_baseline or baseline.files,
                probe_build=operations.probe_build,
                source_only=_source_only,
            )
            if _rollback_build.get("ok") is True:
                _rollback_verified = True
                _max_seed_files = {}
                _agent_res = agent_builder.AgentResult(
                    done=False,
                    summary=(
                        "Первая генерация не завершена. Частичные файлы отброшены; "
                        "сохранено только безопасное ядро MAX без продуктовой страницы."
                    ),
                    files={},
                    steps=_agent_res.steps,
                    transcript=_agent_res.transcript,
                    stop_reason="core_only_rolled_back",
                    evidence=_agent_res.evidence,
                    segments=_agent_res.segments,
                )
                await operations.emit(
                    "agent.step",
                    {
                        "step": _agent_res.steps,
                        "action": "rollback",
                        "human": "Генерация не завершена — сохраняю только ядро MAX",
                        "path": "",
                        "detail": (
                            "Все частичные красные файлы отброшены; MAX core без "
                            "продуктовой страницы снова проходит проверку."
                        ),
                        "ok": False,
                    },
                )
            else:
                _rollback_error = _rollback_failure(
                    _rollback_build.get("detail") or _rollback_build.get("error")
                    or "restored runtime verification failed",
                )
                print(
                    f"[PP] first-MAX safe fallback build red: {_rollback_error}",
                    flush=True,
                )
        except Exception as _rollback_exc:
            raise_if_terminal_cell_error(_rollback_exc)
            _rollback_error = _rollback_failure(_rollback_exc)
            print(
                f"[PP] first-MAX safe fallback failed: {_rollback_error}",
                flush=True,
            )

    # Existing products must traverse restoration above before surfacing
    # a failed provider call. Keep the original cause even if rollback
    # replaces the agent result with its own status message.
    if _provider_failure:
        raise RuntimeError(_provider_failure)

    # A restored (or unverified) stopped tree is never a candidate for
    # finalization. Preserve the original cause before the unchanged-tree
    # guard can mistake the restored baseline for a failed accepted edit.
    if _must_restore_previous and _source_only:
        status = "rolled_back" if _rollback_verified else "rollback_unverified"
        detail = f": {_rollback_error}" if _rollback_error is not None else ""
        raise RuntimeError(f"{_stopped_cause}; {status}{detail}") from _rollback_error

    return _agent_res, _first_max_without_product, _max_seed_files, _seg


async def recover_rejected_candidate(
    *,
    _agent_res: agent_builder.AgentResult,
    _first_max_without_product: bool,
    _render_current_max_starter_files: Callable[[], Awaitable[dict[str, str]]],
    _rt_error: str,
    _runtime_ok: bool,
    _tc_error: str,
    _typecheck_ok: bool,
    accumulated: str,
    baseline: SourceBaseline,
    files: dict[str, str],
    ids: GenerationIds,
    project_info: ProjectGenerationFacts,
    runtime: GenerationRuntime,
    operations: AgentOperations,
    plan: AgentPromptPlan | None = None,
    prompt_text: str = "",
    is_free: bool = False,
    _max_shell_enabled: bool = False,
) -> VerificationRecovery:
    repair_failure: Exception | None = None
    single_repair = uses_single_max_source_repair(runtime)
    if (
        (get_settings().use_native_agent or single_repair)
        and runtime.coordinator is not None
        and runtime.handle is not None
        and plan is not None
        and files
        and (_agent_res.done or _agent_res.needs_finalization)
        and _runtime_ok
        and not _typecheck_ok
        and not (single_repair and getattr(runtime, "source_repair_started", False))
    ):
        from yleum_api.services.generation.agent_finalization import (
            run_native_source_repair,
            source_check_is_repairable,
        )

        proof = await runtime.coordinator.fast_check()
        classify_receipt = getattr(runtime.coordinator, "source_failure", None)
        source_failure = await classify_receipt(proof) if callable(classify_receipt) else None
        receipt_current = not callable(classify_receipt) or source_failure is not None
        if receipt_current and source_check_is_repairable(
            proof.dimension, proof.outcome, proof.redacted_detail, source_failure,
        ):
            before = await runtime.handle.snapshot_files()
            window_open = False
            try:
                deadline = await runtime.coordinator.source_edit_deadline(repair=True)
                remaining = (deadline - datetime.now(UTC)).total_seconds() if deadline else 0
                if remaining < 60:
                    raise TimeoutError("generation deadline exceeded before source repair")
                window_open = True
                async with asyncio.timeout(remaining):
                    repaired = await run_native_source_repair(
                        task=(
                            f"{plan.user}\n\nFINAL SOURCE CHECK FEEDBACK:\n"
                            f"{proof.redacted_detail}\n"
                            "Fix the existing product and keep the tested requirement. "
                            "Preserve working features and data; do not repeat SQL effects. "
                            "Make real source changes, run build, then done."
                        ),
                        prompt_text=prompt_text,
                        baseline=before,
                        runtime=runtime,
                        ids=ids,
                        plan=plan,
                        operations=operations,
                        is_free=is_free,
                        shell_enabled=_max_shell_enabled,
                        edit_deadline=deadline,
                        source_failure=(
                            source_failure
                            if source_failure and source_failure.allowed_paths else None
                        ),
                        feedback=proof.redacted_detail,
                    )
                    after = await runtime.handle.snapshot_files()
                    files = {
                        **files,
                        **repaired.files,
                        **{
                            path: after.get(path, "")
                            for path in set(before) | set(after)
                            if before.get(path) != after.get(path)
                        },
                    }
                    _agent_res.files = files
                    if after != before and (repaired.done or repaired.needs_finalization):
                        check = await operations.probe_build()
                        if check.get("ok"):
                            files = await runtime.handle.export_files()
                            repaired.files = files
                            _agent_res = repaired
                            _typecheck_ok = True
                            _tc_error = ""
            except Exception as exc:
                raise_if_terminal_cell_error(exc)
                if not single_repair and not window_open:
                    raise
                repair_failure = exc
                # Retain every actual repair path even when the provider stops
                # after a partial write. Restore via the ordinary guarded path.
                try:
                    after = await runtime.handle.snapshot_files()
                    files = {
                        **files,
                        **{
                            path: after.get(path, "")
                            for path in set(before) | set(after)
                            if before.get(path) != after.get(path)
                        },
                    }
                    _agent_res.files = files
                except Exception:
                    _log.warning("Native source repair snapshot unavailable")
    _agent_verification_failed = not (_typecheck_ok and _runtime_ok)
    # Final green-tree invariant. A bounded native run may stop for a
    # budget/provider reason, but Studio must never keep its red tree. The
    # earlier rollback covers a non-done AgentResult; this guard covers a
    # model that said `done` while the independent verification disagreed.
    if (get_settings().use_native_agent or single_repair) and _agent_verification_failed:
        from yleum_api.services.generation.source_checkpoint import checkpoint_before_rollback

        await checkpoint_before_rollback(operations)
        _verification_error = _tc_error or _rt_error or "final verification failed"
        _verification_rolled_back = False
        try:
            if baseline.sha and not _first_max_without_product:
                _rollback_build = await _restore_touched_tree(
                    project_id=ids.project_id,
                    project_slug=project_info.slug,
                    handle=_require_project_cell(runtime.handle),
                    baseline_sha=baseline.sha,
                    touched_files=files,
                    probe_build=operations.probe_build,
                    source_only=runtime.coordinator is not None,
                )
                _verification_rolled_back = bool(_rollback_build.get("ok"))
            else:
                # A brand-new MAX project has no product snapshot yet. Its
                # safe fallback is the versioned core without a product page.
                _rollback_build = await _restore_max_core(
                    handle=_require_project_cell(runtime.handle),
                    touched_files=files,
                    render_core=_render_current_max_starter_files,
                    trusted_files=runtime.migration_baseline or baseline.files,
                    probe_build=operations.probe_build,
                    source_only=runtime.coordinator is not None,
                )
                _verification_rolled_back = bool(_rollback_build.get("ok"))
            if _verification_rolled_back:
                _runtime_ok = True
                _typecheck_ok = True
                files = {}
                _agent_res = agent_builder.AgentResult(
                    done=False,
                    summary=(
                        "Сборка не завершена: финальная проверка не прошла. "
                        "Незавершённые изменения отброшены; оставлена рабочая версия."
                    ),
                    files=files,
                    steps=_agent_res.steps,
                    transcript=_agent_res.transcript,
                    stop_reason="verification_rolled_back",
                )
                accumulated = _agent_res.summary
                await operations.emit(
                    "agent.step",
                    {
                        "step": _agent_res.steps,
                        "action": "rollback",
                        "human": "Сборка не завершена — сохраняю рабочую версию",
                        "path": "",
                        "detail": (
                            "Финальная проверка красная; небезопасные изменения не публикуются."
                        ),
                        "ok": False,
                    },
                )
                print(
                    f"[PP] native final verification red -> green rollback ({_verification_error})",
                    flush=True,
                )
            else:
                files = {}
                accumulated = (
                    "Финальная проверка не прошла; изменения не "
                    "опубликованы, чтобы не сломать приложение."
                )
        except Exception as _verification_rollback_exc:
            if repair_failure is None:
                raise_if_terminal_cell_error(_verification_rollback_exc)
            files = {}
            accumulated = (
                "Финальная проверка не прошла; изменения не "
                "опубликованы, чтобы не сломать приложение."
            )
            if repair_failure is None:
                print(
                    "[PP] native final verification rollback failed: "
                    f"{_verification_rollback_exc!r}",
                    flush=True,
                )
            else:
                _log.warning("Native source repair rollback failed")
    if repair_failure is not None:
        raise repair_failure

    return VerificationRecovery(
        _agent_verification_failed, _agent_res, files, _runtime_ok, _typecheck_ok, accumulated
    )


async def _restore_touched_tree(
    *,
    project_id: UUID,
    project_slug: str,
    handle: ProjectCellExecutorHandle,
    baseline_sha: str,
    touched_files: dict[str, str],
    probe_build: Callable[[], Awaitable[dict[str, Any]]],
    source_only: bool = False,
) -> dict[str, Any]:
    """Stage the complete restored tree before compiling any of its dependencies."""
    _baseline_files = await asyncio.to_thread(repo_svc.read_files, project_id, baseline_sha)
    _restore_files = {
        path: _baseline_files[path] for path in touched_files if path in _baseline_files
    }
    _new_paths = [path for path in touched_files if path not in _baseline_files]
    if _restore_files or _new_paths:
        await _apply_project_cell_preview_files(
            files={**_restore_files, **dict.fromkeys(_new_paths, "")},
            project_cell_handle=handle,
            empty_files=tuple(path for path, content in _restore_files.items() if content == ""),
            sync_preview=not source_only,
        )
    _rollback_build = await _verify_restored_runtime(handle, probe_build, source_only=source_only)
    return _rollback_build


async def _restore_max_core(
    *,
    handle: ProjectCellExecutorHandle,
    touched_files: dict[str, str],
    render_core: Callable[[], Awaitable[dict[str, str]]],
    trusted_files: Mapping[str, str],
    probe_build: Callable[[], Awaitable[dict[str, Any]]],
    source_only: bool = False,
) -> dict[str, Any]:
    """Restore the core and remove product importers in one fenced source patch."""
    # The starter renderer omits the machine manifest. Reuse the pre-agent
    # seed/accepted source so rollback cannot keep the rejected draft's commands.
    core_files = include_portable_manifest(await render_core(), trusted_files)
    removed_paths = sorted(
        (touched_files.keys() - core_files.keys()) | {"src/app/page.tsx"}
    )
    await _apply_project_cell_preview_files(
        files={**core_files, **dict.fromkeys(removed_paths, "")},
        project_cell_handle=handle,
        empty_files=tuple(
            path
            for path, content in core_files.items()
            if content == "" and path not in removed_paths
        ),
        sync_preview=not source_only,
    )
    return await _verify_restored_runtime(handle, probe_build, source_only=source_only)


async def _verify_restored_runtime(
    handle: ProjectCellExecutorHandle,
    probe_build: Callable[[], Awaitable[dict[str, Any]]],
    *,
    source_only: bool,
) -> dict[str, Any]:
    if not source_only:
        return await probe_build()
    restore = getattr(handle, "run_role", None)
    if not callable(restore):
        raise RuntimeError("restored runtime verification unavailable")
    # This is a fenced controller operation, not candidate finalization:
    # no new migrations, no promotion and no evaluation of the failed brief.
    operation_id = uuid4()
    try:
        result = await restore(ProjectCellCommandRole.RESTORE_RUNTIME, operation_id)
    except Exception as exc:
        raise_if_terminal_cell_error(exc, operation_id=operation_id)
        exc._rollback_operation_id = operation_id  # type: ignore[attr-defined]
        raise
    return {"ok": result.ok, "detail": result.redacted_detail, "operation_id": str(operation_id)}

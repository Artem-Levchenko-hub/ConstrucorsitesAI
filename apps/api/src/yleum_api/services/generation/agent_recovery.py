from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import Any
from uuid import UUID

from yleum_api.core.config import get_settings
from yleum_api.services import agent_builder
from yleum_api.services import repo as repo_svc
from yleum_api.services.generation.contracts import (
    AgentOperations,
    GenerationIds,
    GenerationRuntime,
    ProjectGenerationFacts,
    SourceBaseline,
    VerificationRecovery,
)
from yleum_api.services.generation.runtime import _apply_project_cell_preview_files
from yleum_api.services.project_cell_errors import raise_if_terminal_cell_error
from yleum_api.services.project_cell_executor import ProjectCellExecutorHandle

_log = logging.getLogger("yleum_api.routers.messages")


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
) -> tuple[agent_builder.AgentResult, bool, dict[str, str], int]:
    _seg = _agent_res.segments
    # A stopped run is never committed as a partially implemented edit,
    # even when its local typecheck happens to be green. Restore every
    # touched path from the last snapshot, remove newly-created files,
    # and verify the known-good application deterministically. This is
    # what makes "stop at wallet/step limit" compatible with the promise
    # that Studio always leaves a complete application behind.
    _bounded_stop = _agent_res.stop_reason in {
        "max_steps_green",
        "max_steps_red",
        "provider_stopped_green",
        "provider_stopped_red",
    }
    # For MAX, a green bounded stop has already passed both the source
    # build and the brief-aware completion contract (including local
    # proof recovery above). Shipping it is safer than discarding a
    # complete product merely because the provider turn ended. Other
    # stacks preserve the historical conservative rollback policy.
    _must_restore_previous = (not _agent_res.done and not _agent_res.needs_finalization) or (
        _bounded_stop and project_info.template != "max_miniapp"
    )
    _first_max_without_product = (
        project_info.template == "max_miniapp" and not _max_has_generated_snapshot
    )
    if _must_restore_previous and baseline.sha and not _first_max_without_product:
        try:
            _unsafe_stop_reason = _agent_res.stop_reason
            _rollback_build = await _restore_touched_tree(
                project_id=ids.project_id,
                project_slug=project_info.slug,
                handle=runtime.handle,
                baseline_sha=baseline.sha,
                touched_files=_agent_res.files,
                probe_build=operations.probe_build,
            )
            if _rollback_build.get("ok"):
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
        except Exception as _rollback_exc:
            raise_if_terminal_cell_error(_rollback_exc)
            print(f"[PP] hard-limit rollback failed: {_rollback_exc!r}", flush=True)
    elif _must_restore_previous and _first_max_without_product:
        # Config sync creates a legitimate snapshot before the first AI
        # build, but that snapshot may contain only managed files and no
        # product page. It is not a rollback target. Restore only the
        # buildable platform core, remove every generated product path,
        # and do not publish the core as a successful application.
        try:
            _rollback_build = await _restore_max_core(
                project_id=ids.project_id,
                project_slug=project_info.slug,
                handle=runtime.handle,
                touched_files=_agent_res.files,
                render_core=_render_current_max_starter_files,
                probe_build=operations.probe_build,
            )
            if _rollback_build.get("ok"):
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
                print(
                    "[PP] first-MAX safe fallback build red: "
                    f"{_rollback_build.get('detail') or _rollback_build.get('error')}",
                    flush=True,
                )
        except Exception as _rollback_exc:
            raise_if_terminal_cell_error(_rollback_exc)
            print(
                f"[PP] first-MAX safe fallback failed: {_rollback_exc!r}",
                flush=True,
            )

    # Existing products must traverse restoration above before surfacing
    # a failed provider call. Keep the original cause even if rollback
    # replaces the agent result with its own status message.
    if _provider_failure:
        raise RuntimeError(_provider_failure)

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
) -> VerificationRecovery:
    _agent_verification_failed = not (_typecheck_ok and _runtime_ok)
    # Final green-tree invariant. A bounded native run may stop for a
    # budget/provider reason, but Studio must never keep its red tree. The
    # earlier rollback covers a non-done AgentResult; this guard covers a
    # model that said `done` while the independent verification disagreed.
    if get_settings().use_native_agent and (not _runtime_ok or not _typecheck_ok):
        _verification_error = _tc_error or _rt_error or "final verification failed"
        _verification_rolled_back = False
        try:
            if baseline.sha and not _first_max_without_product:
                _rollback_build = await _restore_touched_tree(
                    project_id=ids.project_id,
                    project_slug=project_info.slug,
                    handle=runtime.handle,
                    baseline_sha=baseline.sha,
                    touched_files=files,
                    probe_build=operations.probe_build,
                )
                _verification_rolled_back = bool(_rollback_build.get("ok"))
            elif project_info.template == "max_miniapp":
                # A brand-new MAX project has no product snapshot yet. Its
                # safe fallback is the versioned core without a product page.
                _rollback_build = await _restore_max_core(
                    project_id=ids.project_id,
                    project_slug=project_info.slug,
                    handle=runtime.handle,
                    touched_files=files,
                    render_core=_render_current_max_starter_files,
                    probe_build=operations.probe_build,
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
            raise_if_terminal_cell_error(_verification_rollback_exc)
            files = {}
            accumulated = (
                "Финальная проверка не прошла; изменения не "
                "опубликованы, чтобы не сломать приложение."
            )
            print(
                f"[PP] native final verification rollback failed: {_verification_rollback_exc!r}",
                flush=True,
            )

    return VerificationRecovery(
        _agent_verification_failed, _agent_res, files, _runtime_ok, _typecheck_ok, accumulated
    )


async def _restore_touched_tree(
    *,
    project_id: UUID,
    project_slug: str,
    handle: ProjectCellExecutorHandle | None,
    baseline_sha: str,
    touched_files: dict[str, str],
    probe_build: Callable[[], Awaitable[dict[str, Any]]],
) -> dict[str, Any]:
    """Reload the snapshot, restore existing touched paths, then delete new paths."""
    _baseline_files = await asyncio.to_thread(repo_svc.read_files, project_id, baseline_sha)
    _restore_files = {
        path: _baseline_files[path] for path in touched_files if path in _baseline_files
    }
    _new_paths = [path for path in touched_files if path not in _baseline_files]
    if _restore_files:
        await _apply_project_cell_preview_files(
            project_id=project_id,
            project_slug=project_slug,
            files=_restore_files,
            project_cell_handle=handle,
        )
    if _new_paths:
        await _apply_project_cell_preview_files(
            project_id=project_id,
            project_slug=project_slug,
            files={path: "" for path in _new_paths},
            project_cell_handle=handle,
        )
    _rollback_build = await probe_build()
    return _rollback_build


async def _restore_max_core(
    *,
    project_id: UUID,
    project_slug: str,
    handle: ProjectCellExecutorHandle | None,
    touched_files: dict[str, str],
    render_core: Callable[[], Awaitable[dict[str, str]]],
    probe_build: Callable[[], Awaitable[dict[str, Any]]],
) -> dict[str, Any]:
    """Render current config on every recovery, preserving the core write call."""
    _safe_files = await render_core()
    _new_paths = sorted(
        {path for path in touched_files if path not in _safe_files} | {"src/app/page.tsx"}
    )
    await _apply_project_cell_preview_files(
        project_id=project_id,
        project_slug=project_slug,
        files=_safe_files,
        project_cell_handle=handle,
    )
    if _new_paths:
        await _apply_project_cell_preview_files(
            project_id=project_id,
            project_slug=project_slug,
            files={path: "" for path in _new_paths},
            project_cell_handle=handle,
        )
    _rollback_build = await probe_build()
    return _rollback_build

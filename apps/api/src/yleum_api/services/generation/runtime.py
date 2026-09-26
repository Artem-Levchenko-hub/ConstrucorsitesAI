from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable, Mapping, Sequence
from typing import TYPE_CHECKING, Any
from uuid import UUID

from fastapi import status

from yleum_api.core.errors import ApiError
from yleum_api.services import (
    orchestrator_client,
    project_cell_executor,
)

if TYPE_CHECKING:
    from yleum_api.services.agent_builder import Action as AgentBuilderAction
    from yleum_api.services.project_cell_executor import ProjectCellExecutorHandle


def _agent_builder_action(
    name: str,
    args: Mapping[str, Any] | None = None,
) -> AgentBuilderAction:
    from yleum_api.services.agent_builder import Action

    return Action(name=name, args=dict(args or {}))


async def _project_cell_action(
    project_cell_handle: ProjectCellExecutorHandle,
    name: str,
    *,
    args: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    return await project_cell_handle.execute(_agent_builder_action(name, args))


async def _project_cell_build(
    project_cell_handle: ProjectCellExecutorHandle,
) -> dict[str, Any]:
    return await _project_cell_action(project_cell_handle, "build")


async def _project_cell_runtime_check(
    project_cell_handle: ProjectCellExecutorHandle,
    *,
    path: str = "/",
) -> dict[str, Any]:
    return await _project_cell_action(
        project_cell_handle,
        "runtime_check",
        args={"path": path},
    )


async def _project_cell_list_dir(
    project_cell_handle: ProjectCellExecutorHandle,
    path: str,
) -> str:
    result = await _project_cell_action(
        project_cell_handle,
        "list_dir",
        args={"path": path},
    )
    return str(result.get("detail") or "") if result.get("ok") else ""


async def _project_cell_read_file(
    project_cell_handle: ProjectCellExecutorHandle,
    path: str,
) -> str | None:
    result = await _project_cell_action(
        project_cell_handle,
        "read_file",
        args={"path": path},
    )
    if not result.get("ok"):
        return None
    content = result.get("content")
    return content if isinstance(content, str) else None


async def _build_agent_seed_parts(
    project_id: UUID,
    project_slug: str,
    *,
    project_cell_handle: ProjectCellExecutorHandle | None = None,
    refresh_managed_sdk: bool = False,
    max_config_source: str | None = None,
) -> list[str]:
    if refresh_managed_sdk:
        from yleum_api.services.max_managed_generation import (
            managed_browser_files,
            refresh_integration_sdk,
        )

        # Required delivery precedes fail-soft context reads and all model work.
        # Keep this outside the try: a failed lease/write must abort generation.
        if project_cell_handle is not None:
            await refresh_integration_sdk(project_cell_handle, max_config_source=max_config_source)
        else:
            # Legacy provisioning restores template defaults. Reapply the saved
            # profile afterwards so the model never reads that empty catalog.
            await orchestrator_client.hot_reload(
                project_id,
                project_slug,
                managed_browser_files(max_config_source),
            )
    seed_parts: list[str] = []
    try:
        if project_cell_handle is not None:
            ents = await _project_cell_list_dir(project_cell_handle, "entities")
            dash = await _project_cell_list_dir(
                project_cell_handle,
                "src/app/(app)/dashboard",
            )
            crud = await _project_cell_read_file(
                project_cell_handle,
                "src/components/omnia/crud-resource.tsx",
            )
        else:
            ents = await orchestrator_client.agent_list_dir(
                project_id,
                project_slug,
                "entities",
            )
            dash = await orchestrator_client.agent_list_dir(
                project_id,
                project_slug,
                "src/app/(app)/dashboard",
            )
            crud = await orchestrator_client.agent_read_file(
                project_id,
                project_slug,
                "src/components/omnia/crud-resource.tsx",
            )
        seed_parts.append(f"entities/ contains:\n{ents}")
        seed_parts.append(f"src/app/(app)/dashboard/ contains:\n{dash}")
        if crud:
            seed_parts.append(
                "src/components/omnia/crud-resource.tsx (the entity-page "
                'component — render <CrudResource entity="Name"/> in each '
                "page):\n" + crud[:4000]
            )
    except Exception as seed_exc:
        print(f"[PP] agent seed-context skipped: {seed_exc!r}", flush=True)
    return seed_parts


async def _prepare_max_runtime_context(
    *,
    project_id: UUID,
    project_slug: str,
    user_id: UUID,
    generation_run_id: UUID,
    vision_context: str,
    legacy_execute: Callable[[AgentBuilderAction], Awaitable[dict[str, Any]]],
    max_shell_requested: bool,
    agent_emit: Callable[[str, dict[str, Any]], Awaitable[None]],
    max_model_locked_files: frozenset[str],
    max_security_locked_files: frozenset[str],
    capacity_dispatch_token: UUID | None = None,
) -> dict[str, Any]:
    project_cell_handle: ProjectCellExecutorHandle | None = None
    max_sandbox_capabilities: dict[str, Any] = {}
    max_sandbox_attested = False
    max_shell_enabled = _resolve_max_shell_enabled(
        max_shell_requested=max_shell_requested,
        sandbox_attested=False,
        project_cell_handle=None,
    )
    active_max_locked_files = max_model_locked_files
    agent_result = None
    base_agent_executor = legacy_execute
    try:
        project_cell_handle = await project_cell_executor.maybe_create_project_cell_executor(
            project_id=project_id,
            project_slug=project_slug,
            project_template="max_miniapp",
            user_id=user_id,
            generation_run_id=generation_run_id,
            legacy_execute=legacy_execute,
            vision_context=vision_context,
            agent_emit=lambda payload: agent_emit(
                "agent.step",
                {
                    **payload,
                    "action": "capacity_wait",
                    "human": payload.get("action", "Ожидаю ресурсы сервера"),
                    "ok": True,
                },
            ),
            capacity_dispatch_token=capacity_dispatch_token,
        )
    except project_cell_executor.ProjectCellExecutorUnavailable as cell_exc:
        await agent_emit(
            "agent.step",
            {
                "step": 0,
                "action": "project_cell",
                "human": "Project Cell не подготовился",
                "path": "",
                "detail": str(cell_exc),
                "ok": False,
            },
        )
        raise
    else:
        if project_cell_handle is None:
            # No cell, and no legacy dev container to fall back on since the site
            # builder left. Refuse loudly instead of building somewhere else.
            raise project_cell_executor.ProjectCellExecutorUnavailable(
                "У проекта нет своей ячейки — собирать приложение негде."
            )
        base_agent_executor = project_cell_handle.execute
        max_shell_enabled = _resolve_max_shell_enabled(
            max_shell_requested=max_shell_requested,
            sandbox_attested=False,
            project_cell_handle=project_cell_handle,
        )
        active_max_locked_files = max_security_locked_files
        await agent_emit(
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
    return {
        "project_cell_handle": project_cell_handle,
        "base_agent_executor": base_agent_executor,
        "max_sandbox_capabilities": max_sandbox_capabilities,
        "max_sandbox_attested": max_sandbox_attested,
        "max_shell_enabled": max_shell_enabled,
        "active_max_locked_files": active_max_locked_files,
        "agent_result": agent_result,
    }


async def _execute_max_agent_action(
    action: AgentBuilderAction,
    *,
    project_id: UUID,
    project_slug: str,
    vision_context: str,
    base_agent_executor: Callable[[AgentBuilderAction], Awaitable[dict[str, Any]]],
    max_shell_enabled: bool,
    project_cell_handle: ProjectCellExecutorHandle | None,
    active_max_locked_files: frozenset[str],
    max_model_write_rejection: Callable[[str, str], str | None],
) -> dict[str, Any]:
    from yleum_api.services.agent_builder import _KNOWN_ACTIONS

    if action.name not in _KNOWN_ACTIONS:
        return {"ok": False, "error": f"unknown action {action.name}"}
    if (
        project_cell_handle is not None
        and getattr(project_cell_handle, "is_portable", lambda: False)()
    ):
        if action.name in {"write_file", "edit_file"}:
            from yleum_api.services.secret_safety import contains_provider_secret, is_secret_file

            candidate = str(action.args.get("content") or action.args.get("replace") or "")
            if is_secret_file(action.path) or contains_provider_secret(candidate):
                return {
                    "ok": False,
                    "error": "Credential files and provider secrets must stay in Studio.",
                }
        if action.name == "bash" and not max_shell_enabled:
            return {"ok": False, "error": "Project Cell shell is disabled by the operator."}
        return await project_cell_handle.execute(action)
    if project_cell_handle is not None and action.name in {
        "runtime_check",
        "read_logs",
        "probe",
        "verify_isolation",
    }:
        return await project_cell_handle.execute(action)
    if action.name == "runtime_check":
        runtime = await base_agent_executor(action)
        if not runtime.get("ok"):
            return runtime
        from yleum_api.services.max_runtime_probe import (
            probe_max_runtime as probe_max_runtime_legacy,
        )

        try:
            runtime_status = await orchestrator_client.get_status(project_id)
            base_url = str(runtime_status.get("dev_url") or "") or None
            max_probe = await probe_max_runtime_legacy(
                project_id,
                project_slug,
                base_url=base_url,
            )
        except Exception as probe_exc:
            return {
                "ok": False,
                "detail": (f"MAX data-plane proof crashed: {type(probe_exc).__name__}"),
            }
        return {
            "ok": max_probe.ok,
            "detail": (f"{runtime.get('detail') or 'runtime route passed'}; {max_probe.detail}"),
        }
    if action.name == "bash":
        return await _run_max_shell_action(
            action=action,
            project_id=project_id,
            project_slug=project_slug,
            max_shell_enabled=max_shell_enabled,
            base_agent_executor=base_agent_executor,
            project_cell_handle=project_cell_handle,
            active_max_locked_files=active_max_locked_files,
            max_model_write_rejection=max_model_write_rejection,
        )
    if action.name in {"write_file", "edit_file"} and action.path in active_max_locked_files:
        return {
            "ok": False,
            "error": (
                f"{action.path} is managed by Yleum. "
                "Edit src/app/page.tsx, src/app/globals.css or a "
                "new feature-specific component instead."
            ),
        }
    if action.name in {"write_file", "edit_file"}:
        candidate = str(action.args.get("content") or action.args.get("replace") or "")
        secret_rejection = max_model_write_rejection(action.path, candidate)
        if secret_rejection:
            return {"ok": False, "error": secret_rejection}
        from yleum_api.services.max_generation_contract import (
            unsafe_max_backend_paths as unsafe_max_backend_paths_fn,
        )

        if unsafe_max_backend_paths_fn({action.path: candidate}):
            return {
                "ok": False,
                "error": (
                    "Direct DB access is forbidden in MAX product files. "
                    "Use createMaxAction/getMaxActions from "
                    "@/lib/omnia/integration-client."
                ),
            }
        if action.path.startswith("src/app/api/max/") or action.path.startswith(
            "src/app/api/omnia/"
        ):
            return {
                "ok": False,
                "error": (
                    "Yleum owns /api/max and /api/omnia. "
                    "Call the managed integration client instead of "
                    "creating a parallel route."
                ),
            }
    return await base_agent_executor(action)


async def _abort_unsafe_max_backend(
    *,
    project_id: UUID,
    project_slug: str,
    current_files: Mapping[str, str],
    files: dict[str, str],
    unsafe_paths: Sequence[str],
    project_cell_handle: ProjectCellExecutorHandle | None = None,
    violation_kind: str = "managed data isolation",
) -> None:
    """Fail closed on unsafe MAX backend writes.

    The text/native MAX agents write directly into the live dev runtime, so a
    rejected backend change must also restore the preview tree. A rollback
    transport failure must not downgrade this to an advisory-only warning: the
    generation still aborts with a hard 422 and the in-memory file map is reset
    to the last known-safe state.
    """

    normalized_paths = sorted({path for path in unsafe_paths if path})
    # Agent writes are applied to the live preview as they happen.  Rejecting
    # only the violating route would leave the rest of the same generation
    # visible without a snapshot (for example a page calling the deleted API).
    # Restore every touched path atomically from the last known-safe tree;
    # empty content is the orchestrator's explicit delete intent for new files.
    rollback_paths = sorted({path for path in files if path})
    rollback_files = {path: current_files.get(path, "") for path in rollback_paths}
    files.clear()
    files.update(rollback_files)
    rollback_failed = False
    if rollback_files:
        try:
            await _apply_project_cell_preview_files(
                project_id=project_id,
                project_slug=project_slug,
                files=rollback_files,
                project_cell_handle=project_cell_handle,
            )
        except Exception as exc:
            # Живой среды может не быть вовсе — ячейки засыпают после каждой
            # сборки. Тогда откатывать нечего: несуществующая среда не может
            # показывать небезопасный черновик, и говорить обратное — значит
            # пугать владельца ровно в том случае, когда бояться нечего.
            rollback_failed = not getattr(exc, "runtime_absent", False)
            logging.getLogger("yleum_api.routers.messages").warning(
                "MAX unsafe backend rollback hot_reload failed for project %s",
                project_id,
                exc_info=exc,
            )
    if violation_kind == "managed data isolation":
        message = (
            "MAX generation was stopped before publication because product code "
            "bypassed managed data isolation: " + ", ".join(normalized_paths)
        )
    else:
        message = (
            "MAX generation was stopped before publication because it violated the "
            f"{violation_kind}: " + ", ".join(normalized_paths)
        )
    if rollback_failed:
        message += (
            ". Live preview rollback also failed; the runtime may still show "
            "the unsafe draft until the next successful reload."
        )
    raise ApiError(
        "unsafe_generated_backend",
        message,
        status.HTTP_422_UNPROCESSABLE_CONTENT,
    )


def _resolve_max_shell_enabled(
    *,
    max_shell_requested: bool,
    sandbox_attested: bool,
    project_cell_handle: ProjectCellExecutorHandle | None,
) -> bool:
    return max_shell_requested and (sandbox_attested or project_cell_handle is not None)


def _split_project_cell_preview_patch(
    files: Mapping[str, str],
    *,
    empty_files: Sequence[str] = (),
) -> tuple[dict[str, str], tuple[str, ...], tuple[str, ...]]:
    payload = {path: content for path, content in files.items() if path}
    explicit_empty = tuple(
        sorted({path for path in empty_files if path in payload and payload[path] == ""})
    )
    writes: dict[str, str] = {}
    deletes: list[str] = []
    explicit_empty_set = set(explicit_empty)
    for path, content in payload.items():
        if content == "" and path not in explicit_empty_set:
            deletes.append(path)
            continue
        writes[path] = content
    return writes, tuple(sorted(set(deletes))), explicit_empty


class PreviewSyncFailed(RuntimeError):
    """Сверка живого превью не удалась — и отдельно: была ли живая среда.

    Без этого различия вызывающий код не отличит «не смогли привести среду в
    порядок» от «среды нет и приводить нечего», а для владельца разница
    принципиальная: в первом случае среда может отдавать небезопасный код, во
    втором отдавать попросту некому.
    """

    def __init__(self, message: str, *, runtime_absent: bool = False) -> None:
        super().__init__(message)
        self.runtime_absent = runtime_absent


async def _restore_project_cell_source(
    handle: ProjectCellExecutorHandle,
    baseline: Mapping[str, str],
) -> None:
    changed = await handle.export_files()
    writes = {path: baseline[path] for path in changed if path in baseline}
    deletes = tuple(path for path in changed if path not in baseline)
    await handle.stage_patch(writes, deletes)
    restored = await handle.sync_preview()
    if restored.failure is not None:
        raise PreviewSyncFailed(
            restored.failure,
            runtime_absent=getattr(restored, "runtime_absent", False),
        )


async def _apply_project_cell_preview_files(
    *,
    project_id: UUID,
    project_slug: str,
    files: Mapping[str, str],
    project_cell_handle: ProjectCellExecutorHandle | None = None,
    empty_files: Sequence[str] = (),
) -> None:
    writes, deletes, explicit_empty = _split_project_cell_preview_patch(
        files,
        empty_files=empty_files,
    )
    if not writes and not deletes:
        return
    if project_cell_handle is not None:
        await project_cell_handle.stage_patch(writes, deletes)
        sync_result = await project_cell_handle.sync_preview()
        if sync_result.failure is not None:
            raise PreviewSyncFailed(
                sync_result.failure,
                runtime_absent=getattr(sync_result, "runtime_absent", False),
            )
        return
    payload = dict(writes)
    payload.update({path: "" for path in deletes})
    await orchestrator_client.hot_reload(
        project_id,
        project_slug,
        payload,
        empty_files=explicit_empty,
    )


def _extract_max_shell_files(raw_files: Any) -> dict[str, str]:
    shell_files: dict[str, str] = {}
    if not isinstance(raw_files, dict):
        return shell_files
    for raw_path, raw_content in raw_files.items():
        if not isinstance(raw_path, str) or not isinstance(raw_content, str):
            continue
        normalized_path = raw_path.replace("\\", "/")
        while normalized_path.startswith("./"):
            normalized_path = normalized_path[2:]
        if normalized_path:
            shell_files[normalized_path] = raw_content
    return shell_files


async def _rollback_project_cell_shell_files(
    *,
    project_id: UUID,
    project_slug: str,
    snapshot_files: Mapping[str, str],
    touched_paths: Sequence[str],
    project_cell_handle: ProjectCellExecutorHandle,
) -> bool:
    normalized_paths = sorted({path for path in touched_paths if path})
    rollback_files: dict[str, str] = {}
    explicit_empty: list[str] = []
    for path in normalized_paths:
        if path in snapshot_files:
            rollback_files[path] = snapshot_files[path]
            if snapshot_files[path] == "":
                explicit_empty.append(path)
        else:
            rollback_files[path] = ""
    if not rollback_files:
        return False
    try:
        await _apply_project_cell_preview_files(
            project_id=project_id,
            project_slug=project_slug,
            files=rollback_files,
            project_cell_handle=project_cell_handle,
            empty_files=tuple(explicit_empty),
        )
    except Exception as exc:
        logging.getLogger("yleum_api.routers.messages").warning(
            "MAX Project Cell shell rollback failed for project %s",
            project_id,
            exc_info=exc,
        )
        return True
    return False


async def _run_max_shell_action(
    *,
    action: AgentBuilderAction,
    project_id: UUID,
    project_slug: str,
    max_shell_enabled: bool,
    base_agent_executor: Callable[[AgentBuilderAction], Awaitable[dict[str, Any]]],
    project_cell_handle: ProjectCellExecutorHandle | None,
    active_max_locked_files: frozenset[str],
    max_model_write_rejection: Callable[[str, str], str | None],
) -> dict[str, Any]:
    if not max_shell_enabled:
        return {
            "ok": False,
            "error": (
                "MAX project shell is locked: the operator disabled it "
                "or the isolated sandbox did not pass capability "
                "attestation. Until it is ready use "
                "read_file/edit_file/write_file/build."
            ),
        }
    cmd = str(action.args.get("cmd") or "").strip()
    if not cmd:
        return {"ok": False, "error": "bash needs a non-empty cmd string"}

    if project_cell_handle is not None:
        from yleum_api.services.max_generation_contract import (
            unsafe_max_backend_paths as _unsafe_max_backend_paths,
        )

        current_files = await project_cell_handle.snapshot_files()
        shell_result = await base_agent_executor(action)
        shell_files = _extract_max_shell_files(shell_result.get("files"))

        async def _reject(error: str) -> dict[str, Any]:
            rollback_failed = await _rollback_project_cell_shell_files(
                project_id=project_id,
                project_slug=project_slug,
                snapshot_files=current_files,
                touched_paths=tuple(shell_files),
                project_cell_handle=project_cell_handle,
            )
            if rollback_failed:
                error += (
                    " Project Cell rollback also failed; preview may still show "
                    "the rejected draft until the next successful sync."
                )
            return {"ok": False, "error": error}

        for path, content in shell_files.items():
            if path in active_max_locked_files:
                return await _reject(
                    f"{path} is managed by Yleum. "
                    "Shell changes must stay in product-owned files only."
                )
            if path.startswith("src/app/api/max/") or path.startswith("src/app/api/omnia/"):
                return await _reject(
                    "Yleum owns /api/max and /api/omnia. "
                    "Shell changes there are blocked; use the "
                    "managed integration client instead."
                )
            if content:
                secret_rejection = max_model_write_rejection(path, content)
                if secret_rejection:
                    return await _reject(secret_rejection)

        unsafe_shell_paths = _unsafe_max_backend_paths(shell_files)
        if unsafe_shell_paths:
            return await _reject(
                "Direct DB access is forbidden in MAX product "
                "files until row isolation is DB-enforced. "
                "Use createMaxAction/getMaxActions. Unsafe: " + ", ".join(unsafe_shell_paths)
            )

        cell_shell_sync = await project_cell_handle.sync_preview()
        result_files = dict(shell_files)
        result_files.update(cell_shell_sync.generated_files)
        if cell_shell_sync.failure is not None:
            return {
                "ok": False,
                "error": cell_shell_sync.failure,
                **({"files": result_files} if result_files else {}),
            }

        detail = str(shell_result.get("detail") or shell_result.get("error") or "(no output)")
        if result_files:
            listed = ", ".join(sorted(result_files)[:12])
            suffix = "…" if len(result_files) > 12 else ""
            detail += f"\n\nProject Cell synced files: {listed}{suffix}"
        return {
            "ok": bool(shell_result.get("ok")),
            "detail": detail,
            **({"files": result_files} if result_files else {}),
        }

    sandbox = await orchestrator_client.agent_exec_sandbox(project_id, project_slug, cmd)
    shell_files = _extract_max_shell_files(sandbox.get("files"))
    legacy_shell_sync: dict[str, Any] = {}
    for path, content in shell_files.items():
        if path in active_max_locked_files:
            return {
                "ok": False,
                "error": (
                    f"{path} is managed by Yleum. "
                    "Shell changes must stay in product-owned files only."
                ),
            }
        if path.startswith("src/app/api/max/") or path.startswith("src/app/api/omnia/"):
            return {
                "ok": False,
                "error": (
                    "Yleum owns /api/max and /api/omnia. "
                    "Shell changes there are blocked; use the "
                    "managed integration client instead."
                ),
            }
        if content:
            secret_rejection = max_model_write_rejection(path, content)
            if secret_rejection:
                return {"ok": False, "error": secret_rejection}
    if shell_files:
        from yleum_api.services.max_generation_contract import (
            unsafe_max_backend_paths as _unsafe_max_backend_paths,
        )

        unsafe_shell_paths = _unsafe_max_backend_paths(shell_files)
        if unsafe_shell_paths:
            return {
                "ok": False,
                "error": (
                    "Direct DB access is forbidden in MAX product "
                    "files until row isolation is DB-enforced. "
                    "Use createMaxAction/getMaxActions. Unsafe: " + ", ".join(unsafe_shell_paths)
                ),
            }
        base_revision = str(sandbox.get("base_workspace_revision") or "")
        if not base_revision:
            return {
                "ok": False,
                "error": (
                    "Sandbox did not return a workspace revision; "
                    "stale changes were not applied. Rerun the command."
                ),
            }
        try:
            legacy_shell_sync = await orchestrator_client.hot_reload(
                project_id,
                project_slug,
                shell_files,
                base_workspace_revision=base_revision,
            )
        except Exception:
            return {
                "ok": False,
                "error": (
                    "Project files changed while the sandbox command "
                    "was running; its stale diff was discarded. "
                    "Rerun the command on the current workspace."
                ),
            }
        resolved_lock = legacy_shell_sync.get("pnpm_lockfile")
        if isinstance(resolved_lock, str):
            shell_files["pnpm-lock.yaml"] = resolved_lock
        if project_cell_handle is not None:
            await project_cell_handle.apply_external_files(shell_files)
    detail = str(sandbox.get("detail") or "(no output)")
    if shell_files:
        listed = ", ".join(sorted(shell_files)[:12])
        suffix = "…" if len(shell_files) > 12 else ""
        detail += f"\n\nSandbox synced files: {listed}{suffix}"
    sync_failures = [
        f"{name}={legacy_shell_sync.get(name)}: "
        f"{legacy_shell_sync.get(name.replace('_exit_code', '_stderr_tail'), '')}"
        for name in ("package_exit_code", "drizzle_exit_code")
        if legacy_shell_sync.get(name) not in (None, "0", 0)
    ]
    if sync_failures:
        detail += "\n\nRuntime sync failed: " + "; ".join(sync_failures)
    return {
        "ok": bool(sandbox.get("ok")) and not sync_failures,
        "detail": detail,
        **({"files": shell_files} if shell_files else {}),
    }

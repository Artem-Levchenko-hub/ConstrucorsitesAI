from __future__ import annotations

import posixpath
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import PurePosixPath
from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from omnia_api.core.db import get_engine
from omnia_api.models.generation_run import GenerationRun
from omnia_api.models.project import Project
from omnia_api.models.project_cell import ProjectCellWorkspace
from omnia_api.models.user import User
from omnia_api.services.agent_builder import Action, Executor
from omnia_api.services.orchestrator_client import (
    HttpProjectCellOrchestratorClient,
    OrchestratorBadRequest,
    OrchestratorUnavailable,
    project_cell_agent_bootstrap,
    project_cell_agent_exec,
    project_cell_agent_write_files,
)
from omnia_api.services.project_cell_control import inspect_project_cell_control
from omnia_api.services.project_cell_lifecycle import execute_cell_operation
from omnia_api.services.project_cells import (
    ProjectCellBusy,
    ProjectCellNotFound,
    ProjectCellOwnershipError,
    ProjectCellStateConflict,
    ProjectCellValidationError,
    get_or_create_workspace,
    reserve_cell_operation,
)

_PROJECT_CELL_PROFILE_VERSION = "docker-owner-cell-resources-v1"
_PROJECT_CELL_BUILD_TIMEOUT_SECONDS = 600
_PROJECT_CELL_SHELL_TIMEOUT_SECONDS = 300
_PROJECT_CELL_BUILD_CMD = "\n".join(
    [
        "set -eu",
        "if [ ! -f package.json ]; then",
        "  echo 'package.json not found' >&2",
        "  exit 1",
        "fi",
        "if [ -f pnpm-lock.yaml ]; then",
        "  pnpm install --frozen-lockfile || pnpm install",
        "else",
        "  pnpm install",
        "fi",
        "if [ -f drizzle.config.ts ] && [ -f src/lib/db/schema.ts ]; then",
        "  pnpm db:push",
        "fi",
        "rm -rf -- .next/types/app .next/types/validator.ts || true",
        "if grep -q '\"typecheck\"' package.json; then",
        "  pnpm typecheck",
        "else",
        "  ./node_modules/.bin/tsc --noEmit -p ./tsconfig.json",
        "fi",
    ]
)
_MAX_READ_CHARS = 16_000
_MAX_GREP_MATCHES = 200
_MAX_GREP_CHARS = 16_000


class ProjectCellExecutorUnavailable(RuntimeError):
    """Selected owner-only Project Cell path could not be prepared safely."""


@dataclass(frozen=True, slots=True)
class ProjectCellExecutorHandle:
    execute: Executor
    sync_preview: Callable[[], Awaitable[ProjectCellPreviewSyncResult]]
    snapshot_files: Callable[[], Awaitable[dict[str, str]]]
    stage_files: Callable[[dict[str, str]], Awaitable[None]]
    apply_external_files: Callable[[dict[str, str]], Awaitable[None]]
    export_files: Callable[[], Awaitable[dict[str, str]]]
    workspace_id: UUID


@dataclass(frozen=True, slots=True)
class ProjectCellPreviewSyncResult:
    generated_files: dict[str, str]
    failure: str | None


async def maybe_create_project_cell_executor(
    *,
    project_id: UUID,
    project_slug: str,
    project_template: str,
    user_id: UUID,
    generation_run_id: UUID,
    legacy_execute: Executor,
) -> ProjectCellExecutorHandle | None:
    if project_template != "max_miniapp" or not project_slug:
        return None

    session_factory = async_sessionmaker(get_engine(), expire_on_commit=False)
    async with session_factory() as session:
        project = await session.get(Project, project_id)
        user = await session.get(User, user_id)
        run = await session.get(GenerationRun, generation_run_id)
        if project is None or user is None or run is None:
            return None
        readiness = await inspect_project_cell_control(user, project_id)
        if not readiness.selected:
            return None
        if not readiness.ready:
            raise ProjectCellExecutorUnavailable(
                f"Project Cell selected but not ready: {readiness.reason}"
            )
        try:
            workspace, _ = await get_or_create_workspace(
                session,
                project=project,
                user=user,
                run=run,
            )
            workspace.generation_run_id = run.id
            operation, _ = await reserve_cell_operation(
                session,
                workspace_id=workspace.id,
                generation_run_id=run.id,
                kind="ensure",
                idempotency_key=f"generation:{run.id}:ensure:{_PROJECT_CELL_PROFILE_VERSION}",
                request={"profile_version": _PROJECT_CELL_PROFILE_VERSION},
            )
            await session.commit()
        except (
            ProjectCellBusy,
            ProjectCellNotFound,
            ProjectCellOwnershipError,
            ProjectCellStateConflict,
            ProjectCellValidationError,
        ) as exc:
            raise ProjectCellExecutorUnavailable(str(exc)) from exc
        workspace_id = workspace.id

    outcome = await execute_cell_operation(
        session_factory,
        operation.id,
        HttpProjectCellOrchestratorClient(),
    )
    response = outcome.response
    if (
        outcome.status != "completed"
        or response is None
        or response.workspace_id != workspace_id
        or response.state != "resources_ready"
        or response.provider_ref is None
        or response.fencing_epoch is None
        or not all(
            (
                response.has_workspace,
                response.has_agent_home,
                response.has_postgres,
                response.has_redis,
            )
        )
    ):
        raise ProjectCellExecutorUnavailable(
            f"Project Cell ensure failed: {outcome.status}"
        )
    try:
        snapshot = await project_cell_agent_bootstrap(
            workspace_id,
            generation_run_id=generation_run_id,
            fencing_epoch=response.fencing_epoch,
        )
    except OrchestratorUnavailable as exc:
        raise ProjectCellExecutorUnavailable(exc.message) from exc
    await _mark_workspace_ready(
        session_factory=session_factory,
        workspace_id=workspace_id,
        generation_run_id=generation_run_id,
        provider_ref=response.provider_ref,
    )

    workspace_files = {
        _normalize_path(path): content
        for path, content in snapshot.files.items()
    }
    if snapshot.generation_run_id != generation_run_id:
        raise ProjectCellExecutorUnavailable("Project Cell active lease does not match the run")
    leased_run_id = snapshot.generation_run_id
    fencing_epoch = snapshot.fencing_epoch
    workspace_revision = snapshot.workspace_revision
    baseline_files = dict(workspace_files)
    synced_files = dict(workspace_files)
    dirty = False

    async def _persist_files(
        *,
        writes: dict[str, str] | None = None,
        deletes: tuple[str, ...] = (),
    ) -> None:
        nonlocal workspace_revision
        normalized_writes = _normalize_files(writes or {})
        normalized_deletes = _normalize_delete_paths(deletes)
        if not normalized_writes and not normalized_deletes:
            return
        response = await project_cell_agent_write_files(
            workspace_id,
            generation_run_id=leased_run_id,
            fencing_epoch=fencing_epoch,
            expected_revision=workspace_revision,
            files=normalized_writes,
            deletes=normalized_deletes,
        )
        workspace_revision = response.workspace_revision

    async def _apply_external_files(files: dict[str, str]) -> None:
        nonlocal synced_files, dirty
        await _stage_files(files)
        synced_files = dict(workspace_files)
        dirty = False

    async def _stage_files(files: dict[str, str]) -> None:
        nonlocal dirty
        normalized = _normalize_files(files)
        writes, deletes = _split_external_files(normalized)
        await _persist_files(writes=writes, deletes=deletes)
        _apply_to_local_state(workspace_files, writes=writes, deletes=deletes)
        dirty = bool(_diff_files(synced_files, workspace_files))

    async def _refresh_workspace_from_cell() -> dict[str, str]:
        nonlocal dirty, fencing_epoch, workspace_revision
        refreshed = await project_cell_agent_bootstrap(
            workspace_id,
            generation_run_id=leased_run_id,
            fencing_epoch=fencing_epoch,
        )
        if refreshed.generation_run_id != leased_run_id:
            raise ProjectCellExecutorUnavailable("Project Cell active lease changed mid-run")
        if refreshed.fencing_epoch != fencing_epoch:
            raise ProjectCellExecutorUnavailable("Project Cell fencing epoch changed mid-run")
        normalized = _normalize_files(refreshed.files)
        changed = _diff_files(workspace_files, normalized)
        workspace_files.clear()
        workspace_files.update(normalized)
        fencing_epoch = refreshed.fencing_epoch
        workspace_revision = refreshed.workspace_revision
        dirty = bool(_diff_files(synced_files, workspace_files))
        return changed

    async def _export_files() -> dict[str, str]:
        return _diff_files(baseline_files, workspace_files)

    async def _snapshot_files() -> dict[str, str]:
        return dict(workspace_files)

    async def _sync_preview() -> ProjectCellPreviewSyncResult:
        nonlocal synced_files, dirty
        if not dirty:
            return ProjectCellPreviewSyncResult(generated_files={}, failure=None)
        diff = _diff_files(synced_files, workspace_files)
        if not diff:
            dirty = False
            synced_files = dict(workspace_files)
            return ProjectCellPreviewSyncResult(generated_files={}, failure=None)
        hot = await _hot_reload(project_id, project_slug, diff)
        generated_files: dict[str, str] = {}
        lockfile = hot.get("pnpm_lockfile")
        if isinstance(lockfile, str) and workspace_files.get("pnpm-lock.yaml") != lockfile:
            generated_files["pnpm-lock.yaml"] = lockfile
            await _persist_files(writes={"pnpm-lock.yaml": lockfile})
            _apply_to_local_state(
                workspace_files,
                writes={"pnpm-lock.yaml": lockfile},
            )
        failure = _sync_failure_detail(hot)
        if failure is not None:
            return ProjectCellPreviewSyncResult(
                generated_files=generated_files,
                failure=failure,
            )
        synced_files = dict(workspace_files)
        dirty = False
        return ProjectCellPreviewSyncResult(
            generated_files=generated_files,
            failure=None,
        )

    async def _execute(action: Action) -> dict[str, Any]:
        nonlocal dirty, workspace_revision
        try:
            if action.name == "list_dir":
                return {
                    "ok": True,
                    "detail": _list_dir(workspace_files, action.path or "."),
                }
            if action.name == "read_file":
                path = _normalize_path(action.path)
                content = workspace_files.get(path)
                if content is None:
                    return {"ok": False, "error": f"not found: {path}"}
                return {"ok": True, "content": _truncate(content, _MAX_READ_CHARS)}
            if action.name == "grep":
                pattern = str(action.args.get("pattern", ""))
                path = str(action.path or "src")
                return {"ok": True, "detail": _grep(workspace_files, pattern=pattern, path=path)}
            if action.name == "write_file":
                content = action.args.get("content")
                if not isinstance(content, str) or not action.path:
                    return {"ok": False, "error": "write_file needs path + content"}
                path = _normalize_path(action.path)
                await _persist_files(writes={path: content})
                _apply_to_local_state(workspace_files, writes={path: content})
                dirty = True
                return {
                    "ok": True,
                    "content": content,
                    "detail": f"wrote {path} ({len(content)} bytes)",
                }
            if action.name == "edit_file":
                search = action.args.get("search")
                replace = action.args.get("replace")
                if not action.path or not isinstance(search, str) or replace is None:
                    return {"ok": False, "error": "edit_file needs path, search, replace"}
                path = _normalize_path(action.path)
                current = workspace_files.get(path)
                if current is None:
                    return {"ok": False, "error": f"not found: {path}"}
                if search not in current:
                    return {
                        "ok": False,
                        "error": (
                            "search text not found exactly; read the file and copy it "
                            "byte-for-byte"
                        ),
                    }
                if current.count(search) > 1:
                    return {
                        "ok": False,
                        "error": "search text is not unique; add surrounding lines",
                    }
                new_content = current.replace(search, str(replace), 1)
                await _persist_files(writes={path: new_content})
                _apply_to_local_state(workspace_files, writes={path: new_content})
                dirty = True
                return {
                    "ok": True,
                    "content": new_content,
                    "detail": f"patched {path}",
                }
            if action.name == "build":
                result = await project_cell_agent_exec(
                    workspace_id,
                    _PROJECT_CELL_BUILD_CMD,
                    generation_run_id=leased_run_id,
                    fencing_epoch=fencing_epoch,
                    expected_revision=workspace_revision,
                    timeout_seconds=_PROJECT_CELL_BUILD_TIMEOUT_SECONDS,
                )
                workspace_revision = result.workspace_revision
                await _refresh_workspace_from_cell()
                detail = result.detail
                return {
                    "ok": result.ok,
                    "detail": detail or ("ok" if result.ok else "non-zero exit"),
                }
            if action.name == "bash":
                cmd = str(action.args.get("cmd") or "").strip()
                if not cmd:
                    return {"ok": False, "error": "bash needs a non-empty cmd string"}
                result = await project_cell_agent_exec(
                    workspace_id,
                    cmd,
                    generation_run_id=leased_run_id,
                    fencing_epoch=fencing_epoch,
                    expected_revision=workspace_revision,
                    timeout_seconds=_PROJECT_CELL_SHELL_TIMEOUT_SECONDS,
                )
                workspace_revision = result.workspace_revision
                changed_files = await _refresh_workspace_from_cell()
                detail = result.detail or "(no output)"
                if result.timed_out:
                    detail += "\n\nCommand timed out; partial workspace changes were synced back."
                response: dict[str, Any] = {
                    "ok": result.ok,
                    "detail": detail,
                }
                if changed_files:
                    response["files"] = changed_files
                return response
            if action.name in {
                "read_logs",
                "runtime_check",
                "see",
                "probe",
                "verify_isolation",
            }:
                sync_result = await _sync_preview()
                if sync_result.failure is not None:
                    return {
                        "ok": False,
                        "error": sync_result.failure,
                        **(
                            {"files": sync_result.generated_files}
                            if sync_result.generated_files
                            else {}
                        ),
                    }
                legacy_result = await legacy_execute(action)
                if sync_result.generated_files:
                    merged = dict(legacy_result.get("files") or {})
                    merged.update(sync_result.generated_files)
                    legacy_result["files"] = merged
                return legacy_result
            return await legacy_execute(action)
        except OrchestratorUnavailable as exc:
            return {
                "ok": False,
                "error": f"infra: {exc.message}",
                "infra_dead": True,
            }
        except OrchestratorBadRequest as exc:
            infra_dead = "container_not_running" in str(exc.details or "")
            return {
                "ok": False,
                "error": f"{'infra: ' if infra_dead else ''}{exc.message}",
                **({"infra_dead": True} if infra_dead else {}),
            }
        except Exception as exc:
            return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}

    return ProjectCellExecutorHandle(
        execute=_execute,
        sync_preview=_sync_preview,
        snapshot_files=_snapshot_files,
        stage_files=_stage_files,
        apply_external_files=_apply_external_files,
        export_files=_export_files,
        workspace_id=workspace_id,
    )


async def _mark_workspace_ready(
    *,
    session_factory: async_sessionmaker[AsyncSession],
    workspace_id: UUID,
    generation_run_id: UUID,
    provider_ref: str | None,
) -> None:
    async with session_factory() as session:
        workspace = await session.get(ProjectCellWorkspace, workspace_id)
        if workspace is None:
            raise ProjectCellExecutorUnavailable("Project Cell workspace disappeared")
        workspace.state = "ready"
        workspace.provider_ref = provider_ref
        workspace.generation_run_id = generation_run_id
        workspace.ready_at = datetime.now(UTC)
        workspace.last_error = None
        await session.commit()


async def _hot_reload(
    project_id: UUID,
    slug: str,
    files: dict[str, str],
) -> dict[str, Any]:
    from omnia_api.services import orchestrator_client

    return await orchestrator_client.hot_reload(
        project_id=project_id,
        slug=slug,
        files=files,
    )


def _apply_to_local_state(
    state: dict[str, str],
    *,
    writes: dict[str, str],
    deletes: tuple[str, ...] = (),
) -> None:
    for path, content in writes.items():
        state[path] = content
    for path in deletes:
        state.pop(path, None)


def _normalize_files(files: dict[str, str]) -> dict[str, str]:
    return {_normalize_path(path): content for path, content in files.items()}


def _normalize_delete_paths(paths: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(sorted({_normalize_path(path) for path in paths}))


def _split_external_files(files: dict[str, str]) -> tuple[dict[str, str], tuple[str, ...]]:
    writes: dict[str, str] = {}
    deletes: list[str] = []
    for path, content in files.items():
        if content == "":
            deletes.append(path)
            continue
        writes[path] = content
    return writes, tuple(sorted(set(deletes)))


def _normalize_path(path: str) -> str:
    candidate = PurePosixPath(str(path).replace("\\", "/"))
    normalized = posixpath.normpath(candidate.as_posix()).lstrip("/")
    normalized_path = PurePosixPath(normalized)
    if (
        normalized in {"", "."}
        or candidate.is_absolute()
        or normalized_path.is_absolute()
        or ".." in normalized_path.parts
    ):
        raise ValueError(f"unsafe path: {path!r}")
    return normalized_path.as_posix()


def _truncate(value: str, limit: int) -> str:
    if len(value) <= limit:
        return value
    return value[:limit] + f"\n…[truncated {len(value) - limit} chars]"


def _list_dir(files: dict[str, str], path: str) -> str:
    prefix = _normalize_dir_prefix(path)
    entries: set[str] = set()
    for file_path in files:
        if prefix:
            if not file_path.startswith(prefix):
                continue
            remainder = file_path[len(prefix) :]
        else:
            remainder = file_path
        if not remainder:
            continue
        head, _sep, tail = remainder.partition("/")
        if not head:
            continue
        entries.add(f"{head}/" if tail else head)
    return "\n".join(sorted(entries)) or "(empty)"


def _grep(files: dict[str, str], *, pattern: str, path: str) -> str:
    prefix = _normalize_dir_prefix(path)
    if not pattern:
        return "(empty pattern)"
    try:
        matcher = re.compile(pattern)
    except re.error:
        matcher = re.compile(re.escape(pattern))
    lines: list[str] = []
    total_chars = 0
    for file_path, content in sorted(files.items()):
        if prefix and not file_path.startswith(prefix):
            continue
        for line_no, line in enumerate(content.splitlines(), start=1):
            if matcher.search(line) is None:
                continue
            entry = f"{file_path}:{line_no}:{line}"
            lines.append(entry)
            total_chars += len(entry) + 1
            if len(lines) >= _MAX_GREP_MATCHES or total_chars >= _MAX_GREP_CHARS:
                return "\n".join(lines)
    return "\n".join(lines) if lines else "(no matches)"


def _normalize_dir_prefix(path: str) -> str:
    if path in {"", "."}:
        return ""
    normalized = _normalize_path(path)
    return normalized.rstrip("/") + "/"


def _diff_files(before: dict[str, str], after: dict[str, str]) -> dict[str, str]:
    changed = {path: content for path, content in after.items() if before.get(path) != content}
    for path in before.keys() - after.keys():
        changed[path] = ""
    return changed


def _sync_failure_detail(hot: dict[str, Any]) -> str | None:
    failures = [
        (
            name,
            hot.get(name),
            hot.get(name.replace("_exit_code", "_stderr_tail"), ""),
        )
        for name in ("package_exit_code", "drizzle_exit_code")
        if hot.get(name) not in (None, "0", 0, "n/a")
    ]
    if not failures:
        return None
    detail = "; ".join(f"{name}={code}: {stderr}" for name, code, stderr in failures)
    return f"runtime apply failed during Project Cell sync: {detail}"

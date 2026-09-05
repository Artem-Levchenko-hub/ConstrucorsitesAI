"""No-model live acceptance for MAX Project Cell capacity and profile wiring.

The script is intentionally inert without ``--execute``.  Run it inside the
production API container, where it reuses the configured application database
and orchestrator.  It keeps its disposable project/run rows as durable evidence;
it never deletes or changes an existing project or the owner allowlist.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
import time
from collections.abc import Awaitable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, NoReturn
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from omnia_api.core.config import get_settings
from omnia_api.core.db import dispose_engine, get_engine
from omnia_api.models.generation_run import GenerationRun
from omnia_api.models.project import Project
from omnia_api.models.project_cell import ProjectCellOperation, ProjectCellWorkspace
from omnia_api.models.user import User
from omnia_api.services.agent_builder import Action
from omnia_api.services.orchestrator_client import HttpProjectCellOrchestratorClient
from omnia_api.services.project_cell_access import decide_project_cell_access
from omnia_api.services.project_cell_capacity import (
    claim_idle_hibernation_victim,
    hibernate_one_idle_workspace,
    release_one_stale_generation_lease,
)
from omnia_api.services.project_cell_executor import (
    ProjectCellExecutorHandle,
    maybe_create_project_cell_executor,
)
from omnia_api.services.project_cell_lifecycle import execute_cell_operation
from omnia_api.services.project_cells import reserve_cell_operation

_EVIDENCE_KEY = "generation_capacity_acceptance"
_LABEL_PREFIX = "capacity-acceptance"
_PORTABLE_MARKER = "omnia-capacity-portable-ok"
_BASELINE_MARKER = "omnia-capacity-baseline-ok"
_SAFE_REASON = re.compile(r"^[a-z0-9_]{1,64}$")
_TERMINAL = frozenset({"completed", "failed"})


class AcceptanceFailure(RuntimeError):
    """Stable, non-secret acceptance failure code."""


@dataclass(slots=True)
class AcceptanceContext:
    label: str
    stage: str = "guard"
    owner_id: UUID | None = None
    project_id: UUID | None = None
    run_id: UUID | None = None
    requester_project_id: UUID | None = None
    requester_run_id: UUID | None = None
    workspace_id: UUID | None = None
    probe_id: UUID | None = None
    pause_operation_id: UUID | None = None
    observe_operation_id: UUID | None = None
    portable: bool | None = None
    profile_version: str | None = None
    released: bool = False
    progress: list[dict[str, object]] = field(default_factory=list)
    handle: ProjectCellExecutorHandle | None = None

    def ids(self) -> dict[str, UUID]:
        values = {
            "project_id": self.project_id,
            "run_id": self.run_id,
            "requester_project_id": self.requester_project_id,
            "requester_run_id": self.requester_run_id,
            "workspace_id": self.workspace_id,
            "probe_id": self.probe_id,
            "pause_operation_id": self.pause_operation_id,
            "observe_operation_id": self.observe_operation_id,
        }
        return {key: value for key, value in values.items() if value is not None}


def _emit(
    step: str,
    status: str,
    *,
    duration_ms: int | None = None,
    ids: dict[str, UUID] | None = None,
) -> None:
    event: dict[str, object] = {"step": step, "status": status}
    if duration_ms is not None:
        event["duration_ms"] = max(0, duration_ms)
    for key, value in (ids or {}).items():
        event[key] = str(value)
    print(json.dumps(event, separators=(",", ":"), sort_keys=True), flush=True)


def _sanitize_progress(payload: dict[str, object]) -> dict[str, object]:
    status = payload.get("status")
    queue_position = payload.get("queue_position")
    capacity_reason = payload.get("capacity_reason")
    return {
        "status": status if status in {"running", "waiting"} else "running",
        "queue_position": (
            queue_position if type(queue_position) is int and 0 <= queue_position <= 100_000 else 0
        ),
        "capacity_reason": (
            capacity_reason
            if isinstance(capacity_reason, str) and _SAFE_REASON.fullmatch(capacity_reason)
            else None
        ),
    }


def _fail(code: str) -> NoReturn:
    raise AcceptanceFailure(code)


def _require(condition: bool, code: str) -> None:
    if not condition:
        _fail(code)


def _portable_probe_source(probe_id: UUID) -> str:
    return f'''import pg from "pg";

const probeId = "{probe_id}";
const client = new pg.Client({{ connectionString: process.env.DATABASE_URL }});
await client.connect();
try {{
  await client.query(`
    CREATE TABLE IF NOT EXISTS omnia_capacity_acceptance_evidence (
      probe_id uuid PRIMARY KEY,
      state text NOT NULL,
      created_at timestamptz NOT NULL DEFAULT now(),
      updated_at timestamptz NOT NULL DEFAULT now()
    )
  `);
  await client.query(
    `INSERT INTO omnia_capacity_acceptance_evidence (probe_id, state)
     VALUES ($1, $2)
     ON CONFLICT (probe_id) DO UPDATE SET state = EXCLUDED.state, updated_at = now()`,
    [probeId, "created"],
  );
  const updated = await client.query(
    `UPDATE omnia_capacity_acceptance_evidence
     SET state = $2, updated_at = now()
     WHERE probe_id = $1 RETURNING state`,
    [probeId, "updated"],
  );
  if (updated.rowCount !== 1 || updated.rows[0]?.state !== "updated") process.exit(42);
  const selected = await client.query(
    `SELECT state FROM omnia_capacity_acceptance_evidence WHERE probe_id = $1`,
    [probeId],
  );
  if (selected.rows.length !== 1 || selected.rows[0]?.state !== "updated") process.exit(43);
  process.stdout.write("{_PORTABLE_MARKER}\\n");
}} finally {{
  await client.end();
}}
'''


async def _legacy_execute(_action: Action) -> dict[str, Any]:
    return {"ok": False, "error": "legacy action is outside this acceptance"}


async def _choose_owner(session: AsyncSession) -> User:
    settings = get_settings()
    candidates = list(
        (
            await session.execute(
                select(User)
                .where(
                    User.status == "active",
                    User.is_anon.is_(False),
                    User.email.is_not(None),
                    User.email_verified_at.is_not(None),
                )
                .order_by(User.created_at, User.id)
            )
        )
        .scalars()
        .all()
    )
    for user in candidates:
        decision = decide_project_cell_access(user, settings)
        if decision.enabled and decision.provider == "docker_owner_canary":
            return user
    _fail("eligible_owner_missing")


async def _create_evidence_records(
    session_factory: async_sessionmaker[AsyncSession],
    context: AcceptanceContext,
) -> None:
    async with session_factory() as session:
        owner = await _choose_owner(session)
        token = context.label.removeprefix(f"{_LABEL_PREFIX}-")
        project = Project(
            owner_id=owner.id,
            name=f"Capacity acceptance {token}",
            slug=context.label,
            template="max_miniapp",
            language="ru",
        )
        requester_project = Project(
            owner_id=owner.id,
            name=f"Capacity acceptance requester {token}",
            slug=f"{context.label}-requester",
            template="max_miniapp",
            language="ru",
        )
        session.add_all((project, requester_project))
        await session.flush()
        now = datetime.now(UTC)
        run = GenerationRun(
            project_id=project.id,
            user_id=owner.id,
            idempotency_key=f"{context.label}-run",
            prompt_hash="0" * 64,
            status="pending",
            agent_state={},
        )
        requester_run = GenerationRun(
            project_id=requester_project.id,
            user_id=owner.id,
            idempotency_key=f"{context.label}-requester-run",
            prompt_hash="0" * 64,
            status="running",
            started_at=now,
            agent_state={},
        )
        session.add_all((run, requester_run))
        await session.flush()
        context.owner_id = owner.id
        context.project_id = project.id
        context.run_id = run.id
        context.requester_project_id = requester_project.id
        context.requester_run_id = requester_run.id
        await _write_evidence(session, context, status="running")
        await session.commit()


def _evidence(context: AcceptanceContext, *, status: str) -> dict[str, object]:
    return {
        "schema_version": 1,
        "kind": "no_model_resource_acceptance",
        "label": context.label,
        "stage": context.stage,
        "status": status,
        "workspace_id": str(context.workspace_id) if context.workspace_id else None,
        "probe_id": str(context.probe_id) if context.probe_id else None,
        "pause_operation_id": (
            str(context.pause_operation_id) if context.pause_operation_id else None
        ),
        "observe_operation_id": (
            str(context.observe_operation_id) if context.observe_operation_id else None
        ),
        "portable": context.portable,
        "profile_version": context.profile_version,
        "capacity_progress": list(context.progress),
        "updated_at": datetime.now(UTC).isoformat(),
    }


async def _write_evidence(
    session: AsyncSession,
    context: AcceptanceContext,
    *,
    status: str,
) -> None:
    for run_id in (context.run_id, context.requester_run_id):
        if run_id is None:
            continue
        run = await session.get(GenerationRun, run_id)
        if run is None:
            continue
        state = dict(run.agent_state or {})
        state[_EVIDENCE_KEY] = _evidence(context, status=status)
        run.agent_state = state


async def _persist_evidence(
    session_factory: async_sessionmaker[AsyncSession],
    context: AcceptanceContext,
    *,
    status: str = "running",
) -> None:
    async with session_factory() as session:
        await _write_evidence(session, context, status=status)
        await session.commit()


async def _terminalize_runs(
    session_factory: async_sessionmaker[AsyncSession],
    context: AcceptanceContext,
    *,
    status: str,
) -> None:
    _require(status in _TERMINAL, "invalid_terminal_status")
    now = datetime.now(UTC)
    async with session_factory() as session:
        for run_id in (context.run_id, context.requester_run_id):
            if run_id is None:
                continue
            run = await session.get(GenerationRun, run_id)
            if run is None:
                continue
            run.status = status
            run.started_at = run.started_at or now
            run.finished_at = now
            run.error = None if status == "completed" else f"acceptance_failed:{context.stage}"
            run.response_payload = {
                "kind": "no_model_resource_acceptance",
                "status": status,
            }
        await _write_evidence(session, context, status=status)
        await session.commit()


async def _run_action(
    handle: ProjectCellExecutorHandle,
    action: Action,
    *,
    code: str,
) -> dict[str, Any]:
    result = await handle.execute(action)
    _require(result.get("ok") is True, code)
    return result


async def _verify_workspace_leased(
    session_factory: async_sessionmaker[AsyncSession],
    context: AcceptanceContext,
) -> None:
    assert context.workspace_id is not None
    assert context.project_id is not None
    assert context.run_id is not None
    async with session_factory() as session:
        workspace = await session.get(ProjectCellWorkspace, context.workspace_id)
        _require(workspace is not None, "workspace_missing_after_bootstrap")
        assert workspace is not None
        _require(workspace.project_id == context.project_id, "workspace_project_mismatch")
        _require(workspace.owner_id == context.owner_id, "workspace_owner_mismatch")
        _require(workspace.state == "ready", "workspace_not_ready_after_bootstrap")
        _require(workspace.generation_run_id == context.run_id, "workspace_lease_mismatch")
        _require(workspace.provider_ref is not None, "workspace_provider_ref_missing")
        profile = (workspace.provider_metadata or {}).get("profile_version")
        _require(isinstance(profile, str), "workspace_profile_missing")
        assert isinstance(profile, str)
        context.profile_version = profile
        expected_profile = (
            "docker-owner-cell-resources-v2"
            if get_settings().use_cell_resource_profile_v2
            else "docker-owner-cell-resources-v1"
        )
        _require(profile == expected_profile, "workspace_profile_setting_mismatch")
        ensure = await session.scalar(
            select(ProjectCellOperation)
            .where(
                ProjectCellOperation.workspace_id == context.workspace_id,
                ProjectCellOperation.generation_run_id == context.run_id,
                ProjectCellOperation.kind == "ensure",
            )
            .order_by(ProjectCellOperation.created_at.desc())
            .limit(1)
        )
        _require(ensure is not None, "ensure_operation_missing")
        assert ensure is not None
        _require(ensure.status == "completed", "ensure_operation_not_completed")
        result = ensure.result_payload or {}
        _require(result.get("state") == "resources_ready", "ensure_controller_not_ready")
        _require(result.get("workspace_id") == str(context.workspace_id), "ensure_id_mismatch")
        _require(result.get("has_workspace") is True, "ensure_workspace_resource_missing")
        _require(result.get("has_agent_home") is True, "ensure_agent_home_missing")
        _require(result.get("has_postgres") is True, "ensure_postgres_missing")
        _require(result.get("has_redis") is True, "ensure_redis_missing")


async def _verify_release(
    session_factory: async_sessionmaker[AsyncSession],
    context: AcceptanceContext,
) -> None:
    assert context.workspace_id is not None
    assert context.run_id is not None
    async with session_factory() as session:
        workspace = await session.get(ProjectCellWorkspace, context.workspace_id)
        _require(workspace is not None, "workspace_missing_after_release")
        assert workspace is not None
        _require(workspace.state == "ready", "workspace_not_ready_after_release")
        _require(workspace.generation_run_id is None, "workspace_lease_not_released")
        release = await session.scalar(
            select(ProjectCellOperation)
            .where(
                ProjectCellOperation.workspace_id == context.workspace_id,
                ProjectCellOperation.generation_run_id == context.run_id,
                ProjectCellOperation.kind == "release",
            )
            .order_by(ProjectCellOperation.created_at.desc())
            .limit(1)
        )
        _require(release is not None, "release_operation_missing")
        assert release is not None
        _require(release.status == "completed", "release_operation_not_completed")
        result = release.result_payload or {}
        _require(result.get("state") == "resources_ready", "release_controller_not_ready")
        _require(result.get("workspace_id") == str(context.workspace_id), "release_id_mismatch")


async def _prepare_hibernation(
    session_factory: async_sessionmaker[AsyncSession],
    context: AcceptanceContext,
) -> None:
    assert context.workspace_id is not None
    assert context.requester_run_id is not None
    async with session_factory() as session:
        victim = await claim_idle_hibernation_victim(
            session,
            requesting_run_id=context.requester_run_id,
            expected_workspace_id=context.workspace_id,
        )
        victim_id = victim.id if victim is not None else None
        await session.rollback()
        _require(victim_id == context.workspace_id, "disposable_workspace_not_fifo_victim")


async def _verify_hibernation(
    session_factory: async_sessionmaker[AsyncSession],
    context: AcceptanceContext,
) -> None:
    assert context.workspace_id is not None
    assert context.requester_run_id is not None
    checkpoint_ref = f"capacity-{context.requester_run_id.hex[:12]}"
    idempotency_base = f"capacity:{context.requester_run_id}:pause:{context.workspace_id}"
    async with session_factory() as session:
        pause = await session.scalar(
            select(ProjectCellOperation)
            .where(
                ProjectCellOperation.workspace_id == context.workspace_id,
                ProjectCellOperation.generation_run_id.is_(None),
                ProjectCellOperation.kind == "pause",
                ProjectCellOperation.idempotency_key.like(f"{idempotency_base}%"),
            )
            .order_by(ProjectCellOperation.created_at.desc())
            .limit(1)
        )
        _require(pause is not None, "capacity_pause_operation_missing")
        assert pause is not None
        context.pause_operation_id = pause.id
        _require(pause.status == "completed", "capacity_pause_not_completed")
        result = pause.result_payload or {}
        _require(result.get("state") == "resources_paused", "capacity_controller_not_paused")
        _require(result.get("checkpoint_ref") == checkpoint_ref, "capacity_checkpoint_mismatch")
        _require(result.get("workspace_id") == str(context.workspace_id), "capacity_id_mismatch")
        workspace = await session.get(ProjectCellWorkspace, context.workspace_id)
        _require(workspace is not None, "workspace_missing_after_hibernation")
        assert workspace is not None
        _require(workspace.state == "stopped", "workspace_not_stopped")
        _require(workspace.generation_run_id is None, "workspace_released_state_lost")
        _require(workspace.fencing_epoch == pause.fencing_epoch, "capacity_fence_mismatch")


async def _observe_paused_controller(
    session_factory: async_sessionmaker[AsyncSession],
    context: AcceptanceContext,
) -> None:
    assert context.workspace_id is not None
    async with session_factory() as session:
        operation, _ = await reserve_cell_operation(
            session,
            workspace_id=context.workspace_id,
            generation_run_id=None,
            kind="status",
            idempotency_key=f"{context.label}:observe-paused",
            request={},
        )
        context.observe_operation_id = operation.id
        await session.commit()
    outcome = await execute_cell_operation(
        session_factory,
        context.observe_operation_id,
        HttpProjectCellOrchestratorClient(),
    )
    response = outcome.response
    _require(outcome.status == "completed", "paused_observe_not_completed")
    _require(response is not None, "paused_observe_response_missing")
    assert response is not None
    _require(response.workspace_id == context.workspace_id, "paused_observe_id_mismatch")
    _require(response.state == "resources_paused", "paused_observe_state_mismatch")
    _require(response.fencing_epoch is not None, "paused_observe_fence_missing")
    async with session_factory() as session:
        workspace = await session.get(ProjectCellWorkspace, context.workspace_id)
        _require(workspace is not None, "workspace_missing_after_observe")
        assert workspace is not None
        _require(workspace.state == "stopped", "workspace_state_changed_by_observe")
        _require(workspace.generation_run_id is None, "workspace_lease_changed_by_observe")
        _require(workspace.fencing_epoch == response.fencing_epoch, "paused_observe_fence_mismatch")


async def _cleanup_failed(
    session_factory: async_sessionmaker[AsyncSession],
    context: AcceptanceContext,
) -> None:
    try:
        await _terminalize_runs(session_factory, context, status="failed")
    except Exception:
        pass
    if context.workspace_id is None and context.project_id is not None:
        try:
            async with session_factory() as session:
                context.workspace_id = await session.scalar(
                    select(ProjectCellWorkspace.id).where(
                        ProjectCellWorkspace.project_id == context.project_id
                    )
                )
        except Exception:
            pass
    if context.handle is not None and not context.released:
        try:
            await context.handle.release()
            context.released = True
        except Exception:
            pass
    if context.workspace_id is not None and context.requester_run_id is not None:
        try:
            for _attempt in range(3):
                released = await release_one_stale_generation_lease(
                    session_factory,
                    requesting_run_id=context.requester_run_id,
                    client=HttpProjectCellOrchestratorClient(),
                    workspace_id=context.workspace_id,
                )
                if released:
                    break
        except Exception:
            pass


async def _timed(
    context: AcceptanceContext,
    step: str,
    operation: Awaitable[None],
) -> None:
    context.stage = step
    started = time.monotonic()
    _emit(step, "running", ids=context.ids())
    try:
        await operation
    except Exception:
        _emit(
            step,
            "failed",
            duration_ms=round((time.monotonic() - started) * 1000),
            ids=context.ids(),
        )
        raise
    _emit(
        step,
        "ok",
        duration_ms=round((time.monotonic() - started) * 1000),
        ids=context.ids(),
    )


async def _execute_acceptance() -> int:
    context = AcceptanceContext(label=f"{_LABEL_PREFIX}-{uuid4().hex}")
    session_factory = async_sessionmaker(get_engine(), expire_on_commit=False)
    try:
        await _timed(
            context,
            "create_disposable_evidence",
            _create_evidence_records(session_factory, context),
        )

        async def collect_progress(payload: dict[str, object]) -> None:
            context.progress.append(_sanitize_progress(payload))
            await _persist_evidence(session_factory, context)
            _emit("capacity_wait", "running", ids=context.ids())

        async def create_executor() -> None:
            assert context.project_id is not None
            assert context.run_id is not None
            assert context.owner_id is not None
            handle = await maybe_create_project_cell_executor(
                project_id=context.project_id,
                project_slug=context.label,
                project_template="max_miniapp",
                user_id=context.owner_id,
                generation_run_id=context.run_id,
                legacy_execute=_legacy_execute,
                agent_emit=collect_progress,
            )
            _require(handle is not None, "project_cell_executor_not_selected")
            assert handle is not None
            context.handle = handle
            context.workspace_id = handle.workspace_id
            context.portable = handle.is_portable()
            files = await handle.snapshot_files()
            _require(bool(files), "bootstrap_snapshot_empty")
            if context.portable:
                _require(".omnia/cell.json" in files, "portable_manifest_missing")
            await _verify_workspace_leased(session_factory, context)
            await _persist_evidence(session_factory, context)

        await _timed(context, "ensure_and_bootstrap", create_executor())
        assert context.handle is not None

        async def execute_probe() -> None:
            handle = context.handle
            assert handle is not None
            if not context.portable:
                result = await _run_action(
                    handle,
                    Action(
                        name="bash",
                        args={
                            "cmd": (
                                "set -eu; node --version >/dev/null; "
                                "pnpm --version >/dev/null; "
                                f"printf '{_BASELINE_MARKER}\\n'"
                            )
                        },
                    ),
                    code="baseline_command_failed",
                )
                _require(
                    _BASELINE_MARKER in str(result.get("detail") or ""),
                    "baseline_marker_missing",
                )
                return

            context.probe_id = uuid4()
            probe_path = f".omnia/acceptance/{context.probe_id.hex}.mjs"
            await _run_action(
                handle,
                Action(
                    name="write_file",
                    args={
                        "path": probe_path,
                        "content": _portable_probe_source(context.probe_id),
                    },
                ),
                code="portable_probe_write_failed",
            )
            library = await _run_action(
                handle,
                Action(
                    name="bash",
                    args={
                        "cmd": (
                            "pnpm --reporter=silent add --save-exact --ignore-scripts "
                            "is-number@7.0.0 && "
                            "node -e \"const n=require('is-number');"
                            "if(!n('42'))process.exit(41);"
                            "process.stdout.write('omnia-capacity-lib-ok\\\\n')\""
                        )
                    },
                ),
                code="portable_library_install_failed",
            )
            mutation = library.get("mutation")
            _require(isinstance(mutation, dict), "portable_library_evidence_missing")
            assert isinstance(mutation, dict)
            _require(mutation.get("dependency_changed") is True, "dependency_digest_unchanged")
            database = await _run_action(
                handle,
                Action(name="bash", args={"cmd": f"node {probe_path}"}),
                code="portable_postgres_roundtrip_failed",
            )
            _require(
                _PORTABLE_MARKER in str(database.get("detail") or ""),
                "portable_postgres_marker_missing",
            )
            await _persist_evidence(session_factory, context)

        await _timed(
            context,
            "portable_dependency_pg_roundtrip" if context.portable else "baseline_command",
            execute_probe(),
        )

        async def release_executor() -> None:
            assert context.handle is not None
            await context.handle.release()
            context.released = True
            await _verify_release(session_factory, context)
            await _persist_evidence(session_factory, context)

        await _timed(context, "release_generation_lease", release_executor())
        await _timed(
            context,
            "hibernate_preflight",
            _prepare_hibernation(session_factory, context),
        )

        async def hibernate() -> None:
            assert context.requester_run_id is not None
            ok = await hibernate_one_idle_workspace(
                session_factory,
                requesting_run_id=context.requester_run_id,
                client=HttpProjectCellOrchestratorClient(),
                expected_workspace_id=context.workspace_id,
            )
            _require(ok, "capacity_hibernation_failed")
            await _verify_hibernation(session_factory, context)
            await _persist_evidence(session_factory, context)

        await _timed(context, "capacity_hibernate_idle", hibernate())
        await _timed(
            context,
            "observe_paused_controller",
            _observe_paused_controller(session_factory, context),
        )
        context.stage = "completed"
        await _terminalize_runs(session_factory, context, status="completed")
        _emit("acceptance", "completed", ids=context.ids())
        return 0
    except AcceptanceFailure as exc:
        context.stage = str(exc)
        await _cleanup_failed(session_factory, context)
        _emit(context.stage, "failed", ids=context.ids())
        return 1
    except Exception:
        await _cleanup_failed(session_factory, context)
        _emit("acceptance", "failed", ids=context.ids())
        return 1
    finally:
        await dispose_engine()


def _parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--execute",
        action="store_true",
        help="create and run the disposable no-model live acceptance",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    if not args.execute:
        _emit("execute_guard", "required")
        return 2
    return asyncio.run(_execute_acceptance())


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

"""Private durable restoration intents. Only observed activation may complete an operation."""

from __future__ import annotations

import asyncio
import json
import os
import re
from pathlib import Path
from typing import Any, Protocol, cast
from uuid import UUID

import structlog

from omnia_orchestrator.core.cell_resources import CellIdentityConflict, WorkspaceLockTimeout
from omnia_orchestrator.schemas.code_restoration import (
    CodeRestorationApply,
    CodeRestorationCancel,
    CodeRestorationPrepare,
)
from omnia_orchestrator.services.cell_lock import WorkspaceOperationLock
from omnia_orchestrator.services.project_machine import write_controller_json

_TERMINAL = {"completed", "cancelled", "failed"}
_IDENTITY = ("operation_id", "workspace_id", "project_id", "owner_id")
_REPORT_LISTS = (
    "changes",
    "retained_data",
    "unavailable_features",
    "warnings",
    "blockers",
    "next_actions",
)
_log = structlog.get_logger("code_restorations")


class RestorationEngine(Protocol):
    async def prepare(self, request: CodeRestorationPrepare) -> dict[str, Any]: ...
    async def apply(
        self, request: CodeRestorationApply, prepared: dict[str, Any]
    ) -> dict[str, Any]: ...
    async def cancel(self, request: CodeRestorationCancel, prepared: dict[str, Any]) -> None: ...
    async def observe(
        self, request: CodeRestorationApply, prepared: dict[str, Any]
    ) -> dict[str, Any] | None: ...


class CodeRestorationService:
    def __init__(self, *, root: Path | None = None, engine: RestorationEngine | None = None):
        if root is None:
            from omnia_orchestrator.core.config import get_settings

            root = Path(get_settings().cell_state_path).parent / "code-restorations"
        self.root = root
        self.engine = engine
        self._lock = WorkspaceOperationLock(root)
        self._execution_lock = WorkspaceOperationLock(root / "execution")
        self._tasks: dict[str, asyncio.Task[None]] = {}

    def _engine(self) -> RestorationEngine:
        if self.engine is None:
            from omnia_orchestrator.services.code_restoration_engine import CodeRestorationEngine

            self.engine = CodeRestorationEngine()
        return self.engine

    def _path(self, workspace: UUID, operation: UUID) -> Path:
        return self.root / str(workspace) / f"{operation}.json"

    def _read(self, workspace: UUID, operation: UUID) -> dict[str, Any] | None:
        path = self._path(workspace, operation)
        if path.is_symlink() or path.parent.is_symlink() or self.root.is_symlink():
            raise CellIdentityConflict("unsafe restoration journal")
        if not path.exists():
            return None
        value = json.loads(path.read_text(encoding="utf-8"))
        if value.get("workspace_id") != str(workspace) or value.get("operation_id") != str(
            operation
        ):
            raise CellIdentityConflict("restoration journal identity mismatch")
        return cast(dict[str, Any], value)

    def _write(self, saved: dict[str, Any]) -> None:
        path = self._path(UUID(saved["workspace_id"]), UUID(saved["operation_id"]))
        write_controller_json(path, saved)
        # Persist the replacement directory entry on the Linux controller host.
        if hasattr(os, "O_DIRECTORY"):
            fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)

    @staticmethod
    def _check(saved: dict[str, Any], wire: dict[str, Any]) -> None:
        if any(saved[name] != wire[name] for name in _IDENTITY):
            raise CellIdentityConflict("restoration owner or operation mismatch")

    @staticmethod
    def _new(wire: dict[str, Any], state: str) -> dict[str, Any]:
        return {
            **{name: wire[name] for name in _IDENTITY},
            "state": state,
            "phase": state,
            "revision": 1,
            "candidate_id": None,
            "report": None,
            "error": None,
            "observed": None,
            "prepare": None,
            "apply": None,
            "prepared": None,
            "cancel_requested": False,
        }

    @staticmethod
    def _public(saved: dict[str, Any]) -> dict[str, Any]:
        return {
            **{name: saved[name] for name in _IDENTITY},
            **{
                name: saved[name]
                for name in (
                    "state",
                    "phase",
                    "revision",
                    "candidate_id",
                    "report",
                    "error",
                    "observed",
                )
            },
            "can_apply": saved["state"] == "ready" and not saved["cancel_requested"],
            "can_cancel": saved["state"] in {"preparing", "checking", "ready", "needs_changes"}
            and not saved["cancel_requested"],
        }

    def _schedule(self, workspace: UUID, operation: UUID) -> None:
        key = str(operation)
        if key in self._tasks:
            return
        task = asyncio.create_task(self._drive(workspace, operation))
        self._tasks[key] = task
        task.add_done_callback(lambda _task: self._tasks.pop(key, None))

    async def drain(self) -> None:
        while self._tasks:
            await asyncio.gather(*tuple(self._tasks.values()))

    async def close(self) -> None:
        tasks = tuple(self._tasks.values())
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    async def get(
        self, workspace: UUID, operation: UUID, project: UUID, owner: UUID
    ) -> dict[str, Any]:
        async with self._lock.hold(workspace):
            saved = self._read(workspace, operation)
            if saved is None:
                raise LookupError("restoration not found")
            self._check(
                saved,
                dict(
                    workspace_id=str(workspace),
                    operation_id=str(operation),
                    project_id=str(project),
                    owner_id=str(owner),
                ),
            )
            return self._public(saved)

    async def prepare(self, request: CodeRestorationPrepare) -> dict[str, Any]:
        wire = request.model_dump(mode="json")
        async with self._lock.hold(request.workspace_id):
            saved = self._read(request.workspace_id, request.operation_id)
            if saved is not None:
                self._check(saved, wire)
                if saved["prepare"] is not None and saved["prepare"] != wire:
                    raise CellIdentityConflict("restoration prepare envelope mismatch")
                # An earlier cancel is a permanent tombstone, even before source arrived.
                return self._public(saved)
            for path in (self.root / str(request.workspace_id)).glob("*.json"):
                other = self._read(request.workspace_id, UUID(path.stem))
                if other is not None and other["state"] not in _TERMINAL:
                    raise CellIdentityConflict("another restoration is active")
            saved = self._new(wire, "preparing")
            saved["prepare"] = wire
            self._write(saved)
            self._schedule(request.workspace_id, request.operation_id)
            return self._public(saved)

    async def apply(self, request: CodeRestorationApply) -> dict[str, Any]:
        wire = request.model_dump(mode="json")
        async with self._lock.hold(request.workspace_id):
            saved = self._read(request.workspace_id, request.operation_id)
            if saved is None:
                raise CellIdentityConflict("restoration is not prepared")
            self._check(saved, wire)
            if saved["apply"] is not None:
                if saved["apply"] != wire:
                    raise CellIdentityConflict("restoration apply envelope mismatch")
                return self._public(saved)
            source = saved["prepare"]
            if (
                saved["state"] != "ready"
                or saved["cancel_requested"]
                or saved["candidate_id"] != str(request.candidate_id)
                or saved["report"]["revision"] != request.report_revision
                or source["fencing_epoch"] != request.expected_fencing_epoch
                or request.fencing_epoch <= request.expected_fencing_epoch
                or any(
                    source[name] != wire[name]
                    for name in ("expected_source_head", "target_commit_sha", "planned_commit_sha")
                )
            ):
                raise CellIdentityConflict("restoration candidate, report or fence changed")
            saved.update(apply=wire, state="applying", phase="applying", error=None)
            saved["revision"] += 1
            self._write(saved)  # Durable intent precedes every physical effect.
            self._schedule(request.workspace_id, request.operation_id)
            return self._public(saved)

    async def cancel(self, request: CodeRestorationCancel) -> dict[str, Any]:
        wire = request.model_dump(mode="json")
        async with self._lock.hold(request.workspace_id):
            saved = self._read(request.workspace_id, request.operation_id)
            if saved is None:
                saved = self._new(wire, "cancelled")
                saved["cancel_requested"] = True
                self._write(saved)
                return self._public(saved)
            self._check(saved, wire)
            if saved["state"] == "cancelled":
                return self._public(saved)
            if saved["apply"] is not None or saved["state"] in _TERMINAL:
                raise CellIdentityConflict("restoration application cannot be cancelled")
            saved.update(cancel_requested=True, phase="cancelling")
            saved["revision"] += 1
            self._write(saved)
            self._schedule(request.workspace_id, request.operation_id)
            return self._public(saved)

    async def _update(self, workspace: UUID, operation: UUID, **changes: Any) -> dict[str, Any]:
        async with self._lock.hold(workspace):
            saved = self._read(workspace, operation)
            assert saved is not None
            saved.update(changes)
            saved["revision"] += 1
            self._write(saved)
            return saved

    @staticmethod
    def _report(prepared: dict[str, Any]) -> dict[str, Any]:
        report = prepared["report"]
        database_state = report.get("database_state", "unknown")
        if (
            type(report.get("revision")) is not int
            or report["revision"] < 1
            or report.get("mode") not in {"exact", "adapted"}
            or not isinstance(database_state, str)
            or database_state not in {"empty", "present", "unknown"}
            or any(
                not isinstance(report.get(name), list)
                or any(not isinstance(item, str) for item in report[name])
                for name in _REPORT_LISTS
            )
        ):
            raise ValueError("invalid restoration report")
        return {
            **{name: report[name] for name in ("revision", "mode", *_REPORT_LISTS)},
            "database_state": database_state,
        }

    @staticmethod
    def _observed(value: dict[str, Any], request: CodeRestorationApply) -> dict[str, Any]:
        expected = dict(
            candidate_id=str(request.candidate_id),
            source_commit_sha=request.planned_commit_sha,
            fencing_epoch=request.fencing_epoch,
        )
        if type(value.get("fencing_epoch")) is not int or any(
            value.get(name) != target for name, target in expected.items()
        ):
            raise CellIdentityConflict("restoration activation observation mismatch")
        if value.get("applied") is True:
            revision = value.get("source_revision")
            if revision is not None:
                if not isinstance(revision, str) or not re.fullmatch(r"[0-9a-f]{64}", revision):
                    raise CellIdentityConflict("restoration source revision mismatch")
                expected["source_revision"] = revision
            return {**expected, "applied": True}
        if value.get("applied") is False and value.get("safe_to_release") is True:
            return {**expected, "applied": False, "safe_to_release": True}
        raise CellIdentityConflict("restoration activation observation is uncertain")

    async def _drive(self, workspace: UUID, operation: UUID) -> None:
        # Different OS processes cannot execute the same operation concurrently.
        try:
            async with self._execution_lock.hold(operation):
                saved = self._read(workspace, operation)
                if saved is None or saved["state"] in _TERMINAL:
                    return
                engine = self._engine()
                if saved["apply"] is not None:
                    body = CodeRestorationApply.model_validate(saved["apply"])
                    if saved.get("effect_started"):
                        observed = await engine.observe(body, saved["prepared"])
                    else:
                        saved = await self._update(workspace, operation, effect_started=True)
                        observed = await engine.apply(body, saved["prepared"])
                    if observed is not None:
                        proof = self._observed(observed, body)
                        applied = proof["applied"]
                        await self._update(
                            workspace,
                            operation,
                            state="completed" if applied else "failed",
                            phase="completed" if applied else "failed",
                            observed=proof,
                            error=None
                            if applied
                            else ("Восстановление не применено; прежняя версия работает."),
                        )
                    else:
                        await self._update(
                            workspace, operation, state="reconciling", phase="reconciling"
                        )
                    return
                if saved["prepared"] is None:
                    body_prepare = CodeRestorationPrepare.model_validate(saved["prepare"])
                    prepared = await engine.prepare(body_prepare)
                    report = self._report(prepared)
                    candidate = (
                        str(UUID(str(prepared["candidate_id"])))
                        if prepared.get("candidate_id") is not None
                        else None
                    )
                    state = prepared.get("state", "needs_changes")
                    if state not in {"ready", "needs_changes"} or (
                        state == "ready" and (report["blockers"] or candidate is None)
                    ):
                        raise ValueError("invalid candidate readiness")
                    saved = await self._update(
                        workspace,
                        operation,
                        prepared=prepared,
                        candidate_id=candidate,
                        report=report,
                        state=state,
                        phase=state,
                    )
                if saved["cancel_requested"]:
                    cancel = CodeRestorationCancel.model_validate(
                        {name: saved[name] for name in _IDENTITY}
                    )
                    await engine.cancel(cancel, saved["prepared"])
                    await self._update(
                        workspace, operation, state="cancelled", phase="cancelled", error=None
                    )
        except asyncio.CancelledError:
            # Shutdown leaves the durable intent recoverable, never falsely terminal.
            raise
        except WorkspaceLockTimeout:
            # Another controller still owns execution; it owns the outcome too.
            return
        except Exception as exc:
            _log.warning("operation_requires_attention", error_type=type(exc).__name__)
            saved = self._read(workspace, operation)
            if saved is not None and saved["state"] not in _TERMINAL:
                uncertain = saved["apply"] is not None or saved["cancel_requested"]
                state = "reconciling" if uncertain else "failed"
                await self._update(
                    workspace,
                    operation,
                    state=state,
                    phase=state,
                    error="Не удалось завершить проверку восстановления.",
                )

    async def recover(self) -> None:
        for path in self.root.glob("*/*.json"):
            workspace, operation = UUID(path.parent.name), UUID(path.stem)
            async with self._lock.hold(workspace):
                saved = self._read(workspace, operation)
                if saved is not None and saved["state"] not in _TERMINAL:
                    if (
                        saved["state"] not in {"ready", "needs_changes"}
                        or saved["cancel_requested"]
                    ):
                        self._schedule(workspace, operation)


_service: CodeRestorationService | None = None


def get_code_restoration_service() -> CodeRestorationService:
    global _service
    if _service is None:
        _service = CodeRestorationService()
    return _service


def start_restoration_recovery() -> asyncio.Task[None]:
    async def run() -> None:
        while True:
            try:
                await get_code_restoration_service().recover()
            except Exception as exc:
                _log.warning("recovery_failed", error_type=type(exc).__name__)
            await asyncio.sleep(30)

    return asyncio.create_task(run(), name="code-restoration-recovery")

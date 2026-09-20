"""Private durable restoration intents. Only observed activation may complete an operation."""

from __future__ import annotations

import asyncio
import inspect
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
    RestorationSourceBindingV2,
    RestorationSourceBindingV3,
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
    def begin_cancel(self, request: CodeRestorationCancel) -> None: ...
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
        self._cancel_tasks: dict[str, asyncio.Task[None]] = {}
        # Operations whose intent changed while a drive was running: look again
        # once that drive ends — its last check may already be behind the change.
        self._rerun: set[str] = set()
        self._cancel_rerun: set[str] = set()

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
            "binding": None,
            "binding_digest": None,
            "prepare": None,
            "apply": None,
            "prepared": None,
            "cancel_requested": False,
        }

    @staticmethod
    def _public(saved: dict[str, Any]) -> dict[str, Any]:
        wire_state = (
            "reconciling" if saved["state"] in {"cancelling", "cleanup_pending"} else saved["state"]
        )
        wire_phase = (
            "reconciling" if saved["phase"] in {"cancelling", "cleanup_pending"} else saved["phase"]
        )
        return {
            **{name: saved[name] for name in _IDENTITY},
            "state": wire_state,
            "phase": wire_phase,
            **{
                name: saved[name]
                for name in (
                    "revision",
                    "candidate_id",
                    "report",
                    "error",
                    "observed",
                )
            },
            "binding": saved.get("binding"),
            "binding_digest": saved.get("binding_digest"),
            "can_apply": saved["state"] == "ready"
            and saved.get("binding") is not None
            and saved.get("binding_digest") is not None
            and not saved["cancel_requested"],
            "can_cancel": saved["state"] in {"preparing", "checking", "ready", "needs_changes"}
            and not saved["cancel_requested"],
        }

    def _schedule(self, workspace: UUID, operation: UUID) -> None:
        key = str(operation)
        if key in self._tasks:
            self._rerun.add(key)
            return
        task = asyncio.create_task(self._drive(workspace, operation))
        self._tasks[key] = task
        task.add_done_callback(lambda done: self._finished(done, key, workspace, operation))

    def _finished(
        self, done: asyncio.Task[None], key: str, workspace: UUID, operation: UUID
    ) -> None:
        self._tasks.pop(key, None)
        if key in self._rerun:
            self._rerun.discard(key)
            if not done.cancelled():  # shutdown: recovery re-schedules on start
                self._schedule(workspace, operation)

    def _schedule_cancel(self, workspace: UUID, operation: UUID) -> None:
        key = str(operation)
        if key in self._cancel_tasks:
            self._cancel_rerun.add(key)
            return
        task = asyncio.create_task(self._cancel_drive(workspace, operation))
        self._cancel_tasks[key] = task
        task.add_done_callback(lambda done: self._cancel_finished(done, key, workspace, operation))

    def _cancel_finished(
        self, done: asyncio.Task[None], key: str, workspace: UUID, operation: UUID
    ) -> None:
        self._cancel_tasks.pop(key, None)
        if key in self._cancel_rerun:
            self._cancel_rerun.discard(key)
            if not done.cancelled():
                self._schedule_cancel(workspace, operation)

    async def drain(self) -> None:
        while self._tasks or self._cancel_tasks:
            await asyncio.gather(
                *tuple(self._tasks.values()),
                *tuple(self._cancel_tasks.values()),
            )
            # A task that finished eagerly is still registered until its done
            # callback runs; yield once so it is popped (or re-scheduled) and the
            # loop above cannot spin without giving the event loop a turn.
            await asyncio.sleep(0)

    async def close(self) -> None:
        tasks = (*self._tasks.values(), *self._cancel_tasks.values())
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
        wire = request.model_dump(mode="json", exclude_none=True)
        async with self._lock.hold(request.workspace_id):
            saved = self._read(request.workspace_id, request.operation_id)
            if saved is not None:
                self._check(saved, wire)
                if saved["prepare"] is not None and saved["prepare"] != wire:
                    raise CellIdentityConflict("restoration prepare envelope mismatch")
                # An earlier cancel is a permanent tombstone, even before source arrived.
                return self._public(saved)
            if request.binding_contract_version not in {2, 3}:
                raise CellIdentityConflict("restoration binding capability is required")
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
        wire = request.model_dump(mode="json", exclude_none=True)
        async with self._lock.hold(request.workspace_id):
            saved = self._read(request.workspace_id, request.operation_id)
            if saved is None:
                raise CellIdentityConflict("restoration is not prepared")
            self._check(saved, wire)
            if saved["apply"] is not None:
                if saved["apply"] != wire:
                    raise CellIdentityConflict("restoration apply envelope mismatch")
                return self._public(saved)
            if (
                saved.get("binding") is None
                or saved.get("binding_digest") is None
                or request.binding_digest != saved.get("binding_digest")
            ):
                raise CellIdentityConflict("restoration source binding mismatch")
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
                begin_cancel = getattr(self._engine(), "begin_cancel", None)
                if callable(begin_cancel):
                    begin_cancel(request)
                saved = self._new(wire, "cancelled")
                saved["cancel_requested"] = True
                self._write(saved)
                return self._public(saved)
            self._check(saved, wire)
            if saved["state"] == "cancelled":
                return self._public(saved)
            if saved["apply"] is not None or saved["state"] == "completed":
                raise CellIdentityConflict("restoration application cannot be cancelled")
            begin_cancel = getattr(self._engine(), "begin_cancel", None)
            if callable(begin_cancel):
                begin_cancel(request)
            saved.update(
                cancel_requested=True,
                state="cancelling",
                phase="cancelling",
                error=None,
            )
            saved["revision"] += 1
            self._write(saved)
            self._schedule_cancel(request.workspace_id, request.operation_id)
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
        result = {
            **{name: report[name] for name in ("revision", "mode", *_REPORT_LISTS)},
            "database_state": database_state,
        }
        if report.get("format") == 2:
            from omnia_orchestrator.services.versioning.contracts import (
                CapabilityDiff,
                CompatibilityCheck,
                InventoryReport,
            )

            inventory = report.get("inventory")
            capabilities = report.get("capabilities")
            if inventory is not None and InventoryReport.model_validate(inventory).presence != (
                database_state
            ):
                raise ValueError("invalid restoration report")
            result.update(
                format=2,
                inventory=None
                if inventory is None
                else InventoryReport.model_validate(inventory).model_dump(mode="json"),
                checks=[
                    CompatibilityCheck.model_validate(check).model_dump(mode="json")
                    for check in report.get("checks") or []
                ],
                capabilities=None
                if capabilities is None
                else CapabilityDiff.model_validate(capabilities).model_dump(mode="json"),
            )
        return result

    @staticmethod
    def _observed(value: dict[str, Any], request: CodeRestorationApply) -> dict[str, Any]:
        expected = dict(
            candidate_id=str(request.candidate_id),
            source_commit_sha=request.planned_commit_sha,
            fencing_epoch=request.fencing_epoch,
        )
        if request.binding_digest is not None:
            expected["binding_digest"] = request.binding_digest
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
            observed = {**expected, "applied": False, "safe_to_release": True}
            if value.get("superseded_before_effect") is True:
                if (
                    value.get("rejected_before_effect") is not None
                    or value.get("retained_source_fencing_epoch") is not None
                ):
                    raise CellIdentityConflict("superseded restoration receipt is invalid")
                observed["superseded_before_effect"] = True
            elif value.get("rejected_before_effect") is True:
                retained_epoch = value.get("retained_source_fencing_epoch")
                if (
                    type(retained_epoch) is not int
                    or retained_epoch < 1
                    or retained_epoch > request.expected_fencing_epoch
                    or retained_epoch >= request.fencing_epoch
                ):
                    raise CellIdentityConflict("restoration rejection epoch mismatch")
                observed.update(
                    rejected_before_effect=True,
                    retained_source_fencing_epoch=retained_epoch,
                )
            elif (
                value.get("rejected_before_effect") is not None
                or value.get("retained_source_fencing_epoch") is not None
                or value.get("superseded_before_effect") is not None
            ):
                raise CellIdentityConflict("restoration rejection receipt is incomplete")
            return observed
        raise CellIdentityConflict("restoration activation observation is uncertain")

    async def _drive(self, workspace: UUID, operation: UUID) -> None:
        # Different OS processes cannot execute the same operation concurrently.
        try:
            async with self._execution_lock.hold(operation):
                saved = self._read(workspace, operation)
                if saved is None or saved["state"] in _TERMINAL:
                    return
                if saved["cancel_requested"]:
                    self._schedule_cancel(workspace, operation)
                    return
                engine = self._engine()
                if saved["apply"] is not None:
                    body = CodeRestorationApply.model_validate(saved["apply"])
                    bound = (
                        saved.get("binding") is not None
                        and saved.get("binding_digest") is not None
                        and body.binding_digest == saved.get("binding_digest")
                    )
                    journal_exists = getattr(engine, "activation_journal_exists", None)
                    if not bound and not (
                        saved.get("effect_started")
                        and callable(journal_exists)
                        and journal_exists(body)
                    ):
                        await self._update(
                            workspace, operation, state="reconciling", phase="reconciling"
                        )
                        return
                    if saved.get("effect_started"):
                        observed = await engine.observe(body, saved["prepared"])
                    else:
                        begin_preflight = getattr(engine, "begin_preflight", None)
                        if callable(begin_preflight):
                            begin_preflight(body)
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
                    prepared = await engine.prepare(
                        body_prepare, **self._prepare_options(engine, workspace, operation)
                    )
                    saved = await self._commit_prepared(
                        workspace,
                        operation,
                        prepared,
                    )
                    if saved is None:
                        # Cancellation/terminal state won the journal CAS. The
                        # late candidate result is deliberately discarded.
                        self._schedule_cancel(workspace, operation)
                        return
                if saved["cancel_requested"]:
                    self._schedule_cancel(workspace, operation)
        except asyncio.CancelledError:
            # Shutdown leaves the durable intent recoverable, never falsely terminal.
            raise
        except WorkspaceLockTimeout:
            # Another controller still owns execution; it owns the outcome too.
            return
        except Exception as exc:
            _log.warning("operation_requires_attention", error_type=type(exc).__name__)
            if await self._commit_drive_failure(workspace, operation):
                self._schedule_cancel(workspace, operation)

    async def _cancel_drive(self, workspace: UUID, operation: UUID) -> None:
        try:
            saved = self._read(workspace, operation)
            if saved is None or saved["state"] in _TERMINAL or not saved["cancel_requested"]:
                return
            if saved["apply"] is not None:
                raise CellIdentityConflict("an admitted restoration cannot be cancelled")
            cancel = CodeRestorationCancel.model_validate({name: saved[name] for name in _IDENTITY})
            await self._engine().cancel(cancel, saved["prepared"])
            async with self._lock.hold(workspace):
                current = self._read(workspace, operation)
                if current is None or current["state"] in _TERMINAL:
                    return
                current.update(state="cancelled", phase="cancelled", error=None)
                current["revision"] += 1
                self._write(current)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            _log.warning("cancellation_requires_attention", error_type=type(exc).__name__)
            await self._mark_cleanup_pending(workspace, operation)

    async def _mark_cleanup_pending(self, workspace: UUID, operation: UUID) -> None:
        """Record retryable cancellation failure without reviving a terminal result."""
        async with self._lock.hold(workspace):
            saved = self._read(workspace, operation)
            if saved is None or saved["state"] in _TERMINAL:
                return
            saved.update(
                state="cleanup_pending",
                phase="cleanup_pending",
                error="Не удалось подтвердить остановку и очистку восстановления.",
            )
            saved["revision"] += 1
            self._write(saved)

    async def _commit_drive_failure(self, workspace: UUID, operation: UUID) -> bool:
        """CAS a drive error without overwriting a terminal cancellation receipt."""
        async with self._lock.hold(workspace):
            saved = self._read(workspace, operation)
            if saved is None or saved["state"] in _TERMINAL:
                return False
            cancellation = bool(saved["cancel_requested"])
            state = (
                "cleanup_pending"
                if cancellation
                else ("reconciling" if saved["apply"] is not None else "failed")
            )
            saved.update(
                state=state,
                phase=state,
                error=(
                    "Не удалось подтвердить остановку и очистку восстановления."
                    if cancellation
                    else "Не удалось завершить проверку восстановления."
                ),
            )
            saved["revision"] += 1
            self._write(saved)
            return cancellation

    async def _commit_prepared(
        self,
        workspace: UUID,
        operation: UUID,
        prepared: dict[str, Any],
    ) -> dict[str, Any] | None:
        async with self._lock.hold(workspace):
            saved = self._read(workspace, operation)
            if saved is None or saved["state"] in _TERMINAL or saved["cancel_requested"]:
                return None
            report = self._report(prepared)
            candidate = (
                str(UUID(str(prepared["candidate_id"])))
                if prepared.get("candidate_id") is not None
                else None
            )
            state = prepared.get("state", "needs_changes")
            binding = None
            binding_digest = None
            if prepared.get("binding") is not None:
                binding_payload = prepared["binding"]
                binding_model = (
                    RestorationSourceBindingV3.model_validate(binding_payload)
                    if binding_payload.get("version") == 3
                    else RestorationSourceBindingV2.model_validate(binding_payload)
                )
                binding = binding_model.model_dump(mode="json")
                binding_digest = binding_model.digest()
            if state not in {"ready", "needs_changes"} or (
                state == "ready"
                and (
                    report["blockers"]
                    or candidate is None
                    or binding is None
                    or binding_digest is None
                )
            ):
                raise ValueError("invalid candidate readiness")
            saved.update(
                prepared=prepared,
                candidate_id=candidate,
                report=report,
                binding=binding,
                binding_digest=binding_digest,
                state=state,
                phase=state,
            )
            saved["revision"] += 1
            self._write(saved)
            return saved

    def _prepare_options(self, engine: Any, workspace: UUID, operation: UUID) -> dict[str, Any]:
        """Engines that accept ``cancel_requested`` stop at safe checkpoints; test
        fakes with the bare signature keep working unchanged."""
        if "cancel_requested" not in inspect.signature(engine.prepare).parameters:
            return {}

        def cancel_requested() -> bool:
            saved = self._read(workspace, operation)
            return bool(saved and saved["cancel_requested"])

        return {"cancel_requested": cancel_requested}

    async def recover(self) -> None:
        for path in self.root.glob("*/*.json"):
            workspace, operation = UUID(path.parent.name), UUID(path.stem)
            async with self._lock.hold(workspace):
                saved = self._read(workspace, operation)
                if saved is not None and saved["state"] not in _TERMINAL:
                    if saved["cancel_requested"]:
                        self._schedule_cancel(workspace, operation)
                    elif saved["state"] not in {"ready", "needs_changes"}:
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

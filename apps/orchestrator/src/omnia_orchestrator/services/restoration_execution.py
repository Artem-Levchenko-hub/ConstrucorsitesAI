"""Durable exact-container receipts for cancellable restoration verification."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import threading
from collections.abc import AsyncIterator, Callable
from concurrent.futures import ThreadPoolExecutor
from contextlib import AsyncExitStack, asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast
from uuid import UUID, uuid4, uuid5

import docker  # type: ignore[import-untyped]

from omnia_orchestrator.core.cell_resources import CellIdentityConflict
from omnia_orchestrator.services.cell_lock import WorkspaceOperationLock
from omnia_orchestrator.services.project_machine import write_controller_json

_CONTAINER_ID = re.compile(r"^[0-9a-f]{64}$")
_CANCEL_EXECUTOR = ThreadPoolExecutor(max_workers=4, thread_name_prefix="restore-cancel")


class RestorationExecutionCancelled(RuntimeError):
    pass


class RestorationExecutionJournal:
    def __init__(self, root: Path) -> None:
        self.root = root
        self._lock = threading.Lock()
        self._producer_lock = WorkspaceOperationLock(root / "producer-leases")
        self._stop_tasks: dict[str, asyncio.Future[None]] = {}

    def _directory(self, operation_id: UUID) -> Path:
        directory = self.root / str(operation_id)
        if directory.is_symlink() or self.root.is_symlink():
            raise CellIdentityConflict("unsafe restoration execution journal")
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        return directory

    def _write(self, path: Path, payload: dict[str, Any]) -> None:
        write_controller_json(path, payload)
        if hasattr(os, "O_DIRECTORY"):
            descriptor = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)

    def request_cancel(self, request: Any) -> dict[str, Any]:
        path = self._directory(request.operation_id) / "cancel.json"
        identity = {
            "operation_id": str(request.operation_id),
            "workspace_id": str(request.workspace_id),
            "project_id": str(request.project_id),
            "owner_id": str(request.owner_id),
        }
        with self._lock:
            if path.exists():
                saved = cast(
                    dict[str, Any],
                    json.loads(path.read_text(encoding="utf-8")),
                )
                if any(saved.get(key) != value for key, value in identity.items()):
                    raise CellIdentityConflict("restoration cancellation identity changed")
                return saved
            saved = {
                **identity,
                "state": "requested",
                "requested_at": datetime.now(UTC).isoformat(),
            }
            self._write(path, saved)
            return saved

    def cancel_requested(self, operation_id: UUID) -> bool:
        return (self._directory(operation_id) / "cancel.json").is_file()

    @asynccontextmanager
    async def producer(self, request: Any, *, stage: str) -> AsyncIterator[None]:
        if stage not in {"provisioning", "launch"}:
            raise ValueError("invalid restoration producer stage")
        async with self._producer_lock.hold(request.operation_id):
            receipt = self._begin_producer(request, stage=stage)
            try:
                yield
            finally:
                self._finish_producer(request.operation_id, receipt["producer_id"])

    @asynccontextmanager
    async def no_producers(
        self,
        request: Any,
        *,
        timeout_seconds: float,
    ) -> AsyncIterator[None]:
        if timeout_seconds <= 0:
            raise ValueError("producer stop timeout must be positive")
        stack = AsyncExitStack()
        try:
            await asyncio.wait_for(
                stack.enter_async_context(
                    self._producer_lock.hold(request.operation_id)
                ),
                timeout=timeout_seconds,
            )
        except TimeoutError as exc:
            await stack.aclose()
            raise TimeoutError("restoration producer is still running") from exc
        try:
            self._confirm_orphaned_producer_stopped(request)
            yield
        finally:
            await stack.aclose()

    def _begin_producer(self, request: Any, *, stage: str) -> dict[str, Any]:
        directory = self._directory(request.operation_id)
        with self._lock:
            if (directory / "cancel.json").exists():
                raise RestorationExecutionCancelled("restoration cancellation was requested")
            candidate_id = uuid5(request.operation_id, "candidate")
            if candidate_id == request.workspace_id:
                raise CellIdentityConflict("restoration candidate aliases the live workspace")
            receipt = {
                "version": 1,
                "operation_id": str(request.operation_id),
                "producer_id": str(uuid4()),
                "stage": stage,
                "source_workspace_id": str(request.workspace_id),
                "candidate_workspace_id": str(candidate_id),
                "project_id": str(request.project_id),
                "owner_id": str(request.owner_id),
                "state": "running",
                "started_at": datetime.now(UTC).isoformat(),
            }
            self._write(directory / "producer.json", receipt)
            return receipt

    def _finish_producer(self, operation_id: UUID, producer_id: str) -> None:
        path = self._directory(operation_id) / "producer.json"
        with self._lock:
            saved = cast(dict[str, Any], json.loads(path.read_text(encoding="utf-8")))
            if saved.get("producer_id") != producer_id:
                raise CellIdentityConflict("restoration producer receipt changed")
            if saved.get("state") == "running":
                saved["state"] = "finished"
                saved["finished_at"] = datetime.now(UTC).isoformat()
                self._write(path, saved)

    def _confirm_orphaned_producer_stopped(self, request: Any) -> None:
        path = self._directory(request.operation_id) / "producer.json"
        with self._lock:
            if not path.exists():
                return
            saved = cast(dict[str, Any], json.loads(path.read_text(encoding="utf-8")))
            candidate_id = uuid5(request.operation_id, "candidate")
            expected = {
                "version": 1,
                "operation_id": str(request.operation_id),
                "source_workspace_id": str(request.workspace_id),
                "candidate_workspace_id": str(candidate_id),
                "project_id": str(request.project_id),
                "owner_id": str(request.owner_id),
            }
            try:
                producer_id = str(UUID(str(saved.get("producer_id"))))
            except (TypeError, ValueError, AttributeError):
                producer_id = ""
            if (
                candidate_id == request.workspace_id
                or any(saved.get(key) != value for key, value in expected.items())
                or producer_id != saved.get("producer_id")
                or saved.get("stage") not in {"provisioning", "launch"}
                or saved.get("state") not in {"running", "finished", "stopped"}
            ):
                raise CellIdentityConflict("restoration producer identity changed")
            if saved["state"] == "running":
                # Holding the cross-process producer lease proves a running
                # receipt was orphaned by a controller crash.
                saved["state"] = "stopped"
                saved["stopped_at"] = datetime.now(UTC).isoformat()
                self._write(path, saved)

    def complete_cancel(self, request: Any) -> None:
        path = self._directory(request.operation_id) / "cancel.json"
        with self._lock:
            if not path.exists():
                raise CellIdentityConflict("restoration cancellation tombstone is missing")
            saved = json.loads(path.read_text(encoding="utf-8"))
            if saved.get("operation_id") != str(request.operation_id):
                raise CellIdentityConflict("restoration cancellation receipt changed")
            saved["state"] = "completed"
            self._write(path, saved)

    def begin_attempt(
        self,
        request: Any,
        container: Any,
        *,
        stage: str,
        argv: list[str],
        cwd: str,
    ) -> dict[str, Any]:
        directory = self._directory(request.operation_id)
        if (directory / "cancel.json").exists():
            raise RestorationExecutionCancelled("restoration cancellation was requested")
        candidate_id = uuid5(request.operation_id, "candidate")
        if candidate_id == request.workspace_id:
            raise CellIdentityConflict("restoration candidate aliases the live workspace")
        attrs = container.attrs or {}
        container_id = str(attrs.get("Id") or getattr(container, "id", ""))
        created = attrs.get("Created")
        labels = (attrs.get("Config") or {}).get("Labels") or {}
        expected_labels = self._expected_labels(request, candidate_id)
        if (
            not _CONTAINER_ID.fullmatch(container_id)
            or not isinstance(created, str)
            or not created
            or any(labels.get(key) != value for key, value in expected_labels.items())
        ):
            raise CellIdentityConflict("restoration attempt container identity is incomplete")
        argv_digest = hashlib.sha256(
            json.dumps(
                {"argv": argv, "cwd": cwd},
                sort_keys=True,
                separators=(",", ":"),
            ).encode()
        ).hexdigest()
        saved = {
            "version": 1,
            "operation_id": str(request.operation_id),
            "attempt_id": str(uuid4()),
            "stage": stage,
            "source_workspace_id": str(request.workspace_id),
            "candidate_workspace_id": str(candidate_id),
            "project_id": str(request.project_id),
            "owner_id": str(request.owner_id),
            "container_id": container_id,
            "container_created_at": created,
            "expected_labels": expected_labels,
            "fencing_epoch": 1,
            "argv_digest": argv_digest,
            "state": "running",
        }
        with self._lock:
            if (directory / "cancel.json").exists():
                raise RestorationExecutionCancelled("restoration cancellation was requested")
            self._write(directory / "attempt.json", saved)
        return saved

    def finish_attempt(
        self,
        operation_id: UUID,
        attempt_id: str,
        *,
        state: str = "finished",
    ) -> None:
        if state not in {"finished", "stopped", "unknown"}:
            raise ValueError("invalid restoration attempt outcome")
        path = self._directory(operation_id) / "attempt.json"
        with self._lock:
            if not path.exists():
                return
            saved = json.loads(path.read_text(encoding="utf-8"))
            if saved.get("attempt_id") != attempt_id:
                return
            if saved.get("state") == "running":
                saved["state"] = state
                self._write(path, saved)

    async def stop_active(
        self,
        request: Any,
        *,
        api_factory: Callable[[], Any],
        timeout_seconds: float = 4.0,
    ) -> bool:
        path = self._directory(request.operation_id) / "attempt.json"
        with self._lock:
            if not path.exists():
                return True
            saved = json.loads(path.read_text(encoding="utf-8"))
        self._validate_attempt(saved, request)
        if saved.get("state") not in {"running", "unknown"}:
            return True
        loop = asyncio.get_running_loop()
        task_key = f"{request.operation_id}:{saved['attempt_id']}"
        future = self._stop_tasks.get(task_key)
        if future is None:
            future = loop.run_in_executor(
                _CANCEL_EXECUTOR,
                self._stop_exact,
                api_factory,
                saved,
            )
            self._stop_tasks[task_key] = future
        try:
            await asyncio.wait_for(asyncio.shield(future), timeout=timeout_seconds)
        except TimeoutError as exc:
            raise TimeoutError("restoration candidate stop timed out") from exc
        finally:
            if future.done():
                self._stop_tasks.pop(task_key, None)
        with self._lock:
            current = json.loads(path.read_text(encoding="utf-8"))
            if current.get("attempt_id") == saved["attempt_id"]:
                current["state"] = "stopped"
                self._write(path, current)
        return True

    @staticmethod
    def _validate_attempt(saved: dict[str, Any], request: Any) -> None:
        candidate_id = uuid5(request.operation_id, "candidate")
        expected_labels = RestorationExecutionJournal._expected_labels(
            request, candidate_id
        )
        expected = {
            "operation_id": str(request.operation_id),
            "source_workspace_id": str(request.workspace_id),
            "candidate_workspace_id": str(candidate_id),
            "project_id": str(request.project_id),
            "owner_id": str(request.owner_id),
            "fencing_epoch": 1,
        }
        try:
            attempt_id = str(UUID(str(saved.get("attempt_id"))))
        except (TypeError, ValueError, AttributeError):
            attempt_id = ""
        if (
            candidate_id == request.workspace_id
            or saved.get("version") != 1
            or any(saved.get(key) != value for key, value in expected.items())
            or saved.get("expected_labels") != expected_labels
            or not _CONTAINER_ID.fullmatch(str(saved.get("container_id") or ""))
            or not isinstance(saved.get("container_created_at"), str)
            or not saved["container_created_at"]
            or attempt_id != saved.get("attempt_id")
            or re.fullmatch(
                r"(?:install|empty-database-migrations|build:[0-9]+)",
                str(saved.get("stage")),
            )
            is None
            or not _CONTAINER_ID.fullmatch(str(saved.get("argv_digest") or ""))
            or saved.get("state") not in {"running", "unknown", "finished", "stopped"}
        ):
            raise CellIdentityConflict("restoration attempt identity changed")

    @staticmethod
    def _expected_labels(request: Any, candidate_id: UUID) -> dict[str, str]:
        return {
            "omnia.workspace_id": str(candidate_id),
            "omnia.project_id": str(request.project_id),
            "omnia.owner_id": str(request.owner_id),
            "omnia.resource_kind": "development",
            "omnia.fencing_epoch": "1",
        }

    @staticmethod
    def _stop_exact(api_factory: Callable[[], Any], saved: dict[str, Any]) -> None:
        api = api_factory()
        try:
            container_id = saved["container_id"]
            try:
                observed = api.inspect_container(container_id)
            except docker.errors.NotFound:
                return
            labels = (observed.get("Config") or {}).get("Labels") or {}
            if (
                observed.get("Id") != container_id
                or observed.get("Created") != saved["container_created_at"]
                or any(
                    labels.get(key) != value
                    for key, value in saved["expected_labels"].items()
                )
            ):
                raise CellIdentityConflict("restoration candidate container identity changed")
            if (observed.get("State") or {}).get("Running") is True:
                api.kill(container_id, signal="KILL")
            try:
                confirmed = api.inspect_container(container_id)
            except docker.errors.NotFound:
                return
            confirmed_labels = (confirmed.get("Config") or {}).get("Labels") or {}
            if (
                confirmed.get("Id") != container_id
                or confirmed.get("Created") != saved["container_created_at"]
                or any(
                    confirmed_labels.get(key) != value
                    for key, value in saved["expected_labels"].items()
                )
            ):
                raise CellIdentityConflict("restoration candidate container identity changed")
            if (confirmed.get("State") or {}).get("Running") is True:
                raise TimeoutError("restoration candidate is still running")
        finally:
            close = getattr(api, "close", None)
            if callable(close):
                close()


def dedicated_docker_api_factory(
    docker_host: str,
    *,
    transport_timeout_seconds: float,
) -> Callable[[], Any]:
    if not docker_host or transport_timeout_seconds <= 0:
        raise ValueError("dedicated Docker cancellation client is invalid")

    def create() -> Any:
        return docker.APIClient(
            base_url=docker_host,
            timeout=transport_timeout_seconds,
        )

    return create

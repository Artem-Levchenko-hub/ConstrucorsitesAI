"""Dark Docker-owner canary provider; capability stays dark, resource routes work when wired."""

from __future__ import annotations

import asyncio
from typing import cast
from uuid import UUID

from omnia_orchestrator.core.cell_resources import (
    CellFenceRejected,
    CellIdentityConflict,
    CellIndeterminateOperation,
    CellResourceError,
    CellRestoreFailed,
    LifecycleMutation,
)
from omnia_orchestrator.core.workspace_provider import (
    ControlAction,
    WorkspaceHandle,
    WorkspaceProviderUnavailable,
    WorkspaceResourceState,
    WorkspaceResourceStatus,
    WorkspaceSpec,
    WorkspaceStatus,
)
from omnia_orchestrator.services.cell_checkpoint import CellCheckpointManager
from omnia_orchestrator.services.cell_state import CellWorkspaceState
from omnia_orchestrator.services.docker_cell_resources import (
    CellBundleHandle,
    CellBundleObservation,
    DockerCellResourceManager,
)

_UNAVAILABLE_DETAIL = "docker owner canary is unsupported in the foundation"
_READY_DETAIL = "docker owner canary is ready"


class DockerOwnerCanaryProvider:
    def __init__(
        self,
        *,
        resource_manager: DockerCellResourceManager | None = None,
        checkpoint_manager: CellCheckpointManager | None = None,
    ) -> None:
        self.resource_manager = resource_manager
        self.checkpoint_manager = checkpoint_manager

    async def ensure(
        self,
        spec: WorkspaceSpec,
        mutation: LifecycleMutation,
    ) -> WorkspaceHandle:
        handle = await self._require_resource_manager().ensure(spec, mutation)
        return WorkspaceHandle(
            workspace_id=handle.workspace_id,
            provider="docker_owner_canary",
            provider_ref=handle.provider_ref,
        )

    async def wake(
        self,
        workspace_id: UUID,
        mutation: LifecycleMutation,
    ) -> WorkspaceHandle:
        handle = await self._require_resource_manager().wake(workspace_id, mutation)
        return WorkspaceHandle(
            workspace_id=handle.workspace_id,
            provider="docker_owner_canary",
            provider_ref=handle.provider_ref,
        )

    async def pause(
        self,
        workspace_id: UUID,
        checkpoint_ref: str,
        mutation: LifecycleMutation,
    ) -> None:
        await self._execute_composite_pause(
            workspace_id,
            checkpoint_ref,
            mutation,
        )

    async def destroy(self, workspace_id: UUID, mutation: LifecycleMutation) -> None:
        checkpoint_ref = f"final-{mutation.fencing_epoch}-{mutation.operation_id.hex}"
        await self._execute_composite_destroy(
            workspace_id,
            checkpoint_ref,
            mutation,
        )

    async def status(self, project_id: UUID) -> WorkspaceStatus:
        if self.resource_manager is not None and self.checkpoint_manager is not None:
            return WorkspaceStatus(
                project_id=project_id,
                provider="docker_owner_canary",
                enabled=True,
                ready=True,
                state="ready",
                detail=_READY_DETAIL,
            )
        return WorkspaceStatus(
            project_id=project_id,
            provider="docker_owner_canary",
            enabled=True,
            ready=False,
            state="unsupported",
            detail=_UNAVAILABLE_DETAIL,
        )

    async def inspect_resources(self, workspace_id: UUID) -> WorkspaceResourceStatus:
        resource_manager = self._require_resource_manager()
        state_store = resource_manager.state_store
        workspace_state = state_store.load(workspace_id)
        if workspace_state is None or workspace_state.project_id is None:
            return WorkspaceResourceStatus(
                workspace_id=workspace_id,
                state="retained",
                provider_ref=None,
                fencing_epoch=None,
                checkpoint_ref=None,
                has_workspace=False,
                has_agent_home=False,
                has_postgres=False,
                has_redis=False,
            )
        observation = await resource_manager.inspect_by_project(workspace_state.project_id)
        return self._resource_status(
            workspace_id=workspace_id,
            observation=observation,
            provider_ref=workspace_state.provider_ref,
            fencing_epoch=workspace_state.fencing_epoch,
            checkpoint_ref=self._checkpoint_ref_from_state(workspace_state),
        )

    async def observe_resources(
        self,
        workspace_id: UUID,
        mutation: LifecycleMutation,
    ) -> WorkspaceResourceStatus:
        resource_manager = self._require_resource_manager()
        try:
            observation = await resource_manager.reconcile(workspace_id, mutation)
            workspace_state = resource_manager.state_store.load(workspace_id)
        except CellIdentityConflict:
            workspace_state = resource_manager.state_store.load(workspace_id)
            return WorkspaceResourceStatus(
                workspace_id=workspace_id,
                state="conflict",
                provider_ref=workspace_state.provider_ref if workspace_state is not None else None,
                fencing_epoch=mutation.fencing_epoch,
                checkpoint_ref=self._checkpoint_ref_from_state(workspace_state),
                has_workspace=False,
                has_agent_home=False,
                has_postgres=False,
                has_redis=False,
            )
        return self._resource_status(
            workspace_id=workspace_id,
            observation=observation,
            provider_ref=(
                workspace_state.provider_ref if workspace_state is not None else None
            ),
            fencing_epoch=mutation.fencing_epoch,
            checkpoint_ref=self._checkpoint_ref_from_state(workspace_state),
        )

    async def execute_control(
        self,
        workspace_id: UUID,
        action: ControlAction,
        mutation: LifecycleMutation,
    ) -> WorkspaceResourceStatus:
        if action.kind == "wake":
            handle = await self._require_resource_manager().wake(workspace_id, mutation)
            return self._status_from_handle(handle, mutation)
        if action.kind in {"pause", "stop"}:
            assert action.checkpoint_ref is not None
            return await self._execute_composite_pause_status(
                workspace_id,
                action.checkpoint_ref,
                mutation,
            )
        if action.kind == "destroy":
            checkpoint_ref = f"final-{mutation.fencing_epoch}-{mutation.operation_id.hex}"
            return await self._execute_composite_destroy_status(
                workspace_id,
                checkpoint_ref,
                mutation,
            )
        if action.kind == "restore":
            assert action.checkpoint_ref is not None
            return await self._execute_composite_restore_status(
                workspace_id,
                action.checkpoint_ref,
                mutation,
            )
        if action.kind == "reconcile":
            return await self.observe_resources(workspace_id, mutation)
        if action.kind == "release":
            state = self._require_resource_manager().state_store.load(workspace_id)
            if state is None:
                raise CellFenceRejected("workspace generation lease is not active")
            existing = state.operation(mutation.operation_id)
            generation_run_id = state.active_generation_run_id
            if generation_run_id is None and existing is not None:
                generation_run_id = existing.generation_run_id
            if generation_run_id is None:
                raise CellFenceRejected("workspace generation lease is not active")
            handle = await self._require_resource_manager().release_generation(
                workspace_id,
                mutation,
                generation_run_id=generation_run_id,
            )
            return self._status_from_handle(handle, mutation)
        raise WorkspaceProviderUnavailable(_UNAVAILABLE_DETAIL)

    def _require_resource_manager(self) -> DockerCellResourceManager:
        if self.resource_manager is None:
            raise WorkspaceProviderUnavailable(_UNAVAILABLE_DETAIL)
        return self.resource_manager

    def _require_checkpoint_manager(self) -> CellCheckpointManager:
        if self.checkpoint_manager is None:
            raise WorkspaceProviderUnavailable(_UNAVAILABLE_DETAIL)
        return self.checkpoint_manager

    async def _execute_composite_pause(
        self,
        workspace_id: UUID,
        checkpoint_ref: str,
        mutation: LifecycleMutation,
    ) -> None:
        await self._execute_composite_pause_status(
            workspace_id,
            checkpoint_ref,
            mutation,
        )

    async def _execute_composite_pause_status(
        self,
        workspace_id: UUID,
        checkpoint_ref: str,
        mutation: LifecycleMutation,
    ) -> WorkspaceResourceStatus:
        resource_manager = self._require_resource_manager()
        checkpoint_manager = self._require_checkpoint_manager()
        async with resource_manager.operation_lock.hold(workspace_id):
            replay = await self._replay_composite_status(
                workspace_id=workspace_id,
                kind="pause",
                checkpoint_ref=checkpoint_ref,
                mutation=mutation,
            )
            if replay is not None:
                return replay
            prepared = await resource_manager.prepare_control_operation(
                workspace_id,
                mutation,
                kind="pause",
                checkpoint_ref=checkpoint_ref,
            )
            try:
                await checkpoint_manager.create(
                    workspace_id,
                    checkpoint_ref,
                    mutation,
                    record_operation=False,
                )
            except asyncio.CancelledError:
                resource_manager.state_store.mark_indeterminate(
                    workspace_id,
                    mutation=mutation,
                    detail="pause checkpoint cancelled",
                )
                raise
            except Exception as exc:
                resource_manager.state_store.mark_failed(
                    workspace_id,
                    mutation,
                    phase="failed",
                    provider_ref=prepared.provider_ref,
                    bundle_state=prepared.bundle_state,
                    detail=str(exc),
                )
                raise
            try:
                await resource_manager.pause_services_without_lock(
                    workspace_id,
                    mutation,
                    checkpoint_ref=checkpoint_ref,
                    record_operation=False,
                )
            except asyncio.CancelledError:
                resource_manager.state_store.mark_indeterminate(
                    workspace_id,
                    mutation=mutation,
                    detail="pause cancelled",
                )
                raise
            except Exception as exc:
                resource_manager.state_store.mark_indeterminate(
                    workspace_id,
                    mutation=mutation,
                    detail=str(exc),
                )
                raise
            resource_manager.state_store.complete(
                workspace_id,
                mutation,
                phase="completed",
                provider_ref=prepared.provider_ref,
                bundle_state="resources_paused",
            )
            return await self._inspect_with_checkpoint_ref(workspace_id, checkpoint_ref, mutation)

    async def _execute_composite_destroy(
        self,
        workspace_id: UUID,
        checkpoint_ref: str,
        mutation: LifecycleMutation,
    ) -> None:
        await self._execute_composite_destroy_status(
            workspace_id,
            checkpoint_ref,
            mutation,
        )

    async def _execute_composite_destroy_status(
        self,
        workspace_id: UUID,
        checkpoint_ref: str,
        mutation: LifecycleMutation,
    ) -> WorkspaceResourceStatus:
        resource_manager = self._require_resource_manager()
        checkpoint_manager = self._require_checkpoint_manager()
        async with resource_manager.operation_lock.hold(workspace_id):
            replay = await self._replay_composite_status(
                workspace_id=workspace_id,
                kind="destroy",
                checkpoint_ref=checkpoint_ref,
                mutation=mutation,
            )
            if replay is not None:
                return replay
            prepared = await resource_manager.prepare_control_operation(
                workspace_id,
                mutation,
                kind="destroy",
                checkpoint_ref=checkpoint_ref,
            )
            try:
                await checkpoint_manager.create(
                    workspace_id,
                    checkpoint_ref,
                    mutation,
                    record_operation=False,
                )
            except asyncio.CancelledError:
                resource_manager.state_store.mark_indeterminate(
                    workspace_id,
                    mutation=mutation,
                    detail="destroy checkpoint cancelled",
                )
                raise
            except Exception as exc:
                resource_manager.state_store.mark_failed(
                    workspace_id,
                    mutation,
                    phase="failed",
                    provider_ref=prepared.provider_ref,
                    bundle_state=prepared.bundle_state,
                    detail=str(exc),
                )
                raise
            try:
                await resource_manager.destroy_compute_without_lock(
                    workspace_id,
                    mutation,
                    checkpoint_ref=checkpoint_ref,
                    record_operation=False,
                )
            except asyncio.CancelledError:
                resource_manager.state_store.mark_indeterminate(
                    workspace_id,
                    mutation=mutation,
                    detail="destroy cancelled",
                )
                raise
            except Exception as exc:
                resource_manager.state_store.mark_indeterminate(
                    workspace_id,
                    mutation=mutation,
                    detail=str(exc),
                )
                raise
            resource_manager.state_store.complete(
                workspace_id,
                mutation,
                phase="completed",
                provider_ref=prepared.provider_ref,
                bundle_state="retained",
            )
            return await self._inspect_with_checkpoint_ref(workspace_id, checkpoint_ref, mutation)

    async def _execute_composite_restore_status(
        self,
        workspace_id: UUID,
        checkpoint_ref: str,
        mutation: LifecycleMutation,
    ) -> WorkspaceResourceStatus:
        resource_manager = self._require_resource_manager()
        checkpoint_manager = self._require_checkpoint_manager()
        async with resource_manager.operation_lock.hold(workspace_id):
            replay = await self._replay_composite_status(
                workspace_id=workspace_id,
                kind="restore",
                checkpoint_ref=checkpoint_ref,
                mutation=mutation,
            )
            if replay is not None:
                return replay
            await resource_manager.pause_services_without_lock(
                workspace_id,
                mutation,
                checkpoint_ref=None,
                record_operation=False,
            )
            await checkpoint_manager.restore(
                workspace_id,
                checkpoint_ref,
                mutation,
                require_paused_state=False,
            )
            return await self._inspect_with_checkpoint_ref(workspace_id, checkpoint_ref, mutation)

    async def _replay_composite_status(
        self,
        *,
        workspace_id: UUID,
        kind: str,
        checkpoint_ref: str,
        mutation: LifecycleMutation,
    ) -> WorkspaceResourceStatus | None:
        resource_manager = self._require_resource_manager()
        state = resource_manager.state_store.load(workspace_id)
        if state is None:
            return None
        operation = state.operation(mutation.operation_id)
        if operation is None:
            return None
        if operation.matches_replay_envelope(
            kind=kind,
            request_digest=mutation.request_digest,
            fencing_epoch=mutation.fencing_epoch,
            checkpoint_ref=checkpoint_ref,
        ) is False:
            raise CellFenceRejected("replay envelope mismatch")
        if operation.status == "completed":
            return await self._inspect_with_checkpoint_ref(workspace_id, checkpoint_ref, mutation)
        if operation.status == "failed":
            detail = operation.detail or f"{kind} failed"
            if kind == "restore":
                raise CellRestoreFailed(detail)
            raise CellResourceError(detail)
        if operation.status == "indeterminate":
            raise CellIndeterminateOperation("operation replay unavailable")
        raise CellFenceRejected("operation replay unavailable")

    async def _inspect_with_checkpoint_ref(
        self,
        workspace_id: UUID,
        checkpoint_ref: str,
        mutation: LifecycleMutation,
    ) -> WorkspaceResourceStatus:
        status = await self.inspect_resources(workspace_id)
        return WorkspaceResourceStatus(
            workspace_id=status.workspace_id,
            state=status.state,
            provider_ref=status.provider_ref,
            fencing_epoch=mutation.fencing_epoch,
            checkpoint_ref=checkpoint_ref,
            has_workspace=status.has_workspace,
            has_agent_home=status.has_agent_home,
            has_postgres=status.has_postgres,
            has_redis=status.has_redis,
        )

    @staticmethod
    def _checkpoint_ref_from_state(workspace_state: CellWorkspaceState | None) -> str | None:
        if workspace_state is None:
            return None
        operation = workspace_state.operation(workspace_state.last_operation_id)
        if operation is None:
            return None
        if operation.checkpoint_ref:
            return operation.checkpoint_ref
        observed = operation.observed_resources or {}
        value = observed.get("checkpoint_ref")
        return value if isinstance(value, str) and value else None

    @staticmethod
    def _status_from_handle(
        handle: CellBundleHandle,
        mutation: LifecycleMutation,
    ) -> WorkspaceResourceStatus:
        return WorkspaceResourceStatus(
            workspace_id=handle.workspace_id,
            state=_coerce_resource_state(handle.state),
            provider_ref=handle.provider_ref,
            fencing_epoch=mutation.fencing_epoch,
            checkpoint_ref=None,
            has_workspace=True,
            has_agent_home=True,
            has_postgres=True,
            has_redis=True,
        )

    @staticmethod
    def _resource_status(
        *,
        workspace_id: UUID,
        observation: CellBundleObservation,
        provider_ref: str | None,
        fencing_epoch: int | None,
        checkpoint_ref: str | None,
    ) -> WorkspaceResourceStatus:
        return WorkspaceResourceStatus(
            workspace_id=workspace_id,
            state=(
                "conflict"
                if observation.identity_valid is False
                else _coerce_resource_state(observation.state)
            ),
            provider_ref=provider_ref,
            fencing_epoch=fencing_epoch,
            checkpoint_ref=checkpoint_ref,
            has_workspace=observation.volumes.get("workspace", False),
            has_agent_home=observation.volumes.get("agent-home", False),
            has_postgres=(
                observation.volumes.get("postgres", False)
                or observation.containers.get("postgres", False)
            ),
            has_redis=(
                observation.volumes.get("redis", False)
                or observation.containers.get("redis", False)
            ),
        )


def _coerce_resource_state(value: str) -> WorkspaceResourceState:
    allowed = {
        "resources_ready",
        "resources_paused",
        "retained",
        "partial",
        "degraded",
        "conflict",
    }
    if value not in allowed:
        raise WorkspaceProviderUnavailable(_UNAVAILABLE_DETAIL)
    return cast(WorkspaceResourceState, value)

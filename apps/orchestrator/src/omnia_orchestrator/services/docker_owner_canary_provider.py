"""Dark Docker-owner canary provider; capability stays dark, resource routes work when wired."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path
from typing import cast
from uuid import UUID

from omnia_orchestrator.core.cell_resources import (
    CellFenceRejected,
    CellIdentityConflict,
    CellIndeterminateOperation,
    CellRestoreFailed,
    CellTerminalOperationFailed,
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
from omnia_orchestrator.services.cell_deletion import (
    mark_workspace_deleted,
    require_workspace_not_deleted,
)
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
        self._require_editable_workspace(spec.workspace_id)
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
        self._require_editable_workspace(workspace_id)
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
        self._require_editable_workspace(workspace_id)
        await self._execute_composite_pause(
            workspace_id,
            checkpoint_ref,
            mutation,
        )

    async def destroy(self, workspace_id: UUID, mutation: LifecycleMutation) -> None:
        self._require_editable_workspace(workspace_id, allow_deleted=True)
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
        observation = await resource_manager.inspect_by_workspace(workspace_id)
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
        # Reconciliation observes ownership and removes leaked helpers; it never
        # starts compute. Keep it available to recover an interrupted deletion.
        self._require_editable_workspace(workspace_id, allow_deleted=True)
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
            provider_ref=(workspace_state.provider_ref if workspace_state is not None else None),
            fencing_epoch=mutation.fencing_epoch,
            checkpoint_ref=self._checkpoint_ref_from_state(workspace_state),
        )

    async def execute_control(
        self,
        workspace_id: UUID,
        action: ControlAction,
        mutation: LifecycleMutation,
    ) -> WorkspaceResourceStatus:
        self._require_editable_workspace(
            workspace_id, allow_deleted=action.kind in {"destroy", "reconcile"}
        )
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

    def _require_editable_workspace(
        self, workspace_id: UUID, *, allow_deleted: bool = False
    ) -> None:
        manager = self._require_resource_manager()
        if not allow_deleted:
            require_workspace_not_deleted(manager.profile.state_path, workspace_id)
        marker = (
            Path(manager.profile.state_path).parent
            / "cell-publications"
            / "identities"
            / f"{workspace_id}.json"
        )
        if marker.exists() or marker.is_symlink():
            raise CellFenceRejected("production workspace is controlled by publication only")

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
            require_workspace_not_deleted(resource_manager.profile.state_path, workspace_id)
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
            original = resource_manager.state_store.load(workspace_id)
            if original is None:
                await resource_manager.assert_uncreated_workspace(workspace_id)
                mark_workspace_deleted(resource_manager.profile.state_path, workspace_id, mutation)
                return WorkspaceResourceStatus(
                    workspace_id=workspace_id,
                    state="retained",
                    provider_ref=None,
                    fencing_epoch=mutation.fencing_epoch,
                    checkpoint_ref=None,
                    has_workspace=False,
                    has_agent_home=False,
                    has_postgres=False,
                    has_redis=False,
                )
            replay = await self._replay_composite_status(
                workspace_id=workspace_id,
                kind="destroy",
                checkpoint_ref=checkpoint_ref,
                mutation=mutation,
            )
            if replay is not None:
                return replay
            sealed_ref = next(
                (
                    operation.checkpoint_ref
                    for operation in reversed(original.operations)
                    if operation.kind == "destroy"
                    and operation.checkpoint_ref
                    and (operation.observed_resources or {}).get("deletion_checkpoint_ref")
                    == operation.checkpoint_ref
                ),
                None,
            )
            retained = sealed_ref is not None or original.bundle_state in {
                "resources_paused",
                "retained",
            }
            prepared = await resource_manager.prepare_control_operation(
                workspace_id,
                mutation,
                kind="destroy",
                checkpoint_ref=checkpoint_ref,
            )
            mark_workspace_deleted(resource_manager.profile.state_path, workspace_id, mutation)
            try:
                if retained:
                    await self._retain_final_checkpoint(
                        original, checkpoint_ref, mutation, sealed_ref=sealed_ref
                    )
                else:
                    await checkpoint_manager.create(
                        workspace_id,
                        checkpoint_ref,
                        mutation,
                        record_operation=False,
                    )
                # Journal the successful seal before removing even one resource.
                # A later reconcile may observe a partial bundle with no PG left;
                # retries must validate/reuse this backup, never dump absent DBs.
                resource_manager.state_store.advance(
                    workspace_id,
                    mutation,
                    phase="checkpoint_sealed",
                    observed_resources={"deletion_checkpoint_ref": checkpoint_ref},
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
                    **({"capture": False} if retained else {}),
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

    async def _retain_final_checkpoint(
        self,
        state: CellWorkspaceState,
        checkpoint_ref: str,
        mutation: LifecycleMutation,
        *,
        sealed_ref: str | None = None,
    ) -> None:
        manager = self._require_resource_manager()
        checkpoints = self._require_checkpoint_manager()
        if sealed_ref is None:
            for container in await manager.docker.list_workspace_containers(state.workspace_id):
                if container.state not in {"created", "exited", "dead"}:
                    raise CellRestoreFailed("retained workspace still has running compute")
        previous_ref = sealed_ref or next(
            (
                operation.checkpoint_ref
                for operation in reversed(state.operations)
                if operation.status == "completed" and operation.checkpoint_ref
            ),
            None,
        )
        if previous_ref is None or state.resource_names is None:
            raise CellRestoreFailed("retained workspace has no completed checkpoint")
        volume = state.resource_names.checkpoint_volume
        manifest = await checkpoints._load_manifest(volume, previous_ref)
        checkpoints._validate_restore_manifest(
            workspace_id=state.workspace_id,
            state=state,
            checkpoint_ref=previous_ref,
            manifest=manifest,
        )
        artifacts = await checkpoints._load_artifacts(volume, previous_ref, manifest)
        if not {"workspace.tar", "agent-home.tar", "postgres.dump"}.issubset(artifacts):
            raise CellRestoreFailed("retained checkpoint is incomplete")
        runtime = manager.machine_runtime
        if runtime is not None:
            if runtime.exists(state.workspace_id) and "machine.json" not in artifacts:
                raise CellRestoreFailed("retained checkpoint lacks portable database envelope")
            await runtime.validate_restore_payload(state, artifacts.get("machine.json"))
        elif "machine.json" in artifacts:
            raise CellRestoreFailed("portable checkpoint validator unavailable")
        # Copy only a validated backup envelope; never restore it into a live volume.
        final = manifest.model_copy(
            update={
                "checkpoint_ref": checkpoint_ref,
                "fencing_epoch": mutation.fencing_epoch,
                "created_at": datetime.now(UTC),
            }
        )
        await checkpoints._store_checkpoint_artifacts(
            checkpoint_volume=volume,
            checkpoint_ref=checkpoint_ref,
            mutation=mutation,
            manifest=final,
            artifacts=artifacts,
        )

    async def _execute_composite_restore_status(
        self,
        workspace_id: UUID,
        checkpoint_ref: str,
        mutation: LifecycleMutation,
    ) -> WorkspaceResourceStatus:
        resource_manager = self._require_resource_manager()
        checkpoint_manager = self._require_checkpoint_manager()
        async with resource_manager.operation_lock.hold(workspace_id):
            require_workspace_not_deleted(resource_manager.profile.state_path, workspace_id)
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
        if (
            operation.matches_replay_envelope(
                kind=kind,
                request_digest=mutation.request_digest,
                fencing_epoch=mutation.fencing_epoch,
                checkpoint_ref=checkpoint_ref,
            )
            is False
        ):
            raise CellFenceRejected("replay envelope mismatch")
        if operation.status == "completed":
            return await self._inspect_with_checkpoint_ref(workspace_id, checkpoint_ref, mutation)
        if operation.status == "failed":
            detail = operation.detail or f"{kind} failed"
            if kind == "restore":
                raise CellRestoreFailed(detail)
            raise CellTerminalOperationFailed(detail)
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

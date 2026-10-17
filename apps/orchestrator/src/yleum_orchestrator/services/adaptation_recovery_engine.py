"""Controller-owned hold/rebind operations; no candidate creation or DB copy."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any
from uuid import UUID, uuid5

from yleum_orchestrator.core.cell_resources import CellIdentityConflict, LifecycleMutation
from yleum_orchestrator.schemas.restoration_adaptation import (
    RestorationAdaptationPrepare,
    RestorationAdaptationRecoveryHold,
    RestorationAdaptationRecoveryRebind,
    RestorationAdaptationRecoveryReceipt,
)
from yleum_orchestrator.services.adaptation_recovery_barrier import (
    recovery_transition,
    save_machine_recovery_state,
)
from yleum_orchestrator.services.machine_environment import MachineEnvironmentRef
from yleum_orchestrator.services.project_machine import machine_effect
from yleum_orchestrator.services.restoration_binding import canonical_digest
from yleum_orchestrator.services.source_write_journal import (
    _sync_directory,
    require_source_writes_clear,
)


class DockerAdaptationRecovery:
    def __init__(self, manager: Callable[[UUID], Any]) -> None:
        self._manager = manager

    def _candidate_manager(self, original: RestorationAdaptationPrepare) -> Any:
        from yleum_orchestrator.core.config import get_settings
        from yleum_orchestrator.services.cell_publication_capacity import production_manager

        return production_manager(self._manager(original.workspace_id), get_settings())

    async def current_control(self, original: RestorationAdaptationPrepare, epoch: int) -> None:
        manager = self._manager(original.workspace_id)
        async with manager.operation_lock.hold(original.workspace_id):
            self._require_state(manager, original.workspace_id, original, epoch)

    @staticmethod
    def _require_state(
        manager: Any, workspace_id: UUID, original: RestorationAdaptationPrepare, epoch: int
    ) -> Any:
        state = manager.state_store.load(workspace_id)
        if (
            state is None
            or state.project_id != original.project_id
            or state.owner_id != original.owner_id
            or state.active_generation_run_id != original.generation_run_id
            or state.active_generation_fencing_epoch != epoch
        ):
            raise CellIdentityConflict("recovery workspace owner/run/lease changed")
        return state

    def _parts(
        self,
        manager: Any,
        original: RestorationAdaptationPrepare,
        receipt: RestorationAdaptationRecoveryReceipt,
    ) -> tuple[Any, Any, Any]:
        expected = uuid5(
            original.operation_id, f"restoration-adaptation:{original.generation_run_id}"
        )
        if (
            receipt.candidate_workspace_id != expected
            or manager.machine_runtime is None
            or receipt.request_digest != original.digest()
            or receipt.original_control_fencing_epoch != original.fencing_epoch
            or any(
                getattr(receipt, key) != getattr(original, key)
                for key in (
                    "workspace_id",
                    "generation_run_id",
                    "operation_id",
                    "project_id",
                    "owner_id",
                )
            )
        ):
            raise CellIdentityConflict("recovery candidate identity unavailable")
        state = self._require_state(
            manager, expected, original, receipt.current_candidate_fencing_epoch
        )
        machine, backend = manager.machine_runtime.parts(state)
        saved = machine.state()
        if (
            saved.get("restoration_adaptation_run_id") != str(original.generation_run_id)
            or saved.get("epoch") != receipt.current_candidate_fencing_epoch
        ):
            raise CellIdentityConflict("recovery candidate machine binding changed")
        return state, machine, backend

    @staticmethod
    def _record(
        machine: Any, receipt: RestorationAdaptationRecoveryReceipt
    ) -> dict[str, Any] | None:
        operations = machine.state().get("operations")
        if not isinstance(operations, dict):
            raise CellIdentityConflict("physical recovery journal is unknown")
        value = operations.get(str(receipt.transition_id))
        if value is None:
            return None
        if (
            not isinstance(value, dict)
            or value.get("kind") != "adaptation_recovery"
            or (
                value.get("pending") != receipt.model_dump(mode="json")
                and value.get("receipt") != receipt.model_dump(mode="json")
            )
        ):
            raise CellIdentityConflict("physical recovery operation binding changed")
        return value

    @staticmethod
    def _terminal(
        pending: RestorationAdaptationRecoveryReceipt,
        *,
        state: str,
        barrier: dict[str, Any],
        reference: MachineEnvironmentRef,
        incarnation: str,
        proof: dict[str, Any],
    ) -> RestorationAdaptationRecoveryReceipt:
        raw = pending.model_dump(mode="json")
        raw.update(
            state=state,
            barrier_receipt_digest=canonical_digest(barrier),
            checkpoint_ref_digest=canonical_digest(reference.model_dump(mode="json")),
            physical_incarnation_digest=incarnation,
            physical_proof_digest=canonical_digest(proof),
        )
        raw["terminal_receipt_digest"] = canonical_digest(
            {k: v for k, v in raw.items() if k != "terminal_receipt_digest"}
        )
        return RestorationAdaptationRecoveryReceipt.model_validate(raw)

    async def _inspect_locked(
        self,
        original: RestorationAdaptationPrepare,
        receipt: RestorationAdaptationRecoveryReceipt,
        manager: Any,
        *,
        publishing: bool = False,
    ) -> RestorationAdaptationRecoveryReceipt:
        _state, machine, backend = self._parts(manager, original, receipt)
        record = self._record(machine, receipt)
        if record is None:
            raise CellIdentityConflict("physical recovery operation is unknown")
        if record.get("receipt") is None:
            return receipt
        from yleum_orchestrator.services.adaptation_recovery_protocol import (
            AdaptationRecoveryProtocol,
        )

        result = AdaptationRecoveryProtocol._receipt(record["receipt"])
        AdaptationRecoveryProtocol._validate_result(receipt, result)
        reference = MachineEnvironmentRef.model_validate(record["reference"])
        if canonical_digest(reference.model_dump(mode="json")) != result.checkpoint_ref_digest:
            raise CellIdentityConflict("recovery checkpoint reference changed")
        identity = await machine_effect(backend.recovery_volume_identity, reference)
        if identity != result.physical_incarnation_digest:
            raise CellIdentityConflict("recovery candidate volume incarnation changed")
        if result.state == "held":
            proof = await machine_effect(
                backend._retained_preview_proof,
                reference,
                epoch=result.current_candidate_fencing_epoch,
            )
            if proof is None or canonical_digest(proof) != result.physical_proof_digest:
                raise CellIdentityConflict("held candidate quiescence changed")
            barrier = machine.state().get("adaptation_recovery_barrier")
            if (
                not isinstance(barrier, dict)
                or canonical_digest(barrier) != result.barrier_receipt_digest
            ):
                raise CellIdentityConflict("candidate writer barrier changed")
        elif result.state == "resumable":
            proof = await machine_effect(
                backend.recovery_runtime_proof,
                reference,
                epoch=result.current_candidate_fencing_epoch,
            )
            if canonical_digest(proof) != result.physical_proof_digest:
                raise CellIdentityConflict("resumed physical runtime changed")
            barrier = machine.state().get("adaptation_recovery_barrier")
            if barrier is not None:
                if canonical_digest(barrier) != result.barrier_receipt_digest:
                    raise CellIdentityConflict("resumed candidate barrier changed")
                if not publishing:
                    return RestorationAdaptationRecoveryReceipt.model_validate(record["pending"])
        _sync_directory(machine.path.parent)  # Re-seal readable post-rename terminal on restart.
        return result

    async def inspect(
        self, original: RestorationAdaptationPrepare, receipt: RestorationAdaptationRecoveryReceipt
    ) -> RestorationAdaptationRecoveryReceipt:
        manager = self._candidate_manager(original)
        async with manager.operation_lock.hold(receipt.candidate_workspace_id):
            return await self._inspect_locked(original, receipt, manager)

    async def hold(
        self,
        original: RestorationAdaptationPrepare,
        request: RestorationAdaptationRecoveryHold,
        pending: RestorationAdaptationRecoveryReceipt,
    ) -> RestorationAdaptationRecoveryReceipt:
        manager = self._candidate_manager(original)
        async with manager.operation_lock.hold(pending.candidate_workspace_id):
            state, machine, backend = self._parts(manager, original, pending)
            record = self._record(machine, pending)
            if record is not None:
                return await self._inspect_locked(original, pending, manager)
            require_source_writes_clear(manager.state_store.root, pending.candidate_workspace_id)
            saved = machine.state()
            if "adaptation_recovery_barrier" in saved:
                raise CellIdentityConflict("candidate already has a recovery barrier")
            for value in saved.get("operations", {}).values():
                if not isinstance(value, dict) or value.get("state") not in {
                    "completed",
                    "failed",
                    "timed_out",
                    "cancelled",
                }:
                    raise CellIdentityConflict("candidate command outcome is unresolved")
            barrier = {
                "version": 1,
                "transition_id": str(request.transition_id),
                "phase": "hold",
                "request": request.model_dump(mode="json"),
                "cycle": pending.cycle,
                "candidate_fencing_epoch": pending.current_candidate_fencing_epoch,
            }
            saved["adaptation_recovery_barrier"] = barrier
            saved["operations"] = {
                **saved.get("operations", {}),
                str(request.transition_id): {
                    "kind": "adaptation_recovery",
                    "state": "running",
                    "pending": pending.model_dump(mode="json"),
                    "receipt": None,
                },
            }
            save_machine_recovery_state(machine.path, saved)  # Barrier + intent BEFORE any await.
            with recovery_transition(machine.path, request.transition_id, phase="hold"):
                await manager.machine_runtime.halt(state)
            self._require_state(
                manager,
                pending.candidate_workspace_id,
                original,
                pending.current_candidate_fencing_epoch,
            )
            metadata = backend._metadata()
            reference = MachineEnvironmentRef.model_validate(metadata.get("environment_ref"))
            proof = await machine_effect(
                backend._retained_preview_proof,
                reference,
                epoch=pending.current_candidate_fencing_epoch,
            )
            if proof is None or proof != metadata.get("retained_preview_receipt"):
                return pending
            incarnation = await machine_effect(backend.recovery_volume_identity, reference)
            result = self._terminal(
                pending,
                state="held",
                barrier=barrier,
                reference=reference,
                incarnation=incarnation,
                proof=proof,
            )
            self._require_state(
                self._manager(original.workspace_id),
                original.workspace_id,
                original,
                pending.current_control_fencing_epoch,
            )
            saved = machine.state()
            if saved.get("adaptation_recovery_barrier") != barrier:
                raise CellIdentityConflict("candidate barrier changed during quiesce")
            saved["operations"][str(request.transition_id)].update(
                state="completed",
                receipt=result.model_dump(mode="json"),
                reference=reference.model_dump(mode="json"),
            )
            save_machine_recovery_state(machine.path, saved)
            return result

    async def rebind(
        self,
        original: RestorationAdaptationPrepare,
        request: RestorationAdaptationRecoveryRebind,
        pending: RestorationAdaptationRecoveryReceipt,
    ) -> RestorationAdaptationRecoveryReceipt:
        await self.current_control(original, request.new_control_fencing_epoch)
        manager = self._candidate_manager(original)
        async with manager.operation_lock.hold(pending.candidate_workspace_id):
            state = manager.state_store.load(pending.candidate_workspace_id)
            if state is None or manager.machine_runtime is None:
                raise CellIdentityConflict("held candidate is unavailable")
            machine, backend = manager.machine_runtime.parts(state)
            saved = machine.state()
            existing = saved.get("operations", {}).get(str(request.transition_id))
            if existing is not None:
                record = self._record(machine, pending)
                assert record is not None
                if record.get("receipt") is None:
                    return pending  # Unknown physical restore never executes again.
                return await self._inspect_locked(original, pending, manager, publishing=True)
            barrier = saved.get("adaptation_recovery_barrier")
            if not isinstance(barrier, dict) or barrier.get("phase") != "hold":
                raise CellIdentityConflict("candidate is not durably held")
            prior = saved.get("operations", {}).get(barrier.get("transition_id"))
            if not isinstance(prior, dict) or prior.get("receipt") is None:
                raise CellIdentityConflict("candidate hold is unresolved")
            held = RestorationAdaptationRecoveryReceipt.model_validate(prior["receipt"])
            if (
                held.state != "held"
                or held.terminal_receipt_digest != request.expected_hold_receipt_digest
                or held.cycle != request.expected_cycle
                or pending.current_candidate_fencing_epoch != state.fencing_epoch + 1
            ):
                raise CellIdentityConflict("candidate rebind fence/hold changed")
            await self._inspect_locked(original, held, manager)
            reference = MachineEnvironmentRef.model_validate(prior["reference"])
            require_source_writes_clear(manager.state_store.root, pending.candidate_workspace_id)
            barrier = {
                "version": 1,
                "transition_id": str(request.transition_id),
                "phase": "rebind",
                "request": request.model_dump(mode="json"),
                "cycle": pending.cycle,
                "candidate_fencing_epoch": pending.current_candidate_fencing_epoch,
            }
            saved["adaptation_recovery_barrier"] = barrier
            saved["operations"][str(request.transition_id)] = {
                "kind": "adaptation_recovery",
                "state": "running",
                "pending": pending.model_dump(mode="json"),
                "receipt": None,
                "reference": reference.model_dump(mode="json"),
            }
            save_machine_recovery_state(machine.path, saved)
            # Rebind only the existing lease; no capacity admission, ensure/copy, or prompt replay.
            lease = LifecycleMutation(
                uuid5(request.transition_id, "candidate-lease-rebind"),
                pending.current_candidate_fencing_epoch,
                request.request_digest,
            )
            spec = manager._spec_from_state(state, generation_run_id=original.generation_run_id)
            manager.state_store.begin(
                spec, lease, kind="ensure", phase="running", resource_names=state.resource_names
            )
            new_state = manager.state_store.complete(
                state.workspace_id,
                lease,
                bundle_state=state.bundle_state,
                provider_ref=state.provider_ref,
            )
            reservation = manager._capacity_reservation_store()
            if reservation.load(state.workspace_id) is not None:
                reservation.rebind(state.workspace_id, lease)
            _sync_directory(manager.state_store.root)
            saved = machine.state()
            saved.update(epoch=pending.current_candidate_fencing_epoch, ready_epoch=None)
            save_machine_recovery_state(machine.path, saved)
            machine, backend = manager.machine_runtime.parts(new_state)
            with recovery_transition(machine.path, request.transition_id, phase="rebind"):
                retained = await machine_effect(
                    backend.consume_retained_preview,
                    reference,
                    epoch=held.current_candidate_fencing_epoch,
                )
                if not retained:
                    raise CellIdentityConflict(
                        "retained checkpoint proof changed; no restore replay"
                    )
                if reference.manifest is None:
                    raise CellIdentityConflict("retained checkpoint manifest is missing")
                # Recreate the captured environment on its exact untouched volumes.
                # Unlike preview fallback, this cannot import/reset any candidate database/source.
                await machine_effect(
                    backend.ensure, reference.manifest, pending.current_candidate_fencing_epoch
                )
            self._require_state(
                manager, state.workspace_id, original, pending.current_candidate_fencing_epoch
            )
            incarnation = await machine_effect(backend.recovery_volume_identity, reference)
            if incarnation != held.physical_incarnation_digest:
                raise CellIdentityConflict("candidate volume changed while restoring environment")
            proof = await machine_effect(
                backend.recovery_runtime_proof,
                reference,
                epoch=pending.current_candidate_fencing_epoch,
            )
            result = self._terminal(
                pending,
                state="resumable",
                barrier=barrier,
                reference=reference,
                incarnation=incarnation,
                proof=proof,
            )
            self._require_state(
                self._manager(original.workspace_id),
                original.workspace_id,
                original,
                pending.current_control_fencing_epoch,
            )
            saved = machine.state()
            if saved.get("adaptation_recovery_barrier") != barrier:
                raise CellIdentityConflict("candidate barrier changed during restore")
            saved["operations"][str(request.transition_id)].update(
                state="completed", receipt=result.model_dump(mode="json")
            )
            save_machine_recovery_state(machine.path, saved)
            return result

    async def publish(
        self, original: RestorationAdaptationPrepare, receipt: RestorationAdaptationRecoveryReceipt
    ) -> None:
        if receipt.state != "resumable":
            raise CellIdentityConflict("only completed resume can release candidate barrier")
        await self.current_control(original, receipt.current_control_fencing_epoch)
        manager = self._candidate_manager(original)
        async with manager.operation_lock.hold(receipt.candidate_workspace_id):
            verified = await self._inspect_locked(original, receipt, manager, publishing=True)
            if verified != receipt:
                raise CellIdentityConflict("candidate resume is not terminal")
        # Do not acquire source lease lock while holding the candidate lock.
        await self.current_control(original, receipt.current_control_fencing_epoch)
        async with manager.operation_lock.hold(receipt.candidate_workspace_id):
            _state, machine, _backend = self._parts(manager, original, receipt)
            saved = machine.state()
            record = self._record(machine, receipt)
            if record is None or record.get("receipt") != receipt.model_dump(mode="json"):
                raise CellIdentityConflict("candidate resume changed before barrier release")
            barrier = saved.get("adaptation_recovery_barrier")
            if barrier is not None and canonical_digest(barrier) != receipt.barrier_receipt_digest:
                raise CellIdentityConflict("candidate barrier changed before release")
            # Atomic source controller state read, without taking its lock under candidate lock.
            self._require_state(
                self._manager(original.workspace_id),
                original.workspace_id,
                original,
                receipt.current_control_fencing_epoch,
            )
            saved.pop("adaptation_recovery_barrier", None)
            saved["ready_epoch"] = receipt.current_candidate_fencing_epoch
            save_machine_recovery_state(machine.path, saved)

    async def cleanup(
        self,
        original: RestorationAdaptationPrepare,
        receipt: RestorationAdaptationRecoveryReceipt,
        remove_candidate: Any,
    ) -> None:
        if receipt.state not in {"held", "resumable"}:
            raise CellIdentityConflict("cleanup requires a terminal current recovery cycle")
        manager = self._candidate_manager(original)
        cleanup_id = uuid5(receipt.transition_id, "terminal-candidate-cleanup")
        async with manager.operation_lock.hold(receipt.candidate_workspace_id):
            state, machine, backend = self._parts(manager, original, receipt)
            saved = machine.state()
            prior = saved.get("operations", {}).get(str(cleanup_id))
            if prior is not None:
                if not isinstance(prior, dict) or prior.get("receipt") != receipt.model_dump(
                    mode="json"
                ):
                    raise CellIdentityConflict("physical cleanup binding changed")
                if prior.get("state") == "completed":
                    _sync_directory(machine.path.parent)
                    return
                raise CellIdentityConflict("physical cleanup unknown; no destructive replay")
            if await self._inspect_locked(original, receipt, manager) != receipt:
                raise CellIdentityConflict("physical recovery is incomplete")
            require_source_writes_clear(manager.state_store.root, receipt.candidate_workspace_id)
            for value in saved.get("operations", {}).values():
                if not isinstance(value, dict) or value.get("state") not in {
                    "completed",
                    "failed",
                    "timed_out",
                    "cancelled",
                }:
                    raise CellIdentityConflict("candidate command outcome is unresolved")
            for kind in ("release", "destroy"):
                if (
                    state.operation(
                        uuid5(original.generation_run_id, f"restoration-adaptation-{kind}")
                    )
                    is not None
                ):
                    raise CellIdentityConflict("candidate already has a cleanup operation")
            barrier = {
                "version": 1,
                "transition_id": str(cleanup_id),
                "phase": "cleanup",
                "cycle": receipt.cycle,
                "receipt_digest": receipt.terminal_receipt_digest,
                "candidate_fencing_epoch": receipt.current_candidate_fencing_epoch,
            }
            saved["adaptation_recovery_barrier"] = barrier
            saved["operations"][str(cleanup_id)] = {
                "kind": "adaptation_cleanup",
                "state": "running",
                "receipt": receipt.model_dump(mode="json"),
            }
            save_machine_recovery_state(machine.path, saved)
            if receipt.state == "resumable":
                with recovery_transition(machine.path, cleanup_id, phase="cleanup"):
                    await manager.machine_runtime.halt(state)
            self._require_state(
                manager, state.workspace_id, original, receipt.current_candidate_fencing_epoch
            )
            metadata = backend._metadata()
            reference = MachineEnvironmentRef.model_validate(metadata.get("environment_ref"))
            proof = await machine_effect(
                backend._retained_preview_proof,
                reference,
                epoch=receipt.current_candidate_fencing_epoch,
            )
            incarnation = await machine_effect(backend.recovery_volume_identity, reference)
            if proof is None or incarnation != receipt.physical_incarnation_digest:
                raise CellIdentityConflict("terminal cleanup lacks exact physical quiescence")
            if machine.state().get("adaptation_recovery_barrier") != barrier:
                raise CellIdentityConflict("cleanup barrier changed before destruction")
        # Existing cleanup owns its lifecycle locks. The durable barrier forbids all
        # other callers; this exact context permits only the terminal lifecycle.
        with recovery_transition(machine.path, cleanup_id, phase="cleanup"):
            await remove_candidate(
                original, receipt.candidate_workspace_id, receipt.current_candidate_fencing_epoch
            )
        async with manager.operation_lock.hold(receipt.candidate_workspace_id):
            saved = machine.state()
            if saved.get("adaptation_recovery_barrier") != barrier:
                raise CellIdentityConflict("cleanup barrier changed during destruction")
            saved["operations"][str(cleanup_id)]["state"] = "completed"
            save_machine_recovery_state(machine.path, saved)

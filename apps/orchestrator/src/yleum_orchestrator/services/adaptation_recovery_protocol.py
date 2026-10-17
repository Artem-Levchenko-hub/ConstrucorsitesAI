"""Recovery transitions inside the existing adaptation receipt and lock.

Physical authority belongs to the candidate engine, never to a matching source
hash or this protocol's pending record. Status never dispatches an operation.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from uuid import uuid5

from yleum_orchestrator.core.cell_resources import CellIdentityConflict
from yleum_orchestrator.schemas.restoration_adaptation import (
    RestorationAdaptationCleanup,
    RestorationAdaptationPrepare,
    RestorationAdaptationRecoveryHold,
    RestorationAdaptationRecoveryIdentity,
    RestorationAdaptationRecoveryRebind,
    RestorationAdaptationRecoveryReceipt,
)
from yleum_orchestrator.services.restoration_binding import canonical_digest

if TYPE_CHECKING:
    from yleum_orchestrator.services.restoration_adaptation_workspace import (
        RestorationAdaptationWorkspaceService,
    )


class AdaptationRecoveryProtocol:
    def __init__(self, service: RestorationAdaptationWorkspaceService) -> None:
        self.service = service

    def _load(
        self, request: RestorationAdaptationRecoveryIdentity
    ) -> tuple[dict[str, Any], RestorationAdaptationPrepare]:
        saved = self.service._read(request.workspace_id, request.generation_run_id)
        if saved is None or not isinstance(saved.get("response"), dict):
            raise CellIdentityConflict("prepared adaptation is unavailable")
        original = RestorationAdaptationPrepare.model_validate(saved["request"])
        expected = {
            name: getattr(original, name)
            for name in (
                "workspace_id",
                "generation_run_id",
                "operation_id",
                "project_id",
                "owner_id",
            )
        }
        if (
            any(getattr(request, name) != value for name, value in expected.items())
            or request.request_digest != original.digest()
            or saved.get("request_digest") != original.digest()
            or str(request.candidate_workspace_id) != saved["candidate_workspace_id"]
        ):
            raise CellIdentityConflict("recovery original identity changed")
        return saved, original

    @staticmethod
    def _hold(saved: dict[str, Any]) -> dict[str, Any]:
        if "recovery_hold" not in saved:
            return {"version": 1, "cycle": 0, "current": None, "transitions": {}}
        value = saved["recovery_hold"]
        if (
            not isinstance(value, dict)
            or value.get("version") != 1
            or type(value.get("cycle")) is not int
            or value["cycle"] < 0
            or not isinstance(value.get("transitions"), dict)
        ):
            raise CellIdentityConflict("unknown recovery hold is preserved")
        return value

    @staticmethod
    def _receipt(raw: Any) -> RestorationAdaptationRecoveryReceipt:
        receipt = RestorationAdaptationRecoveryReceipt.model_validate(raw)
        if receipt.state in {
            "held",
            "resumable",
        } and receipt.terminal_receipt_digest != canonical_digest(
            receipt.model_dump(mode="json", exclude={"terminal_receipt_digest"})
        ):
            raise CellIdentityConflict("recovery terminal receipt digest changed")
        return receipt

    def _save(
        self,
        saved: dict[str, Any],
        hold: dict[str, Any],
        request: Any,
        receipt: RestorationAdaptationRecoveryReceipt,
    ) -> dict[str, Any]:
        transitions = {
            **hold["transitions"],
            str(request.transition_id): {
                "request": request.model_dump(mode="json"),
                "receipt": receipt.model_dump(mode="json"),
            },
        }
        hold = {
            **hold,
            "current": str(request.transition_id),
            "cycle": receipt.cycle,
            "transitions": transitions,
        }
        updated = {**saved, "recovery_hold": hold}
        if receipt.state == "resumable":
            # Original request/proof identity stays immutable; old proof is historical.
            updated.update(
                state="ready",
                response={
                    **saved["response"],
                    "candidate_fencing_epoch": receipt.current_candidate_fencing_epoch,
                },
            )
            updated.pop("proof_result", None)
        self.service._write(updated)
        return updated

    async def cleanup_locked(
        self, saved: dict[str, Any], request: RestorationAdaptationCleanup
    ) -> None:
        """Called with the owning source receipt lock; unknown deletion is never replayed."""
        if "recovery_hold" not in saved or request.recovery_cycle is None:
            raise CellIdentityConflict("current recovery cleanup authority is required")
        hold = self._hold(saved)
        current = hold["transitions"].get(hold.get("current"))
        if not isinstance(current, dict):
            raise CellIdentityConflict("recovery cleanup transition is unknown")
        receipt = self._receipt(current["receipt"])
        _, original = self._load(receipt)
        response = self.service._response(saved)
        if (
            receipt.state not in {"held", "resumable"}
            or saved.get("owner_run_status") != "terminal"
            or request.recovery_cycle != hold["cycle"]
            or request.recovery_cycle != receipt.cycle
            or request.recovery_receipt_digest != receipt.terminal_receipt_digest
            or request.candidate_workspace_id != receipt.candidate_workspace_id
            or request.candidate_workspace_id != response.candidate_workspace_id
            or request.candidate_fencing_epoch != receipt.current_candidate_fencing_epoch
            or request.proof_digest != response.proof_digest
        ):
            raise CellIdentityConflict("recovery cleanup current cycle/owner proof changed")
        envelope = request.model_dump(mode="json")
        prior = hold.get("cleanup")
        if prior is not None:
            if not isinstance(prior, dict) or prior.get("request") != envelope:
                raise CellIdentityConflict("recovery cleanup envelope changed")
            if prior.get("state") == "completed" and saved.get("state") == "cleaned":
                return
            raise CellIdentityConflict("recovery cleanup outcome is unknown; no redispatch")
        if saved.get("state") not in {
            "ready",
            "proof_ready",
            "migration_required",
            "source_changed",
            "proof_failed",
        }:
            raise CellIdentityConflict("candidate is in another physical transition")
        pending = {"request": envelope, "state": "pending"}
        updated = {**saved, "recovery_hold": {**hold, "cleanup": pending}}
        self.service._write(updated)  # Before any physical cleanup, including quiescence.
        await self.service.engine.recovery_cleanup(original, receipt)
        current_saved = self.service._read(request.workspace_id, request.generation_run_id)
        if current_saved != updated:
            raise CellIdentityConflict("recovery cleanup receipt changed during deletion")
        self.service._write(
            {
                **updated,
                "state": "cleaned",
                "recovery_hold": {**hold, "cleanup": {**pending, "state": "completed"}},
            }
        )

    async def status(
        self, request: RestorationAdaptationRecoveryIdentity
    ) -> RestorationAdaptationRecoveryReceipt:
        async with self.service._lock.hold(request.workspace_id):
            saved, original = self._load(request)
            hold = self._hold(saved)
            prior = hold["transitions"].get(str(request.transition_id))
            if not isinstance(prior, dict):
                raise CellIdentityConflict("unknown recovery transition")
            receipt = self._receipt(prior["receipt"])
            if any(
                getattr(receipt, name) != getattr(request, name)
                for name in RestorationAdaptationRecoveryIdentity.model_fields
            ):
                raise CellIdentityConflict("recovery status identity changed")
            # Historical cycles cannot be exported as current release authority.
            if hold["current"] != str(request.transition_id):
                raise CellIdentityConflict("recovery transition is historical")
            return await self.service.engine.recovery_inspect(original, receipt)

    async def transition(
        self, request: RestorationAdaptationRecoveryHold | RestorationAdaptationRecoveryRebind
    ) -> RestorationAdaptationRecoveryReceipt:
        is_hold = isinstance(request, RestorationAdaptationRecoveryHold)
        async with self.service._lock.hold(request.workspace_id):
            saved, original = self._load(request)
            hold = self._hold(saved)
            prior = hold["transitions"].get(str(request.transition_id))
            if prior is not None:
                if prior["request"] != request.model_dump(mode="json"):
                    raise CellIdentityConflict("recovery transition envelope changed")
                if hold["current"] != str(request.transition_id):
                    raise CellIdentityConflict("recovery transition is historical")
                receipt = self._receipt(prior["receipt"])
                if receipt.state == "resumable":
                    await self.service.engine.recovery_publish(original, receipt)
                if receipt.state in {"hold_pending", "rebinding"}:
                    if isinstance(request, RestorationAdaptationRecoveryHold):
                        inspected = await self.service.engine.recovery_hold(
                            original, request, receipt
                        )
                    else:
                        inspected = await self.service.engine.recovery_rebind(
                            original, request, receipt
                        )
                else:
                    inspected = await self.service.engine.recovery_inspect(original, receipt)
                self._validate_result(receipt, inspected)
                if inspected != receipt:
                    self._save(saved, hold, request, inspected)
                if inspected.state == "resumable":
                    await self.service.engine.recovery_publish(original, inspected)
                return inspected
            if request.expected_cycle != hold["cycle"]:
                raise CellIdentityConflict("recovery cycle changed")
            if saved.get("state") not in {
                "ready",
                "proof_ready",
                "migration_required",
                "source_changed",
                "proof_failed",
            }:
                raise CellIdentityConflict("candidate is in a physical or activation transition")
            current = (
                self._receipt(hold["transitions"][hold["current"]]["receipt"])
                if hold["current"]
                else None
            )
            if isinstance(request, RestorationAdaptationRecoveryHold):
                barrier_id = uuid5(request.transition_id, "candidate-writer-barrier")
                if current is not None and current.state != "resumable":
                    raise CellIdentityConflict("candidate is already held")
                control = request.control_fencing_epoch
                candidate = int(saved["response"]["candidate_fencing_epoch"])
                if request.candidate_fencing_epoch != candidate:
                    raise CellIdentityConflict("candidate lease changed")
                cycle = hold["cycle"]
            else:
                if (
                    current is None
                    or current.state != "held"
                    or current.terminal_receipt_digest != request.expected_hold_receipt_digest
                    or request.new_control_fencing_epoch <= current.current_control_fencing_epoch
                ):
                    raise CellIdentityConflict("rebind requires exact held receipt and fresh lease")
                await self.service.engine.recovery_inspect(original, current)
                barrier_id = current.barrier_operation_id
                control = request.new_control_fencing_epoch
                candidate = current.current_candidate_fencing_epoch + 1
                cycle = hold["cycle"] + 1
            await self.service.engine.recovery_current_control(original, control)
            pending = RestorationAdaptationRecoveryReceipt(
                **{
                    name: getattr(request, name)
                    for name in RestorationAdaptationRecoveryIdentity.model_fields
                },
                state="hold_pending" if is_hold else "rebinding",
                cycle=cycle,
                original_control_fencing_epoch=original.fencing_epoch,
                current_control_fencing_epoch=control,
                original_candidate_fencing_epoch=(
                    current.original_candidate_fencing_epoch if current else candidate
                ),
                current_candidate_fencing_epoch=candidate,
                barrier_operation_id=barrier_id,
                restore_operation_id=(
                    None if is_hold else uuid5(request.transition_id, "candidate-resume")
                ),
            )
            saved = self._save(saved, hold, request, pending)  # Durable intent before any effect.
            if isinstance(request, RestorationAdaptationRecoveryHold):
                result = await self.service.engine.recovery_hold(original, request, pending)
            else:
                result = await self.service.engine.recovery_rebind(original, request, pending)
            self._validate_result(pending, result)
            await self.service.engine.recovery_current_control(original, control)
            self._save(saved, self._hold(saved), request, result)
            if result.state == "resumable":
                await self.service.engine.recovery_publish(original, result)
            return result

    @classmethod
    def _validate_result(
        cls,
        pending: RestorationAdaptationRecoveryReceipt,
        result: RestorationAdaptationRecoveryReceipt,
    ) -> None:
        cls._receipt(result.model_dump(mode="json"))
        changed = {
            "state",
            "barrier_receipt_digest",
            "checkpoint_ref_digest",
            "physical_incarnation_digest",
            "physical_proof_digest",
            "terminal_receipt_digest",
        }
        if pending.model_dump(exclude=changed) != result.model_dump(exclude=changed):
            raise CellIdentityConflict("physical recovery result binding changed")
        allowed = {
            "hold_pending": {"hold_pending", "held"},
            "rebinding": {"rebinding", "resumable"},
            "held": {"held"},
            "resumable": {"resumable"},
        }
        if result.state not in allowed[pending.state]:
            raise CellIdentityConflict("physical recovery result state changed")

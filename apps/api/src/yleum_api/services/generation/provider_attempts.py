"""Commit generation dispatch intent before gateway HTTP, with no request bodies."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any
from uuid import UUID, uuid4, uuid5

from sqlalchemy import or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from yleum_api.core.config import get_settings
from yleum_api.core.db import get_engine
from yleum_api.models.generation_provider_attempt import GenerationProviderAttempt
from yleum_api.services.generation.provider_identity import (
    GenerationProviderIdentity,
    request_digest,
)

if TYPE_CHECKING:
    from yleum_api.models.generation_recovery_effect import GenerationRecoveryEffect
    from yleum_api.services.generation.recovery_contract import RecoveryBinding
    from yleum_api.services.generation.recovery_reconciliation import ColdWireContinuation


async def _standalone_sol_capture(
    session: AsyncSession, body: dict[str, Any], route: str, *, run_id: UUID,
    agent_state: object,
) -> bool:
    """Capture an accepted QA Sol call without enrolling full recovery acceptance.

    Engine authority comes from normal admission, then the frozen owned dispatch.
    This does not mint a recovery contract, change billing or authorize a replay.
    """
    from yleum_api.models.generation_run import GenerationRun
    from yleum_api.services.generation_engines import SOL_MODEL, selected_engine
    from yleum_api.services.generation_runs import load_generation_dispatch

    if selected_engine(agent_state) != "sol":
        return False
    run = await session.get(GenerationRun, run_id)
    if run is None or run.agent_state != agent_state:
        raise ValueError("Sol capture admission changed")
    dispatch = load_generation_dispatch(run)
    if route != "/v1/chat/completions" or body.get("model") != dispatch.force_model:
        raise ValueError("Sol capture request changed its accepted engine")
    return dispatch.model_id == SOL_MODEL


async def prepare_generation_attempt(
    body: dict[str, Any],
    route: str,
    *,
    existing: GenerationProviderIdentity | None = None,
    parent: GenerationProviderIdentity | None = None,
) -> GenerationProviderIdentity | None:
    from yleum_api.services.generation.recovery_reconciliation import (
        has_provider_effect_context,
        prepare_provider_effect,
        requires_retained_provider,
    )

    metadata = body.get("metadata")
    if not isinstance(metadata, dict) or not metadata.get("run_id"):
        if existing is not None or parent is not None or has_provider_effect_context():
            raise RuntimeError("GENERATION_ATTEMPT_UNCONFIRMED: provider dispatch stopped")
        return None  # Generic service/runtime calls are not generation retention.
    try:
        settings = get_settings()
        run_id, project_id = UUID(str(metadata["run_id"])), UUID(str(metadata["project_id"]))
        async with async_sessionmaker(get_engine(), expire_on_commit=False)() as session:
            from yleum_api.services.generation.recovery_diagnostic import (
                KEY,
                apply_diagnostic_budget,
                lock_diagnostic_namespace,
            )

            if settings.generation_provider_attempts_enabled:
                await lock_diagnostic_namespace(session, run_id)
            # No FK/row lock against the caller's project/run transaction. The
            # gateway rechecks current committed ownership when granting dispatch.
            row = (
                (
                    await session.execute(
                        text(
                            "SELECT r.user_id,r.prompt_hash,r.agent_state FROM generation_runs r "
                            "JOIN projects p ON p.id=r.project_id "
                            "WHERE r.id=:run AND r.project_id=:project "
                            "AND p.owner_id=r.user_id AND r.status='running'"
                        ),
                        {"run": run_id, "project": project_id},
                    )
                )
                .mappings()
                .first()
            )
            if (
                row is None
                or body.get("user") not in {None, str(row["user_id"])}
                or (metadata.get("user_id") not in {None, str(row["user_id"])})
            ):
                raise ValueError
            # Writer switches never erase an already persisted executable run.
            # This short MVCC read also detects V2 after local context loss; it
            # takes no caller-owned run/project row locks.
            retained = requires_retained_provider(row["agent_state"])
            sol_capture = not retained and await _standalone_sol_capture(
                session, body, route, run_id=run_id, agent_state=row["agent_state"],
            )
            unit_policy = await session.scalar(text(
                "SELECT version FROM generation_billing_policies WHERE run_id=:run"
            ), {"run": run_id})
            unit_registered = unit_policy == "accepted-units-v2"
            if unit_registered:
                hold = (await session.execute(text(
                    "SELECT h.run_id FROM generation_credit_reservations h "
                    "JOIN billing_accounts a ON a.id=h.billing_account_id "
                    "WHERE h.run_id=:run AND h.project_id=:project AND h.user_id=:user "
                    "AND a.personal_user_id=:user AND h.state='held' AND h.units>0"
                ), {"run": run_id, "project": project_id, "user": row["user_id"]})).first()
                if hold is None or not settings.generation_provider_attempts_enabled:
                    raise ValueError

            if (retained or sol_capture) and (
                not settings.generation_provider_attempts_enabled
                or not getattr(settings, "generation_response_retention_enabled", False)
            ):
                raise ValueError
            if not settings.generation_provider_attempts_enabled:
                if (existing is not None or parent is not None or has_provider_effect_context()
                        or KEY in (row["agent_state"] or {})):
                    raise ValueError
                return None
            if (not retained and not sol_capture and not unit_registered
                    and existing is None and parent is None):
                if has_provider_effect_context() or KEY in (row["agent_state"] or {}):
                    raise ValueError
                # A global transport switch is not admission into recovery.
                # Legacy and shadow runs retain their original uncaptured wire.
                return None
            diagnostic_attempt = await apply_diagnostic_budget(
                session, body, agent_state=row["agent_state"], run_id=run_id,
                project_id=project_id, user_id=row["user_id"], prompt_hash=row["prompt_hash"],
                parent_attempt_id=parent.attempt_id if parent else None,
            )
            digest = request_digest(route, body)
            for continuation in (existing, parent):
                if continuation is not None:
                    await _require_committed_identity(
                        session, continuation, run_id=run_id, project_id=project_id,
                        user_id=row["user_id"], prompt_hash=row["prompt_hash"], digest=digest,
                    )
            effect, prior_identity = await prepare_provider_effect(
                session, run_id=run_id, project_id=project_id, user_id=row["user_id"],
                prompt_hash=row["prompt_hash"], agent_state=row["agent_state"],
                digest=digest, parent=parent, existing=existing,
            )
            if existing is not None:
                if diagnostic_attempt is not None and existing.attempt_id != diagnostic_attempt:
                    raise ValueError
                if effect is not None or (
                    prior_identity is not None and prior_identity != existing
                ):
                    raise ValueError
                await session.commit()
                return existing
            if prior_identity is not None:
                if (diagnostic_attempt is not None
                        and prior_identity.attempt_id != diagnostic_attempt):
                    raise ValueError
                await session.commit()
                return prior_identity
            if effect is not None and effect.parent_effect_id is not None and parent is None:
                from yleum_api.models.generation_recovery_effect import GenerationRecoveryEffect
                previous_effect = await session.get(
                    GenerationRecoveryEffect, effect.parent_effect_id,
                )
                if previous_effect is None:
                    raise ValueError
                previous_attempt = await session.get(
                    GenerationProviderAttempt, previous_effect.provider_attempt_id,
                )
                if previous_attempt is None:
                    raise ValueError
                parent = GenerationProviderIdentity(
                    previous_attempt.run_id, previous_attempt.project_id, previous_attempt.user_id,
                    previous_attempt.attempt_id, previous_attempt.prompt_hash,
                    previous_attempt.request_sha256,
                )
            attempt_id = uuid5(effect.effect_id, "provider-attempt-v1") if effect else (
                uuid5(run_id, "unit-request-v2:" + route + ":" + digest)
                if unit_registered else uuid4()
            )
            if parent is not None:
                attempt_id = uuid5(parent.attempt_id, "definitive-refusal-retry-v1")
            if diagnostic_attempt is not None and attempt_id != diagnostic_attempt:
                raise ValueError
            identity = GenerationProviderIdentity(
                run_id,
                project_id,
                row["user_id"],
                attempt_id,
                row["prompt_hash"],
                digest,
            )
            if parent is not None:
                await require_closed_provider_parent(session, parent, identity)
            if unit_registered and effect is None:
                saved = await session.get(GenerationProviderAttempt, attempt_id)
                if saved is not None:
                    await _require_committed_identity(
                        session, identity, run_id=run_id, project_id=project_id,
                        user_id=row["user_id"], prompt_hash=row["prompt_hash"], digest=digest,
                    )
                    if saved.parent_attempt_id != (parent.attempt_id if parent else None):
                        raise ValueError
                    await session.commit()
                    return identity
            await session.execute(
                text(
                    "INSERT INTO generation_provider_attempts "
                    "(attempt_id,run_id,project_id,user_id,prompt_hash,request_sha256,dispatch_token,"
                    "parent_attempt_id) VALUES(:attempt,:run,:project,:user,"
                    ":prompt,:digest,:token,:parent)"
                ),
                {
                    "attempt": identity.attempt_id,
                    "run": run_id,
                    "project": project_id,
                    "user": identity.user_id,
                    "prompt": identity.prompt_hash,
                    "digest": digest,
                    "token": uuid4(),
                    "parent": parent.attempt_id if parent else None,
                },
            )
            if effect is not None:
                effect.provider_attempt_id = identity.attempt_id
                session.add(effect)
            await session.commit()
        return identity
    except Exception:
        # Includes an unknown commit outcome: never send HTTP on uncertainty.
        raise RuntimeError("GENERATION_ATTEMPT_UNCONFIRMED: provider dispatch stopped") from None


async def _require_committed_identity(
    session: AsyncSession, identity: GenerationProviderIdentity, *,
    run_id: UUID, project_id: UUID, user_id: UUID, prompt_hash: str, digest: str,
) -> None:
    """Task7 continuation carries SQL authority, never just a supplied UUID."""
    expected = {
        "run_id": run_id, "project_id": project_id, "user_id": user_id,
        "prompt_hash": prompt_hash, "request_sha256": digest, "version": 1,
    }
    saved = await session.get(GenerationProviderAttempt, identity.attempt_id)
    if saved is None or any(
        getattr(identity, key) != value or getattr(saved, key) != value
        for key, value in expected.items()
    ):
        raise ValueError("provider continuation has no exact committed identity")


async def native_transcript_attempt_closed(
    session: AsyncSession, attempt: GenerationProviderAttempt,
) -> bool:
    """Check the gateway's terminal proof without a financial call or receiver."""
    if (
        attempt.version != 1 or attempt.phase != "closed"
        or attempt.terminal_reason != "native_transcript_rejected"
        or any(value is not None for value in (
            attempt.provider_call_id, attempt.response_manifest_key, attempt.response_sha256,
        ))
    ):
        return False
    receiver_exists = await session.scalar(text(
        "SELECT EXISTS(SELECT 1 FROM generation_responses WHERE attempt_id=:attempt)"
    ), {"attempt": attempt.attempt_id})
    return receiver_exists is False


@dataclass(frozen=True, slots=True)
class _ExpiredProviderFrame:
    root: GenerationRecoveryEffect
    wire: GenerationRecoveryEffect
    source: RecoveryBinding
    deadline_wire: str

    def receipt(self, attempt: GenerationProviderAttempt) -> dict[str, object]:
        return {
            "version": 1, "kind": "gateway_expired_before_dispatch",
            "attempt_id": str(attempt.attempt_id), "wire_effect_id": str(self.wire.effect_id),
            "root_effect_id": str(self.root.effect_id), "request_sha256": attempt.request_sha256,
            "source_sha256": self.source.source_sha256, "deadline": self.deadline_wire,
        }


async def _closed_role_binding(
    session: AsyncSession, row: GenerationRecoveryEffect, root: GenerationRecoveryEffect,
    previous: RecoveryBinding, phase: str, dimension_name: str,
) -> RecoveryBinding | None:
    from yleum_api.models.project_cell import (
        ProjectCellActivityLease,
        ProjectCellProof,
        ProjectCellProofResult,
    )
    from yleum_api.services.generation.recovery_actions import recovery_binding
    from yleum_api.services.generation.recovery_tool_receipts import CommandBinding, digest
    from yleum_api.services.orchestrator_client import (
        OrchestratorUnavailable,
        ProjectCellCommandReceipt,
    )
    from yleum_api.services.project_cell_proofs import ProofDimension, proof_identity_from_model

    operation_id = row.operation_id
    call = f"role:{operation_id}"
    role_root = uuid5(root.run_id, f"recovery-tool-v1:{root.logical_step_seq}:{call}")
    if (
        operation_id is None or row.tool_call_id != call
        or row.effect_id != uuid5(role_root, "physical:0")
        or (row.run_id, row.project_id, row.user_id, row.logical_step_seq)
        != (root.run_id, root.project_id, root.user_id, root.logical_step_seq)
        or row.kind != "tool" or row.state != "confirmed" or row.payload_version != 1
        or row.parent_effect_id is not None or row.provider_attempt_id is not None
        or row.attempt_seq < root.attempt_seq or row.checkpoint_ref is not None
    ):
        return None
    try:
        intent = CommandBinding.model_validate_json(json.dumps(row.pre_binding, allow_nan=False))
        receipt = ProjectCellCommandReceipt.from_json(row.receipt_ref)
        receipt_digest = digest(row.receipt_ref)
    except (OrchestratorUnavailable, TypeError, ValueError):
        return None
    role_binding, activity = intent.context_binding, intent.activity
    if (
        role_binding is None or activity is None or activity.operation_id != operation_id
        or activity.phase != phase or row.receipt_sha256 != receipt_digest
        or role_binding.workspace_id != previous.workspace_id
        or str(intent.workspace_id) != role_binding.workspace_id
        or intent.fencing_epoch != role_binding.fencing_epoch
        or role_binding.fencing_epoch != previous.fencing_epoch + 2
        or (role_binding.source_sha256, role_binding.schema_sha256)
        != (previous.source_sha256, previous.schema_sha256)
        or receipt.operation_id != operation_id
        or (receipt.workspace_id, receipt.project_id, receipt.owner_id, receipt.generation_run_id)
        != (intent.workspace_id, root.project_id, root.user_id, root.run_id)
        or receipt.dispatch_fencing_epoch != intent.fencing_epoch
        or receipt.request_sha256 != intent.request_sha256
        or any(revision != role_binding.source_sha256 for revision in (
            intent.workspace_revision, receipt.before_revision, receipt.after_revision,
        ))
        or row.post_binding != {
            "workspace_revision": receipt.after_revision,
            "physical_volume_identity": receipt.physical_volume_identity,
        }
    ):
        return None
    lease = await session.get(ProjectCellActivityLease, operation_id)
    if (
        lease is None or lease.kind != "command" or lease.state != "completed"
        or lease.finished_at is None or lease.phase != phase
        or (lease.workspace_id, lease.generation_run_id, lease.fencing_epoch)
        != (intent.workspace_id, root.run_id, intent.fencing_epoch)
        or lease.proof_key != activity.proof_key
    ):
        return None
    results = (await session.execute(
        select(ProjectCellProofResult, ProjectCellProof)
        .join(ProjectCellProof, ProjectCellProofResult.proof_id == ProjectCellProof.id)
        .where(ProjectCellProofResult.operation_id == operation_id).limit(2)
    )).all()
    if len(results) != 1:
        return None
    result, proof = results[0]
    if (
        result.workspace_id != intent.workspace_id or result.dimension != dimension_name
        or result.outcome != "green"
        or (proof.workspace_id, proof.generation_run_id, proof.fencing_epoch)
        != (intent.workspace_id, root.run_id, intent.fencing_epoch)
        or proof.workspace_revision != role_binding.source_sha256
        or result.detail_digest != hashlib.sha256(result.redacted_detail.encode()).hexdigest()
    ):
        return None
    prior_proof = await session.scalar(select(ProjectCellProof).where(
        ProjectCellProof.workspace_id == intent.workspace_id,
        ProjectCellProof.fencing_epoch == intent.fencing_epoch,
        ProjectCellProof.proof_key == lease.proof_key,
    ))
    if prior_proof is None or prior_proof.generation_run_id != root.run_id:
        return None
    try:
        identity = proof_identity_from_model(proof)
        prior_identity = proof_identity_from_model(prior_proof)
        if (
            identity.proof_key != proof.proof_key or prior_identity.proof_key != lease.proof_key
            or identity.dimension_key(ProofDimension(dimension_name)) != result.dimension_key
            or recovery_binding(identity) != role_binding
            or recovery_binding(prior_identity) != role_binding
        ):
            return None
    except ValueError:
        return None
    return role_binding


async def _expired_provider_frame(
    session: AsyncSession, attempt: GenerationProviderAttempt, *,
    current_binding: RecoveryBinding | None = None, max_attempt_seq: int | None = None,
) -> _ExpiredProviderFrame | None:
    """Check the original provider frame separately from a later retired execution lease."""
    from yleum_api.models.generation_recovery_effect import GenerationRecoveryEffect
    from yleum_api.models.project_cell import ProjectCellOperation
    from yleum_api.services.generation.recovery_contract import RecoveryBinding
    from yleum_api.services.generation.recovery_execution import _owned_release_receipt
    from yleum_api.services.generation.recovery_reconciliation import (
        _dispatch_plan,
        provider_root_effect_id,
    )

    if attempt.version != 1 or any(value is not None for value in (
        attempt.provider_call_id, attempt.response_manifest_key, attempt.response_sha256,
        attempt.parent_attempt_id,
    )):
        return None
    effect = GenerationRecoveryEffect
    root = await session.scalar(
        select(effect).where(effect.provider_attempt_id == attempt.attempt_id),
    )
    if root is None or type(root.pre_binding) is not dict or root.logical_step_seq < 1:
        return None
    root_id = provider_root_effect_id(attempt.run_id, root.logical_step_seq)
    wire_id = uuid5(root_id, "wire-v1:0:0")
    if root.effect_id != root_id or attempt.attempt_id != uuid5(root_id, "provider-attempt-v1"):
        return None
    try:
        historical = RecoveryBinding.model_validate_json(
            json.dumps(root.pre_binding["source"], allow_nan=False),
        )
        plan = _dispatch_plan(root.pre_binding)
        if plan.kind != "native" or plan.outer_attempt != 0:
            return None
    except (KeyError, TypeError, ValueError):
        return None
    rows = (await session.scalars(select(effect).where(or_(
        (effect.run_id == attempt.run_id) & (effect.logical_step_seq == root.logical_step_seq),
        effect.parent_effect_id.in_((root_id, wire_id)),
    )).order_by(effect.effect_ordinal).limit(5))).all()
    if len(rows) not in {2, 4} or [row.effect_id for row in rows[:2]] != [root_id, wire_id]:
        return None
    wire = rows[1]
    expected_wire = {"dispatch_v1": {
        **root.pre_binding["dispatch_v1"], "root_effect_id": str(root_id),
        "provider_effect_id": str(root_id), "attempt_id": str(attempt.attempt_id),
        "parent_attempt_id": None, "request_sha256": attempt.request_sha256, "wire_attempt": 0,
    }}
    if (
        root.kind != "provider" or root.effect_ordinal != 0 or root.parent_effect_id is not None
        or wire.kind != "progress" or wire.effect_ordinal != 1 or wire.parent_effect_id != root_id
        or wire.pre_binding != expected_wire or wire.provider_attempt_id is not None
        or (wire.attempt_seq, wire.claim_token) != (root.attempt_seq, root.claim_token)
        or any(
            row.payload_version != 1 or row.logical_step_seq != root.logical_step_seq
            or (row.run_id, row.project_id, row.user_id, row.input_digest)
            != (attempt.run_id, attempt.project_id, attempt.user_id, attempt.request_sha256)
            or row.post_binding is not None or row.operation_id is not None
            or row.tool_call_id is not None or row.receipt_sha256 is not None
            or row.checkpoint_ref is not None
            for row in rows[:2]
        )
        or (max_attempt_seq is not None and any(row.attempt_seq > max_attempt_seq for row in rows))
    ):
        return None
    if len(rows) == 4:
        preparation, fast_check = rows[2:]
        if (
            (preparation.effect_ordinal, fast_check.effect_ordinal) != (2, 3)
            or await session.scalar(select(
                select(effect.effect_id).where(
                    effect.parent_effect_id.in_((preparation.effect_id, fast_check.effect_id)),
                ).exists(),
            )) is not False
        ):
            return None
        role_binding = await _closed_role_binding(session, preparation, root, historical,
                                                  "prepare", "bootstrap")
        if role_binding is None or role_binding != await _closed_role_binding(
            session, fast_check, root, historical, "fast_check", "fast_check",
        ) or (current_binding is not None and current_binding != role_binding):
            return None
        workspace_id = UUID(role_binding.workspace_id)
        releases = (await session.scalars(select(ProjectCellOperation).where(
            ProjectCellOperation.workspace_id == workspace_id,
            ProjectCellOperation.generation_run_id == attempt.run_id,
            ProjectCellOperation.kind == "release",
            ProjectCellOperation.fencing_epoch == historical.fencing_epoch + 1,
        ).limit(2))).all()
        if len(releases) != 1 or not _owned_release_receipt(
            releases[0], run_id=attempt.run_id, workspace_id=workspace_id,
            fence=historical.fencing_epoch + 1,
        ):
            return None
    elif current_binding is not None and (
        historical.fencing_epoch > current_binding.fencing_epoch
        or historical.model_copy(update={"fencing_epoch": current_binding.fencing_epoch})
        != current_binding
    ):
        return None
    if await session.scalar(text(
        "SELECT EXISTS(SELECT 1 FROM generation_responses WHERE attempt_id=:attempt) OR "
        "EXISTS(SELECT 1 FROM generation_provider_attempts WHERE parent_attempt_id=:attempt) OR "
        "(:current AND EXISTS(SELECT 1 FROM provider_calls "
        "WHERE run_id=:run AND created_at>=:created)) OR clock_timestamp() < :deadline"
    ), {
        "attempt": attempt.attempt_id, "run": attempt.run_id, "created": attempt.created_at,
        "deadline": plan.deadline, "current": current_binding is not None,
    }) is not False:
        return None
    return _ExpiredProviderFrame(
        root, wire, historical, root.pre_binding["dispatch_v1"]["deadline"],
    )


def _matches_expiry_receipt(
    attempt: GenerationProviderAttempt, frame: _ExpiredProviderFrame,
) -> bool:
    receipt = frame.receipt(attempt)
    return (
        attempt.phase == "closed" and attempt.terminal_reason == "expired_before_dispatch"
        and all(row.state == "confirmed" and row.receipt_ref == receipt
                for row in (frame.root, frame.wire))
    )


async def expired_reservation_closed(
    session: AsyncSession, attempt: GenerationProviderAttempt, *,
    held_binding: RecoveryBinding | None = None, max_attempt_seq: int | None = None,
) -> bool:
    """Recognize the Gateway seal, optionally with exact held-draft authority."""
    if attempt.phase != "closed" or attempt.terminal_reason != "expired_before_dispatch":
        return False
    frame = await _expired_provider_frame(
        session, attempt, current_binding=held_binding, max_attempt_seq=max_attempt_seq,
    )
    return frame is not None and _matches_expiry_receipt(attempt, frame)


async def require_native_transcript_refusal(identity: GenerationProviderIdentity) -> None:
    """Reload the exact affirmative refusal before allowing changed request bytes."""
    from yleum_api.models.generation_run import GenerationRun
    from yleum_api.models.project import Project

    async with async_sessionmaker(get_engine(), expire_on_commit=False)() as session:
        run = await session.get(GenerationRun, identity.run_id)
        project = await session.get(Project, identity.project_id)
        attempt = await session.get(GenerationProviderAttempt, identity.attempt_id)
        if (
            run is None or run.status != "running" or project is None or attempt is None
            or (run.project_id, run.user_id, run.prompt_hash, project.owner_id)
            != (identity.project_id, identity.user_id, identity.prompt_hash, identity.user_id)
            or (attempt.run_id, attempt.project_id, attempt.user_id, attempt.prompt_hash,
                attempt.request_sha256)
            != (identity.run_id, identity.project_id, identity.user_id, identity.prompt_hash,
                identity.request_sha256)
            or not await native_transcript_attempt_closed(session, attempt)
        ):
            raise ValueError("native transcript refusal is not confirmed for the owned attempt")


async def require_closed_provider_parent(
    session: AsyncSession, parent: GenerationProviderIdentity, identity: GenerationProviderIdentity,
) -> bool:
    """Validate an affirmative receipt and report whether its ancestry used overload failover."""
    prior = (
        (await session.execute(
            text(
                "SELECT a.*,c.provider_scope, EXISTS (WITH RECURSIVE ancestors AS ("
                "SELECT attempt_id,parent_attempt_id,terminal_reason "
                "FROM generation_provider_attempts "
                "WHERE attempt_id=a.parent_attempt_id UNION "
                "SELECT p.attempt_id,p.parent_attempt_id,p.terminal_reason "
                "FROM generation_provider_attempts p "
                "JOIN ancestors x ON p.attempt_id=x.parent_attempt_id) "
                "SELECT 1 FROM ancestors WHERE terminal_reason='upstream_refused') "
                "AS prior_overload "
                "FROM generation_provider_attempts a "
                "JOIN provider_calls c "
                "ON c.id=a.provider_call_id WHERE a.attempt_id=:id "
                "AND a.phase='closed' "
                "AND a.terminal_reason IN ('preconnect','rate_limit_refused','upstream_refused') "
                "AND c.status='failed' AND c.receipt_hash IS NOT NULL"
            ), {"id": parent.attempt_id},
        )).mappings().first()
    )
    if (prior is not None and prior.get("terminal_reason") == "upstream_refused"
            and (prior.get("provider_scope") != "llmgw"
                 or prior.get("prior_overload") is not False)):
        raise ValueError("provider fallback has no first primary refusal")
    if prior is None or any(
        prior[key] != getattr(identity, key)
        for key in ("run_id", "project_id", "user_id", "prompt_hash", "request_sha256")
    ):
        raise ValueError("provider parent has no affirmative committed receipt")
    return prior["terminal_reason"] == "upstream_refused" or prior["prior_overload"] is True


def attach_identity(body: dict[str, Any], identity: GenerationProviderIdentity | None) -> None:
    if identity is not None:
        body["metadata"]["generation_provider_identity"] = identity.to_wire()


async def reserve_generation_wire(
    identity: GenerationProviderIdentity | None, *, wire_attempt: int,
) -> UUID | None:
    from yleum_api.services.generation.recovery_reconciliation import (
        has_provider_effect_context,
        reserve_provider_wire,
    )
    if not has_provider_effect_context():
        return None
    return await reserve_provider_wire(
        async_sessionmaker(get_engine(), expire_on_commit=False), identity,
        wire_attempt=wire_attempt,
    )


async def confirm_generation_preconnect(
    effect_id: UUID | None, identity: GenerationProviderIdentity | None,
) -> None:
    if effect_id is None:
        return
    from yleum_api.services.generation.recovery_reconciliation import record_wire_observation
    await record_wire_observation(
        async_sessionmaker(get_engine(), expire_on_commit=False), effect_id, identity,
        outcome="preconnect",
    )


async def resume_generation_wire(
    identity: GenerationProviderIdentity | None, *,
    allow_provider_refusal: bool, allow_reserved_child: bool = False,
) -> ColdWireContinuation | None:
    from yleum_api.services.generation.recovery_reconciliation import (
        authorize_cold_wire_continuation,
        has_provider_effect_context,
    )
    if not has_provider_effect_context():
        return None
    return await authorize_cold_wire_continuation(
        async_sessionmaker(get_engine(), expire_on_commit=False), identity,
        allow_provider_refusal=allow_provider_refusal,
        allow_reserved_child=allow_reserved_child,
    )


async def mark_generation_wire_outward(
    effect_id: UUID | None, identity: GenerationProviderIdentity | None,
) -> None:
    if effect_id is None:
        return
    from yleum_api.services.generation.recovery_reconciliation import record_wire_observation
    await record_wire_observation(
        async_sessionmaker(get_engine(), expire_on_commit=False), effect_id, identity,
        outcome="outward",
    )

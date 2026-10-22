"""Owner-only, read-only presentation of one coherent committed SQL snapshot."""

from __future__ import annotations

import re
from typing import Any, cast
from uuid import UUID

from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from yleum_api.core.errors import ApiError
from yleum_api.models.generation_billing import GenerationBillingPolicy
from yleum_api.models.generation_event import GenerationEvent
from yleum_api.models.generation_recovery_effect import GenerationRecoveryEffect
from yleum_api.models.generation_run import GenerationRun
from yleum_api.models.message import Message
from yleum_api.models.project import Project
from yleum_api.models.snapshot import Snapshot
from yleum_api.schemas.message import (
    GenerationAcceptancePublic,
    GenerationRecoveryPublic,
    GenerationRunPublic,
)
from yleum_api.services.generation.recovery_contract import RecoveryStateV2
from yleum_api.services.generation.recovery_execution import recovery_state
from yleum_api.services.generation_billing import POLICY_VERSION
from yleum_api.services.generation_failure import public_generation_failure
from yleum_api.services.promotion_permit import (
    MAX_FULL_BUILD_CONTRACT_VERSION,
    MAX_RELEASE_PROOF_CONTRACT_VERSION,
)


class _LoadedRows:
    """Only the validator's read surface, backed by rows from ONE SELECT.

    This is presentation evidence, never an execution session/permit. A missing
    row cannot trigger another database read and mix publication generations.
    """

    def __init__(
        self,
        message: Message | None,
        snapshot: Snapshot | None,
        effects: list[GenerationRecoveryEffect],
    ):
        self.rows: dict[tuple[type[Any], UUID], Any] = {
            (GenerationRecoveryEffect, row.effect_id): row for row in effects
        }
        if message is not None:
            self.rows[Message, message.id] = message
        if snapshot is not None:
            self.rows[Snapshot, snapshot.id] = snapshot

    async def get(self, model: type[Any], key: UUID) -> Any:
        return self.rows.get((model, key))


def _legacy_accepted(
    run: GenerationRun,
    project: Project,
    message: Message | None,
    snapshot: Snapshot | None,
    policy: GenerationBillingPolicy | None,
) -> bool:
    """Confirm a permit-backed build or edit, independently of its billing outcome."""
    state = run.agent_state if isinstance(run.agent_state, dict) else {}
    marker = state.get("billing_accepted_candidate")
    return bool(
        run.response_mode in {"build", "edit"}
        and policy is not None
        and policy.run_id == run.id
        and policy.version == POLICY_VERSION
        and isinstance(marker, dict)
        and set(marker)
        == {
            "snapshot_id",
            "commit_sha",
            "permit_digest",
            "proof_key",
            "artifact_digest",
            "build_contract",
            "release_contract",
        }
        and snapshot is not None
        and snapshot.project_id == project.id
        and project.current_snapshot_id == snapshot.id
        and message is not None
        and message.snapshot_id == snapshot.id
        and marker["snapshot_id"] == str(snapshot.id)
        and marker["commit_sha"] == snapshot.commit_sha
        and re.fullmatch(r"[a-f0-9]{40}", snapshot.commit_sha)
        and state.get("snapshot_id") == str(snapshot.id)
        and state.get("commit_sha") == snapshot.commit_sha
        and marker["build_contract"] == MAX_FULL_BUILD_CONTRACT_VERSION
        and marker["release_contract"] == MAX_RELEASE_PROOF_CONTRACT_VERSION
        and all(
            isinstance(marker[k], str) and re.fullmatch(r"[a-f0-9]{64}", marker[k])
            for k in ("permit_digest", "proof_key", "artifact_digest")
        )
    )


async def project_loaded(
    *,
    project: Project,
    run: GenerationRun,
    message: Message | None,
    snapshot: Snapshot | None,
    policy: GenerationBillingPolicy | None,
    effects: list[GenerationRecoveryEffect],
    user_id: UUID,
    latest_step: dict[str, Any] | None = None,
) -> GenerationRunPublic:
    if (
        project.owner_id != user_id
        or run.project_id != project.id
        or run.user_id != user_id
        or (
            run.assistant_message_id is not None
            and (
                message is None
                or message.id != run.assistant_message_id
                or message.project_id != project.id
                or message.role != "assistant"
            )
        )
        or (run.assistant_message_id is None and message is not None)
    ):
        raise ApiError("not_found", "generation not found", 404)
    view = GenerationRunPublic.model_validate(run)
    view.failure = public_generation_failure(
        run, agent_steps=[latest_step] if latest_step is not None else (
            message.agent_steps if message is not None else None
        ),
    )
    acceptance = GenerationAcceptancePublic(
        run_id=run.id, assistant_message_id=run.assistant_message_id
    )
    view.acceptance = acceptance
    if run.agent_state is not None and not isinstance(run.agent_state, dict):
        view.recovery_unavailable = True
        return view
    try:
        state = recovery_state(
            run
        )  # Owning strict reader; supports future versions only explicitly.
    except (ValueError, TypeError):
        view.recovery_unavailable = True
        return view
    if state is not None:
        reason = {
            "waiting_dependency": "dependency_unavailable",
            "reconciling": "operation_pending",
            "needs_input": "progress_required",
        }.get(state.phase)
        view.recovery = GenerationRecoveryPublic(
            phase=state.phase,
            reason=reason,
            next_check_at=state.next_check_at,
            attempt_seq=state.attempt_seq,
        )
    confirmed = False
    published_snapshot: UUID | None = None
    if isinstance(state, RecoveryStateV2):
        from yleum_api.services.generation.agent_publication import (
            PublicationReceipt,
            _publication_id,
            publication_committed,
        )

        lookup = _LoadedRows(message, snapshot, effects)
        try:
            confirmed = await publication_committed(
                cast(AsyncSession, lookup), run=run, project=project, state=state
            )
            if confirmed:
                row = await lookup.get(GenerationRecoveryEffect, _publication_id(run.id))
                import json

                published_snapshot = PublicationReceipt.model_validate_json(
                    json.dumps(row.receipt_ref), strict=True
                ).snapshot_id
        except (ValueError, TypeError, AttributeError):
            view.recovery_unavailable = True
    else:
        confirmed = _legacy_accepted(run, project, message, snapshot, policy)
        if confirmed and snapshot is not None:
            published_snapshot = snapshot.id
    if confirmed and view.recovery is not None:
        view.recovery.last_verified_action = (
            "publication_confirmed" if isinstance(state, RecoveryStateV2) else "snapshot_saved"
        )
    if confirmed and run.status == "completed" and run.finished_at is not None:
        acceptance.current_request_complete = True
        acceptance.snapshot_id = published_snapshot
    return view


async def project_generation_public(
    session: AsyncSession, *, project_id: UUID, user_id: UUID, run_id: UUID | None = None
) -> GenerationRunPublic | None:
    target = select(GenerationRun.id).where(
        GenerationRun.project_id == project_id, GenerationRun.user_id == user_id
    )
    if run_id is not None:
        target = target.where(GenerationRun.id == run_id)
    target_id = (
        target.order_by(GenerationRun.created_at.desc(), GenerationRun.id.desc())
        .limit(1)
        .correlate(None)
        .scalar_subquery()
    )
    # The UI transcript intentionally stops after 200 steps. Operational
    # diagnostics must follow the uncapped durable journal in the SAME snapshot.
    latest_step = (
        select(GenerationEvent.payload)
        .where(
            GenerationEvent.generation_run_id == GenerationRun.id,
            GenerationEvent.project_id == project_id,
            GenerationEvent.message_id == GenerationRun.assistant_message_id,
            GenerationEvent.event_type == "agent.step",
        )
        .order_by(GenerationEvent.seq.desc())
        .limit(1)
        .correlate(GenerationRun)
        .scalar_subquery()
    )
    query = (
        select(
            Project,
            GenerationRun,
            Message,
            Snapshot,
            GenerationBillingPolicy,
            GenerationRecoveryEffect,
            latest_step,
        )
        .select_from(Project)
        .outerjoin(GenerationRun, GenerationRun.id == target_id)
        .outerjoin(Message, Message.id == GenerationRun.assistant_message_id)
        .outerjoin(Snapshot, Snapshot.id == Project.current_snapshot_id)
        .outerjoin(GenerationBillingPolicy, GenerationBillingPolicy.run_id == GenerationRun.id)
        .outerjoin(
            GenerationRecoveryEffect,
            and_(
                GenerationRecoveryEffect.run_id == GenerationRun.id,
                GenerationRecoveryEffect.kind == "proof",
                GenerationRecoveryEffect.pre_binding["kind"].astext.in_(
                    ("publication_v1", "publication_v2")
                ),
            ),
        )
        .where(Project.id == project_id, Project.owner_id == user_id)
        .execution_options(populate_existing=True, autoflush=False)
    )
    rows = (await session.execute(query)).all()
    if not rows:
        raise ApiError("not_found", "project not found", 404)
    project, run, message, snapshot, policy, _, step = rows[0]
    if run is None:
        if run_id is not None:
            raise ApiError("not_found", "generation not found", 404)
        return None
    return await project_loaded(
        project=project,
        run=run,
        message=message,
        snapshot=snapshot,
        policy=policy,
        effects=[row[5] for row in rows if row[5] is not None],
        user_id=user_id,
        latest_step=step if isinstance(step, dict) else None,
    )

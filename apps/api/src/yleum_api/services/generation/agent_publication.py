from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from collections.abc import Awaitable, Callable
from typing import Any, Literal
from uuid import UUID, uuid5

from pydantic import BaseModel, ConfigDict
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from yleum_api.core.config import get_settings
from yleum_api.core.redis import publish_event
from yleum_api.models.generation_recovery_effect import GenerationRecoveryEffect
from yleum_api.models.generation_run import GenerationRun
from yleum_api.models.message import Message
from yleum_api.models.project import Project
from yleum_api.models.snapshot import Snapshot
from yleum_api.services import repo as repo_svc
from yleum_api.services.generation.contracts import (
    GenerationIds,
    GenerationRuntime,
    ProjectGenerationFacts,
    SourceBaseline,
)
from yleum_api.services.generation.progress import GenerationProgress
from yleum_api.services.generation.publication import _snapshot_payload
from yleum_api.services.generation.recovery_contract import (
    RecoveryBinding,
    RecoveryContractError,
    RecoveryIdentity,
    RecoveryStateV2,
    Sha256,
)
from yleum_api.services.generation.recovery_execution import (
    ExecutionDriver,
    _require_session_owner,
    recovery_state,
)
from yleum_api.services.generation.recovery_store import _locked_owner
from yleum_api.services.generation_artifacts import create_generation_snapshot
from yleum_api.services.generation_deployment_drain import require_generation_admission
from yleum_api.services.max_finalization import ProofBundle
from yleum_api.services.promotion_permit import (
    PromotionPermit,
    PromotionPermitError,
    canonical_files_digest,
    require_promotion_permit,
)

_log = logging.getLogger("yleum_api.routers.messages")


class PublicationBinding(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)
    kind: Literal["publication_v1"] = "publication_v1"
    identity: RecoveryIdentity
    source: RecoveryBinding
    baseline_sha: str | None
    assistant_message_id: UUID
    model_id: str
    prompt_sha256: Sha256


class PublicationReceipt(BaseModel):
    """A committed SQL publication outcome, never a reusable promotion permit."""

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)
    input_digest: Sha256
    snapshot_id: UUID | None
    commit_sha: str | None
    assistant_message_id: UUID


def _publication_id(run_id: UUID) -> UUID:
    return uuid5(run_id, "generation-publication-v1")


def _digest(value: object) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        ).encode()
    ).hexdigest()


def has_publication_intent(state: RecoveryStateV2) -> bool:
    return (
        state.execution.stage == "finalization"
        and state.execution.substep in {"publication_pending", "publication_committed"}
    )


async def publication_committed(
    session: AsyncSession,
    *,
    run: GenerationRun,
    project: Project,
    state: RecoveryStateV2,
) -> bool:
    """Read the exact transaction receipt before checking the pre-publication baseline."""
    if not has_publication_intent(state):
        return False
    if state.execution.pending_effect_id != _publication_id(run.id):
        raise RecoveryContractError("publication operation identity changed")
    row = await session.get(GenerationRecoveryEffect, _publication_id(run.id))
    if (
        row is None
        or row.run_id != run.id
        or row.project_id != project.id
        or row.user_id != run.user_id
        or row.kind != "proof"
        or row.payload_version != 1
        or row.logical_step_seq != state.execution.logical_step_seq
        or type(row.pre_binding) is not dict
        or row.pre_binding.get("kind") != "publication_v1"
        or row.input_digest != _digest(row.pre_binding)
        or row.pre_binding.get("identity") != state.identity.model_dump(mode="json")
        or run.prompt_hash != state.identity.prompt_hash
    ):
        raise RecoveryContractError("publication intent binding changed")
    try:
        bound = PublicationBinding.model_validate_json(json.dumps(row.pre_binding), strict=True)
    except ValueError as exc:
        raise RecoveryContractError("publication binding is corrupt") from exc
    if bound.identity != state.identity or bound.source != state.binding:
        raise RecoveryContractError("publication retained source changed")
    if (
        row.state == "inflight"
        and row.receipt_ref is None
        and row.receipt_sha256 is None
        and state.execution.substep == "publication_pending"
    ):
        return False  # Missing receipt is never permission to repeat the Git writer.
    if (
        row.state != "confirmed"
        or row.receipt_sha256 != _digest(row.receipt_ref)
        or state.execution.substep != "publication_committed"
    ):
        raise RecoveryContractError("publication receipt is incomplete")
    try:
        receipt = PublicationReceipt.model_validate_json(json.dumps(row.receipt_ref), strict=True)
    except ValueError as exc:
        raise RecoveryContractError("publication receipt is corrupt") from exc
    if (
        receipt.input_digest != row.input_digest
        or receipt.assistant_message_id != run.assistant_message_id
        or row.pre_binding.get("assistant_message_id") != str(receipt.assistant_message_id)
    ):
        raise RecoveryContractError("publication receipt owner changed")
    message = await session.get(Message, receipt.assistant_message_id)
    if message is None or message.project_id != project.id:
        raise RecoveryContractError("publication assistant disappeared")
    if receipt.snapshot_id is None:
        if (
            receipt.commit_sha is not None
            or project.current_snapshot_id != state.identity.accepted_snapshot_id
        ):
            raise RecoveryContractError("empty publication baseline changed")
    else:
        snapshot = await session.get(Snapshot, receipt.snapshot_id)
        if (
            snapshot is None
            or snapshot.project_id != project.id
            or snapshot.commit_sha != receipt.commit_sha
            or snapshot.parent_id != state.identity.accepted_snapshot_id
            or project.current_snapshot_id != snapshot.id
            or message.snapshot_id != snapshot.id
            or (run.agent_state or {}).get("snapshot_id") != str(snapshot.id)
            or (run.agent_state or {}).get("commit_sha") != snapshot.commit_sha
        ):
            raise RecoveryContractError("publication snapshot ownership changed")
    return True


async def committed_publication(
    factory: async_sessionmaker[AsyncSession],
    state: RecoveryStateV2,
) -> bool:
    await _require_session_owner(state.identity.run_id)
    async with factory() as session:
        run, project = await _locked_owner(
            session,
            run_id=state.identity.run_id,
            project_id=state.identity.project_id,
            user_id=state.identity.user_id,
        )
        if recovery_state(run) != state:
            raise RecoveryContractError("publication continuation changed")
        result = await publication_committed(session, run=run, project=project, state=state)
        await _require_session_owner(run.id)
        return result


async def _publication_owner(
    session: AsyncSession,
    driver: ExecutionDriver,
) -> tuple[GenerationRun, Project]:
    state = driver.state
    await _require_session_owner(state.identity.run_id)
    await require_generation_admission(session)
    run, project = await _locked_owner(
        session,
        run_id=state.identity.run_id,
        project_id=state.identity.project_id,
        user_id=state.identity.user_id,
    )
    if (
        recovery_state(run) != state
        or state.continuation_claim is None
        or state.phase != "working"
        or state.execution.stage != "finalization"
        or state.identity.prompt_hash != run.prompt_hash
        or project.current_snapshot_id != state.identity.accepted_snapshot_id
    ):
        raise RecoveryContractError("publication current claim or baseline changed")
    from yleum_api.services.generation.recovery_store import _validate_binding

    if state.binding is None:
        raise RecoveryContractError("publication source binding is missing")
    await _validate_binding(session, run=run, binding=state.binding)
    return run, project


async def _begin_publication(
    driver: ExecutionDriver,
    *,
    baseline: SourceBaseline,
    files: dict[str, str],
    ids: GenerationIds,
    model_id: str,
    prompt_text: str,
) -> None:
    from yleum_api.services.generation.recovery_reconciliation import (
        _lock_effect_namespace,
        _next_effect_ordinal,
    )
    from yleum_api.services.promotion_permit import workspace_revision_digest

    state = driver.state
    if (
        state.binding is None
        or (files and state.binding.source_sha256 != workspace_revision_digest(files))
        or state.identity.run_id != ids.run_id
        or state.identity.project_id != ids.project_id
        or state.identity.user_id != ids.user_id
        or baseline.snapshot_id != state.identity.accepted_snapshot_id
    ):
        raise RecoveryContractError("publication source or original scope changed")
    binding = PublicationBinding(
        identity=state.identity,
        source=state.binding,
        baseline_sha=baseline.sha,
        assistant_message_id=ids.assistant_message_id,
        model_id=model_id,
        prompt_sha256=hashlib.sha256(prompt_text.encode()).hexdigest(),
    ).model_dump(mode="json")
    effect_id = _publication_id(ids.run_id)
    seq = state.execution.logical_step_seq + 1
    async with driver.factory() as session:
        run, _ = await _publication_owner(session, driver)
        parent = await session.get(Snapshot, baseline.snapshot_id) if baseline.snapshot_id else None
        if (
            (baseline.snapshot_id is None and baseline.sha is not None)
            or (
                baseline.snapshot_id is not None
                and (
                    parent is None
                    or parent.project_id != run.project_id
                    or parent.commit_sha != baseline.sha
                )
            )
            or run.assistant_message_id != ids.assistant_message_id
        ):
            raise RecoveryContractError("publication original parent or assistant changed")
        if await session.get(GenerationRecoveryEffect, effect_id) is not None:
            raise RecoveryContractError("publication already has an unresolved or committed intent")
        await _lock_effect_namespace(session, run.id, seq)
        assert state.continuation_claim is not None
        session.add(
            GenerationRecoveryEffect(
                effect_id=effect_id,
                run_id=run.id,
                project_id=run.project_id,
                user_id=run.user_id,
                logical_step_seq=seq,
                effect_ordinal=await _next_effect_ordinal(session, run.id, seq),
                kind="proof",
                input_digest=_digest(binding),
                state="inflight",
                attempt_seq=state.attempt_seq,
                claim_token=state.continuation_claim.token,
                pre_binding=binding,
            )
        )
        updated = state.model_copy(
            update={
                "state_revision": state.state_revision + 1,
                "execution": state.execution.model_copy(
                    update={
                        "logical_step_seq": seq,
                        "substep": "publication_pending",
                        "pending_effect_id": effect_id,
                    }
                ),
            }
        )
        run.agent_state = {**run.agent_state, "recovery_v1": updated.model_dump(mode="json")}
        await _require_session_owner(run.id)
        await session.commit()
    driver.state = updated


async def _confirm_publication(
    session: AsyncSession,
    driver: ExecutionDriver,
    *,
    snapshot: Snapshot | None,
    assistant_message_id: UUID,
) -> None:
    state = driver.state
    row = await session.get(GenerationRecoveryEffect, _publication_id(state.identity.run_id))
    assert state.continuation_claim is not None
    if (
        row is None
        or row.state != "inflight"
        or row.run_id != state.identity.run_id
        or row.project_id != state.identity.project_id
        or row.user_id != state.identity.user_id
        or row.kind != "proof"
        or row.logical_step_seq != state.execution.logical_step_seq
        or row.claim_token != state.continuation_claim.token
        or row.attempt_seq != state.attempt_seq
        or row.input_digest != _digest(row.pre_binding)
    ):
        raise RecoveryContractError("publication reservation changed")
    receipt = PublicationReceipt(
        input_digest=row.input_digest,
        snapshot_id=snapshot.id if snapshot is not None else None,
        commit_sha=snapshot.commit_sha if snapshot is not None else None,
        assistant_message_id=assistant_message_id,
    )
    row.receipt_ref = receipt.model_dump(mode="json")
    row.receipt_sha256 = _digest(row.receipt_ref)
    row.state, row.revision = "confirmed", row.revision + 1
    run = await session.get(GenerationRun, state.identity.run_id)
    assert run is not None
    updated = state.model_copy(
        update={
            "state_revision": state.state_revision + 1,
            "execution": state.execution.model_copy(update={"substep": "publication_committed"}),
        }
    )
    run.agent_state = {**run.agent_state, "recovery_v1": updated.model_dump(mode="json")}
    await _require_session_owner(state.identity.run_id)


async def publish_agent_candidate(
    *,
    _att_capture: list[tuple[str, Any]] | None,
    _attestation_stack: str,
    _consume_free_generation: Callable[[AsyncSession], Awaitable[None]],
    _max_finalization_proof: ProofBundle | None,
    _orch_name: str | None,
    accumulated: str,
    baseline: SourceBaseline,
    factory: async_sessionmaker[AsyncSession],
    files: dict[str, str],
    ids: GenerationIds,
    model_id: str,
    progress: GenerationProgress,
    project_info: ProjectGenerationFacts,
    prompt_text: str,
    runtime: GenerationRuntime,
    _promotion_permit: PromotionPermit | None = None,
    continuation: ExecutionDriver | None = None,
) -> None:
    if files:
        permit = require_promotion_permit(
            _promotion_permit,
            proof=_max_finalization_proof,
            expected_generation_run_id=ids.run_id,
            published_files=files,
        )
        handle = runtime.handle
        if handle is None or handle.current_identity is None:
            raise PromotionPermitError(
                "PROMOTION_PERMIT_STALE",
                "active MAX workspace identity is unavailable",
            )
        before = await handle.current_identity()
        reader = handle.refresh_snapshot_files or handle.snapshot_files
        runtime_files = dict(await reader())
        after = await handle.current_identity()
        if before != after or canonical_files_digest(runtime_files) != canonical_files_digest(
            files
        ):
            raise PromotionPermitError(
                "PROMOTION_PERMIT_STALE",
                "active MAX workspace changed before publication",
            )
        require_promotion_permit(
            permit,
            current_identity=after,
            expected_workspace_id=handle.workspace_id,
        )
        if continuation is not None:
            await _begin_publication(
                continuation,
                baseline=baseline,
                files=files,
                ids=ids,
                model_id=model_id,
                prompt_text=prompt_text,
            )
        new_sha = await asyncio.to_thread(
            repo_svc.commit_files,
            ids.project_id,
            files,
            f"AI(agent): {prompt_text[:50]}",
            baseline.sha,
            exact_tree=_max_finalization_proof is not None,
        )
        async with factory() as session:
            if continuation is not None:
                await _publication_owner(session, continuation)
            snapshot, _project = await create_generation_snapshot(
                session,
                project_id=ids.project_id,
                run_id=ids.run_id,
                commit_sha=new_sha,
                prompt_text=prompt_text,
                model_id=model_id,
                parent_snapshot_id=baseline.snapshot_id,
                changed_files=list(files),
            )
            from yleum_api.services.generation_billing import record_accepted_candidate

            if _max_finalization_proof is not None:
                await record_accepted_candidate(
                    session,
                    run_id=ids.run_id,
                    snapshot_id=snapshot.id,
                    permit=permit,
                    proof=_max_finalization_proof,
                )
            _agent_snap_id = snapshot.id
            msg = await session.get(Message, ids.assistant_message_id)
            if msg is not None:
                msg.content = accumulated
                msg.snapshot_id = snapshot.id
                msg.tokens_out = msg.tokens_out or 0
                msg.agent_steps = progress.steps or None
            await _consume_free_generation(session)
            if continuation is not None:
                await _confirm_publication(
                    session,
                    continuation,
                    snapshot=snapshot,
                    assistant_message_id=ids.assistant_message_id,
                )
            await session.commit()
            await session.refresh(snapshot)
        # Persist the build attestation in its OWN transaction (best-effort;
        # a failed insert can NEVER roll back the snapshot committed above).
        if _att_capture and get_settings().use_build_attestation:
            try:
                from yleum_api.models.attestation import Attestation
                from yleum_api.services import attestation as _att

                _rec = _att.build_attestation(
                    gates=_att_capture,
                    stack=_orch_name or project_info.template,
                    project_id=str(ids.project_id),
                    created_at=_att.now_iso(),
                    commit_sha=new_sha,
                )
                async with factory() as _asess:
                    _asess.add(
                        Attestation(
                            project_id=ids.project_id,
                            snapshot_id=_agent_snap_id,
                            commit_sha=new_sha,
                            stack=_attestation_stack,
                            issued_at=str(_rec["created_at"]),
                            overall_passed=bool(_rec["overall_passed"]),
                            digest=_rec["digest"],
                            gates=_rec["gates"],
                        )
                    )
                    await _asess.commit()
                print(
                    f"[ATTEST] persisted snapshot={_agent_snap_id} passed={_rec['overall_passed']}",
                    flush=True,
                )
            except Exception as _ae:  # never affect the build
                print(f"[ATTEST] persist skipped: {_ae}", flush=True)
        if runtime.handle is not None:
            from yleum_api.services.snapshot_preview_capture import capture_snapshot_frontend

            await capture_snapshot_frontend(
                _agent_snap_id,
                ids.project_id,
                new_sha,
                files,
                runtime.handle,
            )
        await publish_event(
            ids.project_id,
            "snapshot.created",
            {"snapshot": _snapshot_payload(snapshot)},
        )
    else:
        # Nothing written — mark the row done so the UI unblocks.
        if continuation is not None:
            await _begin_publication(
                continuation,
                baseline=baseline,
                files=files,
                ids=ids,
                model_id=model_id,
                prompt_text=prompt_text,
            )
        async with factory() as session:
            if continuation is not None:
                await _publication_owner(session, continuation)
            msg = await session.get(Message, ids.assistant_message_id)
            if msg is not None:
                msg.content = accumulated
                msg.tokens_out = msg.tokens_out or 0
                msg.agent_steps = progress.steps or None
            if continuation is not None:
                await _confirm_publication(
                    session,
                    continuation,
                    snapshot=None,
                    assistant_message_id=ids.assistant_message_id,
                )
            await session.commit()

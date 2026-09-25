from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from yleum_api.core.config import get_settings
from yleum_api.core.redis import publish_event
from yleum_api.models.message import Message
from yleum_api.services import repo as repo_svc
from yleum_api.services.generation.contracts import (
    GenerationIds,
    GenerationRuntime,
    ProjectGenerationFacts,
    SourceBaseline,
)
from yleum_api.services.generation.progress import GenerationProgress
from yleum_api.services.generation.publication import _snapshot_payload
from yleum_api.services.generation_artifacts import create_generation_snapshot
from yleum_api.services.max_finalization import ProofBundle
from yleum_api.services.promotion_permit import (
    PromotionPermit,
    PromotionPermitError,
    canonical_files_digest,
    require_promotion_permit,
)
from yleum_api.services.queue import enqueue_preview

_log = logging.getLogger("yleum_api.routers.messages")


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
) -> None:
    if files:
        if project_info.template == "max_miniapp":
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
            if before != after or canonical_files_digest(
                runtime_files
            ) != canonical_files_digest(files):
                raise PromotionPermitError(
                    "PROMOTION_PERMIT_STALE",
                    "active MAX workspace changed before publication",
                )
            require_promotion_permit(
                permit,
                current_identity=after,
                expected_workspace_id=handle.workspace_id,
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
            _agent_snap_id = snapshot.id
            msg = await session.get(Message, ids.assistant_message_id)
            if msg is not None:
                msg.content = accumulated
                msg.snapshot_id = snapshot.id
                msg.tokens_out = msg.tokens_out or 0
                msg.agent_steps = progress.steps or None
            await _consume_free_generation(session)
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
        if project_info.template == "max_miniapp" and runtime.handle is not None:
            from yleum_api.services.snapshot_preview_capture import capture_snapshot_frontend

            await capture_snapshot_frontend(
                _agent_snap_id,
                ids.project_id,
                new_sha,
                files,
                runtime.handle,
            )
        else:
            await asyncio.to_thread(enqueue_preview, _agent_snap_id)
        await publish_event(
            ids.project_id,
            "snapshot.created",
            {"snapshot": _snapshot_payload(snapshot)},
        )
    else:
        # Nothing written — mark the row done so the UI unblocks.
        async with factory() as session:
            msg = await session.get(Message, ids.assistant_message_id)
            if msg is not None:
                msg.content = accumulated
                msg.tokens_out = msg.tokens_out or 0
                msg.agent_steps = progress.steps or None
            await session.commit()

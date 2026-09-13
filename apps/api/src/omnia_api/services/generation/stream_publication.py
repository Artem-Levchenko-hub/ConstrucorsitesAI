from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from omnia_api.core.config import get_settings
from omnia_api.core.redis import publish_event
from omnia_api.models.message import Message
from omnia_api.schemas.project import CONTAINER_BROWSER_TEMPLATES as CONTAINER_NEXT
from omnia_api.services import app_errors
from omnia_api.services import repo as repo_svc
from omnia_api.services.file_extractor import clean_chat_content
from omnia_api.services.generation.contracts import (
    GenerationIds,
    ProjectGenerationFacts,
    PublishedStreamedSource,
    SourceBaseline,
)
from omnia_api.services.generation.file_transforms import (
    _normalize_entity_filenames,
    _preserve_auth_schema,
    _warn_unparseable_entity_json,
)
from omnia_api.services.generation.publication import (
    ORCHESTRATION_LABEL,
    _snapshot_payload,
)
from omnia_api.services.generation_artifacts import create_generation_snapshot
from omnia_api.services.queue import enqueue_preview

_log = logging.getLogger("omnia_api.routers.messages")


async def publish_streamed_candidate(
    *,
    _acc_fingerprint: int | None,
    _consume_free_generation: Callable[[AsyncSession], Awaitable[None]],
    _gen_mode: str,
    accumulated: str,
    baseline: SourceBaseline,
    factory: async_sessionmaker[AsyncSession],
    files: dict[str, str],
    force_model: str | None,
    ids: GenerationIds,
    model_id: str,
    orchestrate: bool,
    project_info: ProjectGenerationFacts,
    prompt_text: str,
    routing_model: str,
    surgical: bool,
    usage_data: dict[str, Any] | None,
) -> PublishedStreamedSource:
    _bad_entities: list[str] = []
    new_snapshot_id: UUID | None = None
    if project_info.template in CONTAINER_NEXT:
        files = _preserve_auth_schema(files)
        # Filename↔Name guard: the registry resolves entities by
        # `entities/<Name>.json`, so a lowercase/plural filename the
        # writer copied from the brief (clients.json for name "Client")
        # would 404 every read/write and DOA the app. Realign here.
        files = _normalize_entity_filenames(files)
        # PROPOSAL P-ENTITYJSON (BS-31): a writer-fumbled, unparseable
        # entities/*.json resolves as 404 'unknown entity' at runtime — the
        # whole section is DOA while the build still reads as «готово». Detect
        # it here; the action half (a fixable «schema» card) fires after commit.
        # Does not mutate `files` — repair stays a user-driven «Починить».
        _bad_entities = _warn_unparseable_entity_json(files)
    # Carry the user's direct style edits (omnia-overrides block + font
    # links) across this regeneration so manual color/font tweaks aren't
    # lost when the model rewrites index.html. Fail-soft, like the guards.
    try:
        from omnia_api.services import overrides as _overrides

        _current_index_with_overrides = baseline.files.get("index.html")
        _generated_index_with_overrides = files.get("index.html")
        if _current_index_with_overrides and _generated_index_with_overrides:
            files["index.html"] = _overrides.carry_over_overrides(
                _current_index_with_overrides,
                _generated_index_with_overrides,
            )
    except Exception as _co_exc:
        print(f"[PP] overrides carry-over skipped err={_co_exc!r}", flush=True)
    new_sha = await asyncio.to_thread(
        repo_svc.commit_files,
        ids.project_id,
        files,
        f"AI: {prompt_text[:50]}",
        baseline.sha,
    )
    async with factory() as session:
        # Orchestrated runs record the "topmix-v1" label; a fired
        # fallback records the model that actually produced the output;
        # an admin-forced run records the forced model.
        snapshot_model_id = (
            model_id
            if model_id != routing_model
            else (force_model or (ORCHESTRATION_LABEL if orchestrate else routing_model))
        )
        snapshot, project = await create_generation_snapshot(
            session,
            project_id=ids.project_id,
            run_id=ids.run_id,
            commit_sha=new_sha,
            prompt_text=prompt_text,
            model_id=snapshot_model_id,
            parent_snapshot_id=baseline.snapshot_id,
            changed_files=list(files),
        )
        new_snapshot_id = snapshot.id

        msg = await session.get(Message, ids.assistant_message_id)
        if msg is not None:
            # Honest chat content: drop leaked raw code and any chip for a
            # file that wasn't actually committed, so the chat reflects
            # what shipped (not the model's raw attempt). Kill switch
            # USE_CLEAN_CHAT_CONTENT falls back to the raw output.
            msg.content = (
                clean_chat_content(accumulated, files, surgical=surgical)
                if get_settings().use_clean_chat_content
                else accumulated
            )
            msg.snapshot_id = snapshot.id
            if usage_data:
                msg.tokens_in = int(usage_data.get("tokens_in") or 0)
                msg.tokens_out = int(usage_data.get("tokens_out") or 0)

        # Burn one free generation — only on a successful free run (we're
        # inside `if files:`, so a snapshot was committed). Counted here,
        # not in the gateway, because the API owns the user row.
        await _consume_free_generation(session)

        # Billing is the LLM Gateway's responsibility — it already
        # wrote `wallet_charges` + `usage` + decremented `wallets`
        # the moment the upstream stream finished (see
        # apps/llm-gateway/src/omnia_gateway/services/streaming.py:
        # billing.charge call). Re-charging here was double-billing
        # users (each prompt cost 2× the displayed price). The frontend
        # still picks up the new balance: usePromptStream invalidates
        # ["wallet"] on every `llm.done` event, which triggers a fresh
        # GET /api/wallet that reflects the gateway-side debit.
        await session.commit()

        await session.refresh(snapshot)

    await asyncio.to_thread(enqueue_preview, new_snapshot_id)
    await publish_event(
        ids.project_id,
        "snapshot.created",
        {"snapshot": _snapshot_payload(snapshot)},
    )

    # BS-31 action half — a declared entity whose JSON didn't parse is DOA at
    # runtime (404 'unknown entity'), so the whole section is dead while the
    # chat still reads as «готово». Surface each as a fixable «schema» card so
    # the user sees the truth + «Починить» instead of a silently-broken screen.
    # Gated on the same kill switch as the other build/runtime cards.
    if _bad_entities and get_settings().use_error_cards:
        for _ent in _bad_entities:
            await app_errors.publish(
                factory,
                ids.project_id,
                ids.assistant_message_id,
                category="schema",
                title="Ошибка в схеме данных",
                detail=(
                    f"Сущность «{_ent}» не читается из-за ошибки в её "
                    f"JSON-схеме, поэтому раздел «{_ent}» не будет работать. "
                    f"Нажмите «Починить», чтобы я пересобрал схему."
                ),
                file=f"entities/{_ent}.json",
            )

    # Sprint 4 — fingerprint the shipped freeform page into the global
    # pool (cross-project dedup signal for next time). Fail-soft.
    if _gen_mode == "freeform" and _acc_fingerprint is not None:
        try:
            from omnia_api.services import originality as _originality

            await _originality.remember(str(ids.project_id), _acc_fingerprint)
        except Exception as _fp_exc:
            print(f"[PP] originality remember failed: {_fp_exc!r}", flush=True)

    return PublishedStreamedSource(new_snapshot_id, project, files, new_sha, snapshot_model_id)

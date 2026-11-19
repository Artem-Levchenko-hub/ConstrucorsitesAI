"""Private hash-only observation of the coder entry plan, never HTTP authority."""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
from collections.abc import Awaitable, Callable
from typing import Literal

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from yleum_api.services.generation.contracts import GenerationIds, SourceBaseline

EntryKind = Literal["legacy", "native"]
EntryRecorder = Callable[[str, EntryKind], Awaitable[None]]
KEY = "coder_entry_plan_v1"
_OBSERVATION_SECONDS = 2.0
_log = logging.getLogger(__name__)


def _sha(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def coder_entry_evidence(
    ids: GenerationIds, baseline: SourceBaseline, *, input_prompt: str,
    coder_user: str, entry_kind: EntryKind, rendered_brief: str | None = None,
) -> dict[str, object]:
    # Only the backend preparation result supplies this reference. Owner/source
    # markers are never evidence that preparation ran or was consumed.
    fragment = rendered_brief or ""
    return {
        "version": 1, "scope": "coder_entry_user_text", "full_http_captured": False,
        "reference_payload_captured": False,
        "run_id": str(ids.run_id), "project_id": str(ids.project_id),
        "owner_id": str(ids.user_id), "user_message_id": str(ids.user_message_id),
        "assistant_message_id": str(ids.assistant_message_id), "entry_kind": entry_kind,
        "input_sha256": _sha(input_prompt), "input_chars": len(input_prompt),
        "coder_user_sha256": _sha(coder_user), "coder_user_chars": len(coder_user),
        "prepared_reference_available": bool(fragment),
        "rendered_brief_included": bool(fragment) and fragment in coder_user,
        "rendered_brief_sha256": _sha(fragment) if fragment else None,
        "rendered_brief_chars": len(fragment),
        "baseline_snapshot_id": str(baseline.snapshot_id) if baseline.snapshot_id else None,
        "baseline_commit_sha": baseline.sha if isinstance(baseline.sha, str)
        and re.fullmatch(r"[a-fA-F0-9]{40,64}", baseline.sha) else None,
        "baseline_files_sha256": _sha(json.dumps(
            baseline.files, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
        )),
    }


async def record_coder_entry(
    factory: async_sessionmaker[AsyncSession], ids: GenerationIds, baseline: SourceBaseline,
    *, input_prompt: str, coder_user: str, entry_kind: EntryKind,
    rendered_brief: str | None = None,
) -> None:
    """Atomically add the first private entry receipt without replacing other state."""
    value = coder_entry_evidence(
        ids, baseline, input_prompt=input_prompt, coder_user=coder_user, entry_kind=entry_kind,
        rendered_brief=rendered_brief,
    )
    async with factory() as session:
        await session.execute(text(
            "UPDATE generation_runs SET agent_state=jsonb_set("
            "COALESCE(agent_state,'{}'::jsonb),'{coder_entry_plan_v1}',"
            "CAST(:value AS jsonb) || jsonb_build_object("
            "'run_prompt_sha256',prompt_hash,'recorded_at',CURRENT_TIMESTAMP),true) "
            "WHERE id=:run AND project_id=:project AND user_id=:owner AND status='running' "
            "AND user_message_id=:message AND assistant_message_id=:assistant "
            "AND NOT (COALESCE(agent_state,'{}'::jsonb) ? :key) "
            "AND EXISTS(SELECT 1 FROM projects p "
            "WHERE p.id=generation_runs.project_id AND p.owner_id=:owner)"
        ), {
            "value": json.dumps(value, ensure_ascii=False), "key": KEY,
            "run": ids.run_id, "project": ids.project_id, "owner": ids.user_id,
            "message": ids.user_message_id, "assistant": ids.assistant_message_id,
        })
        await session.commit()


def entry_recorder(
    factory: async_sessionmaker[AsyncSession], ids: GenerationIds,
    baseline: SourceBaseline, input_prompt: str, rendered_brief: str | None = None,
) -> EntryRecorder:
    async def record(user: str, kind: EntryKind) -> None:
        await record_coder_entry(
            factory, ids, baseline, input_prompt=input_prompt, coder_user=user, entry_kind=kind,
            rendered_brief=rendered_brief,
        )

    return record


async def observe_coder_entry(
    recorder: EntryRecorder | None, coder_user: str, entry_kind: EntryKind,
) -> None:
    """Observation cannot block/repeat a model request or swallow owning cancellation."""
    if recorder is None:
        return
    task: asyncio.Future[None] | None = None

    def discard_result(done: asyncio.Future[None]) -> None:
        # A cancelled DB operation may still be closing its connection. Its
        # cleanup is not a reason to hold the coder or expose an exception body.
        if not done.cancelled():
            done.exception()

    try:
        task = asyncio.ensure_future(recorder(coder_user, entry_kind))
        done, _pending = await asyncio.wait({task}, timeout=_OBSERVATION_SECONDS)
        if not done:
            task.cancel()
            task.add_done_callback(discard_result)
            _log.info("Coder entry evidence unavailable")
        else:
            task.result()
    except asyncio.CancelledError:
        if task is not None:
            task.cancel()
            task.add_done_callback(discard_result)
        raise
    except Exception:
        # Never log the exception, request, prepared plan or code, even on DB errors.
        _log.info("Coder entry evidence unavailable")

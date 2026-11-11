"""Private source checkpoints; they never move accepted snapshots or publish."""
from __future__ import annotations

import asyncio
import hashlib
import io
import json
import logging
import re
from collections.abc import Iterator
from pathlib import PurePosixPath
from typing import Any, TypedDict, cast
from uuid import UUID

from minio.error import S3Error
from pydantic import ValidationError
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from yleum_api.core.config import get_settings
from yleum_api.core.minio import get_minio_client
from yleum_api.models.generation_run import GenerationRun
from yleum_api.models.message import Message
from yleum_api.models.project import Project
from yleum_api.models.project_cell import ProjectCellOperation, ProjectCellWorkspace
from yleum_api.models.snapshot import Snapshot
from yleum_api.schemas.message import PromptResponse
from yleum_api.services.generation.agent_messages import _is_retry_request
from yleum_api.services.generation.contracts import GenerationIds, SourceBaseline
from yleum_api.services.generation.request_binding import bound_message_request
from yleum_api.services.generation_runs import (
    PREVIOUS_CELL_ENSURE_RECONCILIATION_REQUIRED,
    PREVIOUS_CELL_RECONCILIATION_ATTEMPTS,
    GenerationDispatch,
    has_capacity_admitted_dispatch_token,
    has_capacity_dispatch_claim,
    load_generation_dispatch,
    read_capacity_dispatch_claim,
)
from yleum_api.services.max_project_kit import MAX_SECURITY_LOCKED_FILES
from yleum_api.services.orchestrator_client import (
    OrchestratorUnavailable,
    ProjectCellResourceResponse,
)
from yleum_api.services.project_cells import ProjectCellValidationError, _stored_request_payload
from yleum_api.services.repo import MAX_FILE_BYTES, MAX_FILES, MAX_REPO_BYTES

_MAX_PREDECESSOR_HOPS = 20


class SourceCheckpoint(TypedDict):
    files: dict[str, str]
    deleted_paths: list[str]


async def checkpoint_before_rollback(operations: Any) -> None:
    """A failed draft save must never block restoration of the accepted source."""
    from yleum_api.services.agent_builder import AgentResult

    callback = getattr(operations, "checkpoint", None)
    if callback is None:
        return
    try:
        await callback(AgentResult(
            done=False, summary="Сохраняю непроверенный черновик перед восстановлением.",
            files={}, steps=0, stop_reason="checkpoint",
        ))
    except Exception:
        logging.getLogger(__name__).warning("Unaccepted source checkpoint unavailable")

def checkpoint_resume_request(prompt: str) -> bool:
    """An amended request is a new instruction, never replaced by an old brief."""
    text = " ".join(prompt.casefold().split()).rstrip(".!?")
    return _is_retry_request(prompt) or re.fullmatch(
        r"(?:(?:продолжи|продолжай|продолжить|доделай|доделать|доведи|довести|"
        r"дособери|дособерите|дострой|достроить|доработай|допиши)"
        r"(?:\s+(?:приложение|генерацию|сборку|проект))?|"
        r"(?:заверши|закончи)\s+сборку|finish the build|continue building|keep building)",
        text,
    ) is not None


def _validate_path(path: str) -> None:
    pure = PurePosixPath(path)
    if (
        not path or "\\" in path or pure.is_absolute() or pure.as_posix() != path
        or ".." in pure.parts or ".git" in pure.parts
        or any(part in {"node_modules", ".next", ".venv"} for part in pure.parts)
        or pure.name.startswith(".env")
        or pure.name in {"secrets.json", "secrets.yml", "secrets.yaml"}
        or path in MAX_SECURITY_LOCKED_FILES
    ):
        raise ValueError("unsafe checkpoint source path")


def encode_checkpoint(
    *, project_id: UUID, user_id: UUID, run_id: UUID, baseline_sha: str | None,
    files: dict[str, str], deleted_paths: list[str],
) -> bytes:
    if len(files) + len(deleted_paths) > MAX_FILES:
        raise ValueError("checkpoint source budget exceeded")
    if baseline_sha is not None and not re.fullmatch(r"[a-f0-9]{40}", baseline_sha):
        raise ValueError("invalid checkpoint baseline")
    for path, content in files.items():
        _validate_path(path)
        if not isinstance(content, str) or len(content.encode("utf-8")) > MAX_FILE_BYTES:
            raise ValueError("invalid checkpoint source")
    for path in deleted_paths:
        _validate_path(path)
        if path in files:
            raise ValueError("checkpoint cannot write and delete the same path")
    payload = json.dumps({
        "version": 1, "project_id": str(project_id), "user_id": str(user_id),
        "run_id": str(run_id), "baseline_sha": baseline_sha,
        "files": files, "deleted_paths": sorted(set(deleted_paths)),
    }, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    if len(payload) > MAX_REPO_BYTES:
        raise ValueError("checkpoint source budget exceeded")
    return payload


def decode_checkpoint(
    data: bytes, *, project_id: UUID, user_id: UUID, run_id: UUID,
    baseline_sha: str | None,
) -> SourceCheckpoint | None:
    if len(data) > MAX_REPO_BYTES:
        return None
    try:
        payload = json.loads(data)
        if not isinstance(payload, dict) or any(
            payload.get(key) != expected for key, expected in {
                "version": 1, "project_id": str(project_id), "user_id": str(user_id),
                "run_id": str(run_id), "baseline_sha": baseline_sha,
            }.items()
        ):
            return None
        files, deleted = payload.get("files"), payload.get("deleted_paths")
        if not isinstance(files, dict) or not isinstance(deleted, list):
            return None
        if not all(isinstance(k, str) and isinstance(v, str) for k, v in files.items()):
            return None
        if not all(isinstance(path, str) for path in deleted):
            return None
        encode_checkpoint(
            project_id=project_id, user_id=user_id, run_id=run_id,
            baseline_sha=baseline_sha, files=files, deleted_paths=deleted,
        )
        return {"files": files, "deleted_paths": deleted}
    except (TypeError, ValueError, UnicodeError):
        return None


def _object_key(project_id: UUID, run_id: UUID, digest: str) -> str:
    if not re.fullmatch(r"[a-f0-9]{64}", digest):
        raise ValueError("invalid checkpoint object digest")
    return f"generation-checkpoints/{project_id}/{run_id}/{digest}.json"


def _put_checkpoint(project_id: UUID, run_id: UUID, data: bytes) -> str:
    digest = hashlib.sha256(data).hexdigest()
    get_minio_client().put_object(
        get_settings().minio_bucket_projects, _object_key(project_id, run_id, digest),
        io.BytesIO(data), len(data), content_type="application/json",
    )
    return digest


def _get_checkpoint(project_id: UUID, run_id: UUID, digest: str) -> bytes | None:
    try:
        response = get_minio_client().get_object(
            get_settings().minio_bucket_projects, _object_key(project_id, run_id, digest),
        )
    except S3Error as exc:
        if exc.code == "NoSuchKey":
            return None
        raise
    try:
        data = response.read(MAX_REPO_BYTES + 1)
    finally:
        response.close()
        response.release_conn()
    return data if hashlib.sha256(data).hexdigest() == digest else None


async def store_checkpoint_object(project_id: UUID, run_id: UUID, data: bytes) -> tuple[str, str]:
    """Store immutable private bytes; callers validate authorization before upload."""
    digest = await asyncio.to_thread(_put_checkpoint, project_id, run_id, data)
    if digest != hashlib.sha256(data).hexdigest():
        raise ValueError("checkpoint storage returned an invalid content digest")
    return _object_key(project_id, run_id, digest), digest


async def load_checkpoint_object(
    project_id: UUID, run_id: UUID, *, object_key: str, digest: str,
) -> bytes | None:
    """Resolve only an exact content-addressed object in the current run's namespace."""
    if object_key != _object_key(project_id, run_id, digest):
        raise ValueError("checkpoint reference ownership mismatch")
    data = await asyncio.to_thread(_get_checkpoint, project_id, run_id, digest)
    if data is None or len(data) > MAX_REPO_BYTES or hashlib.sha256(data).hexdigest() != digest:
        return None
    return data


def checkpoint_sources(files: dict[str, str]) -> dict[str, str]:
    """Remove server-owned/secret files; validate everything that will be resumed."""
    selected: dict[str, str] = {}
    for path, source in files.items():
        if path in MAX_SECURITY_LOCKED_FILES or PurePosixPath(path).name.startswith(".env"):
            continue
        _validate_path(path)
        selected[path] = source
    return selected


async def persist_checkpoint(
    *, factory: async_sessionmaker[AsyncSession], ids: GenerationIds,
    baseline: SourceBaseline, files: dict[str, str],
    original_prompt: str = "",
) -> None:
    source = checkpoint_sources(files)
    original = checkpoint_sources(baseline.files)
    data = encode_checkpoint(
        project_id=ids.project_id, user_id=ids.user_id, run_id=ids.run_id,
        baseline_sha=baseline.sha, files=source,
        deleted_paths=sorted(original.keys() - source.keys()),
    )
    digest = await asyncio.to_thread(_put_checkpoint, ids.project_id, ids.run_id, data)
    async with factory() as session:
        run = await session.get(GenerationRun, ids.run_id, with_for_update=True)
        if (
            run is None or run.project_id != ids.project_id or run.user_id != ids.user_id
            or run.status != "running"
        ):
            raise ValueError("checkpoint generation identity is not active")
        run.agent_state = {
            **(run.agent_state or {}),
            "source_checkpoint": {
                "digest": digest, "baseline_sha": baseline.sha,
                "original_prompt": original_prompt,
            },
        }
        await session.commit()


async def adjacent_checkpoint(
    *, factory: async_sessionmaker[AsyncSession], ids: GenerationIds,
    baseline: SourceBaseline,
) -> tuple[SourceCheckpoint, str] | None:
    """Persist explicit resume provenance before fallible draft setup begins.

    Explicit references retain their exact identity checks. Legacy unmarked retries
    may cross at most twenty historical turns on the same accepted snapshot;
    malformed markers and unrelated history are barriers, never skipped.
    """
    async with factory() as session:
        current = await session.get(GenerationRun, ids.run_id, with_for_update=True)
        if (
            current is None or current.user_id != ids.user_id
            or current.project_id != ids.project_id
            or current.status != "running" or current.user_message_id != ids.user_message_id
        ):
            return None
        request = await session.get(Message, current.user_message_id)
        if (
            request is None or request.project_id != ids.project_id or request.role != "user"
            or not checkpoint_resume_request(request.content)
        ):
            return None
        hops = iter(range(_MAX_PREDECESSOR_HOPS))
        previous = await _checkpoint_predecessor(session, current, ids, baseline, hops)
        if previous is None or not _owned_failed_source(previous, ids):
            return None
        if _unmarked_checkpoint_state(previous):
            source = await _legacy_checkpoint_source(
                session, newer=current, previous=previous, ids=ids, baseline=baseline, hops=hops,
            )
        else:
            source = await _explicit_checkpoint_source(
                session, previous, ids, baseline, current=current,
            )
        if source is None:
            return None
        state = _checkpoint_state(source)
        state = state.get("source_checkpoint") if state is not None else None
        if not isinstance(state, dict) or state.get("baseline_sha") != baseline.sha:
            return None
        digest = state.get("digest")
        if not isinstance(digest, str) or not re.fullmatch(r"[a-f0-9]{64}", digest):
            return None
        message = await session.get(Message, source.user_message_id)
        if (
            message is None or message.project_id != ids.project_id or message.role != "user"
            or not message.content.strip()
        ):
            return None
        # Keep the validated server-owned object identity before private storage
        # or later setup can fail. Bytes are still verified on every actual return.
        current.agent_state = {
            **(current.agent_state or {}),
            "source_checkpoint_resume": {
                "previous_run_id": str(previous.id), "source_run_id": str(source.id),
                "digest": digest, "baseline_sha": baseline.sha,
            },
        }
        await session.commit()
        data = await asyncio.to_thread(_get_checkpoint, ids.project_id, source.id, digest)
        if data is None:
            return None
        decoded = decode_checkpoint(
            data, project_id=ids.project_id, user_id=ids.user_id,
            run_id=source.id, baseline_sha=baseline.sha,
        )
        if decoded is None:
            return None
        original_prompt = state.get("original_prompt")
        task = (
            original_prompt
            if isinstance(original_prompt, str) and original_prompt.strip() else message.content
        )
        return decoded, task


def _checkpoint_state(run: GenerationRun) -> dict[str, Any] | None:
    return run.agent_state if isinstance(run.agent_state, dict) else None


def _unmarked_checkpoint_state(run: GenerationRun) -> bool:
    state = _checkpoint_state(run)
    return state is not None and not any(
        key in state for key in ("source_checkpoint", "source_checkpoint_resume")
    )


async def _explicit_checkpoint_source(
    session: AsyncSession, previous: GenerationRun, ids: GenerationIds, baseline: SourceBaseline,
    *, current: GenerationRun, resume_only: bool = False,
) -> GenerationRun | None:
    """Resolve the existing explicit object reference without loosening its chain."""
    state = _checkpoint_state(previous)
    if state is None:
        return None
    if "source_checkpoint" in state and not resume_only:
        return previous  # An invalid nearest checkpoint is a hard barrier below.
    inherited = _resume_reference(previous, baseline.sha)
    if inherited is None:
        return None
    message = await session.get(Message, previous.user_message_id)
    hops = iter(range(_MAX_PREDECESSOR_HOPS))
    parent = await _checkpoint_predecessor(session, previous, ids, baseline, hops)
    source = await session.get(GenerationRun, UUID(inherited["source_run_id"]))
    if (
        message is None or message.project_id != ids.project_id or message.role != "user"
        or not checkpoint_resume_request(message.content)
        or not _owned_failed_source(parent, ids) or parent is None
        or str(parent.id) != inherited["previous_run_id"]
        or not _owned_failed_source(source, ids) or source is None
        or source.created_at >= previous.created_at or parent.created_at >= previous.created_at
    ):
        return None
    if parent.id != source.id:
        if _unmarked_checkpoint_state(parent):
            if not _admitted_checkpoint_baseline(current, baseline):
                return None
            # A newly persisted marker may name a historical unmarked parent.
            # Re-prove that same bounded legacy segment; never rewrite the parent.
            legacy_source = await _legacy_checkpoint_source(
                session, newer=previous, previous=parent, ids=ids, baseline=baseline, hops=hops,
            )
            if legacy_source is None or legacy_source.id != source.id:
                return None
        else:
            parent_reference = _resume_reference(parent, baseline.sha)
            parent_message = await session.get(Message, parent.user_message_id)
            if (
                parent_reference is None or parent.created_at <= source.created_at
                or parent_message is None or parent_message.project_id != ids.project_id
                or parent_message.role != "user"
                or not checkpoint_resume_request(parent_message.content)
                or parent_reference["previous_run_id"] in {
                    str(parent.id), str(previous.id), str(ids.run_id),
                }
                or any(parent_reference[key] != inherited[key]
                       for key in ("source_run_id", "digest", "baseline_sha"))
            ):
                return None
    source_state = _checkpoint_state(source)
    checkpoint = source_state.get("source_checkpoint") if source_state is not None else None
    if not isinstance(checkpoint, dict) or checkpoint.get("digest") != inherited["digest"]:
        return None
    return source


async def checkpoint_reference_message(
    session: AsyncSession, *, ids: GenerationIds, baseline: SourceBaseline,
) -> UUID:
    """Follow only the already verified retry chain to the admitted visual turn.

    A retry can save its own source checkpoint while its images still belong to
    an older admitted request. Re-prove each link and each message hash on cold
    loads; never search unrelated history for an image or trust a client URL.
    """
    from yleum_api.services.generation.request_binding import bound_message_request

    current = await session.get(GenerationRun, ids.run_id)
    if (current is None or current.project_id != ids.project_id
            or current.user_id != ids.user_id or current.user_message_id != ids.user_message_id):
        raise ValueError("reference continuation owner conflicts")
    visited: set[UUID] = set()
    for _ in range(20):
        if current.id in visited:
            raise ValueError("reference continuation cycle")
        visited.add(current.id)
        message = await session.get(Message, current.user_message_id)
        bound_message_request(current, message, project_id=ids.project_id, run_id=current.id)
        assert message is not None
        if (message.reference_images or getattr(message, 'font_selections', None)
                or getattr(message, 'resolved_fonts', None)
                or "source_checkpoint_resume" not in (current.agent_state or {})):
            return message.id
        source = await _explicit_checkpoint_source(
            session, current, ids, baseline, current=current, resume_only=True,
        )
        checkpoint = (source.agent_state or {}).get("source_checkpoint") if source else None
        if (source is None or not isinstance(checkpoint, dict)
                or checkpoint.get("baseline_sha") != baseline.sha):
            raise ValueError("reference continuation provenance conflicts")
        current = source
    raise ValueError("reference continuation chain exceeded")


def _bound_checkpoint_dispatch(run: GenerationRun) -> GenerationDispatch | None:
    try:
        dispatch = load_generation_dispatch(run)
    except ValueError:
        return None
    if (
        dispatch.user_message_id != run.user_message_id
        or dispatch.assistant_message_id != run.assistant_message_id
    ):
        return None
    return dispatch


def _admitted_checkpoint_baseline(run: GenerationRun, baseline: SourceBaseline) -> bool:
    dispatch = _bound_checkpoint_dispatch(run)
    return dispatch is not None and dispatch.current_snapshot_id == baseline.snapshot_id


async def _project_checkpoint_baseline(
    session: AsyncSession, ids: GenerationIds, baseline: SourceBaseline,
) -> bool:
    project = await session.get(Project, ids.project_id, populate_existing=True)
    if (
        project is None or project.owner_id != ids.user_id
        or project.current_snapshot_id != baseline.snapshot_id
    ):
        return False
    if baseline.snapshot_id is None:
        return baseline.sha is None
    snapshot = await session.get(Snapshot, baseline.snapshot_id, populate_existing=True)
    return (
        snapshot is not None and snapshot.project_id == ids.project_id
        and baseline.sha is not None and snapshot.commit_sha == baseline.sha
    )


async def _checkpoint_predecessor(
    session: AsyncSession, newer: GenerationRun, ids: GenerationIds, baseline: SourceBaseline,
    hops: Iterator[int],
) -> GenerationRun | None:
    """Skip only standalone continuations proven to have stopped before admission."""
    skipped = False
    for _ in hops:
        previous = await _adjacent_run(session, newer)
        if (
            previous is None or previous.created_at >= newer.created_at
            or await _ambiguous_run_order(session, previous)
        ):
            return None
        reconciliation_failure = (
            previous.status == "failed"
            and previous.error == PREVIOUS_CELL_ENSURE_RECONCILIATION_REQUIRED
        )
        if previous.status != "cancelled" and not reconciliation_failure:
            if skipped and not _admitted_checkpoint_baseline(previous, baseline):
                return None
            return previous
        if not await _interrupted_before_admission(session, previous, newer, ids, baseline):
            return None
        skipped = True
        newer = previous
    return None


async def _interrupted_before_admission(
    session: AsyncSession, previous: GenerationRun, newer: GenerationRun, ids: GenerationIds,
    baseline: SourceBaseline,
) -> bool:
    # The worker can claim the dispatch while waiting for a cell. The promotion
    # transaction sets started_at and the admitted token before source execution.
    if (
        previous.project_id != ids.project_id or previous.user_id != ids.user_id
        or previous.execution_backend != "worker" or previous.response_mode not in {"build", "edit"}
        or previous.started_at is not None or previous.finished_at is None
        or not previous.created_at <= previous.finished_at < newer.created_at
        or has_capacity_admitted_dispatch_token(previous)
        or not _unmarked_checkpoint_state(previous)
    ):
        return False
    failed = previous.status == "failed"
    if failed:
        started = previous.execution_started_at
        state = _checkpoint_state(previous)
        if (
            started is None or not previous.created_at <= started <= previous.finished_at
            or has_capacity_dispatch_claim(previous)
            or state is None
            or set(state) - {"dispatch", "generation_billing", "recovery_requirements_v1"}
        ):
            return False
    elif read_capacity_dispatch_claim(previous) is None:
        return False

    interrupted = _bound_checkpoint_dispatch(previous)
    current = _bound_checkpoint_dispatch(newer)
    if (
        interrupted is None or current is None
        or not interrupted.orchestrate or not current.orchestrate
        or interrupted.current_snapshot_id != baseline.snapshot_id
        or current.current_snapshot_id != baseline.snapshot_id
        or interrupted.reference_images or interrupted.font_selections
        or interrupted.selected_elements
    ):
        return False
    request = await session.get(Message, interrupted.user_message_id)
    response = await session.get(Message, interrupted.assistant_message_id)
    if not (
        request is not None and request.project_id == ids.project_id and request.role == "user"
        and not request.reference_images and not request.font_selections
        and not request.resolved_fonts
        and checkpoint_resume_request(request.content)
        and response is not None and response.project_id == ids.project_id
        and response.role == "assistant" and response.snapshot_id is None
    ):
        return False
    if not failed:
        return True
    try:
        bound_message_request(previous, request, project_id=ids.project_id, run_id=previous.id)
    except (TypeError, ValueError):
        return False
    return (
        _pre_admission_prompt_response(previous)
        and await _project_checkpoint_baseline(session, ids, baseline)
        and await _only_previous_cell_observations(session, previous)
    )


def _pre_admission_prompt_response(run: GenerationRun) -> bool:
    payload = run.response_payload
    if payload is None:
        return True
    if type(payload) is not dict or not {"snapshot_id", "mode", "run_status"} <= payload.keys():
        return False
    try:
        response = PromptResponse.model_validate(payload)
    except ValidationError:
        return False
    return (
        response.run_id == run.id and response.message_id == run.assistant_message_id
        and response.mode == run.response_mode and response.snapshot_id is None
        and response.run_status in {"pending", "queued_for_capacity", "failed"}
    )


async def _only_previous_cell_observations(session: AsyncSession, run: GenerationRun) -> bool:
    """Require the entire observation series from the failed admission branch."""
    started, finished = run.execution_started_at, run.finished_at
    if started is None or finished is None:
        return False
    result = await session.execute(
        select(ProjectCellOperation, ProjectCellWorkspace, GenerationRun)
        .outerjoin(
            ProjectCellWorkspace, ProjectCellWorkspace.id == ProjectCellOperation.workspace_id,
        )
        .outerjoin(GenerationRun, GenerationRun.id == ProjectCellOperation.generation_run_id)
        .where(or_(
            ProjectCellOperation.generation_run_id == run.id,
            ProjectCellOperation.execution_run_id == run.id,
        ))
        .limit(PREVIOUS_CELL_RECONCILIATION_ATTEMPTS + 1)
    )
    rows = result.tuples().all()
    # Replay can replace execution_run_id. An incomplete series is not evidence
    # that the failed worker only observed its predecessor.
    if len(rows) != PREVIOUS_CELL_RECONCILIATION_ATTEMPTS:
        return False
    fences: set[int] = set()
    observed_run_id: UUID | None = None
    for operation, workspace, observed in rows:
        fence = operation.fencing_epoch
        if (
            operation.generation_run_id == run.id or operation.kind != "status"
            or operation.status != "completed" or fence is None or fence < 1 or fence in fences
            or operation.started_at is None or operation.finished_at is None
            or not started <= operation.created_at <= operation.started_at
            <= operation.finished_at <= finished
            or workspace is None or workspace.project_id != run.project_id
            or workspace.owner_id != run.user_id or workspace.fencing_epoch < fence
            or observed is None or observed.project_id != run.project_id
            or observed.user_id != run.user_id
            or (observed_run_id is not None and observed.id != observed_run_id)
            or observed.status not in {"completed", "failed", "cancelled"}
            or observed.finished_at is None
            or not observed.created_at <= observed.finished_at < run.created_at
            or operation.idempotency_key
            != f"capacity:lease-observe:{workspace.id}:{observed.id}:{fence - 1}"
            or type(operation.result_payload) is not dict
        ):
            return False
        observed_run_id = observed.id
        fences.add(fence)
        try:
            if _stored_request_payload(operation):
                return False
            response = ProjectCellResourceResponse.from_json(operation.result_payload)
        except (ProjectCellValidationError, OrchestratorUnavailable, ValueError):
            return False
        lease = response.lease_observation
        if (
            response.workspace_id != workspace.id or response.fencing_epoch != fence
            or response.state != "resources_ready" or lease is None
            or lease.workspace_id != workspace.id or lease.project_id != run.project_id
            or lease.owner_id != run.user_id or lease.operation_id != operation.id
            or lease.fencing_epoch != fence or lease.request_digest != operation.request_digest
            # A quiescent response can clear the previous owner's durable lease.
            or lease.quiescent or lease.active_generation_run_id is not None
        ):
            return False
    return True


async def _legacy_checkpoint_source(
    session: AsyncSession, *, newer: GenerationRun, previous: GenerationRun,
    ids: GenerationIds, baseline: SourceBaseline, hops: Iterator[int],
) -> GenerationRun | None:
    """Read only pre-marker standalone retries with one real admitted baseline."""
    if (
        baseline.snapshot_id is None or baseline.sha is None
        or re.fullmatch(r"[a-f0-9]{40}", baseline.sha) is None
        or not _admitted_checkpoint_baseline(newer, baseline)
        or not await _project_checkpoint_baseline(session, ids, baseline)
    ):
        return None
    candidate: GenerationRun | None = previous
    while candidate is not None:
        if (
            not _owned_failed_source(candidate, ids)
            or not _admitted_checkpoint_baseline(candidate, baseline)
        ):
            return None
        state = _checkpoint_state(candidate)
        if state is None:
            return None
        if "source_checkpoint" in state:
            return candidate  # Stop at the nearest object, even if it is invalid.
        if "source_checkpoint_resume" in state:
            return None  # Explicit references are not legacy omissions.
        message = await session.get(Message, candidate.user_message_id)
        if (
            message is None or message.project_id != ids.project_id or message.role != "user"
            or not checkpoint_resume_request(message.content)
        ):
            return None
        candidate = await _checkpoint_predecessor(session, candidate, ids, baseline, hops)
    return None


async def _ambiguous_run_order(session: AsyncSession, run: GenerationRun) -> bool:
    """A UUID tie-break is not evidence that an equally timed turn came first."""
    tied = await session.scalar(select(1).select_from(GenerationRun).where(
        GenerationRun.project_id == run.project_id,
        GenerationRun.created_at == run.created_at,
        GenerationRun.id != run.id,
    ).limit(1))
    return tied is not None


async def _adjacent_run(session: AsyncSession, current: GenerationRun) -> GenerationRun | None:
    return cast(GenerationRun | None, await session.scalar(
        select(GenerationRun).where(
            GenerationRun.project_id == current.project_id,
            GenerationRun.id != current.id,
            GenerationRun.created_at <= current.created_at,
        ).order_by(GenerationRun.created_at.desc(), GenerationRun.id.desc()).limit(1)
    ))


def _owned_failed_source(run: GenerationRun | None, ids: GenerationIds) -> bool:
    return bool(
        run is not None and run.status == "failed" and run.project_id == ids.project_id
        and run.user_id == ids.user_id and run.response_mode in {"build", "edit"}
        and run.user_message_id is not None
    )


def _resume_reference(run: GenerationRun, baseline_sha: str | None) -> dict[str, Any] | None:
    agent_state = _checkpoint_state(run)
    state: Any = agent_state.get("source_checkpoint_resume") if agent_state is not None else None
    if (
        not isinstance(state, dict)
        or set(state) != {"previous_run_id", "source_run_id", "digest", "baseline_sha"}
        or state.get("baseline_sha") != baseline_sha
        or not isinstance(state.get("digest"), str)
        or not re.fullmatch(r"[a-f0-9]{64}", state["digest"])
    ):
        return None
    try:
        for key in ("previous_run_id", "source_run_id"):
            value = state[key]
            if not isinstance(value, str) or str(UUID(value)) != value or value == str(run.id):
                return None
    except ValueError:
        return None
    return state

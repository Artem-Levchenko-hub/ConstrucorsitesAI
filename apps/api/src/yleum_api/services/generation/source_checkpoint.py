"""Private source checkpoints; they never move accepted snapshots or publish."""
from __future__ import annotations

import asyncio
import hashlib
import io
import json
import logging
import re
from pathlib import PurePosixPath
from typing import Any, TypedDict, cast
from uuid import UUID

from minio.error import S3Error
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from yleum_api.core.config import get_settings
from yleum_api.core.minio import get_minio_client
from yleum_api.models.generation_run import GenerationRun
from yleum_api.models.message import Message
from yleum_api.services.generation.agent_messages import _is_retry_request
from yleum_api.services.generation.contracts import GenerationIds, SourceBaseline
from yleum_api.services.max_project_kit import MAX_SECURITY_LOCKED_FILES
from yleum_api.services.repo import MAX_FILE_BYTES, MAX_FILES, MAX_REPO_BYTES


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

    Each validated resume carries the original immutable object identity forward;
    no private-object copy or unbounded history walk is needed after an outage.
    Only the adjacent failed task may authorize that reference.
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
        previous = await _adjacent_run(session, current)
        if (
            not _owned_failed_source(previous, ids)
            or previous is None or previous.created_at == current.created_at
        ):
            return None
        source = previous
        state: Any = (source.agent_state or {}).get("source_checkpoint")
        if state is None:
            inherited = _resume_reference(previous, baseline.sha)
            if inherited is None:
                return None
            message = await session.get(Message, previous.user_message_id)
            parent = await _adjacent_run(session, previous)
            inherited_source = await session.get(GenerationRun, UUID(inherited["source_run_id"]))
            if (
                message is None or message.project_id != ids.project_id or message.role != "user"
                or not checkpoint_resume_request(message.content)
                or not _owned_failed_source(parent, ids) or parent is None
                or str(parent.id) != inherited["previous_run_id"]
                or not _owned_failed_source(inherited_source, ids) or inherited_source is None
                or inherited_source.created_at >= previous.created_at
                or parent.created_at >= previous.created_at
            ):
                return None
            source = inherited_source
            if parent.id != source.id:
                parent_reference = _resume_reference(parent, baseline.sha)
                parent_message = await session.get(Message, parent.user_message_id)
                if (
                    parent_reference is None or parent.created_at <= source.created_at
                    or parent_message is None or parent_message.project_id != ids.project_id
                    or parent_message.role != "user"
                    or not checkpoint_resume_request(parent_message.content)
                    or parent_reference["previous_run_id"] in {
                        str(parent.id), str(previous.id), str(current.id),
                    }
                    or any(parent_reference[key] != inherited[key]
                           for key in ("source_run_id", "digest", "baseline_sha"))
                ):
                    return None
            state = (source.agent_state or {}).get("source_checkpoint")
            if not isinstance(state, dict) or state.get("digest") != inherited["digest"]:
                return None
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
    state: Any = (run.agent_state or {}).get("source_checkpoint_resume")
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

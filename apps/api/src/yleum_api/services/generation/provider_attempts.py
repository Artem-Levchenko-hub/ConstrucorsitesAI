"""Commit generation dispatch intent before gateway HTTP, with no request bodies."""

from __future__ import annotations

from typing import Any
from uuid import UUID, uuid4, uuid5

from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker

from yleum_api.core.config import get_settings
from yleum_api.core.db import get_engine
from yleum_api.services.generation.provider_identity import (
    GenerationProviderIdentity,
    request_digest,
)


async def prepare_generation_attempt(
    body: dict[str, Any],
    route: str,
    *,
    existing: GenerationProviderIdentity | None = None,
    parent: GenerationProviderIdentity | None = None,
) -> GenerationProviderIdentity | None:
    metadata = body.get("metadata")
    if not isinstance(metadata, dict) or not metadata.get("run_id"):
        return None  # Generic service/runtime calls are not generation retention.
    if not get_settings().generation_provider_attempts_enabled:
        return None
    try:
        run_id, project_id = UUID(str(metadata["run_id"])), UUID(str(metadata["project_id"]))
        digest = request_digest(route, body)
        if existing is not None:
            if (existing.run_id, existing.project_id, existing.request_sha256) != (
                run_id,
                project_id,
                digest,
            ):
                raise ValueError
            return existing
        async with async_sessionmaker(get_engine(), expire_on_commit=False)() as session:
            # No FK/row lock against the caller's project/run transaction. The
            # gateway rechecks current committed ownership when granting dispatch.
            row = (
                (
                    await session.execute(
                        text(
                            "SELECT r.user_id,r.prompt_hash FROM generation_runs r "
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
            identity = GenerationProviderIdentity(
                run_id,
                project_id,
                row["user_id"],
                uuid5(parent.attempt_id, "definitive-refusal-retry-v1") if parent else uuid4(),
                row["prompt_hash"],
                digest,
            )
            if parent is not None:
                prior = (
                    (
                        await session.execute(
                            text(
                                "SELECT a.* FROM generation_provider_attempts a "
                                "JOIN provider_calls c "
                                "ON c.id=a.provider_call_id WHERE a.attempt_id=:id "
                                "AND a.phase='closed' "
                                "AND a.terminal_reason IN ('preconnect','rate_limit_refused') "
                                "AND c.status='failed' AND c.receipt_hash IS NOT NULL"
                            ),
                            {"id": parent.attempt_id},
                        )
                    )
                    .mappings()
                    .first()
                )
                if prior is None or any(
                    prior[key] != getattr(identity, key)
                    for key in (
                        "run_id",
                        "project_id",
                        "user_id",
                        "prompt_hash",
                        "request_sha256",
                    )
                ):
                    raise ValueError
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
            await session.commit()
        return identity
    except Exception:
        # Includes an unknown commit outcome: never send HTTP on uncertainty.
        raise RuntimeError("GENERATION_ATTEMPT_UNCONFIRMED: provider dispatch stopped") from None


def attach_identity(body: dict[str, Any], identity: GenerationProviderIdentity | None) -> None:
    if identity is not None:
        body["metadata"]["generation_provider_identity"] = identity.to_wire()

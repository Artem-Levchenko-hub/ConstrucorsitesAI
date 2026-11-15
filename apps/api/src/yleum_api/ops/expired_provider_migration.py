"""One-shot schema expansion for an owned, selected expired reservation."""

from __future__ import annotations

import asyncio
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Literal
from uuid import UUID

from alembic.migration import MigrationContext
from alembic.operations import Operations
from alembic.script import Script, ScriptDirectory
from sqlalchemy import Connection, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine, AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

from yleum_api.core.config import get_settings
from yleum_api.models.base import Base
from yleum_api.models.generation_run import GenerationRun
from yleum_api.models.project import Project
from yleum_api.models.project_cell import ProjectCellOperation, ProjectCellWorkspace
from yleum_api.services import generation_deployment_drain as drain
from yleum_api.services.generation.expired_provider_drain import (
    _NEW_HEAD,
    _OLD_HEAD,
    ExpiredProviderReport,
    ExpiredProviderScope,
    Mode,
    _selected_run,
    classify_expired_provider,
)
from yleum_api.services.generation.recovery_contract import RecoveryStateV3
from yleum_api.services.generation.recovery_execution import recovery_state
from yleum_api.services.generation.recovery_readers import require_recovery_readers

_COUNTS = {"generations": 1, "operations": 0, "leases": 0, "restorations": 0}
_MIGRATIONS = Path(__file__).resolve().parents[3] / "migrations"
_CHECK_PREFIX = "ck_generation_provider_attempts_ck_generation_attempt_"
_LOCAL_REASONS = (
    "COALESCE((terminal_reason = ANY (ARRAY['native_transcript_rejected'::text, "
    "'expired_before_dispatch'::text])), false)"
)
_FINANCIAL_REASONS = (
    "(terminal_reason = ANY (ARRAY['completed'::text, 'preconnect'::text, "
    "'rate_limit_refused'::text, 'upstream_refused'::text]))"
)
_INPUT_FIELDS = {
    "version", "release_sha", "reader_release_sha", "run_id", "attempt_id", "state_revision",
    "context_sha256", "source_sha256", "clock_sha256",
}


class MigrationBootstrapDenied(ValueError):
    """Target migration or the selected release scope could not be confirmed."""


@dataclass(frozen=True, slots=True)
class MigrationBootstrapResult:
    action: Literal["upgraded", "already_current"]
    settled: bool


@dataclass(frozen=True, slots=True)
class MigrationBootstrapInput:
    scope: ExpiredProviderScope
    reader_release_sha: str


def _unique_scope(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise MigrationBootstrapDenied("duplicate migration scope field")
        result[key] = value
    return result


def _scope(raw: bytes) -> MigrationBootstrapInput:
    if not 0 < len(raw) <= 4096:
        raise MigrationBootstrapDenied("invalid migration scope")
    value = json.loads(raw, object_pairs_hook=_unique_scope)
    if (
        type(value) is not dict or set(value) != _INPUT_FIELDS
        or type(value["version"]) is not int or value["version"] != 1
        or any(type(value[k]) is not str for k in _INPUT_FIELDS - {"version", "state_revision"})
    ):
        raise MigrationBootstrapDenied("invalid migration scope")
    run_id, attempt_id = UUID(value["run_id"]), UUID(value["attempt_id"])
    if (str(run_id), str(attempt_id)) != (value["run_id"], value["attempt_id"]):
        raise MigrationBootstrapDenied("invalid migration scope")
    return MigrationBootstrapInput(
        ExpiredProviderScope(
            value["release_sha"], run_id, attempt_id, value["state_revision"],
            value["context_sha256"], value["source_sha256"], value["clock_sha256"],
        ),
        reader_release_sha=value["reader_release_sha"],
    )


def _exact_context(connection: Connection) -> MigrationContext:
    return MigrationContext.configure(connection, opts={"target_metadata": Base.metadata})


def _packaged_revision() -> tuple[ScriptDirectory, Script]:
    if not _MIGRATIONS.is_dir():
        raise MigrationBootstrapDenied("target migration package is missing")
    scripts = ScriptDirectory(str(_MIGRATIONS))
    migration = scripts.get_revision(_NEW_HEAD)
    if (
        scripts.get_heads() != [_NEW_HEAD] or migration is None
        or migration.down_revision != _OLD_HEAD
        or Path(migration.path).resolve()
        != (_MIGRATIONS / "versions/0107_expired_provider_reservation.py").resolve()
    ):
        raise MigrationBootstrapDenied("packaged migration graph changed")
    return scripts, migration


def _expand_0107(
    connection: Connection, scripts: ScriptDirectory, migration: Script,
) -> None:
    """Use the packaged revision and Alembic bookkeeping in the caller's transaction."""
    context = _exact_context(connection)
    if context.get_current_heads() != (_OLD_HEAD,):
        raise MigrationBootstrapDenied("database migration head changed")
    with Operations.context(context):
        migration.module.upgrade()
    context.stamp(scripts, _NEW_HEAD)
    if context.get_current_heads() != (_NEW_HEAD,):
        raise MigrationBootstrapDenied("migration did not install the expected head")


async def _verify_0107(connection: AsyncConnection) -> None:
    rows = (await connection.execute(text(
        "SELECT conname, pg_get_constraintdef(oid) AS definition FROM pg_constraint "
        "WHERE conrelid='public.generation_provider_attempts'::regclass "
        "AND contype='c' AND convalidated AND conname IN (:call,:terminal)"
    ), {"call": _CHECK_PREFIX + "call", "terminal": _CHECK_PREFIX + "terminal"})).mappings().all()
    checks = {row["conname"]: row["definition"] for row in rows}
    expected = {
        _CHECK_PREFIX + "call": (
            "CHECK ((((phase = 'reserved'::text) OR ((phase = 'closed'::text) AND "
            f"{_LOCAL_REASONS})) = (provider_call_id IS NULL)))"
        ),
        _CHECK_PREFIX + "terminal": (
            "CHECK (((terminal_reason IS NULL) OR ((phase = 'closed'::text) AND "
            f"({_FINANCIAL_REASONS} OR ({_LOCAL_REASONS} AND (response_manifest_key IS NULL) "
            "AND (response_sha256 IS NULL))))))"
        ),
    }
    if checks != expected:
        raise MigrationBootstrapDenied("validated 0107 constraints are unconfirmed")


async def _read_eligibility(
    connection: AsyncConnection, scope: ExpiredProviderScope, *, mode: Mode,
    reader_release_sha: str,
) -> ExpiredProviderReport:
    async with AsyncSession(bind=connection) as session:
        report = await classify_expired_provider(
            session, scope, mode=mode, reader_release_sha=reader_release_sha,
        )
        await session.rollback()
        return report


async def _lock_selected_rows(
    connection: AsyncConnection, scope: ExpiredProviderScope, *, reader_release_sha: str,
) -> None:
    """Close the gap between the read-only precheck and the exact DDL."""
    async with AsyncSession(bind=connection) as session:
        if await drain._locked_drain(session, scope.release_sha) is None:
            raise MigrationBootstrapDenied("owned SQL drain disappeared")
        hint = await session.get(GenerationRun, scope.run_id)
        if hint is None:
            raise MigrationBootstrapDenied("selected generation disappeared")
        project = await session.get(Project, hint.project_id, with_for_update={"nowait": True})
        run = await session.get(
            GenerationRun, scope.run_id, with_for_update={"nowait": True}, populate_existing=True,
        )
        state = recovery_state(run) if run is not None else None
        if project is None or not isinstance(state, RecoveryStateV3):
            raise MigrationBootstrapDenied("selected generation owner changed")
        cursor = state.execution
        if cursor.control_workspace_id is None or cursor.release_operation_id is None:
            raise MigrationBootstrapDenied("selected workspace release disappeared")
        for model, row_id in (
            (ProjectCellWorkspace, cursor.control_workspace_id),
            (ProjectCellOperation, cursor.release_operation_id),
        ):
            if await session.get(model, row_id, with_for_update={"nowait": True}) is None:
                raise MigrationBootstrapDenied("selected workspace release disappeared")
        # Same attempt row used by both Gateway dispatch and response-ledger admission.
        if await session.scalar(text(
            "SELECT attempt_id FROM generation_provider_attempts "
            "WHERE attempt_id=:attempt FOR UPDATE NOWAIT",
        ), {"attempt": scope.attempt_id}) is None:
            raise MigrationBootstrapDenied("selected provider reservation disappeared")
        await require_recovery_readers(release_sha=reader_release_sha, own=False)
        if (
            await drain._active_blockers(session) != _COUNTS
            or not await _selected_run(session, scope, mode="eligibility")
            or await session.scalar(text("SELECT pg_try_advisory_xact_lock(1195724366,:key)"), {
                "key": int.from_bytes(scope.run_id.bytes[-4:], "big", signed=True),
            }) is not True
        ):
            raise MigrationBootstrapDenied("selected provider reservation changed")
        await require_recovery_readers(release_sha=reader_release_sha, own=False)


async def _execute_owned(
    connection: AsyncConnection, scope: ExpiredProviderScope, *, reader_release_sha: str,
) -> MigrationBootstrapResult:
    scripts, migration = _packaged_revision()
    if await connection.scalar(text("SELECT current_schema()")) != "public":
        raise MigrationBootstrapDenied("canonical platform schema is unconfirmed")
    heads = (await connection.scalars(text("SELECT version_num FROM public.alembic_version"))).all()
    if heads == [_NEW_HEAD]:
        await _verify_0107(connection)
        await connection.rollback()
        settled = await _read_eligibility(
            connection, scope, mode="settled", reader_release_sha=reader_release_sha,
        )
        if settled.sql_drained:
            return MigrationBootstrapResult("already_current", True)
    elif heads != [_OLD_HEAD]:
        raise MigrationBootstrapDenied("database migration head is unsupported")
    await connection.rollback()
    eligibility = await _read_eligibility(
        connection, scope, mode="eligibility", reader_release_sha=reader_release_sha,
    )
    if not eligibility.eligible or eligibility.sql_drained or eligibility.active != _COUNTS:
        raise MigrationBootstrapDenied("owned expired reservation eligibility was not confirmed")
    if heads == [_NEW_HEAD]:
        return MigrationBootstrapResult("already_current", False)
    async with connection.begin():
        await connection.execute(text("SET LOCAL lock_timeout = '2s'"))
        await connection.execute(text("SET LOCAL statement_timeout = '20s'"))
        await _lock_selected_rows(connection, scope, reader_release_sha=reader_release_sha)
        await connection.run_sync(_expand_0107, scripts, migration)
        await _verify_0107(connection)
    return MigrationBootstrapResult("upgraded", False)


async def migrate_expired_provider_schema(
    engine: AsyncEngine, scope: ExpiredProviderScope, *, runtime_sha: str, reader_release_sha: str,
) -> MigrationBootstrapResult:
    if runtime_sha != scope.release_sha:
        raise MigrationBootstrapDenied("target API image has a different release identity")
    if reader_release_sha == runtime_sha or not re.fullmatch(r"[0-9a-f]{40}", reader_release_sha):
        raise MigrationBootstrapDenied("incumbent API reader identity is not distinct and exact")
    async with engine.connect() as connection:
        try:
            acquired = await connection.scalar(
                text("SELECT pg_try_advisory_lock(hashtext(:key))"), {"key": drain._LOCK},
            )
            if acquired is not True:
                raise MigrationBootstrapDenied("SQL release owner is busy")
            await connection.commit()
            try:
                return await _execute_owned(
                    connection, scope, reader_release_sha=reader_release_sha,
                )
            finally:
                # Release a session lock explicitly before returning a pooled connection.
                await connection.rollback()
                if await connection.scalar(text("SELECT pg_advisory_unlock(hashtext(:key))"), {
                    "key": drain._LOCK,
                }) is not True:
                    raise MigrationBootstrapDenied("SQL release lock could not be verified")
                await connection.commit()
        except MigrationBootstrapDenied:
            await connection.invalidate()
            raise
        except (SQLAlchemyError, ValueError):
            await connection.invalidate()
            raise MigrationBootstrapDenied("owned schema expansion is unconfirmed") from None
        except BaseException:
            # Even cancellation or lost ACK must never leak a session lock to the pool.
            await connection.invalidate()
            raise


async def _main(request: MigrationBootstrapInput) -> MigrationBootstrapResult:
    settings = get_settings()
    engine = create_async_engine(settings.database_url, poolclass=NullPool)
    try:
        return await migrate_expired_provider_schema(
            engine, request.scope, runtime_sha=settings.omnia_release_sha,
            reader_release_sha=request.reader_release_sha,
        )
    finally:
        await engine.dispose()


def main() -> int:
    try:
        if len(sys.argv) != 1:
            raise MigrationBootstrapDenied("migration accepts only one bounded scope on stdin")
        request = _scope(sys.stdin.buffer.read(4097))
        result = asyncio.run(_main(request))
    except Exception as exc:
        print(json.dumps({"ok": False, "error_class": type(exc).__name__}))
        return 75
    print(json.dumps({
        "ok": True, "head": _NEW_HEAD, "action": result.action, "settled": result.settled,
        "release_sha": request.scope.release_sha, "reader_release_sha": request.reader_release_sha,
    }))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

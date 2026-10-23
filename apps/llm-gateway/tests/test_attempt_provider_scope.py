"""Control closure requires committed expense evidence, including backup calls."""

from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest

from yleum_gateway.services import generation_attempts as attempts
from yleum_gateway.services import provider_calls


@asynccontextmanager
async def context(value):
    yield value


@pytest.fixture
def connection(monkeypatch):
    conn = Mock()
    conn.transaction.side_effect = lambda: context(conn)
    conn.execute = AsyncMock(return_value="UPDATE 1")
    conn.fetchrow = AsyncMock()
    pool = Mock()
    pool.acquire.side_effect = lambda: context(conn)
    monkeypatch.setattr(attempts, "get_pool", lambda: pool)
    return conn


@pytest.mark.parametrize("scope", ["llmgw", "aitunnel"])
async def test_attempt_passes_scope_to_same_transaction_journal(connection, monkeypatch, scope):
    attempt, token, run, project, user = (uuid4() for _ in range(5))
    monkeypatch.setattr(attempts, "_owned", AsyncMock(return_value={"phase": "reserved",
        "dispatch_token": token, "run_id": run, "project_id": project, "user_id": user}))
    insert = AsyncMock()
    monkeypatch.setattr(provider_calls, "insert_call", insert)
    kwargs = {} if scope == "llmgw" else {"provider_scope": scope}
    result = await attempts.mark_attempt_dispatching(attempt, dispatch_token=token,
        route="/v1/messages", model="claude-sonnet-5.5", run_id=run, project_id=project,
        user_id=user, **kwargs)
    assert insert.await_args.args == (connection, result)
    assert insert.await_args.kwargs["provider_scope"] == scope
    assert connection.execute.await_args.args[3] == result


@pytest.mark.parametrize("status,digest,allowed", [
    ("failed", "a" * 64, True), ("started", None, False),
    ("ambiguous", "a" * 64, False), ("failed", None, False),
])
async def test_refused_attempt_closes_only_after_committed_failure(connection, status, digest, allowed):
    attempt = uuid4()
    connection.fetchrow.return_value = {"attempt_id": attempt, "phase": "dispatching",
        "terminal_reason": None, "status": status, "receipt_hash": digest}
    if allowed:
        await attempts.close_attempt(uuid4(), reason="upstream_refused")
        assert connection.execute.await_args.args[1:] == (attempt, "closed", "upstream_refused")
    else:
        with pytest.raises(attempts.AttemptConflict):
            await attempts.close_attempt(uuid4(), reason="upstream_refused")
        connection.execute.assert_not_awaited()


async def test_unknown_reason_fails_without_database_mutation(connection):
    with pytest.raises(attempts.AttemptConflict):
        await attempts.close_attempt(uuid4(), reason="invented")
    connection.fetchrow.assert_not_awaited()
    connection.execute.assert_not_awaited()

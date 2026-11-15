from __future__ import annotations

import asyncio
import hashlib
import io
import json
from contextlib import asynccontextmanager
from typing import Any
from unittest.mock import AsyncMock
from uuid import UUID

import pytest

from yleum_gateway.ops import expired_provider_reservation as cli

TARGET = "a" * 40
INCUMBENT = "b" * 40
SOURCE = "e" * 64


def request() -> dict[str, Any]:
    return {
        "version": 1,
        "action": "close_expired_before_dispatch",
        "release_sha": TARGET,
        "reader_release_sha": INCUMBENT,
        "source_sha256": SOURCE,
        "state_revision": 9,
        "identity": {
            "version": 1,
            "run_id": str(UUID(int=33)),
            "project_id": str(UUID(int=22)),
            "user_id": str(UUID(int=11)),
            "attempt_id": str(UUID(int=44)),
            "prompt_hash": "c" * 64,
            "request_sha256": "d" * 64,
        },
    }


def test_exact_scope_reuses_provider_identity() -> None:
    data = request()
    scope = cli._scope(json.dumps(data).encode(), TARGET)
    assert scope.identity.to_wire() == data["identity"]
    assert (scope.release_sha, scope.state_revision, scope.source_sha256) == (TARGET, 9, SOURCE)


@pytest.mark.parametrize("raw,launch_sha", [
    (lambda: b"", TARGET),
    (lambda: b"x" * 4097, TARGET),
    (lambda: json.dumps(request()).encode("utf-16"), TARGET),
    (lambda: json.dumps(request()).encode() + b"{}", TARGET),
    (lambda: b'{"version":1,' + json.dumps(request()).encode()[1:], TARGET),
    (lambda: json.dumps({**request(), "unknown_private_field": "private-value"}).encode(), TARGET),
    (lambda: json.dumps({**request(), "version": True}).encode(), TARGET),
    (lambda: json.dumps({**request(), "state_revision": True}).encode(), TARGET),
    (lambda: json.dumps({**request(), "state_revision": -1}).encode(), TARGET),
    (lambda: json.dumps({**request(), "reader_release_sha": TARGET}).encode(), TARGET),
    (lambda: json.dumps({**request(), "reader_release_sha": "0" * 40}).encode(), TARGET),
    (lambda: json.dumps({**request(), "release_sha": TARGET.upper()}).encode(), TARGET),
    (lambda: json.dumps({**request(), "source_sha256": SOURCE.upper()}).encode(), TARGET),
    (lambda: json.dumps({**request(), "action": "arbitrary_control"}).encode(), TARGET),
    (lambda: json.dumps(request()).encode(), INCUMBENT),
    (lambda: json.dumps({**request(), "identity": {**request()["identity"], "unexpected": 1}}).encode(), TARGET),
    (lambda: json.dumps({**request(), "identity": {**request()["identity"], "run_id": str(UUID(int=0))}}).encode(), TARGET),
    (lambda: json.dumps({**request(), "identity": {**request()["identity"], "run_id": UUID(int=11).hex}}).encode(), TARGET),
])
def test_rejects_invalid_scope_before_pool(raw, launch_sha, monkeypatch, capsys) -> None:
    monkeypatch.setenv("OMNIA_RELEASE_SHA", launch_sha)
    monkeypatch.setattr(cli.sys, "argv", ["operation"])
    monkeypatch.setattr(cli.sys, "stdin", io.TextIOWrapper(io.BytesIO(raw())))
    connect = AsyncMock()
    monkeypatch.setattr(cli.db, "init_pool", connect)

    assert cli.main() == 3
    connect.assert_not_awaited()
    captured = capsys.readouterr()
    assert json.loads(captured.out) == {"result": "not_confirmed"}
    assert captured.err == ""


@pytest.mark.parametrize("failure", [
    None, "missing_head", "stale_head", "extra_head", "wrong_schema", "constraint", "lock", "writer",
])
def test_receipt_only_after_locked_ddl_guard(monkeypatch, capsys, failure) -> None:
    monkeypatch.setenv("OMNIA_RELEASE_SHA", TARGET)
    monkeypatch.setattr(cli.sys, "argv", ["operation"])
    monkeypatch.setattr(
        cli.sys, "stdin", io.TextIOWrapper(io.BytesIO(json.dumps(request()).encode())),
    )
    receipt = {"version": 1, "kind": "confirmed", "private": "some-private-receipt-value"}
    expected_sha = hashlib.sha256(
        json.dumps(receipt, sort_keys=True, separators=(",", ":")).encode(),
    ).hexdigest()
    held: list[str] = []
    calls: list[str] = []

    class Connection:
        @asynccontextmanager
        async def transaction(self):
            calls.append("start")
            try:
                yield
            finally:
                held.clear()
                calls.append("transaction_closed")

        async def execute(self, statement):
            if failure == "lock":
                raise RuntimeError("private DB connection")
            held.append(statement)

        async def fetchval(self, _query):
            return "shadow" if failure == "wrong_schema" else "public"

        async def fetch(self, query, *params):
            if "alembic_version" in query:
                codes = {
                    "missing_head": [], "stale_head": ["0106_native_transcript_refusal"],
                    "extra_head": [cli._HEAD, "other"],
                }.get(failure, [cli._HEAD])
                return [{"version_num": head} for head in codes]
            assert "convalidated" in query and "public.generation_provider_attempts" in query
            return [
                {"conname": name, "definition": ddl if failure != "constraint" else "CHECK (true)"}
                for name, ddl in cli._EXPECTED_CHECKS.items()
            ]

    class Pool:
        @asynccontextmanager
        async def acquire(self):
            yield Connection()

    async def writer(scope):
        assert scope.release_sha == TARGET
        assert held == [
            "LOCK TABLE public.alembic_version IN SHARE MODE NOWAIT",
            "LOCK TABLE public.generation_provider_attempts IN ACCESS SHARE MODE NOWAIT",
        ]
        calls.append("writer")
        if failure == "writer":
            raise RuntimeError("some-private-receipt-value")
        await asyncio.sleep(0)
        return receipt

    close_pool = AsyncMock()
    monkeypatch.setattr(cli.db, "init_pool", AsyncMock(return_value=Pool()))
    monkeypatch.setattr(cli.db, "close_pool", close_pool)
    monkeypatch.setattr(cli, "close_expired_before_dispatch", writer)

    assert cli.main() == (0 if failure is None else 3)
    close_pool.assert_awaited_once()
    captured = capsys.readouterr()
    assert not captured.err
    result = json.loads(captured.out)
    if failure is None:
        assert result == {"result": "settled", "receipt_sha256": expected_sha}
        assert calls == ["start", "writer", "transaction_closed"]
    else:
        assert result == {"result": "not_confirmed"}
        assert ("writer" in calls) is (failure == "writer")

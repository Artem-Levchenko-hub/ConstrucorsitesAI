"""Read-only provider failures must not poison an order idempotency key."""

import hashlib
import json
from types import SimpleNamespace
from uuid import uuid4

import pytest

from yleum_api.core.errors import ApiError
from yleum_api.services.integration_operations import SafePreDispatchError, execute_once


class _Rows:
    def __init__(self, row: object | None) -> None:
        self.row = row

    def one_or_none(self) -> object | None:
        return self.row


class _Session:
    def __init__(self, row: object | None = None) -> None:
        self.row = row
        self.insert_attempted = False

    async def scalars(self, _statement: object) -> _Rows:
        return _Rows(self.row)

    async def scalar(self, _statement: object) -> None:
        self.insert_attempted = True
        raise AssertionError("preflight must finish before dispatch receipt")


@pytest.mark.asyncio
async def test_preflight_provider_failure_does_not_create_dispatch_receipt() -> None:
    session = _Session()

    async def prepare() -> None:
        raise ApiError("integration_provider_unavailable", "outage", 503)

    async def send() -> dict[str, str]:
        raise AssertionError("order must not be submitted")

    with pytest.raises(ApiError) as error:
        await execute_once(
            session,  # type: ignore[arg-type]
            project_id=uuid4(),
            integration_id=uuid4(),
            provider="moysklad",
            max_user_id=123,
            kind="customer_order",
            client_key="one-order",
            payload={"idempotency_key": "one-order"},
            prepare=prepare,
            send=send,
        )
    assert error.value.status_code == 503
    assert session.insert_attempted is False


@pytest.mark.asyncio
async def test_successful_replay_skips_provider_preflight() -> None:
    payload = {"idempotency_key": "one-order"}
    digest = hashlib.sha256(
        json.dumps(payload, sort_keys=True, ensure_ascii=True, separators=(",", ":")).encode()
    ).hexdigest()
    session = _Session(
        SimpleNamespace(request_digest=digest, status="succeeded", result={"id": "existing"})
    )

    async def prepare() -> None:
        raise AssertionError("replay must not ask provider again")

    async def send() -> dict[str, str]:
        raise AssertionError("replay must not create another order")

    result = await execute_once(
        session,  # type: ignore[arg-type]
        project_id=uuid4(),
        integration_id=uuid4(),
        provider="moysklad",
        max_user_id=123,
        kind="customer_order",
        client_key="one-order",
        payload=payload,
        prepare=prepare,
        send=send,
    )
    assert result == {"id": "existing"}
    assert session.insert_attempted is False


@pytest.mark.asyncio
async def test_failed_read_after_receipt_is_removed_before_any_provider_write() -> None:
    class DispatchSession:
        def __init__(self) -> None:
            self.row = SimpleNamespace(status="dispatching")
            self.deleted = False
            self.commits = 0

        async def scalar(self, _statement: object) -> object:
            return uuid4()

        async def get(self, _model: object, _identifier: object) -> object:
            return self.row

        async def delete(self, row: object) -> None:
            assert row is self.row
            self.deleted = True

        async def commit(self) -> None:
            self.commits += 1

    session = DispatchSession()

    async def send() -> dict[str, str]:
        raise SafePreDispatchError("integration_provider_unavailable", "outage", 503)

    with pytest.raises(SafePreDispatchError):
        await execute_once(
            session,  # type: ignore[arg-type]
            project_id=uuid4(),
            integration_id=uuid4(),
            provider="moysklad",
            max_user_id=123,
            kind="customer_order",
            client_key="one-order",
            payload={"idempotency_key": "one-order"},
            send=send,
        )
    assert session.deleted is True
    assert session.commits == 2

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from omnia_api.core.errors import ApiError
from omnia_api.routers.runtime import _acquire_runtime_start_lock


@pytest.mark.asyncio
async def test_runtime_start_lock_fails_fast_as_conflict() -> None:
    result = SimpleNamespace(scalar_one=lambda: False)
    session = SimpleNamespace(execute=AsyncMock(return_value=result))

    with pytest.raises(ApiError) as exc_info:
        await _acquire_runtime_start_lock(session, uuid4())

    assert exc_info.value.status_code == 409
    statement = str(session.execute.await_args.args[0])
    assert "pg_try_advisory_xact_lock" in statement


@pytest.mark.asyncio
async def test_runtime_start_lock_allows_uncontended_start() -> None:
    result = SimpleNamespace(scalar_one=lambda: True)
    session = SimpleNamespace(execute=AsyncMock(return_value=result))

    await _acquire_runtime_start_lock(session, uuid4())

    session.execute.assert_awaited_once()

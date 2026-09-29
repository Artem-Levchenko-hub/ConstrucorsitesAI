import asyncio
import builtins
from unittest.mock import AsyncMock

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker

from tests.test_generation_runs import _owner_and_project
from yleum_api.core.errors import ApiError
from yleum_api.services.generation_deployment_drain import (
    begin_drain,
    drain_status,
    end_drain,
    require_generation_admission,
)
from yleum_api.services.generation_runs import reserve_generation_run


@pytest.mark.parametrize("active", [0, 1])
async def test_bootstrap_status_works_without_new_model_or_table(monkeypatch, active):
    original = builtins.__import__

    def legacy_import(name, *args, **kwargs):
        if name == "yleum_api.models.deployment_drain":
            raise ModuleNotFoundError("old API has no fence model")
        return original(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", legacy_import)
    session = AsyncMock()
    session.scalar.side_effect = [active, 0, 0, 0]
    result = await drain_status(session, bootstrap=True)
    assert result["drained"] is (active == 0)
    assert result["bootstrap_readonly"] is True
    session.get.assert_not_awaited()
    session.execute.assert_not_awaited()


async def test_durable_drain_blocks_new_run_but_replays_existing_idempotency(db_session):
    owner, project = await _owner_and_project(db_session)
    args = dict(
        project_id=project.id, user_id=owner.id, prompt="Build", idempotency_key="before-drain"
    )
    original, _ = await reserve_generation_run(db_session, **args)
    await db_session.commit()
    await begin_drain(db_session, "a" * 40)
    await db_session.commit()
    replay, reused = await reserve_generation_run(db_session, **args)
    assert reused and replay.id == original.id
    with pytest.raises(ApiError) as denied:
        await reserve_generation_run(db_session, **{**args, "idempotency_key": "after-drain"})
    assert (denied.value.code, denied.value.status_code) == ("generation_draining", 503)
    await db_session.rollback()
    with pytest.raises(ValueError, match="another release"):
        await end_drain(db_session, "b" * 40)
    await db_session.rollback()
    await end_drain(db_session, "a" * 40)
    await db_session.commit()
    await require_generation_admission(db_session)


async def test_fence_serializes_against_inflight_admission_transaction(test_engine):
    factory = async_sessionmaker(test_engine, expire_on_commit=False)
    async with factory() as admitted, factory() as deploy:
        await require_generation_admission(admitted)
        started = asyncio.Event()

        async def close():
            started.set()
            await begin_drain(deploy, "c" * 40)
            await deploy.commit()

        task = asyncio.create_task(close())
        await started.wait()
        await asyncio.sleep(0.05)
        assert not task.done()
        await admitted.commit()
        await asyncio.wait_for(task, 2)
    async with factory() as next_admission:
        with pytest.raises(ApiError):
            await require_generation_admission(next_admission)
        await next_admission.rollback()
        await end_drain(next_admission, "c" * 40)
        await next_admission.commit()

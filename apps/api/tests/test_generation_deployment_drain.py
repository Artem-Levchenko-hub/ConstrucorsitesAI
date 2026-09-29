import asyncio
import builtins
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

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


@pytest.mark.parametrize(
    "bad", [None, "target", "workspace", "fence", "request", "status", "response"]
)
def test_only_exact_newer_completed_reconcile_settles_history(bad):
    from yleum_api.models.project_cell import ProjectCellOperation
    from yleum_api.services.generation_deployment_drain import reconciliation_settles
    from yleum_api.services.orchestrator_client import ProjectCellResourceResponse
    from yleum_api.services.project_cells import _canonical_operation_envelope

    target = ProjectCellOperation(
        id=uuid4(), workspace_id=uuid4(), status="indeterminate", kind="pause", fencing_epoch=10
    )
    receipt = ProjectCellOperation(
        id=uuid4(),
        workspace_id=target.workspace_id,
        generation_run_id=None,
        kind="reconcile",
        status="completed",
        fencing_epoch=12,
    )
    receipt.request_payload, receipt.request_digest = _canonical_operation_envelope(
        target.workspace_id, None, "reconcile", {"indeterminate_operation_id": str(target.id)}
    )
    receipt.result_payload = ProjectCellResourceResponse(
        workspace_id=target.workspace_id,
        state="degraded",
        provider_ref="owned",
        fencing_epoch=12,
        checkpoint_ref=None,
        has_workspace=True,
        has_agent_home=True,
        has_postgres=True,
        has_redis=True,
    ).to_wire_json() | {"reconciles_operation_id": str(target.id)}
    if bad == "target":
        receipt.result_payload["reconciles_operation_id"] = str(uuid4())
    if bad == "workspace":
        receipt.workspace_id = uuid4()
    if bad == "fence":
        receipt.fencing_epoch = 10
    if bad == "request":
        receipt.request_digest = "invalid"
    if bad == "status":
        receipt.status = "indeterminate"
    if bad == "response":
        receipt.result_payload["fencing_epoch"] = 11
    assert reconciliation_settles(target, receipt) is (bad is None)
    assert target.status == "indeterminate"


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
    session.scalars.return_value = SimpleNamespace(all=lambda: [])
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

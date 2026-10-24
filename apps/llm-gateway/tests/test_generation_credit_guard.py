from types import SimpleNamespace
from uuid import uuid4

import pytest

from yleum_gateway.core.errors import BillingReconciliationRequiredError
from yleum_gateway.services.generation_credit_guard import covered


@pytest.fixture
def proof():
    user, project, run, account, grant = (uuid4() for _ in range(5))
    identity = SimpleNamespace(
        user_id=user,
        project_id=project,
        run_id=run,
        attempt_id=uuid4(),
        request_sha256="a" * 64,
        prompt_hash="b" * 64,
    )
    row = dict(
        version="accepted-units-v2",
        is_free=False,
        user_id=user,
        project_id=project,
        status="running",
        billing_account_id=account,
        project_owner=user,
        hold_user=user,
        hold_project=project,
        kind="edit",
        state="held",
        units=1,
        allocations=[{"grant_id": str(grant), "units": 1}],
    )

    class Connection:
        async def fetchrow(self, query, *args):
            if query.startswith("SELECT p.version"):
                return row
            if query.startswith("SELECT request_sha256"):
                return dict(request_sha256="a" * 64, prompt_hash="b" * 64)
            return dict(bucket="flexible", units=5)

        async def fetchval(self, *args):
            return user

    return Connection(), identity, row


async def test_bound_generation_hold_replaces_wallet_floor_without_free_spoof(proof):
    conn, identity, row = proof
    assert await covered(
        conn, identity.user_id, identity.project_id, identity.run_id, "native_agent", identity
    )


@pytest.mark.parametrize(
    "change",
    [
        {"is_free": True},
        {"state": "released"},
        {"units": 2},
        {"hold_user": None},
        {"hold_project": None},
        {"kind": "initial"},
    ],
)
async def test_wrong_owner_state_quantity_or_type_is_rejected(proof, change):
    conn, identity, row = proof
    row.update(change)
    with pytest.raises(BillingReconciliationRequiredError):
        await covered(
            conn, identity.user_id, identity.project_id, identity.run_id, "native_agent", identity
        )


@pytest.mark.parametrize("stage", ["runtime_ai", "visitor", "arbitrary"])
async def test_runtime_visitor_cannot_borrow_generation_hold(proof, stage):
    conn, identity, row = proof
    with pytest.raises(BillingReconciliationRequiredError):
        await covered(conn, identity.user_id, identity.project_id, identity.run_id, stage, identity)


async def test_missing_registered_identity_is_not_coverage(proof):
    conn, identity, row = proof
    with pytest.raises(BillingReconciliationRequiredError):
        await covered(conn, identity.user_id, identity.project_id, identity.run_id, None, None)

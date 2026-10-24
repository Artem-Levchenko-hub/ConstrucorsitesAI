"""Prove generation-unit coverage from committed SQL, never caller free flags."""

import json
from typing import Any
from uuid import UUID

from yleum_gateway.core.errors import BillingReconciliationRequiredError

_STAGES = {
    None,
    "native_agent",
    "build_plan",
    "verification",
    "build",
    "edit",
    "director",
    "polish",
    "audit",
    "repair",
    "source_repair",
    "generation",
    "discovery",
    "interview",
}


async def covered(
    conn: Any,
    user_id: UUID,
    project_id: UUID | None,
    run_id: UUID | None,
    stage: str | None,
    identity: Any,
    *,
    dispatch: bool = True,
) -> bool:
    if run_id is None:
        return False
    row = await conn.fetchrow(
        "SELECT p.version,p.is_free,r.user_id,r.project_id,pr.owner_id AS project_owner,r.status,h.billing_account_id,h.user_id AS hold_user,h.project_id AS hold_project,h.kind,h.state,h.units,h.allocations FROM generation_runs r JOIN projects pr ON pr.id=r.project_id JOIN generation_billing_policies p ON p.run_id=r.id LEFT JOIN generation_credit_reservations h ON h.run_id=r.id WHERE r.id=$1",
        run_id,
    )
    if row is None or row["version"] == "accepted-build-v1":
        return False
    if row["version"] != "accepted-units-v2":
        raise BillingReconciliationRequiredError("Unknown generation policy")
    fail = (
        stage not in _STAGES
        or identity is None
        or row["is_free"] is not False
        or row["user_id"] != user_id
        or row["project_id"] != project_id
        or row["project_owner"] != user_id
        or row["hold_user"] != user_id
        or row["hold_project"] != project_id
        or not row["units"]
        or row["units"] < 1
        or (
            dispatch
            and (
                row["state"] != "held"
                or row["status"] not in {"pending", "running", "queued_for_capacity"}
            )
        )
    )
    if fail:
        raise BillingReconciliationRequiredError("Generation unit hold is not valid")
    if (
        identity.run_id != run_id
        or identity.project_id != project_id
        or identity.user_id != user_id
    ):
        raise BillingReconciliationRequiredError("Generation identity conflicts")
    attempt = await conn.fetchrow(
        "SELECT request_sha256,prompt_hash FROM generation_provider_attempts WHERE attempt_id=$1 AND run_id=$2 AND project_id=$3 AND user_id=$4",
        identity.attempt_id,
        run_id,
        project_id,
        user_id,
    )
    if (
        not attempt
        or attempt["request_sha256"] != identity.request_sha256
        or attempt["prompt_hash"] != identity.prompt_hash
    ):
        raise BillingReconciliationRequiredError("Generation attempt conflicts")
    allocations = (
        json.loads(row["allocations"])
        if isinstance(row["allocations"], str)
        else row["allocations"]
    )
    if not isinstance(allocations, list) or not allocations:
        raise BillingReconciliationRequiredError("Unit allocations missing")
    total = 0
    buckets = []
    seen = set()
    for item in allocations:
        if (
            not isinstance(item, dict)
            or set(item) != {"grant_id", "units"}
            or type(item["units"]) is not int
            or item["units"] < 1
        ):
            raise BillingReconciliationRequiredError("Unit allocation invalid")
        key = UUID(item["grant_id"])
        if key in seen:
            raise BillingReconciliationRequiredError("Unit grant duplicated")
        seen.add(key)
        grant = await conn.fetchrow(
            "SELECT bucket,units FROM generation_credit_grants WHERE id=$1 AND billing_account_id=$2",
            key,
            row["billing_account_id"],
        )
        if not grant or grant["units"] < item["units"]:
            raise BillingReconciliationRequiredError("Unit grant binding conflicts")
        total += item["units"]
        buckets.append(grant["bucket"])
    valid = (
        row["kind"] == "initial"
        and (
            (buckets == ["initial"] and total == 1) or (set(buckets) == {"flexible"} and total == 5)
        )
    ) or (
        row["kind"] == "edit" and total == 1 and set(buckets) <= {"edit", "edit_only", "flexible"}
    )
    if not valid or total != row["units"]:
        raise BillingReconciliationRequiredError("Unit quantity conflicts")
    owner = await conn.fetchval(
        "SELECT personal_user_id FROM billing_accounts WHERE id=$1", row["billing_account_id"]
    )
    if owner != user_id:
        raise BillingReconciliationRequiredError("Unit account conflicts")
    return True

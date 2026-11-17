"""Prove the committed generation billing policy before provider dispatch."""

import json
from typing import Any
from uuid import UUID

from yleum_gateway.core.errors import BillingReconciliationRequiredError
from yleum_gateway.services.generation_identity import GenerationProviderIdentity

KIMI_PILOT_STAGES = frozenset({"build_plan", "native_agent", "verification"})
KIMI_REPAIR_STAGE = "source_repair"

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


async def _read_coverage(conn: Any, run_id: UUID, *, initial_build: bool) -> Any:
    # Only the explicitly pinned initial-build pilot reads frozen dispatch
    # fields. Keep submitted request metadata out of this authority check.
    build_fields = """
        , r.response_mode, pr.template AS project_template, r.prompt_hash AS run_prompt_hash,
        r.user_message_id, r.assistant_message_id,
        r.agent_state #>> '{dispatch,force_model}' AS frozen_model,
        r.agent_state #>> '{dispatch,user_id}' AS dispatch_user,
        r.agent_state #>> '{dispatch,project_id}' AS dispatch_project,
        (r.agent_state #> '{dispatch,schema_version}' = '1'::jsonb
         AND r.agent_state #> '{dispatch,is_free}' = to_jsonb(p.is_free)) AS dispatch_policy_eligible,
        (r.agent_state #>> '{recovery_v1,execution,stage}' = 'finalization'
         AND r.agent_state #> '{recovery_v1,execution,budget,repair_calls}' = '2'::jsonb) AS repair_window
    """ if initial_build else ""
    return await conn.fetchrow(
        "SELECT p.version,p.is_free,r.user_id,r.project_id,pr.owner_id AS project_owner,"
        "r.status,h.billing_account_id,h.user_id AS hold_user,h.project_id AS hold_project,"
        f"h.kind,h.state,h.units,h.allocations {build_fields} "
        "FROM generation_runs r JOIN projects pr ON pr.id=r.project_id "
        "JOIN generation_billing_policies p ON p.run_id=r.id "
        "LEFT JOIN generation_credit_reservations h ON h.run_id=r.id WHERE r.id=$1",
        run_id,
    )


def _initial_build_matches(
    row: Any, identity: GenerationProviderIdentity, model: str, message_id: UUID | None,
) -> bool:
    return (
        row["response_mode"] == "build"
        and _max_dispatch_matches(row, identity)
        and (message_id is None or message_id in {row["user_message_id"], row["assistant_message_id"]})
        and row["frozen_model"] == model
    )


def _max_dispatch_matches(row: Any, identity: GenerationProviderIdentity) -> bool:
    return (
        row["project_template"] == "max_miniapp"
        and row["user_id"] == row["project_owner"] == identity.user_id
        and row["project_id"] == identity.project_id
        and row["run_prompt_hash"] == identity.prompt_hash
        and row["dispatch_user"] == str(identity.user_id)
        and row["dispatch_project"] == str(identity.project_id)
        and row["dispatch_policy_eligible"] is True
    )


async def _attempt_matches(conn: Any, identity: GenerationProviderIdentity) -> bool:
    attempt = await conn.fetchrow(
        "SELECT request_sha256,prompt_hash FROM generation_provider_attempts "
        "WHERE attempt_id=$1 AND run_id=$2 AND project_id=$3 AND user_id=$4",
        identity.attempt_id, identity.run_id, identity.project_id, identity.user_id,
    )
    return bool(
        attempt and attempt["request_sha256"] == identity.request_sha256
        and attempt["prompt_hash"] == identity.prompt_hash
    )


async def _has_active_qa_plan(conn: Any, user_id: UUID) -> bool:
    # Select the same effective live subscription as the API first. Checking
    # only for the existence of a QA row could revive a superseded plan.
    plan = await conn.fetchrow(
        "SELECT u.role,s.user_id AS subscriber,s.status AS subscription_status,"
        "p.code AS plan_code,p.is_active AS plan_active,"
        "(p.entitlements->'qa_unlimited_generations' = 'true'::jsonb) AS qa_unlimited,"
        "(s.current_period_end IS NULL OR s.current_period_end > now()) AS in_period "
        "FROM users u JOIN billing_accounts a ON a.personal_user_id=u.id "
        "JOIN subscriptions s ON s.billing_account_id=a.id "
        "JOIN billing_plans p ON p.id=s.plan_id "
        "WHERE u.id=$1 AND s.status IN ('trialing','active','past_due','paused') "
        "ORDER BY s.created_at DESC LIMIT 1",
        user_id,
    )
    return bool(
        plan and plan["role"] == "admin" and plan["subscriber"] == user_id
        and plan["subscription_status"] == "active" and plan["plan_code"] == "qa_internal"
        and plan["plan_active"] is False and plan["qa_unlimited"] is True and plan["in_period"] is True
    )


async def kimi_pilot_allowed(
    conn: Any, identity: GenerationProviderIdentity, stage: str, model: str,
    *, requested_free: bool, message_id: UUID | None = None,
) -> bool:
    if stage == KIMI_REPAIR_STAGE:
        row = await _read_coverage(conn, identity.run_id, initial_build=True)
        if (
            not row or row["status"] != "running" or row["response_mode"] not in {"build", "edit"}
            or row["repair_window"] is not True
            or message_id is None or row["assistant_message_id"] != message_id
            or row["is_free"] is not requested_free or not _max_dispatch_matches(row, identity)
        ):
            return False
        if requested_free:
            return bool(
                row["version"] == "accepted-build-v1" and row["kind"] is None
                and await _attempt_matches(conn, identity)
                and await _has_active_qa_plan(conn, identity.user_id)
            )
        return await covered(conn, identity.user_id, identity.project_id, identity.run_id, stage, identity)
    if stage not in KIMI_PILOT_STAGES:
        return False
    if not requested_free:
        return await covered(
            conn, identity.user_id, identity.project_id, identity.run_id, stage, identity,
            initial_build_model=model, initial_build_message_id=message_id,
        )

    row = await _read_coverage(conn, identity.run_id, initial_build=True)
    return bool(
        row and row["version"] == "accepted-build-v1" and row["is_free"] is True
        and row["status"] == "running" and row["kind"] is None
        and _initial_build_matches(row, identity, model, message_id)
        and await _attempt_matches(conn, identity)
        and await _has_active_qa_plan(conn, identity.user_id)
    )


async def covered(
    conn: Any,
    user_id: UUID,
    project_id: UUID | None,
    run_id: UUID | None,
    stage: str | None,
    identity: Any,
    *,
    dispatch: bool = True,
    initial_build_model: str | None = None,
    initial_build_message_id: UUID | None = None,
) -> bool:
    if run_id is None:
        return False
    row = await _read_coverage(conn, run_id, initial_build=initial_build_model is not None)
    if row is None or row["version"] == "accepted-build-v1":
        return False
    if row["version"] != "accepted-units-v2":
        raise BillingReconciliationRequiredError("Unknown generation policy")
    if initial_build_model is not None and (
        identity is None or row["kind"] != "initial"
        or not _initial_build_matches(row, identity, initial_build_model, initial_build_message_id)
    ):
        return False
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
    if not await _attempt_matches(conn, identity):
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

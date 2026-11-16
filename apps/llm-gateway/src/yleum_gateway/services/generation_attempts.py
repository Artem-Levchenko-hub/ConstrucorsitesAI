"""Durable dispatch control; no billing, response retention or replay authority."""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Literal
from uuid import UUID, uuid4, uuid5

from yleum_gateway.core.config import get_settings
from yleum_gateway.core.db import get_pool
from yleum_gateway.core.errors import GatewayError
from yleum_gateway.services import provider_calls
from yleum_gateway.services.generation_identity import (
    GenerationProviderIdentity,
    decode_request,
    request_digest,
)

NATIVE_TRANSCRIPT_REJECTED = "native_transcript_rejected"
EXPIRED_BEFORE_DISPATCH = "expired_before_dispatch"
_EXPIRY_RECEIPT_KIND = "gateway_expired_before_dispatch"
_ADMISSION_LOCK = "yleum:generation:deployment-drain:v1"


class AttemptConflict(GatewayError):
    """Dispatch was not confirmed; never fall through to legacy provider admission."""

    code = "generation_attempt_unconfirmed"
    http_status = 409


@dataclass(frozen=True, slots=True)
class AttemptAdmission:
    action: Literal["dispatch", "stored", "pending", "conflict"]
    dispatch_token: UUID | None = None
    provider_call_id: UUID | None = None
    response_manifest_key: str | None = None


@dataclass(frozen=True, slots=True)
class ExpiredReservationScope:
    identity: GenerationProviderIdentity
    release_sha: str
    state_revision: int
    source_sha256: str


@dataclass(frozen=True, slots=True)
class RuntimeExpiredReservationScope:
    identity: GenerationProviderIdentity
    state_revision: int
    context_sha256: str
    source_sha256: str


@dataclass(frozen=True, slots=True)
class ReleaseExpiredReservationScope(RuntimeExpiredReservationScope):
    release_sha: str


def _record(value: Any) -> dict[str, Any]:
    if isinstance(value, str):
        value = json.loads(value)
    if type(value) is not dict:
        raise ValueError("invalid generation record")
    return value


def _release_matches(reply: dict[str, Any], workspace: UUID, fence: int) -> bool:
    return (
        (reply.get("workspace_id"), reply.get("fencing_epoch"), reply.get("state"))
        == (str(workspace), fence, "resources_ready")
        and type(reply["fencing_epoch"]) is int
        and all(type(reply.get(key)) is bool for key in (
            "has_workspace", "has_agent_home", "has_postgres", "has_redis",
        ))
    )


async def _locked_parked_cursor(
    conn: Any, scope: ExpiredReservationScope,
) -> tuple[dict[str, Any], dict[str, Any], int, int, Any]:
    """Snapshot the selected parked owner under the existing admission fence."""
    identity = scope.identity
    if (
        not re.fullmatch(r"[0-9a-f]{40}", scope.release_sha)
        or not re.fullmatch(r"[0-9a-f]{64}", scope.source_sha256)
        or type(scope.state_revision) is not int or scope.state_revision < 0
        or not await conn.fetchval("SELECT pg_try_advisory_xact_lock(hashtext($1))", _ADMISSION_LOCK)
        or await conn.fetchval(
            "SELECT release_sha FROM deployment_drains WHERE scope='generation'",
        ) != scope.release_sha
        or not await conn.fetchval(
            "SELECT pg_try_advisory_xact_lock(1195724366,$1)",
            int.from_bytes(identity.run_id.bytes[-4:], "big", signed=True),
        )
    ):
        raise ValueError("generation drain ownership is not confirmed")
    return await _locked_released_cursor(conn, scope)


async def _runtime_admission_owned(conn: Any, scope: RuntimeExpiredReservationScope) -> bool:
    if isinstance(scope, ReleaseExpiredReservationScope):
        return bool(
            re.fullmatch(r"[0-9a-f]{40}", scope.release_sha)
            and await conn.fetchval(
                "SELECT pg_try_advisory_xact_lock(hashtext($1))", _ADMISSION_LOCK,
            )
            and await conn.fetchval(
                "SELECT release_sha FROM deployment_drains WHERE scope='generation'",
            ) == scope.release_sha
        )
    return bool(
        await conn.fetchval(
            "SELECT pg_try_advisory_xact_lock_shared(hashtext($1))", _ADMISSION_LOCK,
        )
        and not await conn.fetchval(
            "SELECT EXISTS(SELECT 1 FROM deployment_drains WHERE scope='generation')",
        )
    )


async def _locked_runtime_cursor(
    conn: Any, scope: RuntimeExpiredReservationScope,
) -> tuple[dict[str, Any], dict[str, Any], int, int, Any]:
    # The worker calls before acquiring its own per-run lock. Do not wait on
    # either a deployment or an executing worker, or hold these locks over IO.
    await conn.execute("SET LOCAL statement_timeout = '3s'")
    if (
        type(scope.state_revision) is not int or scope.state_revision < 0
        or not _hex_digest(scope.source_sha256) or not _hex_digest(scope.context_sha256)
        or not await _runtime_admission_owned(conn, scope)
        or not await conn.fetchval(
            "SELECT pg_try_advisory_xact_lock(1195724366,$1)",
            int.from_bytes(scope.identity.run_id.bytes[-4:], "big", signed=True),
        )
    ):
        raise ValueError("generation runtime ownership is not confirmed")
    return await _locked_released_cursor(conn, scope)


def _runtime_repair_budget_matches(
    agent: dict[str, Any], state: dict[str, Any], cursor: dict[str, Any],
) -> bool:
    calls = _record(cursor["budget"])["repair_calls"]
    if type(calls) is not int or calls not in {1, 2, 3}:
        return False
    if calls < 3:
        return True
    marker = agent.get("retired_catalog_repair_resume_v1")
    clock = _record(state["clock"])
    if (
        not isinstance(marker, dict)
        or set(marker) != {"version", "segment_seq", "deadline", "predecessor_attachment"}
        or type(marker["version"]) is not int or marker["version"] != 1
        or type(marker["segment_seq"]) is not int
        or type(clock.get("segment_seq")) is not int or marker["segment_seq"] != clock["segment_seq"]
        or clock.get("stage") != "repair" or clock.get("repair_granted") is not True
        or not isinstance(clock.get("segment_deadline"), str)
        or not isinstance(marker["deadline"], str)
        or not (marker["predecessor_attachment"] is None
                or isinstance(marker["predecessor_attachment"], dict))
    ):
        return False
    deadline = datetime.fromisoformat(marker["deadline"])
    current_deadline = datetime.fromisoformat(clock["segment_deadline"])
    return (
        deadline.utcoffset() == timedelta(0)
        and deadline.isoformat() == marker["deadline"]
        and current_deadline.utcoffset() == timedelta(0)
        and current_deadline <= deadline
    )


async def _locked_released_cursor(
    conn: Any, scope: ExpiredReservationScope | RuntimeExpiredReservationScope,
) -> tuple[dict[str, Any], dict[str, Any], int, int, Any]:
    runtime = isinstance(scope, RuntimeExpiredReservationScope)
    identity = scope.identity
    owner = await conn.fetchrow(
        "SELECT r.agent_state,r.started_at,r.execution_started_at,p.current_snapshot_id "
        "FROM generation_runs r "
        "JOIN projects p ON p.id=r.project_id WHERE r.id=$1 AND p.id=$2 "
        "AND r.user_id=$3 AND p.owner_id=$3 AND r.prompt_hash=$4 AND r.status='running' "
        "AND r.execution_backend='worker' AND r.response_mode IN ('build','edit') "
        "AND p.template='max_miniapp' FOR UPDATE OF p,r NOWAIT",
        identity.run_id, identity.project_id, identity.user_id, identity.prompt_hash,
    )
    if owner is None:
        raise ValueError("generation run is not an owned parked MAX run")
    agent = _record(owner["agent_state"])
    state = _record(agent["recovery_v1"])
    own = _record(state["identity"])
    cursor = _record(state["execution"])
    binding = _record(state["binding"])
    snapshot = owner["current_snapshot_id"]
    revision = state["state_revision"]
    attempt_seq = state["attempt_seq"]
    step = cursor["logical_step_seq"]
    fence = binding["fencing_epoch"]
    if (
        "restoration_adaptation" in agent
        or type(state["version"]) is not int or state["version"] != 3
        or type(revision) is not int or revision != scope.state_revision
        or type(attempt_seq) is not int or attempt_seq < 1
        or (not runtime and (
            state["phase"] != "needs_input" or state["next_check_at"] is not None
        ))
        or state["unresolved_operation_ids"] != []
        or (own["run_id"], own["project_id"], own["user_id"], own["prompt_hash"]) != (
            str(identity.run_id), str(identity.project_id), str(identity.user_id), identity.prompt_hash,
        )
        or own["accepted_snapshot_id"] != (str(snapshot) if snapshot else None)
        or cursor["stage"] != "finalization" or cursor["substep"] != "response_wait"
        or cursor["physical_state"] != "released" or type(step) is not int or step < 1
        or type(fence) is not int or fence < 1 or type(cursor["control_fencing_epoch"]) is not int
        or cursor["control_fencing_epoch"] != fence + 1
        or cursor["control_workspace_id"] != binding["workspace_id"]
        or binding["source_sha256"] != scope.source_sha256
        or set(binding) != {
            "workspace_id", "fencing_epoch", "source_sha256", "schema_sha256", "environment_sha256",
        }
        or any(
            not re.fullmatch(r"[0-9a-f]{64}", binding[key])
            for key in ("schema_sha256", "environment_sha256")
        )
        or state["context_ref"] != cursor["execution_context_ref"]
    ):
        raise ValueError("generation parked scope changed")
    for ref in (state["checkpoint"], state["context_ref"]):
        if (
            type(ref) is not dict or set(ref) != {"object_key", "sha256"}
            or type(ref["object_key"]) is not str or not ref["object_key"]
            or not re.fullmatch(r"[0-9a-f]{64}", ref["sha256"])
        ):
            raise ValueError("generation retained references are unconfirmed")
    if isinstance(scope, RuntimeExpiredReservationScope):
        claim = _record(state["continuation_claim"])
        phase, due = state["phase"], state["next_check_at"]
        if (
            owner["started_at"] is None or owner["execution_started_at"] is None
            or not _runtime_repair_budget_matches(agent, state, cursor)
            or phase not in {"working", "waiting_dependency"}
            or (phase == "waiting_dependency") != (due is not None)
            or (due is not None and (
                (next_check := datetime.fromisoformat(due)).utcoffset() is None
                # The named drain owns this exact cursor independently of the
                # worker schedule. The persisted dispatch deadline is checked below.
                or (
                    not isinstance(scope, ReleaseExpiredReservationScope)
                    and next_check > await conn.fetchval("SELECT clock_timestamp()")
                )
            ))
            or _record(state["clock"])["wait_dependency"] is not None
            or state["context_ref"]["sha256"] != scope.context_sha256
            or claim["run_id"] != str(identity.run_id)
            or type(claim["attempt_seq"]) is not int or claim["attempt_seq"] != attempt_seq
            or type(cursor["next_tool_index"]) is not int or cursor["next_tool_index"] != 0
            or cursor["response_sha256"] is not None
            or type(cursor["lease_cycle_seq"]) is not int or cursor["lease_cycle_seq"] < 0
        ):
            raise ValueError("generation runtime scope changed")
    workspace = UUID(binding["workspace_id"])
    release = await conn.fetchrow(
        "SELECT o.* FROM project_cell_workspaces w "
        "JOIN project_cell_operations o ON o.workspace_id=w.id "
        "WHERE w.id=$1 AND w.project_id=$2 AND w.owner_id=$3 AND w.deleted_at IS NULL "
        "AND w.generation_run_id IS NULL AND w.fencing_epoch=$4 AND o.id=$5 "
        "AND o.generation_run_id=$6 AND o.kind='release' AND o.status='completed' "
        "AND o.fencing_epoch=$4 AND o.finished_at IS NOT NULL FOR UPDATE OF w,o NOWAIT",
        workspace, identity.project_id, identity.user_id, fence + 1,
        UUID(cursor["release_operation_id"]), identity.run_id,
    )
    if release is None:
        raise ValueError("current physical release is unconfirmed")
    reply = _record(release["result_payload"])
    if (
        not _release_matches(reply, workspace, fence + 1)
        or await conn.fetchval(
            "SELECT EXISTS(SELECT 1 FROM project_cell_activity_leases "
            "WHERE (workspace_id=$1 OR generation_run_id=$2) AND finished_at IS NULL)",
            workspace, identity.run_id,
        )
    ):
        raise ValueError("current physical release is unconfirmed")
    if runtime and await conn.fetchval(
        "SELECT EXISTS(SELECT 1 FROM project_cell_operations "
        "WHERE (workspace_id=$1 OR generation_run_id=$2 OR execution_run_id=$2) "
        "AND status IN ('pending','waiting_capacity','running'))",
        workspace, identity.run_id,
    ):
        raise ValueError("generation has an active operation")
    return cursor, binding, step, attempt_seq, release


_CELL_PROFILES = ("docker-owner-cell-resources-v1", "docker-owner-cell-resources-v2")


def _runtime_lease_operation_matches(
    operation: Any, identity: GenerationProviderIdentity, *, workspace: UUID, fence: int,
    profile: str, cycle: int, kind: str, request: dict[str, Any],
) -> bool:
    envelope = {
        "workspace_id": str(workspace), "generation_run_id": str(identity.run_id),
        "kind": kind, "request": request,
    }
    reply = _record(operation["result_payload"])
    attempt_count = operation["attempt_count"]
    # A completed release may have reconciled the same envelope after an ACK loss.
    if (
        operation["workspace_id"] != workspace or operation["kind"] != kind
        or operation["generation_run_id"] != identity.run_id
        or operation["execution_run_id"] != identity.run_id
        or operation["fencing_epoch"] != fence
        or type(attempt_count) is not int or attempt_count < 1
        or (kind == "ensure" and attempt_count != 1)
        or operation["status"] != "completed" or operation["error"] is not None
        or operation["capacity_reason"] is not None or operation["next_attempt_at"] is not None
        or operation["idempotency_key"]
        != f"generation:{identity.run_id}:{kind}:{profile}:cycle:{cycle}"
        or _record(operation["request_payload"]) != envelope
        or operation["request_digest"] != _record_sha(envelope)
        or not _release_matches(reply, workspace, fence)
        or not all(reply[key] for key in (
            "has_workspace", "has_agent_home", "has_postgres", "has_redis",
        ))
        or operation["started_at"] is None or operation["finished_at"] is None
        or operation["started_at"] > operation["finished_at"]
    ):
        return False
    observation = reply.get("lease_observation")
    if observation is None:
        return True
    observed = _record(observation)
    ensuring = kind == "ensure"
    return (
        (observed.get("workspace_id"), observed.get("project_id"), observed.get("owner_id"))
        == (str(workspace), str(identity.project_id), str(identity.user_id))
        and (observed.get("operation_id"), observed.get("request_digest"))
        == (str(operation["id"]), operation["request_digest"])
        and type(observed.get("fencing_epoch")) is int and observed["fencing_epoch"] == fence
        and observed.get("active_generation_run_id") == (str(identity.run_id) if ensuring else None)
        and observed.get("active_generation_fencing_epoch") == (fence if ensuring else None)
        and observed.get("quiescent") is not ensuring
        and type(observed.get("quiescent")) is bool
    )


async def _require_reconciled_runtime_history(
    conn: Any, identity: GenerationProviderIdentity, *, workspace: UUID,
    first_fence: int, created: datetime,
) -> None:
    """Earlier unresolved rows need their own committed, superseding observation."""
    unresolved = await conn.fetch(
        "SELECT o.*,r.project_id,r.user_id FROM project_cell_operations o "
        "LEFT JOIN generation_runs r ON r.id=o.generation_run_id "
        "WHERE (o.workspace_id=$1 OR o.generation_run_id=$2 OR o.execution_run_id=$2) "
        "AND o.status='indeterminate' FOR UPDATE OF o NOWAIT",
        workspace, identity.run_id,
    )
    for old in unresolved:
        old_fence, run_id = old["fencing_epoch"], old["generation_run_id"]
        if (
            old["workspace_id"] != workspace or run_id is None
            or (old["project_id"], old["user_id"]) != (identity.project_id, identity.user_id)
            or type(old_fence) is not int or not 0 < old_fence < first_fence
            or old["started_at"] is None
        ):
            raise ValueError("generation has an unresolved operation")
        target = str(old["id"])
        # Capacity recovery executes as the requesting run while reconciling the
        # old lease. Both owners have already been bound to this project.
        matches = await conn.fetch(
            "SELECT * FROM project_cell_operations WHERE workspace_id=$1 "
            "AND generation_run_id=$2 AND execution_run_id IN ($2,$8) AND kind='reconcile' "
            "AND status='completed' AND fencing_epoch>$3 AND fencing_epoch<=$4 "
            "AND request_payload->'request'->>'indeterminate_operation_id'=$5 "
            "AND started_at>=$6 AND finished_at>=started_at AND finished_at<=$7 "
            "FOR UPDATE NOWAIT",
            workspace, run_id, old_fence, first_fence, target,
            old["finished_at"] or old["started_at"], created, identity.run_id,
        )
        if len(matches) != 1:
            raise ValueError("historical operation reconciliation is unconfirmed")
        reconciled = matches[0]
        envelope = {
            "workspace_id": str(workspace), "generation_run_id": str(run_id), "kind": "reconcile",
            "request": {"indeterminate_operation_id": target},
        }
        reply = _record(reconciled["result_payload"])
        if (
            reconciled["error"] is not None
            or _record(reconciled["request_payload"]) != envelope
            or reconciled["request_digest"] != _record_sha(envelope)
            or reply.get("reconciles_operation_id") != target
            or not _release_matches(reply, workspace, reconciled["fencing_epoch"])
            or not all(reply[key] for key in (
                "has_workspace", "has_agent_home", "has_postgres", "has_redis",
            ))
        ):
            raise ValueError("historical operation reconciliation conflicts")
        if (observation := reply.get("lease_observation")) is not None:
            observed = _record(observation)
            if (
                (observed.get("workspace_id"), observed.get("project_id"), observed.get("owner_id"))
                != (str(workspace), str(identity.project_id), str(identity.user_id))
                or (observed.get("operation_id"), observed.get("request_digest"))
                != (str(reconciled["id"]), reconciled["request_digest"])
                or type(observed.get("fencing_epoch")) is not int
                or observed["fencing_epoch"] != reconciled["fencing_epoch"]
            ):
                raise ValueError("historical lease observation conflicts")


async def _require_runtime_release_chain(
    conn: Any, identity: GenerationProviderIdentity, *, cursor: dict[str, Any],
    historical: dict[str, Any], binding: dict[str, Any], latest_release: Any,
    created: datetime,
) -> None:
    """Verify each owned fence crossing without a new wire or environment inference."""
    workspace = UUID(binding["workspace_id"])
    first_fence, held_fence = historical["fencing_epoch"], binding["fencing_epoch"]
    await _require_reconciled_runtime_history(
        conn, identity, workspace=workspace, first_fence=first_fence, created=created,
    )
    cycles, odd = divmod(held_fence - first_fence, 2)
    final_cycle = cursor["lease_cycle_seq"]
    matches = [
        profile for profile in _CELL_PROFILES
        if latest_release["idempotency_key"]
        == f"generation:{identity.run_id}:release:{profile}:cycle:{final_cycle}"
    ]
    if (
        odd or cycles > final_cycle or len(matches) != 1
        or (cycles == 0 and historical != binding)
    ):
        raise ValueError("released lease lineage conflicts")
    rows = await conn.fetch(
        "SELECT * FROM project_cell_operations "
        "WHERE workspace_id=$1 AND fencing_epoch>$2 AND fencing_epoch<=$3 "
        "ORDER BY fencing_epoch FOR UPDATE NOWAIT",
        workspace, first_fence, held_fence,
    )
    if len(rows) != 2 * cycles:
        raise ValueError("released lease lineage is incomplete")
    profile = matches[0]
    last_finish = created
    for offset, operation in enumerate([*rows, latest_release], start=1):
        ensuring = offset % 2 == 0
        if ensuring:
            kind = "ensure"
            request: dict[str, Any] = {"profile_version": profile}
        else:
            kind = "release"
            if operation["id"] == latest_release["id"]:
                expected_environment = binding["environment_sha256"]
            elif offset == 1:
                expected_environment = historical["environment_sha256"]
            else:
                saved = _record(_record(operation["request_payload"])["request"])
                expected_environment = _record(saved["require_environment_proof"])[
                    "environment_sha256"
                ]
                if not _hex_digest(expected_environment):
                    raise ValueError("intermediate environment receipt is invalid")
            request = {"require_environment_proof": {
                "source_sha256": binding["source_sha256"],
                "environment_sha256": expected_environment,
            }}
        if (
            not _runtime_lease_operation_matches(
                operation, identity, workspace=workspace, fence=first_fence + offset,
                profile=profile, cycle=final_cycle - cycles + offset // 2,
                kind=kind, request=request,
            )
            or operation["started_at"] < last_finish
        ):
            raise ValueError("released lease lineage is unconfirmed")
        last_finish = operation["finished_at"]


async def _prior_release(
    conn: Any, identity: GenerationProviderIdentity, historical: dict[str, Any], *,
    released_at: datetime,
) -> datetime:
    workspace = UUID(historical["workspace_id"])
    fence = historical["fencing_epoch"] + 1
    rows = await conn.fetch(
        "SELECT result_payload,finished_at FROM project_cell_operations "
        "WHERE workspace_id=$1 AND generation_run_id=$2 AND kind='release' "
        "AND status='completed' AND fencing_epoch=$3 AND finished_at < $4 FOR UPDATE NOWAIT",
        workspace, identity.run_id, fence, released_at,
    )
    if len(rows) != 1 or not _release_matches(_record(rows[0]["result_payload"]), workspace, fence):
        raise ValueError("historic physical release is unconfirmed")
    finished: datetime = rows[0]["finished_at"]
    return finished


def _record_sha(value: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False,
    ).encode()).hexdigest()


def _hex_digest(value: object) -> bool:
    return type(value) is str and re.fullmatch(r"[0-9a-f]{64}", value) is not None


async def _confirmed_later_commands(
    conn: Any, rows: list[Any], identity: GenerationProviderIdentity, binding: dict[str, Any],
    *, step: int, root_attempt: int, attempt_seq: int, released_at: datetime,
    previous_release_at: datetime | None,
) -> None:
    if [row["effect_ordinal"] for row in rows] != [2, 3]:
        raise ValueError("provider step includes unrecognized effects")
    roles = (("bootstrap", "prepare", "max-platform-bootstrap-v2"), ("fast_check", "fast_check", "v1"))
    run, workspace = identity.run_id, UUID(binding["workspace_id"])
    source, fence = binding["source_sha256"], binding["fencing_epoch"]
    for row, (role, phase, contract) in zip(rows, roles, strict=True):
        operation = row["operation_id"]
        tool_call = f"role:{operation}"
        effect = uuid5(uuid5(run, f"recovery-tool-v1:{step}:{tool_call}"), "physical:0")
        if (
            (row["run_id"], row["project_id"], row["user_id"])
            != (run, identity.project_id, identity.user_id)
            or row["logical_step_seq"] != step or row["payload_version"] != 1
            or not root_attempt < row["attempt_seq"] <= attempt_seq
            or row["kind"] != "tool" or row["state"] != "confirmed"
            or row["provider_attempt_id"] is not None or row["parent_effect_id"] is not None
            or row["checkpoint_ref"] is not None or operation is None
            or row["tool_call_id"] != tool_call or row["effect_id"] != effect
            or not _hex_digest(row["input_digest"])
        ):
            raise ValueError("later command ownership conflicts")
        pre, post, receipt = (
            _record(row[key]) for key in ("pre_binding", "post_binding", "receipt_ref")
        )
        activity = _record(pre["activity"])
        reference = _record(pre["context_ref"])
        proof = activity["proof_key"]
        request = pre["request_sha256"]
        volume = receipt["physical_volume"]
        volume_identity = receipt["physical_volume_identity"]
        if (
            type(pre["version"]) is not int or type(pre["fencing_epoch"]) is not int
            or pre != {
                "version": 1, "kind": "command_v1", "workspace_id": str(workspace),
                "fencing_epoch": fence, "workspace_revision": source,
                "request_sha256": request, "context_ref": reference, "context_binding": binding,
                "activity": {"operation_id": str(operation), "proof_key": proof, "phase": phase},
            }
            or set(reference) != {"object_key", "sha256"}
            or type(reference["object_key"]) is not str or not reference["object_key"]
            or not all(_hex_digest(field) for field in (
                reference["sha256"], proof, request, volume_identity, receipt["response_sha256"],
            ))
            or operation != uuid5(run, f"command:{workspace}:{fence}:{proof}:{role}:{contract}")
            or type(volume) is not str or not volume.strip()
            or type(receipt["version"]) is not int
            or type(receipt["dispatch_fencing_epoch"]) is not int
            or receipt != {
                "version": 1, "operation_id": str(operation), "workspace_id": str(workspace),
                "project_id": str(identity.project_id), "owner_id": str(identity.user_id),
                "generation_run_id": str(run), "dispatch_fencing_epoch": fence,
                "physical_volume": volume, "physical_volume_identity": volume_identity,
                "request_sha256": request, "before_revision": source, "after_revision": source,
                "response_sha256": receipt["response_sha256"],
            }
            or row["receipt_sha256"] != _record_sha(receipt)
            or post != {"workspace_revision": source, "physical_volume_identity": volume_identity}
        ):
            raise ValueError("later terminal command evidence conflicts")
        proofs = await conn.fetch(
            "SELECT r.id FROM project_cell_activity_leases l "
            "JOIN project_cell_proofs q ON q.workspace_id=l.workspace_id "
            "AND q.generation_run_id=l.generation_run_id AND q.fencing_epoch=l.fencing_epoch "
            "AND q.proof_key=l.proof_key "
            "JOIN project_cell_proof_results r ON r.operation_id=l.operation_id "
            "AND r.workspace_id=l.workspace_id "
            "JOIN project_cell_proofs p ON p.id=r.proof_id AND p.workspace_id=l.workspace_id "
            "AND p.generation_run_id=l.generation_run_id AND p.fencing_epoch=l.fencing_epoch "
            "WHERE l.operation_id=$1 AND l.workspace_id=$2 AND l.generation_run_id=$3 "
            "AND l.fencing_epoch=$4 AND l.proof_key=$5 AND l.phase=$6 "
            "AND l.kind='command' AND l.state='completed' "
            "AND l.finished_at BETWEEN l.started_at AND $7 "
            "AND r.dimension=$8 AND r.outcome='green' AND r.artifact_ref IS NULL "
            "AND r.created_at <= $7 AND ($12::timestamptz IS NULL OR l.started_at >= $12) "
            "AND q.workspace_revision=$9 AND p.workspace_revision=$9 "
            "AND q.schema_data_digest=$10 AND p.schema_data_digest=$10 "
            "AND q.base_image_digest=$11 AND q.toolchain_digest=$11 "
            "AND p.base_image_digest=$11 AND p.toolchain_digest=$11 "
            "FOR UPDATE OF l,q,r,p NOWAIT",
            operation, workspace, run, fence, proof, phase, released_at, role,
            source, binding["schema_sha256"], binding["environment_sha256"], previous_release_at,
        )
        if len(proofs) != 1:
            raise ValueError("later command has no owned terminal proof")
    if await conn.fetchval(
        "SELECT EXISTS(SELECT 1 FROM generation_recovery_effects "
        "WHERE parent_effect_id=ANY($1::uuid[]))", [row["effect_id"] for row in rows],
    ):
        raise ValueError("later command is not a terminal independent effect")


async def close_expired_before_dispatch(scope: ExpiredReservationScope) -> dict[str, Any]:
    """Maintenance only. Atomically prevent any future provider dispatch for this attempt.

    The Gateway dispatch, reservation and response admission paths serialize on
    the attempt row. Neither local wire intent nor its age proves no charge.
    """
    return await _seal_expired_reservation(scope)


async def close_runtime_expired_before_dispatch(
    scope: RuntimeExpiredReservationScope,
) -> dict[str, Any]:
    """Settle the currently released expired reservation during normal runtime."""
    return await _seal_expired_reservation(scope)


async def close_released_before_dispatch(
    scope: ReleaseExpiredReservationScope,
) -> dict[str, Any]:
    """Settle the exact released runtime cursor under the owning release fence."""
    return await _seal_expired_reservation(scope)


async def _seal_expired_reservation(
    scope: ExpiredReservationScope | RuntimeExpiredReservationScope,
) -> dict[str, Any]:
    identity = scope.identity
    runtime = isinstance(scope, RuntimeExpiredReservationScope)
    try:
        async with (
            asyncio.timeout(3 if runtime else None),
            get_pool().acquire() as conn,
            conn.transaction(),
        ):
            if isinstance(scope, RuntimeExpiredReservationScope):
                owned = await _locked_runtime_cursor(conn, scope)
            else:
                owned = await _locked_parked_cursor(conn, scope)
            cursor, binding, step, attempt_seq, release = owned
            released_at = release["finished_at"]
            root_id = uuid5(identity.run_id, f"recovery-provider-v1:{step}")
            wire_id = uuid5(root_id, "wire-v1:0:0")
            if (
                cursor["provider_effect_id"] != str(root_id)
                or cursor["pending_effect_id"] != str(root_id)
                or identity.attempt_id != uuid5(root_id, "provider-attempt-v1")
            ):
                raise ValueError("provider cursor changed")
            attempt = await _owned(conn, identity.attempt_id, nowait=True)
            if (
                not matches(attempt, identity) or attempt["provider_call_id"] is not None
                or attempt["response_manifest_key"] is not None
                or attempt["response_sha256"] is not None or attempt["parent_attempt_id"] is not None
                or await conn.fetchval(
                    "SELECT EXISTS(SELECT 1 FROM generation_responses WHERE attempt_id=$1) OR "
                    "EXISTS(SELECT 1 FROM generation_provider_attempts WHERE parent_attempt_id=$1) "
                    "OR EXISTS(SELECT 1 FROM provider_calls WHERE run_id=$2 AND created_at >= $3)",
                    identity.attempt_id, identity.run_id, attempt["created_at"],
                )
            ):
                raise ValueError("provider attempt already has a call or response admission")
            effects = await conn.fetch(
                "SELECT * FROM generation_recovery_effects WHERE "
                "(run_id=$1 AND logical_step_seq=$2) OR parent_effect_id=$3 "
                "OR parent_effect_id=$4 ORDER BY effect_ordinal FOR UPDATE NOWAIT",
                identity.run_id, step, root_id, wire_id,
            )
            if [row["effect_id"] for row in effects[:2]] != [root_id, wire_id]:
                raise ValueError("provider effect has additional or missing work")
            root, wire = provider_effects = effects[:2]
            later = effects[2:]
            if runtime and later:
                raise ValueError("runtime expiry has additional effects in the provider step")
            if (
                any(
                    (row["run_id"], row["project_id"], row["user_id"], row["input_digest"])
                    != (identity.run_id, identity.project_id, identity.user_id, identity.request_sha256)
                    or row["logical_step_seq"] != step or row["payload_version"] != 1
                    or row["post_binding"] is not None or row["operation_id"] is not None
                    or row["tool_call_id"] is not None
                    for row in provider_effects
                )
                or root["kind"] != "provider" or root["effect_ordinal"] != 0
                or root["parent_effect_id"] is not None
                or root["provider_attempt_id"] != identity.attempt_id
                or root["attempt_seq"] > attempt_seq
                or wire["kind"] != "progress" or wire["effect_ordinal"] != 1
                or wire["parent_effect_id"] != root_id or wire["provider_attempt_id"] is not None
                or (wire["attempt_seq"], wire["claim_token"]) != (root["attempt_seq"], root["claim_token"])
            ):
                raise ValueError("provider or wire ownership changed")
            root_binding = _record(root["pre_binding"])
            wire_binding = _record(wire["pre_binding"])
            wire_plan = _record(wire_binding["dispatch_v1"])
            historical = _record(root_binding["source"])
            plan = _record(root_binding["dispatch_v1"])
            deadline = datetime.fromisoformat(plan["deadline"])
            historic_fence = historical["fencing_epoch"]
            changed_environment = historical["environment_sha256"] != binding["environment_sha256"]
            if (
                set(root_binding) != {"source", "dispatch_v1"}
                or set(plan) != {"version", "clock_basis", "kind", "deadline",
                              "outer_attempt", "outer_allowance", "wire_allowance"}
                or type(plan["version"]) is not int or plan["version"] != 1
                or plan["clock_basis"] != "utc" or plan["kind"] != "native"
                or type(plan["outer_attempt"]) is not int or plan["outer_attempt"] != 0
                or any(type(plan[key]) is not int or plan[key] < 1
                       for key in ("outer_allowance", "wire_allowance"))
                or deadline.utcoffset() is None
                or deadline > await conn.fetchval("SELECT clock_timestamp()")
                or type(historic_fence) is not int or not 0 < historic_fence <= binding["fencing_epoch"]
                or set(historical) != set(binding)
                or not _hex_digest(historical["environment_sha256"])
                or any(historical[key] != binding[key] for key in (
                    "workspace_id", "schema_sha256", "source_sha256",
                ))
                or (not runtime and changed_environment and (
                    historic_fence + 1 >= binding["fencing_epoch"] or not later
                ))
                or set(wire_binding) != {"dispatch_v1"}
                or any(type(wire_plan[key]) is not int for key in (
                    "version", "outer_attempt", "outer_allowance", "wire_allowance", "wire_attempt",
                ))
                or wire_plan != {
                    **plan, "root_effect_id": str(root_id), "provider_effect_id": str(root_id),
                    "attempt_id": str(identity.attempt_id), "parent_attempt_id": None,
                    "request_sha256": identity.request_sha256, "wire_attempt": 0,
                }
            ):
                raise ValueError("provider wire is not the exact expired reservation")
            if runtime:
                await _require_runtime_release_chain(
                    conn, identity, cursor=cursor, historical=historical, binding=binding,
                    latest_release=release, created=max(root["created_at"], wire["created_at"]),
                )
            previous_release_at = (
                await _prior_release(conn, identity, historical, released_at=released_at)
                if not runtime and changed_environment else None
            )
            if later:
                await _confirmed_later_commands(
                    conn, later, identity, binding, step=step, root_attempt=root["attempt_seq"],
                    attempt_seq=attempt_seq, released_at=released_at,
                    previous_release_at=previous_release_at,
                )
            receipt = {
                "version": 1, "kind": _EXPIRY_RECEIPT_KIND, "attempt_id": str(identity.attempt_id),
                "wire_effect_id": str(wire_id), "root_effect_id": str(root_id),
                "request_sha256": identity.request_sha256, "source_sha256": scope.source_sha256,
                "deadline": plan["deadline"],
            }
            if (
                attempt["phase"] == "closed" and attempt["terminal_reason"] == EXPIRED_BEFORE_DISPATCH
                and all(
                    e["state"] == "confirmed" and _record(e["receipt_ref"]) == receipt
                    for e in provider_effects
                )
            ):
                return receipt
            if (
                attempt["phase"] != "reserved" or attempt["terminal_reason"] is not None
                or (root["state"], wire["state"]) != ("prepared", "inflight")
                or any(
                    e["receipt_ref"] is not None or e["receipt_sha256"] is not None
                    for e in provider_effects
                )
            ):
                raise ValueError("provider attempt state changed")
            changed = await conn.execute(
                "UPDATE generation_provider_attempts SET phase='closed',terminal_reason=$2,"
                "updated_at=now() WHERE attempt_id=$1 AND phase='reserved'",
                identity.attempt_id, EXPIRED_BEFORE_DISPATCH,
            )
            effects_changed = await conn.execute(
                "UPDATE generation_recovery_effects SET state='confirmed',receipt_ref=$2::jsonb,"
                "revision=revision+1,updated_at=now() WHERE effect_id=ANY($1::uuid[])",
                [root_id, wire_id], json.dumps(receipt, allow_nan=False),
            )
            if changed != "UPDATE 1" or effects_changed != "UPDATE 2":
                raise ValueError("expired provider reservation was not sealed")
    except Exception:
        raise AttemptConflict("expired provider reservation was not confirmed") from None
    return receipt


class _ObjectPairs(list[tuple[str, Any]]):
    """Keep every object key while distinguishing JSON objects from arrays."""


def _contains_identity(raw: bytes) -> bool:
    # Decode keys (including Unicode escapes), retaining ALL top-level metadata
    # occurrences. A later duplicate cannot erase the reserved protocol marker.
    body = json.loads(raw, object_pairs_hook=_ObjectPairs)
    return isinstance(body, _ObjectPairs) and any(
        key == "metadata" and isinstance(value, _ObjectPairs)
        and any(field == "generation_provider_identity" for field, _ in value)
        for key, value in body
    )


def parse_attempt(
    raw: bytes, route: str, *, require_identity: bool = False,
) -> GenerationProviderIdentity | None:
    try:
        if not require_identity and not _contains_identity(raw):
            return None  # Preserve ordinary identity-absent JSON semantics.
        body = decode_request(raw)
    except (ValueError, UnicodeError, RecursionError):
        raise AttemptConflict("generation request is not canonical") from None
    metadata = body.get("metadata")
    if not isinstance(metadata, dict) or "generation_provider_identity" not in metadata:
        if require_identity:
            raise AttemptConflict("authenticated generation identity is required")
        return None
    if not get_settings().generation_provider_attempts_enabled:
        raise AttemptConflict("generation attempt protocol is disabled")
    try:
        identity = GenerationProviderIdentity.from_wire(metadata["generation_provider_identity"])
        if request_digest(route, body) != identity.request_sha256:
            raise ValueError
        if (
            metadata.get("run_id") != str(identity.run_id)
            or (metadata.get("project_id") != str(identity.project_id))
            or metadata.get("user_id") not in {None, str(identity.user_id)}
            or (body.get("user") not in {None, str(identity.user_id)})
        ):
            raise ValueError
        return identity
    except (ValueError, TypeError):
        raise AttemptConflict("generation attempt binding conflicts") from None


async def _owned(conn: Any, attempt_id: UUID, *, nowait: bool = False) -> Any:
    # Lock only dispatch control. API callers can hold project/run row locks while
    # awaiting HTTP; MVCC current committed owner checks must not deadlock them.
    return await conn.fetchrow(
        "SELECT a.* FROM generation_provider_attempts a "
        "JOIN generation_runs r ON r.id=a.run_id "
        "JOIN projects p ON p.id=a.project_id "
        "WHERE a.attempt_id=$1 AND r.project_id=a.project_id AND r.user_id=a.user_id "
        "AND p.owner_id=a.user_id AND r.prompt_hash=a.prompt_hash AND r.status='running' "
        "AND a.version=1 FOR UPDATE OF a" + (" NOWAIT" if nowait else ""),
        attempt_id,
    )


def matches(row: Any, identity: GenerationProviderIdentity) -> bool:
    return row is not None and all(
        row[key] == getattr(identity, key)
        for key in ("run_id", "project_id", "user_id", "prompt_hash", "request_sha256", "version")
    )


def is_native_transcript_rejected(row: Any) -> bool:
    return (
        row["phase"] == "closed"
        and row["terminal_reason"] == NATIVE_TRANSCRIPT_REJECTED
        and row["provider_call_id"] is None
        and row["response_manifest_key"] is None
        and row["response_sha256"] is None
    )


def native_transcript_rejection_details() -> dict[str, bool | str]:
    return {
        "kind": NATIVE_TRANSCRIPT_REJECTED, "reason": NATIVE_TRANSCRIPT_REJECTED,
        "provider_charge_ambiguous": False, "durable": True,
    }


async def close_native_transcript_rejection(identity: GenerationProviderIdentity) -> None:
    """Fence a signed local rejection against both dispatch and receipt admission."""
    try:
        async with get_pool().acquire() as conn, conn.transaction():
            row = await _owned(conn, identity.attempt_id)
            if not matches(row, identity) or await conn.fetchval(
                "SELECT EXISTS(SELECT 1 FROM generation_responses WHERE attempt_id=$1)",
                identity.attempt_id,
            ):
                raise AttemptConflict("generation transcript refusal conflicts")
            if is_native_transcript_rejected(row):
                return
            if (
                row["phase"] != "reserved" or row["terminal_reason"] is not None
                or row["provider_call_id"] is not None or row["response_manifest_key"] is not None
                or row["response_sha256"] is not None
            ):
                raise AttemptConflict("generation transcript refusal conflicts")
            changed = await conn.execute(
                "UPDATE generation_provider_attempts SET phase='closed', terminal_reason=$2, "
                "updated_at=now() WHERE attempt_id=$1 AND phase='reserved'",
                identity.attempt_id, NATIVE_TRANSCRIPT_REJECTED,
            )
            if changed != "UPDATE 1":
                raise AttemptConflict("generation transcript refusal conflicts")
    except Exception:
        raise AttemptConflict("generation transcript refusal was not confirmed") from None


async def reserve_generation_attempt(identity: GenerationProviderIdentity) -> AttemptAdmission:
    try:
        async with get_pool().acquire() as conn, conn.transaction():
            row = await _owned(conn, identity.attempt_id)
            if not matches(row, identity):
                return AttemptAdmission("conflict")
            if row["phase"] != "reserved":
                # Task8 alone can verify and serve retained private body bytes.
                return AttemptAdmission("pending", provider_call_id=row["provider_call_id"])
            token = uuid4()
            await conn.execute(
                "UPDATE generation_provider_attempts SET dispatch_token=$2, "
                "updated_at=now() WHERE attempt_id=$1",
                identity.attempt_id,
                token,
            )
        return AttemptAdmission("dispatch", dispatch_token=token)
    except Exception:
        raise AttemptConflict("generation attempt admission unavailable") from None


async def mark_attempt_dispatching(
    attempt_id: UUID,
    *,
    dispatch_token: UUID,
    route: str,
    model: str,
    user_id: UUID | None = None,
    project_id: UUID | None = None,
    run_id: UUID | None = None,
    message_id: UUID | None = None,
    stage: str | None = None,
    free: bool = False,
    provider_call_id: UUID | None = None,
    provider_scope: str = "llmgw",
) -> UUID:
    call = provider_call_id or uuid4()
    try:
        async with get_pool().acquire() as conn, conn.transaction():
            row = await _owned(conn, attempt_id)
            if row is None or row["phase"] != "reserved" or row["dispatch_token"] != dispatch_token:
                raise AttemptConflict("generation attempt binding conflicts")
            if (
                row["run_id"] != run_id
                or row["project_id"] != project_id
                or (user_id is not None and row["user_id"] != user_id)
            ):
                raise AttemptConflict("generation attempt binding conflicts")
            await provider_calls.insert_call(
                conn,
                call,
                route=route,
                model=model,
                user_id=user_id,
                project_id=project_id,
                run_id=run_id,
                message_id=message_id,
                stage=stage,
                free=free,
                provider_scope=provider_scope,
            )
            changed = await conn.execute(
                "UPDATE generation_provider_attempts SET phase='dispatching',provider_call_id=$3,"
                "updated_at=now() WHERE attempt_id=$1 AND dispatch_token=$2 AND phase='reserved'",
                attempt_id,
                dispatch_token,
                call,
            )
            if changed != "UPDATE 1":
                raise AttemptConflict("generation attempt binding conflicts")
        # The only permission to POST exists AFTER successful commit. Unknown
        # commit outcomes raise; reconnecting or elapsed time never grants it.
        return call
    except Exception:
        raise AttemptConflict("generation dispatch was not confirmed") from None


async def dispatch(identity: GenerationProviderIdentity | None, **kwargs: Any) -> UUID:
    from yleum_gateway.services.generation_response_capture import before_dispatch

    await before_dispatch(identity)
    if identity is None:
        return await provider_calls.start_call(**kwargs)
    admitted = await reserve_generation_attempt(identity)
    if admitted.action != "dispatch" or admitted.dispatch_token is None:
        raise AttemptConflict("generation attempt is pending or conflicting")
    return await mark_attempt_dispatching(
        identity.attempt_id, dispatch_token=admitted.dispatch_token, **kwargs
    )


async def close_attempt(
    call_id: UUID,
    *,
    reason: Literal["completed", "preconnect", "rate_limit_refused", "upstream_refused"] | None,
) -> None:
    """Record control evidence only AFTER the existing financial receipt committed."""
    if reason not in (None, "completed", "preconnect", "rate_limit_refused", "upstream_refused"):
        raise AttemptConflict("generation terminal reason is unsupported")
    try:
        async with get_pool().acquire() as conn, conn.transaction():
            row = await conn.fetchrow(
                "SELECT a.attempt_id,a.phase,a.terminal_reason,c.status,c.receipt_hash "
                "FROM generation_provider_attempts a JOIN provider_calls c ON c.id=a.provider_call_id "
                "WHERE a.provider_call_id=$1 FOR UPDATE OF a",
                call_id,
            )
            if row is None:
                raise AttemptConflict("generation attempt binding conflicts")
            wanted = "completed" if reason == "completed" else "failed"
            if reason is not None and (row["status"] != wanted or not row["receipt_hash"]):
                raise AttemptConflict("generation attempt binding conflicts")
            phase = "closed" if reason else "indeterminate"
            if row["phase"] != "dispatching":
                if row["phase"] == phase and row["terminal_reason"] == reason:
                    return
                raise AttemptConflict("generation attempt binding conflicts")
            await conn.execute(
                "UPDATE generation_provider_attempts SET phase=$2,terminal_reason=$3,"
                "updated_at=now() WHERE attempt_id=$1",
                row["attempt_id"],
                phase,
                reason,
            )
    except Exception:
        raise AttemptConflict("generation terminal receipt was not confirmed") from None


async def finish(
    identity: GenerationProviderIdentity | None,
    call_id: UUID,
    *,
    reason: Literal["preconnect", "rate_limit_refused", "upstream_refused"] | None = None,
    **receipt: Any,
) -> None:
    """The existing journal commits first; only then may a safe retry be advertised."""
    await provider_calls.finish_call(call_id, **receipt)
    if identity is not None:
        await close_attempt(
            call_id,
            reason=("completed" if receipt.get("status", "completed") == "completed" else reason),
        )

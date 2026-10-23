"""Atomically retain completed usage and settle its wallet debit exactly once."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from typing import Any
from uuid import UUID, uuid4

import structlog

from yleum_gateway.core.config import get_settings
from yleum_gateway.core.db import get_pool
from yleum_gateway.core.errors import BillingReconciliationRequiredError, WalletEmptyError
from yleum_gateway.services.generation_identity import GenerationProviderIdentity
from yleum_gateway.services.pricing import validate_cost_provenance

log = structlog.get_logger(__name__)

# Every billing account is personal: one user, one wallet (business scope retired).
_RESOLVED_ACCOUNT = """
    SELECT ba.id
      FROM billing_accounts ba
     WHERE ba.scope = 'personal' AND ba.personal_user_id = $1
     LIMIT 1
"""


@dataclass(frozen=True, slots=True)
class ProviderUsageBinding:
    """Server dispatch result, revalidated against its journal inside charge."""

    identity: GenerationProviderIdentity
    call_id: UUID


def settlement_receipt_hash(
    *, user_id: UUID, project_id: UUID | None, run_id: UUID | None, model_id: str,
    tokens_in: int, tokens_out: int, cost_rub: Decimal, free: bool, stage: str | None,
    cache_read_tokens: int, cache_write_tokens: int, provider_cost_usd: Decimal | None,
) -> str:
    """Preserve the established settlement identity exactly across cold readers."""
    return hashlib.sha256(json.dumps(
        [str(user_id), str(project_id), str(run_id), model_id, tokens_in, tokens_out,
         str(cost_rub.normalize()), free, stage, max(0, cache_read_tokens),
         max(0, cache_write_tokens),
         str(provider_cost_usd.normalize()) if provider_cost_usd is not None else None],
        separators=(",", ":"),
    ).encode()).hexdigest()


def _sql_amount(value: Decimal | None, places: int) -> Decimal | None:
    """Check PostgreSQL NUMERIC projections without changing original receipts."""
    return value.quantize(Decimal(1).scaleb(-places), rounding=ROUND_HALF_UP) if value is not None else None


def _journal_amounts(call: Any) -> tuple[dict[str, Any] | None, tuple[Decimal | None, ...]]:
    provenance = json.loads(call["cost_provenance"]) if call["cost_provenance"] else None
    keys = ("calculated_cost_rub", "provider_cost_rub", "provider_cost_usd")
    if provenance is None:
        return None, tuple(call[key] for key in keys)
    validate_cost_provenance(provenance)
    originals = tuple(
        Decimal(provenance[key]) if provenance[key] is not None else None
        for key in ("calculated_cost_rub", "reported_cost_rub", "provider_cost_usd")
    )
    if (
        any(call[key] != _sql_amount(value, 8) for key, value in zip(keys, originals, strict=True))
        or any(call[key] != provenance[key] for key in (
            "tokens_in", "tokens_out", "cache_read_tokens", "cache_write_tokens",
        ))
    ):
        raise BillingReconciliationRequiredError("Journal cost projection conflicts")
    return provenance, originals


def _journal_receipt_matches(call: Any, provenance: Any, amounts: tuple[Any, ...]) -> bool:
    from yleum_gateway.services.provider_calls import receipt_digest

    return bool(call["receipt_hash"] == receipt_digest(
        call["status"], call["actual_model"], call["provider_request_id"],
        tuple(call[key] for key in ("tokens_in", "tokens_out", "cache_read_tokens", "cache_write_tokens")),
        amounts, provenance, call["error_type"],
    ))


async def _lock_usage_call(
    conn: Any, binding: ProviderUsageBinding, *, user_id: UUID,
    project_id: UUID | None, run_id: UUID | None, model_id: str,
    tokens_in: int, tokens_out: int, cache_read_tokens: int, cache_write_tokens: int,
    cost_rub: Decimal,
    provider_scope: str, provider_request_id: str | None,
    provider_cost_usd: Decimal | None, provenance_json: str | None,
) -> Any:
    # Existing billing takes the run lock first. Never use _owned here: it locks
    # the attempt and denies late financial receipts after Stop/terminal state.
    row = await conn.fetchrow(
        "SELECT c.*,a.run_id AS bound_run,a.project_id AS bound_project,"
        "a.user_id AS bound_user,a.prompt_hash,a.request_sha256,a.version "
        "FROM provider_calls c JOIN generation_provider_attempts a ON a.provider_call_id=c.id "
        "WHERE c.id=$1 AND a.attempt_id=$2 FOR UPDATE OF c",
        binding.call_id, binding.identity.attempt_id,
    )
    expected = {
        "bound_run": binding.identity.run_id, "bound_project": binding.identity.project_id,
        "bound_user": binding.identity.user_id, "prompt_hash": binding.identity.prompt_hash,
        "request_sha256": binding.identity.request_sha256, "version": 1,
        "user_id": user_id, "project_id": project_id, "run_id": run_id,
        "actual_model": model_id, "tokens_in": tokens_in, "tokens_out": tokens_out,
        "cache_read_tokens": max(0, cache_read_tokens),
        "cache_write_tokens": max(0, cache_write_tokens),
        "provider_scope": provider_scope, "provider_request_id": provider_request_id,
        "provider_cost_usd": _sql_amount(provider_cost_usd, 8),
    }
    if (
        row is None or row["status"] not in {"completed", "ambiguous"}
        or row["finished_at"] is None or not row["receipt_hash"]
        or any(row[key] != value for key, value in expected.items())
        or row["bound_run"] != run_id or row["bound_project"] != project_id
        or row["bound_user"] != user_id
        or (json.loads(row["cost_provenance"]) if row["cost_provenance"] else None)
        != (json.loads(provenance_json) if provenance_json else None)
        or (provenance_json is None and _sql_amount(cost_rub, 8) != (
            row["provider_cost_rub"] if row["provider_cost_rub"] is not None else row["calculated_cost_rub"]
        ))
    ):
        raise BillingReconciliationRequiredError("Provider usage binding conflicts")
    provenance, amounts = _journal_amounts(row)
    if not _journal_receipt_matches(row, provenance, amounts):
        raise BillingReconciliationRequiredError("Provider journal receipt conflicts")
    return row


async def _link_usage(conn: Any, call: Any, usage_id: UUID) -> None:
    if call is None:
        return
    changed = await conn.fetchval(
        "UPDATE provider_calls SET usage_id=$2 WHERE id=$1 "
        "AND (usage_id IS NULL OR usage_id=$2) RETURNING id", call["id"], usage_id,
    )
    if changed != call["id"]:
        raise BillingReconciliationRequiredError("Provider usage link conflicts")


async def confirmed_provider_accounting(conn: Any, attempt: Any) -> bool:
    """Read an exact committed receipt chain; never charge, match by time, or lock writers.

    Caller owns current attempt/body locks. This reader takes no run/call/budget
    write locks, so it cannot invert billing or storage cleanup lock ordering.
    """
    if attempt["phase"] != "closed":
        return False
    call = await conn.fetchrow("SELECT * FROM provider_calls WHERE id=$1", attempt["provider_call_id"])
    if (
        call is None or call["finished_at"] is None
        or any(call[key] != attempt[key] for key in ("run_id", "project_id", "user_id"))
    ):
        return False
    provenance, amounts = _journal_amounts(call)
    if not _journal_receipt_matches(call, provenance, amounts):
        return False
    if attempt["terminal_reason"] == "upstream_refused":
        return call["provider_scope"] == "llmgw" and call["status"] == "failed" and call["usage_id"] is None
    if attempt["terminal_reason"] in {"preconnect", "rate_limit_refused"}:
        return call["status"] == "failed" and call["usage_id"] is None
    if (
        attempt["terminal_reason"] != "completed" or call["status"] != "completed"
        or call["usage_id"] is None
    ):
        return False
    usage = await conn.fetchrow("SELECT * FROM usage WHERE id=$1", call["usage_id"])
    settlement = await conn.fetchrow("SELECT * FROM usage_settlements WHERE usage_id=$1", call["usage_id"])
    if (
        usage is None or settlement is None or settlement["status"] not in {"settled", "free", "deferred"}
        or any(usage[key] != call[key] for key in (
            "run_id", "project_id", "user_id", "message_id", "tokens_in", "tokens_out",
            "cache_read_tokens", "cache_write_tokens", "provider_request_id", "provider_cost_usd",
        ))
        or usage["model_id"] != call["actual_model"]
        or settlement["user_id"] != call["user_id"]
        or settlement["provider_scope"] != call["provider_scope"]
        or settlement["provider_request_id"] != call["provider_request_id"]
        or (json.loads(usage["cost_provenance"]) if usage["cost_provenance"] else None) != provenance
    ):
        return False
    original_cost = (
        Decimal(provenance["effective_cost_rub"]) if provenance is not None else
        amounts[1] if amounts[1] is not None else amounts[0]
    )
    if original_cost is None or usage["cost_rub"] != _sql_amount(original_cost, 4):
        return False
    policy = await conn.fetchrow("SELECT version,is_free FROM generation_billing_policies WHERE run_id=$1", call["run_id"])
    if policy is not None and policy["version"] != "accepted-build-v1":
        return False
    free = bool(policy["is_free"]) if policy is not None else call["free"]
    if settlement["receipt_hash"] != settlement_receipt_hash(
        user_id=usage["user_id"], project_id=usage["project_id"], run_id=usage["run_id"],
        model_id=usage["model_id"], tokens_in=usage["tokens_in"], tokens_out=usage["tokens_out"],
        cost_rub=original_cost, free=free, stage=usage["stage"],
        cache_read_tokens=usage["cache_read_tokens"], cache_write_tokens=usage["cache_write_tokens"],
        provider_cost_usd=amounts[2],
    ):
        return False
    if settlement["status"] == "settled":
        wallet = await conn.fetchrow("SELECT * FROM wallet_charges WHERE id=$1", settlement["wallet_charge_id"])
        return (
            not free and wallet is not None and wallet["user_id"] == call["user_id"]
            and wallet["billing_account_id"] == settlement["billing_account_id"]
            and wallet["entry_type"] == "usage" and wallet["amount_rub"] == -usage["cost_rub"]
            and wallet["external_ref"] == f"usage:{usage['id']}"
            and wallet["message_id"] == usage["message_id"]
        )
    return settlement["wallet_charge_id"] is None and (
        settlement["status"] == "deferred" or free
    )


async def get_balance(user_id: UUID) -> Decimal:
    """Return the shared account balance visible to `user_id`."""
    pool = get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            f"""
            SELECT w.balance_rub
              FROM wallets w
              JOIN ({_RESOLVED_ACCOUNT}) account
                ON account.id = w.billing_account_id
            """,
            user_id,
        )
    return Decimal(row["balance_rub"]) if row else Decimal("0")


async def precheck_balance(user_id: UUID, estimated_cost_rub: Decimal) -> None:
    """Raise WalletEmptyError if balance is below threshold + estimate.

    Done before invoking the LLM (cheap rejection of broke users / DoS).
    """
    balance = await get_balance(user_id)
    floor = Decimal(str(get_settings().min_balance_rub))
    if balance < estimated_cost_rub + floor:
        raise WalletEmptyError(
            "Insufficient wallet balance for request",
            details={
                "balance_rub": str(balance),
                "estimated_cost_rub": str(estimated_cost_rub),
                "min_floor_rub": str(floor),
            },
        )


async def charge(
    *,
    user_id: UUID,
    project_id: UUID | None,
    message_id: UUID | None,
    model_id: str,
    tokens_in: int,
    tokens_out: int,
    cost_rub: Decimal,
    description: str,
    free: bool = False,
    run_id: UUID | None = None,
    stage: str | None = None,
    cache_read_tokens: int = 0,
    cache_write_tokens: int = 0,
    retry_count: int = 0,
    provider_request_id: str | None = None,
    provider_cost_usd: Decimal | None = None,
    provider_scope: str = "llmgw",
    cost_provenance: dict[str, Any] | None = None,
    provider_binding: ProviderUsageBinding | None = None,
) -> UUID:
    """Commit usage even when unpaid, then raise 402 outside the transaction.

    A real provider request ID identifies a receipt within its owner/provider
    scope. Delivery message UUIDs and retry counters do not change that identity.
    Replay returns the original result; unpaid receipts remain unpaid until an
    explicit reconciliation workflow. Calls without an upstream ID cannot be
    deduplicated. The gateway owns this transaction independently of its caller.
    """
    if not cost_rub.is_finite() or cost_rub < 0:
        raise ValueError("Settlement cost must be finite and nonnegative")
    if provider_cost_usd is not None and (
        not provider_cost_usd.is_finite() or provider_cost_usd < 0
    ):
        raise ValueError("Provider cost must be finite and nonnegative")
    if not provider_scope or provider_request_id == "":
        raise ValueError("Settlement provider scope and receipt ID must be nonempty")
    provenance_json = (
        json.dumps(validate_cost_provenance(cost_provenance, cost_rub=cost_rub))
        if cost_provenance is not None else None
    )
    pool = get_pool()
    charge_id = uuid4()
    usage_id = uuid4()
    unpaid = False
    async with pool.acquire() as conn, conn.transaction():
        deferred = False
        if run_id is None and message_id is not None and stage != "runtime_ai":
            matches = await conn.fetch(
                "SELECT id FROM generation_runs WHERE user_id=$1 AND project_id=$2 "
                "AND (assistant_message_id=$3 OR user_message_id=$3) LIMIT 2",
                user_id,
                project_id,
                message_id,
            )
            if len(matches) > 1:
                raise BillingReconciliationRequiredError("Ambiguous generation message binding")
            if matches:
                run_id = matches[0]["id"]
            elif await conn.fetchval(
                "SELECT EXISTS(SELECT 1 FROM generation_runs WHERE "
                "assistant_message_id=$1 OR user_message_id=$1)",
                message_id,
            ):
                raise BillingReconciliationRequiredError("Generation message owner binding changed")
        if run_id is None and stage in {
            "native_agent",
            "build_plan",
            "verification",
            "source_repair",
        }:
            raise BillingReconciliationRequiredError("Generation receipt requires a trusted run")
        if run_id is not None:
            run = await conn.fetchrow(
                "SELECT r.user_id,r.project_id,r.assistant_message_id,r.user_message_id,"
                "r.status,p.is_free,p.version FROM generation_runs r "
                "LEFT JOIN generation_billing_policies p ON p.run_id=r.id "
                "WHERE r.id=$1 FOR UPDATE OF r",
                run_id,
            )
            if (
                stage == "runtime_ai"
                or run is None
                or run["user_id"] != user_id
                or run["project_id"] != project_id
            ):
                raise BillingReconciliationRequiredError("Generation billing owner binding changed")
            if message_id is not None and message_id not in {
                run["assistant_message_id"],
                run["user_message_id"],
            }:
                raise BillingReconciliationRequiredError(
                    "Generation billing message binding changed"
                )
            if run["version"] is not None:
                if run["version"] != "accepted-build-v1":
                    raise BillingReconciliationRequiredError("Unknown generation billing policy")
                free = bool(run["is_free"])
                deferred = True
            else:
                # Missing trusted policy is never permission to charge a build.
                # Historical debits remain immutable; new/late expense is ours.
                deferred = True
        receipt_hash = settlement_receipt_hash(
            user_id=user_id, project_id=project_id, run_id=run_id, model_id=model_id,
            tokens_in=tokens_in, tokens_out=tokens_out, cost_rub=cost_rub, free=free, stage=stage,
            cache_read_tokens=cache_read_tokens, cache_write_tokens=cache_write_tokens,
            provider_cost_usd=provider_cost_usd,
        )
        call = None
        if provider_binding is not None:
            call = await _lock_usage_call(
                conn, provider_binding, user_id=user_id, project_id=project_id, run_id=run_id,
                model_id=model_id, tokens_in=tokens_in, tokens_out=tokens_out,
                cost_rub=cost_rub,
                cache_read_tokens=cache_read_tokens, cache_write_tokens=cache_write_tokens,
                provider_scope=provider_scope, provider_request_id=provider_request_id,
                provider_cost_usd=provider_cost_usd, provenance_json=provenance_json,
            )
            if call["usage_id"] is not None:
                linked = await conn.fetchrow(
                    "SELECT s.* FROM usage_settlements s JOIN usage u ON u.id=s.usage_id "
                    "WHERE s.usage_id=$1 AND s.user_id=$2 AND u.user_id=$2 "
                    "AND u.project_id IS NOT DISTINCT FROM $3 AND u.run_id IS NOT DISTINCT FROM $4",
                    call["usage_id"], user_id, project_id, run_id,
                )
                if linked is None or linked["receipt_hash"] != receipt_hash:
                    raise BillingReconciliationRequiredError("Linked provider receipt conflicts")
                if linked["status"] == "unpaid":
                    raise WalletEmptyError("Completed provider receipt remains unpaid")
                if linked["status"] not in {"settled", "free", "deferred"}:
                    raise BillingReconciliationRequiredError("Linked provider receipt is unresolved")
                return UUID(str(linked["wallet_charge_id"] or linked["id"]))
        if provider_request_id is not None:
            # Lock before reading: concurrent deliveries observe the first commit.
            # Hash collisions only serialize unrelated receipts; uniqueness is exact.
            lock_key = json.dumps([str(user_id), provider_scope, provider_request_id])
            await conn.execute(
                "SELECT pg_advisory_xact_lock(hashtext('gateway:settlement'), hashtext($1))",
                lock_key,
            )
            existing = await conn.fetchrow(
                "SELECT id, usage_id, wallet_charge_id, receipt_hash, status FROM usage_settlements "
                "WHERE user_id=$1 AND provider_scope=$2 AND provider_request_id=$3",
                user_id,
                provider_scope,
                provider_request_id,
            )
            if existing is not None:
                if existing["receipt_hash"] != receipt_hash:
                    raise BillingReconciliationRequiredError(
                        "Provider receipt was replayed with conflicting settlement data"
                    )
                if call is not None:
                    if existing["status"] not in {"settled", "free", "deferred", "unpaid"}:
                        raise BillingReconciliationRequiredError("Provider receipt is unresolved")
                    await _link_usage(conn, call, existing["usage_id"])
                if existing["status"] != "unpaid":
                    return UUID(str(existing["wallet_charge_id"] or existing["id"]))
                unpaid = True
            elif provider_scope == "llmgw" and await conn.fetchval(
                "SELECT EXISTS(SELECT 1 FROM usage u WHERE u.user_id=$1 "
                "AND (u.provider_request_id=$2 OR u.provider_request_id=$3) "
                "AND NOT EXISTS(SELECT 1 FROM usage_settlements s WHERE s.usage_id=u.id))",
                user_id,
                provider_request_id,
                provider_request_id[:200] if len(provider_request_id) > 200 else None,
            ):
                # Existing receipts predate the registry. Never guess a winner
                # among historical duplicates or silently debit them again.
                # The old native writer truncated upstream IDs to 200 characters;
                # only unregistered legacy usage may match that ambiguous prefix.
                raise BillingReconciliationRequiredError(
                    "A historical provider receipt requires explicit reconciliation"
                )
        if not unpaid:
            billing_account_id = await conn.fetchval(_RESOLVED_ACCOUNT, user_id)
            if not free and not deferred:
                debit = await conn.fetchrow(
                    f"""
                    WITH account AS ({_RESOLVED_ACCOUNT})
                    UPDATE wallets w
                       SET balance_rub = balance_rub - $2,
                           updated_at = now()
                      FROM account
                     WHERE w.billing_account_id = account.id
                       AND w.balance_rub >= $2
                    RETURNING w.balance_rub, w.billing_account_id
                    """,
                    user_id,
                    cost_rub,
                )
                unpaid = debit is None
                if debit is not None:
                    balance_after = Decimal(debit["balance_rub"])
                    billing_account_id = debit["billing_account_id"]

                    await conn.execute(
                        """
                        INSERT INTO wallet_charges
                            (id, billing_account_id, user_id, message_id, entry_type,
                             amount_rub, balance_after_rub, external_ref, description)
                        VALUES ($1, $2, $3, $4, 'usage', $5, $6, $7, $8)
                        """,
                        charge_id,
                        billing_account_id,
                        user_id,
                        message_id,
                        -cost_rub,  # negative = debit per data-model.md convention
                        balance_after,
                        f"usage:{usage_id}",
                        description,
                    )
            await conn.execute(
                """
                INSERT INTO usage
                    (id, user_id, project_id, message_id, run_id, model_id,
                     tokens_in, tokens_out, cost_rub, stage, cache_read_tokens,
                     cache_write_tokens, retry_count, provider_request_id,
                     provider_cost_usd, cost_provenance)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12,
                        $13, $14, $15, $16::jsonb)
                """,
                usage_id,
                user_id,
                project_id,
                message_id,
                run_id,
                model_id,
                tokens_in,
                tokens_out,
                cost_rub,
                stage,
                max(0, cache_read_tokens),
                max(0, cache_write_tokens),
                max(0, retry_count),
                provider_request_id,
                provider_cost_usd,
                provenance_json,
            )

            await conn.execute(
                "INSERT INTO usage_settlements "
                "(id,user_id,billing_account_id,provider_scope,provider_request_id,receipt_hash,"
                "usage_id,wallet_charge_id,status) VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9)",
                charge_id,
                user_id,
                billing_account_id,
                provider_scope,
                provider_request_id,
                receipt_hash,
                usage_id,
                charge_id if not free and not unpaid and not deferred else None,
                "unpaid" if unpaid else "deferred" if deferred else "free" if free else "settled",
            )
            await _link_usage(conn, call, usage_id)

    if unpaid:
        raise WalletEmptyError(
            "Wallet balance insufficient for completed provider receipt",
            details={"user_id": str(user_id), "cost_rub": str(cost_rub)},
        )

    log.info(
        "billing.charged",
        charge_id=str(charge_id),
        user_id=str(user_id),
        model_id=model_id,
        tokens_in=tokens_in,
        tokens_out=tokens_out,
        cost_rub=str(cost_rub),
        run_id=str(run_id) if run_id else None,
        stage=stage,
        cache_read_tokens=cache_read_tokens,
        cache_write_tokens=cache_write_tokens,
        retry_count=retry_count,
        free=free,
    )
    return charge_id

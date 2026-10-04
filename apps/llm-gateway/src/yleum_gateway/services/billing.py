"""Atomically retain completed usage and settle its wallet debit exactly once."""

from __future__ import annotations

import hashlib
import json
from decimal import Decimal
from uuid import UUID, uuid4

import structlog

from yleum_gateway.core.config import get_settings
from yleum_gateway.core.db import get_pool
from yleum_gateway.core.errors import BillingReconciliationRequiredError, WalletEmptyError

log = structlog.get_logger(__name__)

# Every billing account is personal: one user, one wallet (business scope retired).
_RESOLVED_ACCOUNT = """
    SELECT ba.id
      FROM billing_accounts ba
     WHERE ba.scope = 'personal' AND ba.personal_user_id = $1
     LIMIT 1
"""


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
        receipt_hash = hashlib.sha256(
            json.dumps(
                [
                    str(user_id),
                    str(project_id),
                    str(run_id),
                    model_id,
                    tokens_in,
                    tokens_out,
                    str(cost_rub.normalize()),
                    free,
                    stage,
                    max(0, cache_read_tokens),
                    max(0, cache_write_tokens),
                    str(provider_cost_usd.normalize()) if provider_cost_usd is not None else None,
                ],
                separators=(",", ":"),
            ).encode()
        ).hexdigest()
        if provider_request_id is not None:
            # Lock before reading: concurrent deliveries observe the first commit.
            # Hash collisions only serialize unrelated receipts; uniqueness is exact.
            lock_key = json.dumps([str(user_id), provider_scope, provider_request_id])
            await conn.execute(
                "SELECT pg_advisory_xact_lock(hashtext('gateway:settlement'), hashtext($1))",
                lock_key,
            )
            existing = await conn.fetchrow(
                "SELECT id, wallet_charge_id, receipt_hash, status FROM usage_settlements "
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
                     provider_cost_usd)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12,
                        $13, $14, $15)
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

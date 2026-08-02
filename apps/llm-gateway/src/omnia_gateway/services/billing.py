"""Wallet billing — atomic debit + audit trail.

Variant 1 from AGENT-C-LLM-GATEWAY.md: gateway writes directly to the shared
Postgres tables `wallets`, `wallet_charges`, `usage`.

R-10 fail fast: balance check is a single conditional UPDATE; if RowCount = 0
we raise WalletEmptyError without ever calling the LLM (when used as a
pre-check) or after the fact for accurate post-stream billing.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from uuid import UUID, uuid4

import structlog

from omnia_gateway.core.config import get_settings
from omnia_gateway.core.db import get_pool
from omnia_gateway.core.errors import WalletEmptyError

log = structlog.get_logger(__name__)


class RunBudgetExceededError(RuntimeError):
    """An atomic reservation would exceed the configured run budget."""


class UnknownRunError(RuntimeError):
    """The caller supplied a run id that does not exist."""


class InvalidRunAttributionError(RuntimeError):
    """The supplied user/project does not own the generation run."""


class InactiveRunError(RuntimeError):
    """The generation run is no longer allowed to contact the provider."""


@dataclass(frozen=True, slots=True)
class NativeRunReservation:
    usage_id: UUID
    requests_before: int
    cost_rub_before: Decimal
    provider_cost_usd_before: Decimal

_RESOLVED_ACCOUNT = """
    SELECT ba.id
      FROM billing_accounts ba
      LEFT JOIN business_members bm
        ON ba.scope = 'business'
       AND bm.business_id = ba.business_id
       AND bm.user_id = $1
     WHERE (ba.scope = 'business' AND bm.user_id IS NOT NULL)
        OR (ba.scope = 'personal' AND ba.personal_user_id = $1)
     ORDER BY CASE WHEN ba.scope = 'business' THEN 0 ELSE 1 END
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


async def reserve_native_run_request(
    *,
    run_id: UUID,
    user_id: UUID,
    project_id: UUID | None,
    message_id: UUID | None,
    model_id: str,
    stage: str,
    reserved_cost_rub: Decimal,
    reserved_provider_cost_usd: Decimal,
    max_requests: int,
    max_cost_rub: Decimal,
    max_provider_cost_usd: Decimal,
) -> NativeRunReservation:
    """Atomically reserve one provider request against a durable run budget.

    The placeholder lives in ``usage`` so it participates in the same indexed
    aggregate as settled calls and survives process restarts. A transaction
    advisory lock serializes reservations for one run without blocking the
    later foreign-key insert/update performed by ``charge``.
    """
    pool = get_pool()
    usage_id = uuid4()
    async with pool.acquire() as conn, conn.transaction():
        await conn.execute(
            "SELECT pg_advisory_xact_lock(hashtextextended($1::uuid::text, 0))",
            run_id,
        )
        run = await conn.fetchrow(
            "SELECT user_id, project_id, status FROM generation_runs WHERE id = $1",
            run_id,
        )
        if run is None:
            raise UnknownRunError(f"generation run does not exist: {run_id}")
        if run["user_id"] != user_id or (
            project_id is not None and run["project_id"] != project_id
        ):
            raise InvalidRunAttributionError("generation run attribution mismatch")
        if str(run["status"]) not in {"pending", "running"}:
            raise InactiveRunError(f"generation run is {run['status']}")
        row = await conn.fetchrow(
            """
            SELECT COUNT(*) AS requests,
                   COALESCE(SUM(cost_rub), 0) AS cost_rub,
                   COALESCE(SUM(provider_cost_usd), 0) AS provider_cost_usd
              FROM usage
             WHERE run_id = $1
            """,
            run_id,
        )
        requests = int(row["requests"] if row else 0)
        cost_rub = Decimal(row["cost_rub"] if row else 0)
        provider_cost_usd = Decimal(row["provider_cost_usd"] if row else 0)
        if (
            requests >= max_requests
            or cost_rub + reserved_cost_rub > max_cost_rub
            or provider_cost_usd + reserved_provider_cost_usd > max_provider_cost_usd
        ):
            raise RunBudgetExceededError(
                f"run budget exhausted after {requests} provider requests"
            )
        await conn.execute(
            """
            INSERT INTO usage
                (id, user_id, project_id, message_id, run_id, model_id,
                 tokens_in, tokens_out, cost_rub, stage, cache_read_tokens,
                 cache_write_tokens, retry_count, provider_request_id,
                 provider_cost_usd)
            VALUES ($1, $2, $3, $4, $5, $6, 0, 0, $7, $8, 0, 0, 0,
                    'native-budget-reservation', $9)
            """,
            usage_id,
            user_id,
            run["project_id"],
            message_id,
            run_id,
            model_id,
            reserved_cost_rub,
            f"{stage}:reservation",
            reserved_provider_cost_usd,
        )
    return NativeRunReservation(
        usage_id=usage_id,
        requests_before=requests,
        cost_rub_before=cost_rub,
        provider_cost_usd_before=provider_cost_usd,
    )


async def release_native_run_reservation(usage_id: UUID) -> None:
    """Release a reservation when the provider explicitly rejected the call."""
    pool = get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """
            DELETE FROM usage
             WHERE id = $1
               AND provider_request_id = 'native-budget-reservation'
            """,
            usage_id,
        )


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
    reserved_usage_id: UUID | None = None,
) -> UUID:
    """Atomic debit + audit trail.

    One transaction:
      1. UPDATE wallets … WHERE balance_rub >= cost  → 0 rows = WalletEmptyError.
      2. INSERT wallet_charges (negative amount = debit).
      3. INSERT usage, or reconcile a pre-provider budget reservation.
    Returns the wallet_charges row id.

    ``free=True`` (first-N free generations) skips steps 1–2 entirely: the
    wallet is NOT debited and no wallet_charges row is written, but the
    ``usage`` row is still inserted with the real ``cost_rub`` so analytics
    can measure what the free tier actually costs us.
    """
    pool = get_pool()
    charge_id = uuid4()
    usage_id = reserved_usage_id or uuid4()
    async with pool.acquire() as conn, conn.transaction():
        if not free:
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
            if debit is None:
                raise WalletEmptyError(
                    "Wallet balance went negative mid-charge",
                    details={"user_id": str(user_id), "cost_rub": str(cost_rub)},
                )
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
        if reserved_usage_id is None:
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
        else:
            updated = await conn.execute(
                """
                UPDATE usage
                   SET model_id = $2,
                       tokens_in = $3,
                       tokens_out = $4,
                       cost_rub = $5,
                       stage = $6,
                       cache_read_tokens = $7,
                       cache_write_tokens = $8,
                       retry_count = $9,
                       provider_request_id = $10,
                       provider_cost_usd = $11
                 WHERE id = $1
                   AND provider_request_id = 'native-budget-reservation'
                """,
                reserved_usage_id,
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
            if updated != "UPDATE 1":
                raise RuntimeError("native usage reservation was lost before settlement")

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

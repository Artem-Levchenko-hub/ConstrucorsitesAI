"""Wallet billing — atomic debit + audit trail.

Variant 1 from AGENT-C-LLM-GATEWAY.md: gateway writes directly to the shared
Postgres tables `wallets`, `wallet_charges`, `usage`.

R-10 fail fast: balance check is a single conditional UPDATE; if RowCount = 0
we raise WalletEmptyError without ever calling the LLM (when used as a
pre-check) or after the fact for accurate post-stream billing.
"""

from __future__ import annotations

from decimal import Decimal
from uuid import UUID, uuid4

import structlog

from omnia_gateway.core.config import get_settings
from omnia_gateway.core.db import get_pool
from omnia_gateway.core.errors import WalletEmptyError

log = structlog.get_logger(__name__)


async def get_balance(user_id: UUID) -> Decimal:
    """Return current wallet balance for `user_id` (0 if no wallet row)."""
    pool = get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT balance_rub FROM wallets WHERE user_id = $1", user_id)
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
) -> UUID:
    """Atomic debit + audit trail.

    One transaction:
      1. UPDATE wallets … WHERE balance_rub >= cost  → 0 rows = WalletEmptyError.
      2. INSERT wallet_charges (negative amount = debit).
      3. INSERT usage.
    Returns the wallet_charges row id.
    """
    pool = get_pool()
    charge_id = uuid4()
    usage_id = uuid4()
    async with pool.acquire() as conn, conn.transaction():
        updated = await conn.execute(
            """
            UPDATE wallets
               SET balance_rub = balance_rub - $1,
                   updated_at = now()
             WHERE user_id = $2 AND balance_rub >= $1
            """,
            cost_rub,
            user_id,
        )
        # asyncpg returns "UPDATE N" — extract N.
        affected = int(updated.rsplit(" ", 1)[-1]) if updated else 0
        if affected == 0:
            raise WalletEmptyError(
                "Wallet balance went negative mid-charge",
                details={"user_id": str(user_id), "cost_rub": str(cost_rub)},
            )

        await conn.execute(
            """
            INSERT INTO wallet_charges
                (id, user_id, message_id, amount_rub, description)
            VALUES ($1, $2, $3, $4, $5)
            """,
            charge_id,
            user_id,
            message_id,
            -cost_rub,  # negative = debit per data-model.md convention
            description,
        )
        await conn.execute(
            """
            INSERT INTO usage
                (id, user_id, project_id, message_id, model_id,
                 tokens_in, tokens_out, cost_rub)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
            """,
            usage_id,
            user_id,
            project_id,
            message_id,
            model_id,
            tokens_in,
            tokens_out,
            cost_rub,
        )

    log.info(
        "billing.charged",
        charge_id=str(charge_id),
        user_id=str(user_id),
        model_id=model_id,
        tokens_in=tokens_in,
        tokens_out=tokens_out,
        cost_rub=str(cost_rub),
    )
    return charge_id

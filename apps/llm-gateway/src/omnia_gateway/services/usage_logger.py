"""Persist per-request token usage to Postgres `usage`.

Schema lives in docs/02-data-model.md. Wallet debit + wallet_charges insert
land in M3 (`services/billing.py`); for M0 we only record analytics.
"""

from __future__ import annotations

from decimal import Decimal
from uuid import UUID, uuid4

import structlog

from omnia_gateway.core.db import get_pool

log = structlog.get_logger(__name__)


async def log_usage(
    *,
    user_id: UUID | None,
    project_id: UUID | None,
    message_id: UUID | None,
    model_id: str,
    tokens_in: int,
    tokens_out: int,
    cost_rub: Decimal,
) -> UUID:
    """INSERT INTO usage. Returns the generated row id.

    Failures are logged and re-raised; the chat router decides whether the
    request as a whole still succeeds (M0: log-and-swallow; M3: hard fail
    when billing is wired up).
    """
    row_id = uuid4()
    pool = get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO usage
                (id, user_id, project_id, message_id, model_id,
                 tokens_in, tokens_out, cost_rub)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
            """,
            row_id,
            user_id,
            project_id,
            message_id,
            model_id,
            tokens_in,
            tokens_out,
            cost_rub,
        )
    log.info(
        "usage.logged",
        usage_id=str(row_id),
        user_id=str(user_id) if user_id else None,
        project_id=str(project_id) if project_id else None,
        message_id=str(message_id) if message_id else None,
        model_id=model_id,
        tokens_in=tokens_in,
        tokens_out=tokens_out,
        cost_rub=str(cost_rub),
    )
    return row_id

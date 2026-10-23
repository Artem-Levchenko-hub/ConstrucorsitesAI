"""Finish the Free catalog transition after old billing writers are retired."""

import argparse
import asyncio
import json
import re

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from yleum_api.core.config import get_settings
from yleum_api.models.billing import (
    FREE_PLAN_ID,
    FREE_PLAN_V1_ID,
    FREE_PLAN_V2_ID,
    FREE_PLAN_V3_ID,
    BillingPlan,
    Subscription,
)


async def normalize_live_free_subscriptions(session: AsyncSession) -> int:
    """Idempotent catalog-only update; never alter paid, ended or financial rows."""
    target = await session.scalar(select(BillingPlan).where(BillingPlan.id == FREE_PLAN_ID))
    if target is None or not (
        target.is_active
        and target.code == "free"
        and target.version == 4
        and target.price_rub == 0
        and target.included_credit_rub == 0
        and target.entitlements.get("included_creations") == 1
        and target.entitlements.get("included_edits") == 1
        and target.entitlements.get("generation_allowance_period") == "lifetime"
    ):
        raise ValueError("exact active Free v4 terms required for cutover")
    result = await session.scalars(
        update(Subscription)
        .where(
            Subscription.plan_id.in_((FREE_PLAN_V1_ID, FREE_PLAN_V2_ID, FREE_PLAN_V3_ID)),
            Subscription.status.in_(
                ("pending_payment", "trialing", "active", "past_due", "paused")
            ),
        )
        .values(plan_id=FREE_PLAN_ID)
        .returning(Subscription.id)
    )
    return len(result.all())


async def _command(release_sha: str) -> None:
    from yleum_api.core.db import dispose_engine, get_engine
    from yleum_api.services.generation_deployment_drain import _locked_drain, drain_status

    if not re.fullmatch(r"[0-9a-f]{40}", release_sha):
        raise ValueError("full target release required")
    if get_settings().omnia_release_sha != release_sha:
        raise ValueError("cutover process does not run the target release")
    try:
        async with async_sessionmaker(get_engine(), expire_on_commit=False)() as session:
            if await _locked_drain(session, release_sha) is None:
                raise ValueError("owned deployment drain required")
            status = await drain_status(session)
            if status["release_sha"] != release_sha or not status["drained"]:
                raise ValueError("target release must remain quiesced during cutover")
            changed = await normalize_live_free_subscriptions(session)
            await session.commit()
            print(
                json.dumps(
                    {
                        "free_catalog_cutover": True,
                        "normalized": changed,
                        "release_sha": release_sha,
                    },
                    sort_keys=True,
                )
            )
    finally:
        await dispose_engine()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("release_sha")
    asyncio.run(_command(parser.parse_args().release_sha))

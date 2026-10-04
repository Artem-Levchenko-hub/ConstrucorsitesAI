"""Only a proven, prospectively admitted build can produce one wallet settlement.

Provider receipts and financial history remain append-only. The generation row
is the common serialization point for receipt recording, terminal sealing and
reconciliation. No function commits its caller's transaction.
"""

import re
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import and_, func, or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from yleum_api.models.billing import BillingAccount
from yleum_api.models.generation_billing import (
    GenerationBillingIntent,
    GenerationBillingOutbox,
    GenerationBillingPolicy,
)
from yleum_api.models.generation_run import GenerationRun
from yleum_api.models.message import Message
from yleum_api.models.snapshot import Snapshot
from yleum_api.models.usage import Usage
from yleum_api.models.wallet import Wallet
from yleum_api.models.wallet_charge import WalletCharge
from yleum_api.services.billing_accounts import resolve_billing_account

POLICY_VERSION = "accepted-build-v1"


async def admit_policy(session: AsyncSession, run_id: UUID, *, is_free: bool) -> None:
    run = await session.get(GenerationRun, run_id, with_for_update=True)
    if run is None or run.status not in {"pending", "running", "queued_for_capacity"}:
        raise ValueError("Generation billing admission is not active")
    existing = await session.get(GenerationBillingPolicy, run_id)
    if existing is not None:
        if existing.is_free != is_free or existing.version != POLICY_VERSION:
            raise ValueError("Generation billing policy is immutable")
        return
    session.add(GenerationBillingPolicy(run_id=run_id, is_free=is_free, version=POLICY_VERSION))
    await session.flush()


async def record_accepted_candidate(
    session: AsyncSession,
    *,
    run_id: UUID,
    snapshot_id: UUID,
    permit: Any,
    proof: Any,
) -> None:
    from yleum_api.services.promotion_permit import require_promotion_permit

    permit = require_promotion_permit(permit, proof=proof, expected_generation_run_id=run_id)
    run = await session.get(GenerationRun, run_id, with_for_update=True)
    if run is None or await session.get(GenerationBillingPolicy, run_id) is None:
        return  # Historical completions never acquire a new charging policy.
    snapshot = await session.get(Snapshot, snapshot_id)
    if (
        run.status not in {"pending", "running", "queued_for_capacity"}
        or snapshot is None
        or snapshot.project_id != run.project_id
    ):
        raise ValueError("Accepted billing candidate binding changed")
    run.agent_state = {
        **(run.agent_state or {}),
        "billing_accepted_candidate": {
            "snapshot_id": str(snapshot.id),
            "commit_sha": snapshot.commit_sha,
            "permit_digest": permit.permit_digest,
            "proof_key": permit.proof_key,
            "artifact_digest": permit.artifact_digest,
            "build_contract": permit.build_contract_version,
            "release_contract": permit.proof_contract_version,
        },
    }


async def _validate_billable_intent(
    session: AsyncSession,
    run: GenerationRun,
    intent: GenerationBillingIntent,
) -> None:
    policy = await session.get(GenerationBillingPolicy, run.id)
    snapshot = (
        await session.get(Snapshot, intent.accepted_snapshot_id)
        if intent.accepted_snapshot_id
        else None
    )
    message = (
        await session.get(Message, run.assistant_message_id) if run.assistant_message_id else None
    )
    marker = (run.agent_state or {}).get("billing_accepted_candidate")
    if not (
        run.status == "completed"
        and run.response_mode == "build"
        and intent.user_id == run.user_id
        and policy
        and not policy.is_free
        and policy.version == POLICY_VERSION
        and isinstance(marker, dict)
        and marker == intent.receipt
        and snapshot
        and snapshot.project_id == run.project_id
        and message
        and message.project_id == run.project_id
        and message.snapshot_id == snapshot.id
        and marker.get("snapshot_id") == str(snapshot.id)
        and marker.get("commit_sha") == snapshot.commit_sha
        and (run.agent_state or {}).get("snapshot_id") == str(snapshot.id)
        and (run.agent_state or {}).get("commit_sha") == snapshot.commit_sha
    ):
        # Never guess whether to charge/waive/refund a contradictory financial
        # intent. Keep it intact and let the worker report reconciliation needed.
        raise RuntimeError("Generation billing requires explicit reconciliation")


async def _debit_intent(
    session: AsyncSession,
    run: GenerationRun,
    intent: GenerationBillingIntent,
) -> bool:
    if intent.outcome != "billable" or intent.billing_account_id is None:
        return False
    await _validate_billable_intent(session, run, intent)
    reference = "generation:" + str(intent.run_id)
    if await session.scalar(select(WalletCharge.id).where(WalletCharge.external_ref == reference)):
        return True
    wallet = await session.scalar(
        select(Wallet)
        .where(Wallet.billing_account_id == intent.billing_account_id)
        .with_for_update()
    )
    if wallet is None or wallet.balance_rub < intent.amount_rub:
        return False  # Frozen unpaid intent remains retriable; build stays accepted.
    wallet.balance_rub -= intent.amount_rub
    session.add(
        WalletCharge(
            billing_account_id=intent.billing_account_id,
            user_id=intent.user_id,
            entry_type="usage",
            amount_rub=-intent.amount_rub,
            balance_after_rub=wallet.balance_rub,
            external_ref=reference,
            description="Accepted generation build",
        )
    )
    await session.flush()
    return True


async def refund_legacy_failed_locked(session: AsyncSession, run: GenerationRun) -> int:
    if run.status not in {"failed", "cancelled"}:
        return 0
    original = list(
        (
            await session.scalars(
                select(WalletCharge)
                .join(Usage, WalletCharge.external_ref == text("'usage:' || usage.id::text"))
                .where(
                    or_(
                        Usage.run_id == run.id,
                        and_(
                            Usage.run_id.is_(None),
                            Usage.message_id == run.assistant_message_id,
                            select(func.count())
                            .select_from(GenerationRun)
                            .where(
                                GenerationRun.assistant_message_id == Usage.message_id,
                                GenerationRun.user_id == Usage.user_id,
                                GenerationRun.project_id == Usage.project_id,
                            )
                            .correlate(Usage)
                            .scalar_subquery()
                            == 1,
                        ),
                    ),
                    Usage.user_id == run.user_id,
                    Usage.project_id == run.project_id,
                    WalletCharge.user_id == run.user_id,
                    WalletCharge.billing_account_id.in_(
                        select(BillingAccount.id).where(
                            BillingAccount.personal_user_id == run.user_id,
                        )
                    ),
                    WalletCharge.entry_type == "usage",
                    WalletCharge.amount_rub < 0,
                    WalletCharge.amount_rub == -Usage.cost_rub,
                    (Usage.stage.is_(None) | (Usage.stage != "runtime_ai")),
                )
                .order_by(WalletCharge.id)
            )
        ).all()
    )
    refunded = 0
    for charge in original:
        ref = "generation-refund:" + str(charge.id)
        if await session.scalar(select(WalletCharge.id).where(WalletCharge.external_ref == ref)):
            continue
        wallet = await session.scalar(
            select(Wallet)
            .where(Wallet.billing_account_id == charge.billing_account_id)
            .with_for_update()
        )
        if wallet is None:
            continue
        credit = -charge.amount_rub
        wallet.balance_rub += credit
        session.add(
            WalletCharge(
                billing_account_id=charge.billing_account_id,
                user_id=charge.user_id,
                message_id=charge.message_id,
                entry_type="refund",
                amount_rub=credit,
                balance_after_rub=wallet.balance_rub,
                external_ref=ref,
                description="Failed generation: reversal of linked usage charge",
            )
        )
        await session.flush()
        refunded += 1
    return refunded


async def seal_terminal_locked(session: AsyncSession, run: GenerationRun) -> None:
    """Caller holds run FOR UPDATE and has chosen the durable terminal verdict."""
    if run.status not in {"completed", "failed", "cancelled"}:
        return
    policy = await session.get(GenerationBillingPolicy, run.id)
    if policy is None:
        return  # Never retrocharge historical free/successful generations.
    intent = await session.get(GenerationBillingIntent, run.id)
    if intent is None:
        accepted = (run.agent_state or {}).get("billing_accepted_candidate")
        snapshot = None
        message = (
            await session.get(Message, run.assistant_message_id)
            if run.assistant_message_id
            else None
        )
        if isinstance(accepted, dict):
            try:
                snapshot = await session.get(Snapshot, UUID(str(accepted.get("snapshot_id"))))
            except (TypeError, ValueError):
                pass
        from yleum_api.services.promotion_permit import (
            MAX_FULL_BUILD_CONTRACT_VERSION,
            MAX_RELEASE_PROOF_CONTRACT_VERSION,
        )

        eligible = bool(
            run.status == "completed"
            and run.response_mode == "build"
            and not policy.is_free
            and policy.version == POLICY_VERSION
            and isinstance(accepted, dict)
            and snapshot
            and snapshot.project_id == run.project_id
            and message
            and message.snapshot_id == snapshot.id
            and accepted.get("commit_sha") == snapshot.commit_sha
            and (run.agent_state or {}).get("snapshot_id") == str(snapshot.id)
            and (run.agent_state or {}).get("commit_sha") == snapshot.commit_sha
            and accepted.get("build_contract") == MAX_FULL_BUILD_CONTRACT_VERSION
            and accepted.get("release_contract") == MAX_RELEASE_PROOF_CONTRACT_VERSION
            and all(
                isinstance(accepted.get(k), str) and re.fullmatch("[0-9a-f]{64}", accepted[k])
                for k in ("permit_digest", "proof_key", "artifact_digest")
            )
        )
        usages = list(
            (
                await session.scalars(
                    select(Usage)
                    .where(
                        Usage.run_id == run.id,
                        Usage.user_id == run.user_id,
                        Usage.project_id == run.project_id,
                        Usage.stage.is_(None) | (Usage.stage != "runtime_ai"),
                    )
                    .order_by(Usage.id)
                )
            ).all()
        )
        account = await resolve_billing_account(session, run.user_id) if eligible else None
        intent = GenerationBillingIntent(
            run_id=run.id,
            user_id=run.user_id,
            billing_account_id=account.id if account else None,
            outcome="billable" if eligible else "waived",
            amount_rub=sum((u.cost_rub for u in usages), Decimal(0)) if eligible else Decimal(0),
            usage_ids=[str(u.id) for u in usages],
            accepted_snapshot_id=snapshot.id if eligible and snapshot else None,
            receipt=dict(accepted)
            if eligible and isinstance(accepted, dict)
            else {"reason": run.status if run.status != "completed" else "no_paid_accepted_build"},
        )
        session.add(intent)
        await session.flush()
    outbox = await session.get(GenerationBillingOutbox, run.id)
    if intent.outcome == "billable" and outbox is None:
        outbox = GenerationBillingOutbox(run_id=run.id, next_attempt_at=datetime.now(UTC))
        session.add(outbox)
    # Terminal commit never performs wallet work. The durable outbox can fail
    # independently without changing the accepted/failed generation result.
    run.agent_state = {
        **(run.agent_state or {}),
        "generation_billing": {
            "outcome": intent.outcome,
            "amount_rub": str(intent.amount_rub),
            "payment": "settled"
            if outbox and outbox.settled_at
            else "pending"
            if intent.outcome == "billable"
            else "waived",
        },
    }


async def _settle_terminal_locked(session: AsyncSession, run: GenerationRun) -> None:
    """Separate payment transaction; caller holds the same generation-row lock."""
    existing = await session.get(GenerationBillingIntent, run.id)
    if existing is not None and existing.outcome == "billable":
        await _validate_billable_intent(session, run, existing)
    await refund_legacy_failed_locked(session, run)
    await seal_terminal_locked(session, run)
    intent = await session.get(GenerationBillingIntent, run.id)
    if intent is None or intent.outcome != "billable":
        return
    outbox = await session.get(GenerationBillingOutbox, run.id)
    assert outbox is not None
    settled = await _debit_intent(session, run, intent)
    if settled:
        outbox.settled_at = datetime.now(UTC)
    else:
        outbox.next_attempt_at = datetime.now(UTC) + timedelta(seconds=60)
    run.agent_state = {
        **(run.agent_state or {}),
        "generation_billing": {
            "outcome": intent.outcome,
            "amount_rub": str(intent.amount_rub),
            "payment": "settled" if settled else "unpaid",
        },
    }


async def reconcile_generation_billing(session: AsyncSession, *, limit: int = 100) -> int:
    """Retry frozen intents and exact linked failed charges, with two-worker locking."""
    candidates = text("""
        (EXISTS(SELECT 1 FROM generation_billing_policies p
          WHERE p.run_id=generation_runs.id)
         AND (NOT EXISTS(SELECT 1 FROM generation_billing_intents i
           WHERE i.run_id=generation_runs.id)
          OR EXISTS(SELECT 1 FROM generation_billing_intents i
           JOIN generation_billing_outbox o ON o.run_id=i.run_id
           WHERE i.run_id=generation_runs.id AND i.outcome='billable'
             AND o.settled_at IS NULL AND o.next_attempt_at<=now()
            AND NOT EXISTS(SELECT 1 FROM wallet_charges c
             WHERE c.external_ref='generation:' || i.run_id::text))))
        OR (generation_runs.status IN ('failed','cancelled') AND EXISTS(
          SELECT 1 FROM usage u JOIN wallet_charges c ON c.external_ref='usage:' || u.id::text
           WHERE (u.run_id=generation_runs.id OR
             (u.run_id IS NULL AND u.message_id=generation_runs.assistant_message_id
              AND (SELECT count(*) FROM generation_runs bound
                WHERE bound.assistant_message_id=u.message_id AND bound.user_id=u.user_id
                  AND bound.project_id=u.project_id)=1)) AND u.user_id=generation_runs.user_id
             AND u.project_id=generation_runs.project_id AND c.user_id=u.user_id
             AND c.entry_type='usage' AND c.amount_rub<0 AND c.amount_rub=-u.cost_rub
             AND EXISTS(SELECT 1 FROM billing_accounts a
               WHERE a.id=c.billing_account_id AND a.personal_user_id=generation_runs.user_id)
             AND (u.stage IS NULL OR u.stage!='runtime_ai')
             AND NOT EXISTS(SELECT 1 FROM wallet_charges r
              WHERE r.external_ref='generation-refund:' || c.id::text)))
    """)
    rows = list(
        (
            await session.scalars(
                select(GenerationRun)
                .where(GenerationRun.status.in_(["completed", "failed", "cancelled"]), candidates)
                .order_by(GenerationRun.finished_at, GenerationRun.id)
                .with_for_update(skip_locked=True)
                .limit(limit)
            )
        ).all()
    )
    for run in rows:
        await _settle_terminal_locked(session, run)
    return len(rows)

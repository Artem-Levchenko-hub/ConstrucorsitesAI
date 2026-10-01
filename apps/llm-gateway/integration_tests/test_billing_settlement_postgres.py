"""Opt-in physical settlement tests; each run owns and drops its entire database."""

from __future__ import annotations

import asyncio
import json
import os
from collections.abc import AsyncIterator, Iterator
from decimal import Decimal
from pathlib import Path
from urllib.parse import urlparse
from uuid import UUID, uuid4

import asyncpg
import pytest

from yleum_gateway.core.errors import BillingReconciliationRequiredError, WalletEmptyError
from yleum_gateway.services import billing

ROOT = Path(__file__).resolve().parents[3]


async def _admin(dsn: str, statement: str) -> None:
    connection = await asyncpg.connect(dsn)
    try:
        await connection.execute(statement)
    finally:
        await connection.close()


@pytest.fixture(scope="session")
def database_url() -> Iterator[str]:
    admin = os.environ.get("QA_BILLING_POSTGRES_ADMIN_URL")
    if not admin:
        pytest.skip("requires owned disposable QA_BILLING_POSTGRES_ADMIN_URL")
    parsed = urlparse(admin)
    if parsed.hostname not in {"127.0.0.1", "localhost"} or parsed.path != "/postgres":
        pytest.fail("settlement integration tests require a local disposable admin database")
    name = "qa_settlement_" + uuid4().hex
    target = admin.rsplit("/", 1)[0] + "/" + name
    asyncio.run(_admin(admin, f'CREATE DATABASE "{name}"'))
    old_database = os.environ.get("DATABASE_URL")
    old_jwt = os.environ.get("JWT_SECRET")
    try:
        from alembic import command
        from alembic.config import Config
        from yleum_api.core.config import get_settings

        os.environ["DATABASE_URL"] = target.replace("postgresql://", "postgresql+asyncpg://", 1)
        os.environ["JWT_SECRET"] = "settlement-physical-test-synthetic-secret"
        get_settings.cache_clear()
        config = Config(str(ROOT / "apps/api/alembic.ini"))
        config.set_main_option("script_location", str(ROOT / "apps/api/migrations"))
        command.upgrade(config, "head")
        yield target
    finally:
        for key, previous in (("DATABASE_URL", old_database), ("JWT_SECRET", old_jwt)):
            if previous is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = previous
        get_settings.cache_clear()
        asyncio.run(_admin(admin, f'DROP DATABASE "{name}" WITH (FORCE)'))


@pytest.fixture
async def pool(database_url: str, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[asyncpg.Pool]:
    connection_pool = await asyncpg.create_pool(database_url, min_size=1, max_size=5)
    monkeypatch.setattr(billing, "get_pool", lambda: connection_pool)
    try:
        yield connection_pool
    finally:
        await connection_pool.close()


async def _owner(pool: asyncpg.Pool, balance: str = "100") -> tuple[UUID, UUID, list[UUID]]:
    user, account, project = uuid4(), uuid4(), uuid4()
    messages = [uuid4(), uuid4()]
    async with pool.acquire() as connection, connection.transaction():
        await connection.execute("INSERT INTO users(id,is_anon) VALUES($1,true)", user)
        await connection.execute(
            "INSERT INTO billing_accounts(id,personal_user_id,created_by_user_id) VALUES($1,$2,$2)",
            account,
            user,
        )
        await connection.execute(
            "INSERT INTO wallets(user_id,billing_account_id,balance_rub) VALUES($1,$2,$3)",
            user,
            account,
            Decimal(balance),
        )
        await connection.execute(
            "INSERT INTO projects(id,owner_id,name,slug,template) "
            "VALUES($1,$2,'settlement QA',$3,'max_miniapp')",
            project,
            user,
            project.hex,
        )
        for message in messages:
            await connection.execute(
                "INSERT INTO messages(id,project_id,role,content) "
                "VALUES($1,$2,'assistant','synthetic receipt delivery')",
                message,
                project,
            )
    print(
        json.dumps(
            {
                "phase": "before",
                "user_id": str(user),
                "balance_rub": balance,
                "charges": 0,
                "usage": 0,
                "settlements": [],
            }
        )
    )
    return user, project, messages


async def _charge(user: UUID, project: UUID, message: UUID, key: str = "provider-event") -> UUID:
    return await billing.charge(
        user_id=user,
        project_id=project,
        message_id=message,
        model_id="qa-no-provider",
        tokens_in=3,
        tokens_out=5,
        cost_rub=Decimal("12.5"),
        description="synthetic completed provider receipt",
        provider_request_id=key,
    )


async def _counts(pool: asyncpg.Pool, user: UUID) -> tuple[Decimal, int, int]:
    async with pool.acquire() as connection:
        row = await connection.fetchrow(
            "SELECT balance_rub, (SELECT count(*) FROM wallet_charges WHERE user_id=$1) AS charges, "
            "(SELECT count(*) FROM usage WHERE user_id=$1) AS usages FROM wallets WHERE user_id=$1",
            user,
        )
        receipts = await connection.fetch(
            "SELECT provider_scope,provider_request_id,status,wallet_charge_id FROM usage_settlements "
            "WHERE user_id=$1 ORDER BY created_at",
            user,
        )
    print(
        json.dumps(
            {
                "phase": "observed",
                "user_id": str(user),
                "balance_rub": str(row["balance_rub"]),
                "charges": row["charges"],
                "usage": row["usages"],
                "settlements": [dict(item) for item in receipts],
            },
            default=str,
        )
    )
    return Decimal(row["balance_rub"]), row["charges"], row["usages"]


async def test_same_provider_receipt_with_new_delivery_debits_once(pool: asyncpg.Pool) -> None:
    user, project, messages = await _owner(pool)
    first = await _charge(user, project, messages[0])
    replay = await billing.charge(
        user_id=user,
        project_id=project,
        message_id=messages[1],
        model_id="qa-no-provider",
        tokens_in=3,
        tokens_out=5,
        cost_rub=Decimal("12.5000"),
        description="fresh delivery metadata",
        provider_request_id="provider-event",
        retry_count=7,
    )
    assert replay == first
    assert await _counts(pool, user) == (Decimal("87.5"), 1, 1)


async def test_concurrent_provider_deliveries_debit_once(pool: asyncpg.Pool) -> None:
    user, project, messages = await _owner(pool)
    results = await asyncio.gather(*(_charge(user, project, message) for message in messages))
    assert results[0] == results[1]
    assert await _counts(pool, user) == (Decimal("87.5"), 1, 1)


async def test_unpaid_usage_survives_caller_transaction_rollback(pool: asyncpg.Pool) -> None:
    user, project, messages = await _owner(pool, balance="1")
    with pytest.raises(WalletEmptyError):
        async with pool.acquire() as caller, caller.transaction():
            await caller.execute("UPDATE users SET session_version=1 WHERE id=$1", user)
            await _charge(user, project, messages[0])
    assert await _counts(pool, user) == (Decimal("1"), 0, 1)
    async with pool.acquire() as connection:
        assert await connection.fetchval("SELECT session_version FROM users WHERE id=$1", user) == 0
        assert (
            await connection.fetchval(
                "SELECT status FROM usage_settlements WHERE user_id=$1",
                user,
            )
            == "unpaid"
        )
    with pytest.raises(WalletEmptyError):
        await _charge(user, project, messages[1])
    assert await _counts(pool, user) == (Decimal("1"), 0, 1)


async def test_lost_reply_after_commit_replays_existing_charge(
    pool: asyncpg.Pool,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user, project, messages = await _owner(pool)
    original = billing.log.info

    def lost_reply(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("synthetic process failure after database commit")

    monkeypatch.setattr(billing.log, "info", lost_reply)
    with pytest.raises(RuntimeError, match="after database commit"):
        await _charge(user, project, messages[0])
    assert await _counts(pool, user) == (Decimal("87.5"), 1, 1)
    monkeypatch.setattr(billing.log, "info", original)
    await _charge(user, project, messages[1])
    assert await _counts(pool, user) == (Decimal("87.5"), 1, 1)


async def test_same_provider_id_has_independent_owner_scope(pool: asyncpg.Pool) -> None:
    for _ in range(2):
        user, project, messages = await _owner(pool)
        await _charge(user, project, messages[0])
        assert await _counts(pool, user) == (Decimal("87.5"), 1, 1)


@pytest.mark.parametrize("scopes", [("llmgw", "other-provider"), ("other-provider", "llmgw")])
async def test_distinct_provider_scopes_are_independent(
    pool: asyncpg.Pool,
    scopes: tuple[str, str],
) -> None:
    user, project, messages = await _owner(pool)
    for scope, message in zip(scopes, messages, strict=True):
        await billing.charge(
            user_id=user,
            project_id=project,
            message_id=message,
            model_id="qa-no-provider",
            tokens_in=3,
            tokens_out=5,
            cost_rub=Decimal("12.5"),
            description="synthetic receipt",
            provider_request_id="same-event",
            provider_scope=scope,
        )
    assert await _counts(pool, user) == (Decimal("75"), 2, 2)


async def test_conflicting_replay_does_not_change_financial_history(pool: asyncpg.Pool) -> None:
    user, project, messages = await _owner(pool)
    await _charge(user, project, messages[0])
    with pytest.raises(BillingReconciliationRequiredError, match="conflicting"):
        await billing.charge(
            user_id=user,
            project_id=project,
            message_id=messages[1],
            model_id="qa-no-provider",
            tokens_in=3,
            tokens_out=5,
            cost_rub=Decimal("13"),
            description="conflicting synthetic receipt",
            provider_request_id="provider-event",
        )
    assert await _counts(pool, user) == (Decimal("87.5"), 1, 1)


async def test_free_usage_replays_without_wallet_charge(pool: asyncpg.Pool) -> None:
    user, project, messages = await _owner(pool)
    ids = []
    for message in messages:
        ids.append(
            await billing.charge(
                user_id=user,
                project_id=project,
                message_id=message,
                model_id="qa-no-provider",
                tokens_in=3,
                tokens_out=5,
                cost_rub=Decimal("12.5000"),
                description="free synthetic receipt",
                provider_request_id="provider-event",
                free=True,
            )
        )
    assert ids[0] == ids[1]
    assert await _counts(pool, user) == (Decimal("100"), 0, 1)
    async with pool.acquire() as connection:
        assert (
            await connection.fetchval("SELECT status FROM usage_settlements WHERE user_id=$1", user)
            == "free"
        )


async def test_failure_before_commit_rolls_back_debit_and_all_receipt_rows(
    pool: asyncpg.Pool,
) -> None:
    user, project, messages = await _owner(pool)
    async with pool.acquire() as connection:
        await connection.execute(
            "CREATE FUNCTION qa_reject_settlement() RETURNS trigger LANGUAGE plpgsql AS $$ "
            "BEGIN RAISE EXCEPTION 'synthetic settlement persistence failure'; END $$"
        )
        await connection.execute(
            "CREATE TRIGGER qa_reject_settlement BEFORE INSERT ON usage_settlements "
            "FOR EACH ROW EXECUTE FUNCTION qa_reject_settlement()"
        )
    try:
        with pytest.raises(asyncpg.RaiseError, match="synthetic settlement persistence failure"):
            await _charge(user, project, messages[0])
        assert await _counts(pool, user) == (Decimal("100"), 0, 0)
    finally:
        async with pool.acquire() as connection:
            await connection.execute("DROP TRIGGER qa_reject_settlement ON usage_settlements")
            await connection.execute("DROP FUNCTION qa_reject_settlement()")
    await _charge(user, project, messages[1])
    assert await _counts(pool, user) == (Decimal("87.5"), 1, 1)


async def test_legacy_duplicate_receipts_require_reconciliation_without_new_debit(
    pool: asyncpg.Pool,
) -> None:
    user, project, messages = await _owner(pool)
    # Seed the old accounting contract directly; never erase financial rows.
    async with pool.acquire() as connection, connection.transaction():
        account = await connection.fetchval(
            "SELECT billing_account_id FROM wallets WHERE user_id=$1",
            user,
        )
        for message, balance_after in zip(messages, (Decimal("87.5"), Decimal("75")), strict=True):
            usage_id, charge_id = uuid4(), uuid4()
            await connection.execute(
                "UPDATE wallets SET balance_rub=$2 WHERE user_id=$1",
                user,
                balance_after,
            )
            await connection.execute(
                "INSERT INTO usage(id,user_id,project_id,message_id,model_id,tokens_in,tokens_out,"
                "cost_rub,cache_read_tokens,cache_write_tokens,retry_count,provider_request_id) "
                "VALUES($1,$2,$3,$4,'qa-no-provider',3,5,12.5,0,0,0,'provider-event')",
                usage_id,
                user,
                project,
                message,
            )
            await connection.execute(
                "INSERT INTO wallet_charges(id,billing_account_id,user_id,message_id,entry_type,"
                "amount_rub,balance_after_rub,external_ref,description) "
                "VALUES($1,$2,$3,$4,'usage',-12.5,$5,$6,'synthetic legacy receipt')",
                charge_id,
                account,
                user,
                message,
                balance_after,
                f"usage:{usage_id}",
            )
    assert await _counts(pool, user) == (Decimal("75"), 2, 2)
    with pytest.raises(BillingReconciliationRequiredError, match="historical.*reconciliation"):
        await _charge(user, project, messages[1])
    assert await _counts(pool, user) == (Decimal("75"), 2, 2)


async def test_unkeyed_completed_usage_is_retained_when_unpaid(pool: asyncpg.Pool) -> None:
    user, project, messages = await _owner(pool, balance="1")
    with pytest.raises(WalletEmptyError):
        await billing.charge(
            user_id=user,
            project_id=project,
            message_id=messages[0],
            model_id="qa-no-provider",
            tokens_in=3,
            tokens_out=5,
            cost_rub=Decimal("12.5"),
            description="unkeyed synthetic completion",
        )
    assert await _counts(pool, user) == (Decimal("1"), 0, 1)
    async with pool.acquire() as connection:
        receipt = await connection.fetchrow(
            "SELECT provider_request_id,status FROM usage_settlements WHERE user_id=$1",
            user,
        )
    assert dict(receipt) == {"provider_request_id": None, "status": "unpaid"}


@pytest.mark.parametrize("same_owner", [True, False])
async def test_historical_truncated_id_requires_owner_scoped_reconciliation(
    pool: asyncpg.Pool,
    same_owner: bool,
) -> None:
    historical_user, project, messages = await _owner(pool)
    provider_id = "provider-" + "x" * 205
    async with pool.acquire() as connection, connection.transaction():
        account = await connection.fetchval(
            "SELECT billing_account_id FROM wallets WHERE user_id=$1",
            historical_user,
        )
        usage_id, charge_id = uuid4(), uuid4()
        await connection.execute(
            "UPDATE wallets SET balance_rub=87.5 WHERE user_id=$1", historical_user
        )
        await connection.execute(
            "INSERT INTO usage(id,user_id,project_id,message_id,model_id,tokens_in,tokens_out,"
            "cost_rub,cache_read_tokens,cache_write_tokens,retry_count,provider_request_id) "
            "VALUES($1,$2,$3,$4,'qa-no-provider',3,5,12.5,0,0,0,$5)",
            usage_id,
            historical_user,
            project,
            messages[0],
            provider_id[:200],
        )
        await connection.execute(
            "INSERT INTO wallet_charges(id,billing_account_id,user_id,message_id,entry_type,"
            "amount_rub,balance_after_rub,external_ref,description) "
            "VALUES($1,$2,$3,$4,'usage',-12.5,87.5,$5,'synthetic legacy truncated receipt')",
            charge_id,
            account,
            historical_user,
            messages[0],
            f"usage:{usage_id}",
        )
    assert await _counts(pool, historical_user) == (Decimal("87.5"), 1, 1)
    if same_owner:
        with pytest.raises(BillingReconciliationRequiredError, match="historical.*reconciliation"):
            await _charge(historical_user, project, messages[1], provider_id)
    else:
        user, own_project, own_messages = await _owner(pool)
        await _charge(user, own_project, own_messages[0], provider_id)
        assert await _counts(pool, user) == (Decimal("87.5"), 1, 1)
    assert await _counts(pool, historical_user) == (Decimal("87.5"), 1, 1)


async def test_new_full_receipt_ids_with_shared_legacy_prefix_remain_distinct(
    pool: asyncpg.Pool,
) -> None:
    user, project, messages = await _owner(pool)
    keys = ["x" * 200 + "first", "x" * 200 + "second"]
    ids = [
        await _charge(user, project, message, key)
        for message, key in zip(messages, keys, strict=True)
    ]
    assert ids[0] != ids[1]
    replay = await _charge(user, project, messages[1], keys[0])
    assert replay == ids[0]
    assert await _counts(pool, user) == (Decimal("75"), 2, 2)
    async with pool.acquire() as connection:
        actual = await connection.fetch(
            "SELECT provider_request_id FROM usage_settlements WHERE user_id=$1",
            user,
        )
    assert {row["provider_request_id"] for row in actual} == set(keys)

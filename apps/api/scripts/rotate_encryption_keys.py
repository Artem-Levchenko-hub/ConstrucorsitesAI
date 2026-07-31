#!/usr/bin/env python3
"""Transactionally re-encrypt production secrets during key rotation.

Secret material is accepted only through environment variables and is never
printed. Every stored token is decrypted before the first UPDATE; the database
transaction rolls back if any token is unreadable.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import hashlib
import json
import os
from dataclasses import dataclass

import asyncpg  # type: ignore[import-untyped]
from cryptography.fernet import Fernet


@dataclass(frozen=True)
class ColumnSpec:
    table: str
    columns: tuple[str, ...]
    key_kind: str


SPECS = (
    ColumnSpec("users", ("github_token_enc",), "jwt"),
    ColumnSpec("deploy_targets", ("ssh_secret_enc",), "strong"),
    ColumnSpec("max_integrations", ("bot_token_enc", "webhook_secret_enc"), "strong"),
    ColumnSpec("app_integrations", ("credentials_enc",), "strong"),
)


def _required_env(name: str) -> str:
    value = os.environ.get(name, "")
    if not value:
        raise RuntimeError(f"{name} is required")
    return value


def jwt_fernet(secret: str) -> Fernet:
    key = base64.urlsafe_b64encode(hashlib.sha256(secret.encode("utf-8")).digest())
    return Fernet(key)


def strong_fernet(secret: str) -> Fernet:
    raw = secret.encode("utf-8")
    try:
        return Fernet(raw)
    except (ValueError, TypeError):
        key = base64.urlsafe_b64encode(hashlib.sha256(raw).digest())
        return Fernet(key)


def _database_url() -> str:
    url = _required_env("DATABASE_URL")
    return url.replace("postgresql+asyncpg://", "postgresql://", 1)


def _key_pairs() -> dict[str, tuple[Fernet, Fernet]]:
    old_jwt = _required_env("OLD_JWT_SECRET")
    new_jwt = _required_env("NEW_JWT_SECRET")
    old_strong = _required_env("OLD_SECRETS_ENCRYPTION_KEY")
    new_strong = _required_env("NEW_SECRETS_ENCRYPTION_KEY")
    if old_jwt == new_jwt or old_strong == new_strong:
        raise RuntimeError("old and new encryption keys must differ")
    return {
        "jwt": (jwt_fernet(old_jwt), jwt_fernet(new_jwt)),
        "strong": (strong_fernet(old_strong), strong_fernet(new_strong)),
    }


async def verify_active() -> dict[str, int]:
    active = {
        "jwt": jwt_fernet(_required_env("JWT_SECRET")),
        "strong": strong_fernet(_required_env("SECRETS_ENCRYPTION_KEY")),
    }
    connection = await asyncpg.connect(_database_url())
    counts: dict[str, int] = {}
    try:
        async with connection.transaction(readonly=True):
            for spec in SPECS:
                columns = ", ".join(spec.columns)
                rows = await connection.fetch(f"SELECT {columns} FROM {spec.table}")
                for row in rows:
                    for column in spec.columns:
                        token = row[column]
                        if token is not None:
                            active[spec.key_kind].decrypt(token.encode("ascii"))
                            key = f"{spec.table}.{column}"
                            counts[key] = counts.get(key, 0) + 1
    finally:
        await connection.close()
    return counts


async def rotate() -> dict[str, int]:
    pairs = _key_pairs()
    connection = await asyncpg.connect(_database_url())
    counts: dict[str, int] = {}
    try:
        async with connection.transaction():
            # Stop concurrent secret writes for the short re-encryption window.
            await connection.execute(
                "LOCK TABLE users, deploy_targets, max_integrations, app_integrations "
                "IN SHARE ROW EXCLUSIVE MODE"
            )

            prepared: list[tuple[ColumnSpec, object, str, bytes]] = []
            for spec in SPECS:
                columns = ", ".join(spec.columns)
                rows = await connection.fetch(f"SELECT id, {columns} FROM {spec.table}")
                old_fernet, _ = pairs[spec.key_kind]
                for row in rows:
                    for column in spec.columns:
                        token = row[column]
                        if token is None:
                            continue
                        plaintext = old_fernet.decrypt(token.encode("ascii"))
                        prepared.append((spec, row["id"], column, plaintext))

            for spec, row_id, column, plaintext in prepared:
                _, new_fernet = pairs[spec.key_kind]
                encrypted = new_fernet.encrypt(plaintext).decode("ascii")
                await connection.execute(
                    f"UPDATE {spec.table} SET {column} = $1 WHERE id = $2",
                    encrypted,
                    row_id,
                )
                counts[f"{spec.table}.{column}"] = (
                    counts.get(f"{spec.table}.{column}", 0) + 1
                )

            # Read back and decrypt with the new keys before COMMIT.
            for spec in SPECS:
                columns = ", ".join(spec.columns)
                rows = await connection.fetch(f"SELECT {columns} FROM {spec.table}")
                _, new_fernet = pairs[spec.key_kind]
                for row in rows:
                    for column in spec.columns:
                        token = row[column]
                        if token is not None:
                            new_fernet.decrypt(token.encode("ascii"))
    finally:
        await connection.close()
    return counts


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--verify-only",
        action="store_true",
        help="decrypt every stored token with the active container keys without writing",
    )
    args = parser.parse_args()
    if args.verify_only:
        counts = asyncio.run(verify_active())
        print(json.dumps({"status": "ok", "verified": counts}, sort_keys=True))
    else:
        counts = asyncio.run(rotate())
        print(json.dumps({"status": "ok", "rotated": counts}, sort_keys=True))


if __name__ == "__main__":
    main()

"""Симметричное шифрование секретов «в покое» (GitHub OAuth-токены в БД).

Ключ Fernet выводится из `jwt_secret` (SHA-256 → urlsafe-base64 32 байта), поэтому
отдельный секрет заводить не нужно. Побочный эффект: ротация `jwt_secret`
инвалидирует сохранённые токены — пользователь просто переподключит GitHub.
"""

from __future__ import annotations

import base64
import hashlib
from functools import lru_cache

from cryptography.fernet import Fernet

from omnia_api.core.config import get_settings


@lru_cache(maxsize=1)
def _fernet() -> Fernet:
    secret = get_settings().jwt_secret.get_secret_value().encode("utf-8")
    key = base64.urlsafe_b64encode(hashlib.sha256(secret).digest())
    return Fernet(key)


def encrypt_secret(plain: str) -> str:
    """Зашифровать секрет для хранения в БД (возвращает ASCII-токен)."""
    return _fernet().encrypt(plain.encode("utf-8")).decode("ascii")


def decrypt_secret(token: str) -> str:
    """Расшифровать секрет, сохранённый `encrypt_secret`."""
    return _fernet().decrypt(token.encode("ascii")).decode("utf-8")

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


@lru_cache(maxsize=1)
def _fernet_strong() -> Fernet:
    """Fernet для «тяжёлых» секретов (SSH-креды чужих VPS, ПДн для доменов).

    Использует отдельный `secrets_encryption_key` из env, чтобы ротация
    `jwt_secret` (которая логаутит пользователей) НЕ обнуляла доступ к чужим
    машинам и не делала нерасшифровываемыми паспортные данные. Если ключ не
    задан (dev), откатывается на jwt-производный ключ — работает из коробки;
    в проде ключ обязателен (см. config.secrets_encryption_key).
    """
    raw = get_settings().secrets_encryption_key
    if raw is not None:
        key = raw.get_secret_value().encode("utf-8")
        # Принимаем как готовый Fernet-ключ (44-символьный urlsafe-base64),
        # так и произвольную строку — тогда выводим ключ через SHA-256.
        try:
            Fernet(key)
            return Fernet(key)
        except (ValueError, TypeError):
            derived = base64.urlsafe_b64encode(hashlib.sha256(key).digest())
            return Fernet(derived)
    return _fernet()


def encrypt_strong(plain: str) -> str:
    """Зашифровать «тяжёлый» секрет отдельным ключом (SSH-креды, ПДн)."""
    return _fernet_strong().encrypt(plain.encode("utf-8")).decode("ascii")


def decrypt_strong(token: str) -> str:
    """Расшифровать секрет, сохранённый `encrypt_strong`."""
    return _fernet_strong().decrypt(token.encode("ascii")).decode("utf-8")

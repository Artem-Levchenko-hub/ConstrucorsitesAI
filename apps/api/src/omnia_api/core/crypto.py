"""Symmetric encryption for secrets stored at rest (GitHub OAuth tokens).

Uses Fernet (AES-128-CBC + HMAC) keyed off `GITHUB_TOKEN_ENC_KEY`. The key is a
url-safe base64 32-byte value produced by `Fernet.generate_key()`. Storing the
ciphertext (not the raw token) means a database leak does not, on its own, hand
an attacker push access to users' repositories.
"""

from functools import lru_cache

from cryptography.fernet import Fernet, InvalidToken

from omnia_api.core.config import get_settings


class TokenEncryptionError(RuntimeError):
    """Raised when token encryption/decryption cannot be performed."""


@lru_cache(maxsize=1)
def _fernet() -> Fernet:
    key = get_settings().github_token_enc_key
    if key is None:
        raise TokenEncryptionError("GITHUB_TOKEN_ENC_KEY is not configured")
    return Fernet(key.get_secret_value().encode("utf-8"))


def encrypt_token(plaintext: str) -> str:
    return _fernet().encrypt(plaintext.encode("utf-8")).decode("utf-8")


def decrypt_token(ciphertext: str) -> str:
    try:
        return _fernet().decrypt(ciphertext.encode("utf-8")).decode("utf-8")
    except InvalidToken as exc:
        raise TokenEncryptionError("stored token could not be decrypted") from exc

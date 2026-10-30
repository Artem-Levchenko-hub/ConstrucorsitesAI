"""A rollback reader accepts future hashes while retaining the legacy writer."""

import pytest
from passlib.context import CryptContext
from passlib.exc import UnknownHashError

from yleum_api.core.security import consume_dummy_verify, hash_password, verify_password


@pytest.fixture(scope="module")
def bcrypt_sha256_context() -> CryptContext:
    return CryptContext(schemes=["bcrypt_sha256"], bcrypt_sha256__rounds=12)


@pytest.fixture(scope="module")
def legacy_context() -> CryptContext:
    return CryptContext(schemes=["bcrypt"], bcrypt__rounds=12)


@pytest.mark.parametrize(
    ("password", "wrong_password"),
    [
        pytest.param("secret123", "secret124", id="short"),
        pytest.param("a" * 72 + "1A", "a" * 72 + "1B", id="ascii-suffix"),
        pytest.param("я" * 36 + "1A", "я" * 36 + "1B", id="unicode-suffix"),
        pytest.param("a" * 71 + "я1", "a" * 71 + "ю1", id="split-utf8-character"),
        pytest.param("🔐" * 127 + "1", "🔐" * 127 + "2", id="maximum-unicode-length"),
    ],
)
async def test_reader_accepts_bcrypt_sha256_v2_without_truncating_password(
    bcrypt_sha256_context: CryptContext, password: str, wrong_password: str,
) -> None:
    password_hash = bcrypt_sha256_context.hash(password)
    assert password_hash.startswith("$bcrypt-sha256$v=2,t=2b,r=12$")

    assert await verify_password(password, password_hash) is True
    assert await verify_password(wrong_password, password_hash) is False


@pytest.mark.parametrize(
    ("password", "equivalent_password"),
    [
        pytest.param("a" * 71 + "1", "a" * 71 + "1extra", id="exactly-72-bytes"),
        pytest.param("a" * 72 + "1A", "a" * 72 + "1B", id="ascii-suffix"),
        pytest.param("я" * 36 + "1A", "я" * 36 + "1B", id="unicode-suffix"),
        pytest.param("a" * 71 + "я1", "a" * 71 + "ю1", id="split-utf8-character"),
    ],
)
async def test_reader_preserves_legacy_bcrypt_password_equivalence(
    legacy_context: CryptContext, password: str, equivalent_password: str,
) -> None:
    password_hash = legacy_context.hash(password)

    assert await verify_password(password, password_hash) is True
    assert await verify_password(equivalent_password, password_hash) is True
    assert await verify_password("wrong-password1", password_hash) is False


@pytest.mark.parametrize("password", ["secret123", "a" * 72 + "1A", "я" * 36 + "1A"])
async def test_writer_remains_readable_by_bcrypt_only_rollback(
    legacy_context: CryptContext, password: str,
) -> None:
    password_hash = await hash_password(password)

    assert password_hash.startswith("$2b$12$")
    assert legacy_context.verify(password, password_hash) is True
    assert await verify_password(password, password_hash) is True


@pytest.mark.parametrize("password_hash", ["", "not-a-password-hash"])
async def test_reader_still_raises_for_unknown_hash(password_hash: str) -> None:
    with pytest.raises(UnknownHashError):
        await verify_password("secret123", password_hash)


async def test_reader_still_raises_for_malformed_bcrypt_hash() -> None:
    with pytest.raises(ValueError):
        await verify_password("secret123", "$2b$12$too-short")


async def test_dummy_verification_remains_usable() -> None:
    assert await consume_dummy_verify() is None
    assert await consume_dummy_verify() is None

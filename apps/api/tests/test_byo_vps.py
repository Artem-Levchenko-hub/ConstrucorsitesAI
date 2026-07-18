"""Юнит-тесты BYO-VPS + свой домен (без БД): шифрование секретов, генерация
SSH-пары, валидация домена. Проверяют security-критичную логику, не требуя
Postgres."""

from __future__ import annotations

import re

from omnia_api.core.crypto import (
    decrypt_secret,
    decrypt_strong,
    encrypt_secret,
    encrypt_strong,
)
from omnia_api.core.ssh_keys import generate_ssh_keypair
from omnia_api.routers.domains import _HOST_RE


def test_encrypt_strong_roundtrip() -> None:
    secret = "-----BEGIN OPENSSH PRIVATE KEY-----\nabc\n-----END-----"
    token = encrypt_strong(secret)
    assert token != secret
    assert decrypt_strong(token) == secret


def test_strong_and_weak_are_distinct_ciphertexts() -> None:
    # Разные механизмы шифрования → разный шифротекст для одной строки
    # (у strong в dev тот же ключ, но токены всё равно не совпадают из-за IV).
    plain = "super-secret-password"
    assert encrypt_strong(plain) != encrypt_secret(plain)
    # но каждый расшифровывается своим механизмом
    assert decrypt_strong(encrypt_strong(plain)) == plain
    assert decrypt_secret(encrypt_secret(plain)) == plain


def test_generate_ssh_keypair_shape() -> None:
    private_pem, public_line = generate_ssh_keypair(comment="omnia-test")
    assert private_pem.startswith("-----BEGIN OPENSSH PRIVATE KEY-----")
    assert private_pem.rstrip().endswith("-----END OPENSSH PRIVATE KEY-----")
    assert public_line.startswith("ssh-ed25519 ")
    assert public_line.endswith(" omnia-test")


def test_generate_ssh_keypair_unique() -> None:
    a, _ = generate_ssh_keypair()
    b, _ = generate_ssh_keypair()
    assert a != b  # каждая пара уникальна


VALID_HOSTS = ["shop.example.ru", "a.b.example.com", "my-site.рф".encode("idna").decode()]
INVALID_HOSTS = [
    "example",            # нет TLD
    "-bad.example.com",   # метка начинается с дефиса
    "http://example.ru",  # со схемой
    "example.ru/path",    # с путём
    "a..b.com",           # пустая метка
    "спам.ru",            # не-ASCII (домен должен прийти в punycode)
]


def test_domain_host_regex_valid() -> None:
    for host in VALID_HOSTS:
        assert _HOST_RE.match(host), f"должен быть валиден: {host}"


def test_domain_host_regex_invalid() -> None:
    for host in INVALID_HOSTS:
        assert not _HOST_RE.match(host), f"должен быть отклонён: {host}"


def test_domain_regex_is_case_insensitive() -> None:
    assert _HOST_RE.match("Shop.Example.RU")
    assert re.IGNORECASE  # sanity

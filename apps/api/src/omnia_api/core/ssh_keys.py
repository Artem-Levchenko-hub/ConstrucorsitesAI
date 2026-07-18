"""Генерация SSH-пары для BYO-VPS (режим авторизации 'key').

Когда пользователь выбирает подключение по ключу и не приносит свой приватный
ключ, мы генерим пару ed25519: приватный шифруем и храним у себя, публичный
показываем пользователю — он добавляет его в `~/.ssh/authorized_keys` на своём
сервере одной командой. Так пароль от сервера у нас не оседает.
"""

from __future__ import annotations

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey


def generate_ssh_keypair(comment: str = "omnia-deploy") -> tuple[str, str]:
    """Вернуть (private_openssh_pem, public_authorized_keys_line)."""
    key = Ed25519PrivateKey.generate()
    private_pem = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.OpenSSH,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode("ascii")
    public_line = key.public_key().public_bytes(
        encoding=serialization.Encoding.OpenSSH,
        format=serialization.PublicFormat.OpenSSH,
    ).decode("ascii")
    return private_pem, f"{public_line} {comment}"

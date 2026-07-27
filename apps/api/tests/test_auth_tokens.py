from types import SimpleNamespace
from uuid import uuid4

from pydantic import SecretStr

from omnia_api.core import security


def _settings() -> SimpleNamespace:
    return SimpleNamespace(
        jwt_secret=SecretStr("local-test-secret-with-at-least-32-bytes"),
        jwt_algorithm="HS256",
        jwt_ttl_days=7,
    )


def test_access_token_round_trip(monkeypatch) -> None:
    monkeypatch.setattr(security, "get_settings", _settings)
    user_id = uuid4()

    token = security.create_access_token(user_id)

    assert security.decode_access_token(token) == user_id


def test_invalid_access_token_is_rejected(monkeypatch) -> None:
    monkeypatch.setattr(security, "get_settings", _settings)

    assert security.decode_access_token("not-a-jwt") is None

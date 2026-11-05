from types import SimpleNamespace
from uuid import uuid4

from pydantic import SecretStr

from yleum_api.core import security


def _settings() -> SimpleNamespace:
    return SimpleNamespace(
        jwt_secret=SecretStr("local-test-secret-with-at-least-32-bytes"),
        jwt_algorithm="HS256",
        jwt_ttl_days=7,
    )


def test_access_token_round_trip(monkeypatch) -> None:
    monkeypatch.setattr(security, "get_settings", _settings)
    user_id = uuid4()

    session_id = uuid4()
    token = security.create_access_token(user_id, session_id=session_id, session_version=3)

    assert security.decode_access_token(token) == user_id
    assert security.decode_access_claims(token) == security.AccessClaims(user_id, session_id, 3)


def test_sessionless_access_token_does_not_authorize(monkeypatch) -> None:
    monkeypatch.setattr(security, "get_settings", _settings)

    token = security.create_access_token(uuid4())

    assert security.decode_access_claims(token) is None
    assert security.decode_access_token(token) is None


def test_invalid_access_token_is_rejected(monkeypatch) -> None:
    monkeypatch.setattr(security, "get_settings", _settings)

    assert security.decode_access_token("not-a-jwt") is None

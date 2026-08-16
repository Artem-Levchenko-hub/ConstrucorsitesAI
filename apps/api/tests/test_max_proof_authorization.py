from types import SimpleNamespace
from uuid import uuid4

import pytest
from pydantic import SecretStr
from starlette.responses import Response

from omnia_api.core.errors import ApiError
from omnia_api.routers import integration_runtime
from omnia_api.services import max_proof_authorization as proof_auth


def _settings() -> SimpleNamespace:
    return SimpleNamespace(jwt_secret=SecretStr("test-max-proof-secret-at-least-32-bytes"))


def test_proof_authorization_binds_project_revision_capability_and_key(monkeypatch) -> None:
    monkeypatch.setattr(proof_auth, "get_settings", _settings)
    monkeypatch.setattr(proof_auth.time, "time", lambda: 1_900_000_000)
    project_id = uuid4()
    proof_key = "a" * 64
    token = proof_auth.issue_max_proof_authorization(
        project_id,
        proof_key=proof_key,
        source_digest="b" * 64,
        capability_id="feature_1",
    )

    claims = proof_auth.validate_max_proof_authorization(
        token,
        project_id,
        proof_key=proof_key,
    )

    assert claims is not None
    assert claims.project_id == project_id
    assert claims.proof_key == proof_key
    assert claims.source_digest == "b" * 64
    assert claims.capability_id == "feature_1"


def test_proof_authorization_rejects_wrong_project_key_and_tampering(monkeypatch) -> None:
    monkeypatch.setattr(proof_auth, "get_settings", _settings)
    monkeypatch.setattr(proof_auth.time, "time", lambda: 1_900_000_000)
    project_id = uuid4()
    token = proof_auth.issue_max_proof_authorization(
        project_id,
        proof_key="a" * 64,
        source_digest="b" * 64,
        capability_id="primary_action",
    )

    assert (
        proof_auth.validate_max_proof_authorization(token, uuid4(), proof_key="a" * 64) is None
    )
    assert (
        proof_auth.validate_max_proof_authorization(token, project_id, proof_key="c" * 64)
        is None
    )
    replacement = "A" if token[-1] != "A" else "B"
    assert (
        proof_auth.validate_max_proof_authorization(
            token[:-1] + replacement,
            project_id,
            proof_key="a" * 64,
        )
        is None
    )


def test_proof_authorization_expires(monkeypatch) -> None:
    monkeypatch.setattr(proof_auth, "get_settings", _settings)
    now = 1_900_000_000
    monkeypatch.setattr(proof_auth.time, "time", lambda: now)
    project_id = uuid4()
    token = proof_auth.issue_max_proof_authorization(
        project_id,
        proof_key="a" * 64,
        source_digest="b" * 64,
        capability_id="__bootstrap__",
        ttl_seconds=30,
    )
    monkeypatch.setattr(proof_auth.time, "time", lambda: now + 31)

    assert (
        proof_auth.validate_max_proof_authorization(token, project_id, proof_key="a" * 64)
        is None
    )


async def test_runtime_status_binds_only_valid_signed_proof(monkeypatch) -> None:
    monkeypatch.setattr(proof_auth, "get_settings", _settings)
    monkeypatch.setattr(proof_auth.time, "time", lambda: 1_900_000_000)
    project_id = uuid4()
    proof_key = "a" * 64
    token = proof_auth.issue_max_proof_authorization(
        project_id,
        proof_key=proof_key,
        source_digest="b" * 64,
        capability_id="feature_1",
    )

    async def fake_context(*_args: object, **_kwargs: object) -> integration_runtime.RuntimeContext:
        return integration_runtime.RuntimeContext(project_id, max_user_id=0, is_preview=True)

    async def fake_connections(*_args: object, **_kwargs: object) -> dict[str, object]:
        return {}

    monkeypatch.setattr(integration_runtime, "_runtime_context", fake_context)
    monkeypatch.setattr(integration_runtime, "_connections", fake_connections)
    response = Response()

    result = await integration_runtime.runtime_integration_status(
        project_id,
        object(),  # type: ignore[arg-type]
        response,
        x_omnia_max_preview_capability="signed-preview",
        x_omnia_proof_key=proof_key,
        x_omnia_proof_authorization=token,
    )

    assert result.providers == []
    assert response.headers["X-Omnia-Proof-Key-Bound"] == proof_key
    assert response.headers["Cache-Control"] == "no-store"


async def test_runtime_status_rejects_unsigned_proof_even_in_preview(monkeypatch) -> None:
    async def fake_context(*_args: object, **_kwargs: object) -> integration_runtime.RuntimeContext:
        return integration_runtime.RuntimeContext(uuid4(), max_user_id=0, is_preview=True)

    monkeypatch.setattr(integration_runtime, "_runtime_context", fake_context)

    with pytest.raises(ApiError) as raised:
        await integration_runtime.runtime_integration_status(
            uuid4(),
            object(),  # type: ignore[arg-type]
            Response(),
            x_omnia_max_preview_capability="signed-preview",
            x_omnia_proof_key="a" * 64,
            x_omnia_proof_authorization="v1.not-signed",
        )

    assert raised.value.code == "max_proof_authorization_invalid"

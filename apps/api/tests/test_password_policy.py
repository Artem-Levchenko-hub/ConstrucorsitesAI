"""New-password admission is shared; existing credentials keep authenticating."""

import json
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from pydantic import ValidationError

from yleum_api.core.errors import validation_error_handler
from yleum_api.core.password_policy import (
    PASSWORD_MAX_LENGTH,
    PASSWORD_MIN_LENGTH,
    new_password_error,
)
from yleum_api.schemas.account_credentials import EmailChange, PasswordChange
from yleum_api.schemas.user import PasswordResetConsume, UserCreate, UserLogin

CASES = json.loads(
    (Path(__file__).parent / "fixtures/password_policy_cases.json").read_text(encoding="utf-8")
)


@pytest.mark.parametrize("case", CASES, ids=lambda case: case["id"])
def test_all_new_password_entry_points_agree(case):
    password = case["password"]
    assert (new_password_error(password) is None) is case["accepted"]
    requests = (
        (UserCreate, {"email": "owner@example.com", "password": password}, "password"),
        (PasswordResetConsume, {"token": "x" * 64, "password": password}, "password"),
        (PasswordChange, {"current_password": "old", "new_password": password}, "new_password"),
    )
    for model, payload, field in requests:
        if case["accepted"]:
            assert getattr(model(**payload), field) == password
        else:
            with pytest.raises(ValidationError):
                model(**payload)


@pytest.mark.parametrize("password", ["a", "secret123", "alllettersnodigit", "🔐" * 128])
def test_existing_passwords_are_not_subject_to_new_policy(password):
    assert UserLogin(email="owner@example.com", password=password).password == password
    change = PasswordChange(current_password=password, new_password="moss river blue")
    assert change.current_password == password
    email = EmailChange(current_password=password, new_email="next@example.com")
    assert email.current_password == password


def test_new_password_contract_documents_unicode_length_bounds():
    for model, field in ((UserCreate, "password"), (PasswordResetConsume, "password"),
                         (PasswordChange, "new_password")):
        schema = model.model_json_schema()["properties"][field]
        assert schema["minLength"] == PASSWORD_MIN_LENGTH == 15
        assert schema["maxLength"] == PASSWORD_MAX_LENGTH == 128


async def test_password_validation_response_does_not_echo_rejected_secrets():
    app = FastAPI()
    app.add_exception_handler(RequestValidationError, validation_error_handler)

    @app.post("/api/auth/register")
    async def register(payload: UserCreate):
        return {"accepted": True}

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="https://qa.invalid",
    ) as client:
        response = await client.post(
            "/api/auth/register",
            json={"email": "owner@example.com", "password": "passwordpassword"},
        )
    assert response.status_code == 422
    assert "passwordpassword" not in response.text
    errors = response.json()["error"]["details"]["errors"]
    assert errors == [{"type": "password_policy", "loc": ["body", "password"]}]

from typing import Literal

from pydantic import BaseModel, EmailStr, Field

from yleum_api.core.password_policy import NewPassword


class PasswordChange(BaseModel):
    current_password: str = Field(min_length=1, max_length=128)
    new_password: NewPassword


class EmailChange(BaseModel):
    current_password: str = Field(min_length=1, max_length=128)
    new_email: EmailStr


class EmailChangeConfirm(BaseModel):
    token: str = Field(min_length=32, max_length=512)


class CredentialInfo(BaseModel):
    has_password: bool


class PasswordChanged(BaseModel):
    ok: Literal[True] = True
    reauthenticate: Literal[True] = True


class ConfirmationSent(BaseModel):
    status: Literal["confirmation_sent"] = "confirmation_sent"


class EmailConfirmed(BaseModel):
    status: Literal["pending", "completed"]
    reauthenticate: bool

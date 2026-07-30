import re
from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

# Bounded set of viral-funnel signup sources (V4.2b). A closed enum is the
# teeth: an attacker (or a buggy client) cannot stuff arbitrary strings into the
# provenance column — anything outside this set is a 422. "share_link" is the
# viral return-edge (stranger came in from a /p/<slug> "Сделай свой" CTA),
# "remix" reserves the fork-claim edge, "direct" tags a deliberate non-viral
# signup. Omitting the field entirely (NULL) is the organic/blank case.
SignupSource = Literal["share_link", "remix", "direct"]
RegistrationProduct = Literal["general", "max"]


class UserCreate(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)
    # Viral-funnel provenance (V4.2b return-edge), both optional → organic signup
    # leaves them NULL. Validated: `source` against the closed SignupSource enum
    # and `referrer_project_id` as a real UUID, so junk provenance is rejected
    # with a 422 rather than silently recorded.
    source: SignupSource | None = None
    referrer_project_id: UUID | None = None
    product: RegistrationProduct = "general"
    terms_accepted: bool = False
    privacy_accepted: bool = False
    personal_data_accepted: bool = False
    marketing_accepted: bool = False
    document_version: str | None = Field(default=None, max_length=32)

    @field_validator("password")
    @classmethod
    def must_contain_digit(cls, v: str) -> str:
        if not re.search(r"\d", v):
            raise ValueError("password must contain at least one digit")
        return v


class UserLogin(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=128)


class UserPublic(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    # Nullable since V4.1a: anonymous principals have no email.
    email: EmailStr | None = None
    is_anon: bool = False
    created_at: datetime
    last_login_at: datetime | None = None
    email_verified_at: datetime | None = None
    status: str = "active"


class EmailTokenRequest(BaseModel):
    email: EmailStr


class EmailTokenConsume(BaseModel):
    token: str = Field(min_length=32, max_length=512)


class PasswordResetConsume(BaseModel):
    token: str = Field(min_length=32, max_length=512)
    password: str = Field(min_length=8, max_length=128)

    @field_validator("password")
    @classmethod
    def must_contain_digit(cls, v: str) -> str:
        if not re.search(r"\d", v):
            raise ValueError("password must contain at least one digit")
        return v


class SessionPublic(BaseModel):
    id: UUID
    current: bool
    user_agent: str | None
    ip_address: str | None
    created_at: datetime
    last_seen_at: datetime

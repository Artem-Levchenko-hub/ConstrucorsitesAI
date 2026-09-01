"""Per-attempt auth providers for runner -> gateway native messages."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from typing import Any, Protocol
from uuid import UUID, uuid4


@dataclass(frozen=True, slots=True)
class MessagesAttemptAuth:
    message_id: str
    project_id: str
    run_id: str
    session_id: str
    workspace_id: str
    fencing_epoch: int
    cancel_epoch: int
    headers: Mapping[str, str]


MessagesAuthFactory = Callable[[int], Awaitable[MessagesAttemptAuth]]


class JWTSigner(Protocol):
    def sign(self, claims: Mapping[str, Any]) -> str: ...


class RunnerIdentityLike(Protocol):
    project_id: UUID
    run_id: UUID
    session_id: UUID
    workspace_id: UUID
    fencing_epoch: int
    cancel_epoch: int


def _now_epoch_seconds() -> int:
    return int(time.time())


def _b64url_encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


@dataclass(frozen=True, slots=True)
class HS256JWTSigner:
    secret: str

    def __post_init__(self) -> None:
        if not self.secret.strip():
            raise ValueError("secret is required")

    def sign(self, claims: Mapping[str, Any]) -> str:
        header = {"alg": "HS256", "typ": "JWT"}
        header_segment = _b64url_encode(
            json.dumps(header, separators=(",", ":"), sort_keys=True).encode("utf-8")
        )
        payload_segment = _b64url_encode(
            json.dumps(dict(claims), separators=(",", ":"), sort_keys=True).encode("utf-8")
        )
        signing_input = f"{header_segment}.{payload_segment}".encode("ascii")
        signature = _b64url_encode(
            hmac.new(
                self.secret.encode("utf-8"),
                signing_input,
                hashlib.sha256,
            ).digest()
        )
        return f"{header_segment}.{payload_segment}.{signature}"


@dataclass(frozen=True, slots=True)
class ProjectCellJWTMessagesAuth:
    signer: JWTSigner
    issuer: str
    audience: str
    ttl_seconds: int = 300
    extra_headers: Mapping[str, str] = field(default_factory=dict)
    clock: Callable[[], int] = _now_epoch_seconds
    jti_factory: Callable[[], UUID] = uuid4
    not_before_skew_seconds: int = 1

    def __post_init__(self) -> None:
        if not self.issuer.strip():
            raise ValueError("issuer is required")
        if not self.audience.strip():
            raise ValueError("audience is required")
        if self.ttl_seconds <= 0:
            raise ValueError("ttl_seconds must be positive")
        if self.not_before_skew_seconds < 0:
            raise ValueError("not_before_skew_seconds must be non-negative")

    def headers(self, identity: RunnerIdentityLike) -> Mapping[str, str] | None:
        _ = identity
        headers = self._extra_headers_without_authorization()
        return headers or None

    def auth_factory(self, identity: RunnerIdentityLike) -> MessagesAuthFactory:
        async def build(attempt: int) -> MessagesAttemptAuth:
            _ = attempt
            issued_at = int(self.clock())
            message_id = str(self.jti_factory())
            extra_headers = self._extra_headers_without_authorization()
            claims = {
                "iss": self.issuer,
                "aud": self.audience,
                "jti": message_id,
                "project_id": str(identity.project_id),
                "run_id": str(identity.run_id),
                "session_id": str(identity.session_id),
                "workspace_id": str(identity.workspace_id),
                "fencing_epoch": identity.fencing_epoch,
                "cancel_epoch": identity.cancel_epoch,
                "nbf": issued_at - self.not_before_skew_seconds,
                "exp": issued_at + self.ttl_seconds - self.not_before_skew_seconds,
            }
            token = self.signer.sign(claims)
            return MessagesAttemptAuth(
                message_id=message_id,
                project_id=str(identity.project_id),
                run_id=str(identity.run_id),
                session_id=str(identity.session_id),
                workspace_id=str(identity.workspace_id),
                fencing_epoch=identity.fencing_epoch,
                cancel_epoch=identity.cancel_epoch,
                headers={
                    **extra_headers,
                    "Authorization": f"Bearer {token}",
                },
            )

        return build

    def _extra_headers_without_authorization(self) -> dict[str, str]:
        return {
            key: value
            for key, value in self.extra_headers.items()
            if key.strip().lower() != "authorization"
        }

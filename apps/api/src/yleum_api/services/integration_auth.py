"""Verify short-lived assertions minted only by the trusted MAX core proxy."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
import time
from uuid import UUID

_DOMAIN = b"omnia:integration-assertion:v1"


def verify_integration_assertion(
    assertion: str,
    bot_token: str,
    *,
    project_id: UUID,
    method: str,
    path: str,
    body: bytes,
) -> int:
    return _verify_assertion(
        assertion, bot_token, project_id=project_id, method=method, path=path,
        body=body, purpose=_DOMAIN,
    )


def verify_business_analytics_assertion(
    assertion: str, bot_token: str, *, project_id: UUID, method: str, path: str, body: bytes,
) -> int:
    if method != "POST" or path != f"/api/runtime/projects/{project_id}/analytics":
        raise ValueError("invalid business analytics destination")
    return _verify_assertion(
        assertion, bot_token, project_id=project_id, method=method, path=path,
        body=body, purpose=b"yleum:business-analytics:v1",
    )


def _verify_assertion(
    assertion: str, bot_token: str, *, project_id: UUID, method: str, path: str,
    body: bytes, purpose: bytes,
) -> int:
    """Authenticate both the MAX identity and the exact upstream operation."""
    if len(assertion) > 4096:
        raise ValueError("invalid integration assertion")
    pieces = assertion.split(".")
    if (
        len(pieces) != 3
        or pieces[0] != "v1"
        or not re.fullmatch(r"[A-Za-z0-9_-]+", pieces[1])
        or not re.fullmatch(r"[0-9a-f]{64}", pieces[2])
    ):
        raise ValueError("invalid integration assertion")
    encoded, provided = pieces[1:]
    key = hmac.digest(bot_token.encode(), purpose, "sha256")
    expected = hmac.new(key, f"v1.{encoded}".encode(), "sha256").hexdigest()
    if not hmac.compare_digest(provided, expected):
        raise ValueError("invalid integration assertion")
    claims = json.loads(base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4)))
    if not isinstance(claims, dict):
        raise ValueError("invalid integration assertion")
    now = int(time.time())
    issued, expires = claims.get("iat"), claims.get("exp")
    user = claims.get("max_user_id")
    if (
        type(issued) is not int
        or type(expires) is not int
        or not 0 < expires - issued <= 60
        or issued > now + 5
        or expires <= now
        or claims.get("project_id") != str(project_id)
        or claims.get("method") != method
        or claims.get("path") != path
        or claims.get("body_sha256") != hashlib.sha256(body).hexdigest()
        or not isinstance(user, str)
        or not re.fullmatch(r"[1-9][0-9]{0,19}", user)
    ):
        raise ValueError("invalid integration assertion")
    return int(user)

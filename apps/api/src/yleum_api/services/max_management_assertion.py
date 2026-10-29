"""Purpose-separated server owner assertions. These never reach browsers."""

import base64
import hashlib
import hmac
import json
import re
import time
from uuid import UUID

OWNER_PATH = "/api/omnia/owner"
OWNER_HEADER = "X-Yleum-Owner-Assertion"


def derive_owner_key(bot_token: str) -> bytes:
    if not bot_token:
        raise ValueError("owner credential unavailable")
    return hmac.digest(bot_token.encode("utf-8"), b"yleum:owner-management:v1", "sha256")


def sign_owner_assertion(
    bot_token: str,
    project_id: UUID,
    owner_id: UUID,
    publication_id: UUID,
    contract_hash: str,
    body: bytes,
    *,
    now: int | None = None,
) -> str:
    issued = int(time.time()) if now is None else now
    if type(issued) is not int or not re.fullmatch(r"[0-9a-f]{64}", contract_hash):
        raise ValueError("invalid assertion identity")
    claims = {
        "project_id": str(project_id),
        "owner_id": str(owner_id),
        "publication_id": str(publication_id),
        "contract_hash": contract_hash,
        "method": "POST",
        "path": OWNER_PATH,
        "body_sha256": hashlib.sha256(body).hexdigest(),
        "iat": issued,
        "exp": issued + 60,
    }
    encoded = (
        base64.urlsafe_b64encode(
            json.dumps(
                claims,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        )
        .decode("ascii")
        .rstrip("=")
    )
    signed = f"1.{encoded}"
    signature = (
        base64.urlsafe_b64encode(
            hmac.digest(
                derive_owner_key(bot_token),
                signed.encode("ascii"),
                "sha256",
            )
        )
        .decode("ascii")
        .rstrip("=")
    )
    return f"{signed}.{signature}"

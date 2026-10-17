"""Version1 ingress identity protocol, mirrored locally by the gateway.

Identity is a binding, never an authentication credential. No request text is
retained here. Hash the ingress wire before defaults or provider conversion.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import dataclass
from typing import Any
from uuid import UUID

_ROUTES = {"/v1/chat/completions", "/v1/messages", "/v1/project-cell/messages"}
_SEMANTIC = {
    "model",
    "messages",
    "system",
    "tools",
    "tool_choice",
    "temperature",
    "stream",
    "max_tokens",
    "thinking",
    "output_config",
    "stop_sequences",
    "top_p",
    "top_k",
}
_TRANSPORT = {"metadata", "user"}
_HASH = re.compile(r"[0-9a-f]{64}")


@dataclass(frozen=True, slots=True)
class GenerationProviderIdentity:
    run_id: UUID
    project_id: UUID
    user_id: UUID
    attempt_id: UUID
    prompt_hash: str
    request_sha256: str
    version: int = 1

    def __post_init__(self) -> None:
        if (
            type(self.version) is not int
            or self.version != 1
            or any(
                not isinstance(value, UUID)
                for value in (self.run_id, self.project_id, self.user_id, self.attempt_id)
            )
            or any(
                type(value) is not str or _HASH.fullmatch(value) is None
                for value in (self.prompt_hash, self.request_sha256)
            )
        ):
            raise ValueError("invalid generation provider identity")

    def to_wire(self) -> dict[str, object]:
        return {
            "version": self.version,
            "run_id": str(self.run_id),
            "project_id": str(self.project_id),
            "user_id": str(self.user_id),
            "attempt_id": str(self.attempt_id),
            "prompt_hash": self.prompt_hash,
            "request_sha256": self.request_sha256,
        }

    @classmethod
    def from_wire(cls, value: object) -> GenerationProviderIdentity:
        keys = {
            "version",
            "run_id",
            "project_id",
            "user_id",
            "attempt_id",
            "prompt_hash",
            "request_sha256",
        }
        if not isinstance(value, dict) or set(value) != keys:
            raise ValueError("invalid generation provider identity")
        try:
            ids = {
                key: UUID(value[key])
                for key in ("run_id", "project_id", "user_id", "attempt_id")
                if type(value[key]) is str
            }
            if len(ids) != 4 or any(str(parsed) != value[key] for key, parsed in ids.items()):
                raise ValueError
            return cls(
                **ids,
                prompt_hash=value["prompt_hash"],
                request_sha256=value["request_sha256"],
                version=value["version"],
            )
        except (ValueError, TypeError, AttributeError):
            raise ValueError("invalid generation provider identity") from None


def _json_value(value: object, depth: int = 0) -> None:
    if depth > 64:
        raise ValueError("generation request nesting exceeded")
    if value is None or type(value) in {str, bool, int}:
        return
    if type(value) is float and math.isfinite(value):
        return
    if type(value) is list:
        for item in value:
            _json_value(item, depth + 1)
        return
    if type(value) is dict and all(type(key) is str for key in value):
        for item in value.values():
            _json_value(item, depth + 1)
        return
    raise ValueError("noncanonical generation request")


def decode_request(raw: bytes) -> dict[str, Any]:
    def unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate generation request key")
            result[key] = value
        return result

    try:
        value = json.loads(raw, object_pairs_hook=unique)
        _json_value(value)
        if not isinstance(value, dict):
            raise ValueError
        return value
    except (ValueError, TypeError, UnicodeError, RecursionError):
        raise ValueError("invalid generation request JSON") from None


def canonical_request(route: str, body: dict[str, Any]) -> bytes:
    if route not in _ROUTES or set(body) - (_SEMANTIC | _TRANSPORT):
        raise ValueError("unsupported generation request fields")
    if (
        not isinstance(body.get("model"), str)
        or not body["model"]
        or (not isinstance(body.get("messages"), list))
    ):
        raise ValueError("invalid generation request")
    semantic = {key: value for key, value in body.items() if key in _SEMANTIC}
    _json_value(semantic)
    try:
        return json.dumps(
            {"version": 1, "route": route, "body": semantic},
            sort_keys=True,
            ensure_ascii=False,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (ValueError, UnicodeError):
        raise ValueError("invalid generation request encoding") from None


def request_digest(route: str, body: dict[str, Any]) -> str:
    return hashlib.sha256(canonical_request(route, body)).hexdigest()

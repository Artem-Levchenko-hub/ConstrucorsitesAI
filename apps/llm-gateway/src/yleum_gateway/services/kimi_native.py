"""Authenticated provider-specific assistant history for native Kimi turns."""

from __future__ import annotations

import base64
import hmac
import json
import os
from dataclasses import dataclass, field
from typing import Any, Literal

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from yleum_gateway.services.generation_identity import GenerationProviderIdentity
from yleum_gateway.services.native_request_validation import NativeRequestValidationError

KIMI_MODEL = "kimi-k3.1-pro"
KIMI_LLMGW_MODEL = "moonshotai/kimi-k3.1-pro"
CARRIER_TYPE = "provider_reasoning"

_DOMAIN = b"yleum-native-kimi-history-v1"
_MAX_BYTES = 512 * 1024
_NONCE_BYTES = 12
_TAG_BYTES = 16
_ROUTES = {"/v1/messages", "/v1/project-cell/messages"}
_MODELS = {"aitunnel": KIMI_MODEL, "llmgw": KIMI_LLMGW_MODEL}
_REASONING_FIELDS = {
    "aitunnel": ("reasoning", "reasoning_details"),
    "llmgw": ("reasoning_content",),
}
_FINISH_REASONS = {"stop", "tool_calls", "length", "content_filter"}
_MALFORMED = "Invalid Kimi provider response"


def _canonical(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=True, allow_nan=False, sort_keys=True, separators=(",", ":")).encode()


def _load_json_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result = dict(pairs)
    if len(result) != len(pairs):
        raise ValueError(_MALFORMED)
    return result


def _tool_bundle(calls: list[dict[str, Any]]) -> list[dict[str, Any]]:
    tools: list[dict[str, Any]] = []
    seen: set[str] = set()
    for call in calls:
        if not isinstance(call, dict) or call.get("type") != "function":
            raise ValueError(_MALFORMED)
        call_id, function = call.get("id"), call.get("function")
        if not isinstance(call_id, str) or not call_id.strip() or call_id in seen or not isinstance(function, dict):
            raise ValueError(_MALFORMED)
        name, arguments = function.get("name"), function.get("arguments")
        if not isinstance(name, str) or not name.strip() or not isinstance(arguments, str):
            raise ValueError(_MALFORMED)
        try:
            inputs = json.loads(arguments, object_pairs_hook=_load_json_pairs)
        except (ValueError, RecursionError):
            raise ValueError(_MALFORMED) from None
        if not isinstance(inputs, dict):
            raise ValueError(_MALFORMED)
        seen.add(call_id)
        tools.append({"type": "tool_use", "id": call_id, "name": name, "input": inputs})
    return tools


@dataclass(frozen=True, slots=True)
class KimiNativeSession:
    """Construct only after verifying the normal signed generation identity."""

    identity: GenerationProviderIdentity
    route: str
    provider_scope: Literal["llmgw", "aitunnel"]
    secret: str = field(repr=False)

    def __post_init__(self) -> None:
        if self.route not in _ROUTES or self.provider_scope not in _MODELS or len(self.secret.encode()) < 32:
            raise ValueError("Kimi history authentication is unavailable")

    @property
    def wire_model(self) -> str:
        return _MODELS[self.provider_scope]

    def _cipher(self) -> AESGCM:
        return AESGCM(hmac.digest(self.secret.encode(), _DOMAIN, "sha256"))

    def _associated_data(self, text: str | None, tools: list[dict[str, Any]]) -> bytes:
        owner = self.identity
        # Subsequent tool turns sign a new attempt ID and request digest. The
        # stable generation identity, assistant text and tool bundle bind reuse.
        return _canonical([
            self.route, self.provider_scope, self.wire_model, owner.version,
            str(owner.run_id), str(owner.project_id), str(owner.user_id), owner.prompt_hash, text, tools,
        ])

    def _reasoning(self, message: dict[str, Any]) -> dict[str, Any]:
        fields = _REASONING_FIELDS[self.provider_scope]
        reasoning = {key: message[key] for key in fields if key in message}
        if not reasoning or (
            fields[0] in reasoning and not isinstance(reasoning[fields[0]], str)
        ) or (
            "reasoning_details" in reasoning and not isinstance(reasoning["reasoning_details"], list)
        ):
            raise ValueError(_MALFORMED)
        return reasoning

    def _seal(self, text: str | None, tools: list[dict[str, Any]], message: dict[str, Any]) -> str:
        plaintext = _canonical(self._reasoning(message))
        if len(plaintext) > _MAX_BYTES:
            raise ValueError(_MALFORMED)
        nonce = os.urandom(_NONCE_BYTES)
        encrypted = self._cipher().encrypt(nonce, plaintext, self._associated_data(text, tools))
        return base64.urlsafe_b64encode(nonce + encrypted).decode("ascii")

    def restore(self, token: object, text: str | None, calls: list[dict[str, Any]]) -> dict[str, Any]:
        try:
            max_encoded = 4 * ((_MAX_BYTES + _NONCE_BYTES + _TAG_BYTES + 2) // 3)
            if not isinstance(token, str) or not token or len(token) > max_encoded:
                raise ValueError
            envelope = base64.b64decode(token, altchars=b"-_", validate=True)
            if base64.urlsafe_b64encode(envelope).decode("ascii") != token:
                raise ValueError
            nonce, ciphertext = envelope[:_NONCE_BYTES], envelope[_NONCE_BYTES:]
            if not _TAG_BYTES < len(ciphertext) <= _MAX_BYTES + _TAG_BYTES:
                raise ValueError
            tools = _tool_bundle(calls)
            raw = self._cipher().decrypt(nonce, ciphertext, self._associated_data(text, tools))
            return self._reasoning(json.loads(raw))
        except (InvalidTag, ValueError, TypeError, RecursionError):
            raise ValueError("Kimi history authentication failed") from None

    def response_content(self, data: dict[str, Any], *, expected_model: str) -> list[dict[str, Any]]:
        choices = data.get("choices")
        if expected_model != KIMI_MODEL or data.get("model") != self.wire_model or (
            not isinstance(choices, list) or len(choices) != 1 or not isinstance(choices[0], dict)
        ):
            raise ValueError(_MALFORMED)
        choice = choices[0]
        finish, message = choice.get("finish_reason"), choice.get("message")
        if not isinstance(finish, str) or finish not in _FINISH_REASONS or not isinstance(message, dict) or (
            message.get("role") != "assistant"
        ):
            raise ValueError(_MALFORMED)
        if finish == "content_filter":
            return []

        text = message.get("content")
        if text is not None and not isinstance(text, str):
            raise ValueError(_MALFORMED)
        content = [{"type": "text", "text": text}] if text is not None else []
        if finish == "length":
            return content

        calls = message.get("tool_calls")
        if finish == "stop":
            if calls not in (None, []) or not text or not text.strip():
                raise ValueError(_MALFORMED)
            calls = []
        elif not isinstance(calls, list) or not calls:
            raise ValueError(_MALFORMED)
        try:
            tools = _tool_bundle(calls)
            token = self._seal(text, tools, message)
        except (TypeError, ValueError, RecursionError):
            raise ValueError(_MALFORMED) from None
        return [*content, *tools, {"type": CARRIER_TYPE, "token": token}]


def assistant_text(content: object, field_name: str) -> str | None:
    """Accept the closed assistant transcript produced by this adapter."""
    if not isinstance(content, list):
        raise NativeRequestValidationError("invalid_kimi_reasoning", field_name)
    shapes = {
        "text": {"type", "text"},
        "tool_use": {"type", "id", "name", "input"},
        CARRIER_TYPE: {"type", "token"},
    }
    text = None
    for block in content:
        kind = block.get("type") if isinstance(block, dict) else None
        if not isinstance(kind, str) or kind not in shapes or set(block) != shapes[kind]:
            raise NativeRequestValidationError("invalid_kimi_reasoning", field_name)
        if kind == "text":
            if text is not None or not isinstance(block["text"], str):
                raise NativeRequestValidationError("invalid_kimi_reasoning", field_name)
            text = block["text"]
        elif kind == "tool_use" and not (
            isinstance(block["id"], str) and isinstance(block["name"], str) and isinstance(block["input"], dict)
        ):
            raise NativeRequestValidationError("invalid_kimi_reasoning", field_name)
    return text


def restore_history(
    session: KimiNativeSession | None,
    role: object,
    content: object,
    calls: list[dict[str, Any]],
    field_name: str,
    *,
    text: str | None = None,
) -> dict[str, Any]:
    carriers = [
        block for block in content if isinstance(block, dict) and block.get("type") == CARRIER_TYPE
    ] if isinstance(content, list) else []
    if not carriers and (session is None or role != "assistant"):
        return {}
    if (
        session is None or role != "assistant"
        or len(carriers) != 1 or set(carriers[0]) != {"type", "token"}
    ):
        raise NativeRequestValidationError("invalid_kimi_reasoning", field_name)
    try:
        return session.restore(carriers[0]["token"], text, calls)
    except ValueError:
        raise NativeRequestValidationError("invalid_kimi_reasoning", field_name) from None

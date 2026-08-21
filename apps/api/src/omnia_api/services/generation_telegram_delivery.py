from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass
from typing import Any
from uuid import UUID

import httpx

from omnia_api.services.secret_safety import redact_provider_secrets

_MESSAGE_LIMIT = 3600
_PROJECT_LABEL_LIMIT = 120
_ERROR_LIMIT = 500
_EMAIL_RE = re.compile(r"(?i)\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b")
_URL_RE = re.compile(r"(?i)\b(?:https?|postgres(?:ql)?(?:\+asyncpg)?|redis)://[^\s]+")
_TELEGRAM_ORIGIN = "https://api.telegram.org"
_TELEGRAM_TIMEOUT = httpx.Timeout(connect=5.0, read=15.0, write=15.0, pool=5.0)
_MAX_RETRY_AFTER_SECONDS = 900


@dataclass(frozen=True)
class StartDelivery:
    text: str
    prompt_document: bytes | None
    prompt_filename: str | None


class TelegramFailure(Exception):
    def __init__(
        self,
        code: str,
        *,
        retryable: bool,
        retry_after_seconds: int | None = None,
    ) -> None:
        super().__init__(code)
        self.code = code
        self.retryable = retryable
        self.retry_after_seconds = retry_after_seconds


class TelegramBotClient:
    """Small fixed-boundary client for the three outbound report methods."""

    def __init__(
        self,
        *,
        token: str,
        chat_id: int,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        normalized_token = (token or "").strip()
        if not normalized_token or "/" in normalized_token or any(
            char.isspace() for char in normalized_token
        ):
            raise ValueError("Telegram bot token is missing or invalid")
        if chat_id >= 0:
            raise ValueError("Telegram chat id must be a negative group id")
        self._chat_id = int(chat_id)
        self._client = httpx.AsyncClient(
            base_url=f"{_TELEGRAM_ORIGIN}/bot{normalized_token}/",
            timeout=_TELEGRAM_TIMEOUT,
            transport=transport,
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def _post(self, method: str, **kwargs: Any) -> int:
        try:
            response = await self._client.post(method, **kwargs)
        except httpx.TimeoutException as exc:
            raise TelegramFailure("telegram_timeout", retryable=True) from exc
        except httpx.TransportError as exc:
            raise TelegramFailure("telegram_network_error", retryable=True) from exc

        payload: dict[str, Any] | None
        try:
            parsed = response.json()
            payload = parsed if isinstance(parsed, dict) else None
        except (ValueError, TypeError):
            payload = None

        error_code = response.status_code
        if payload is not None and isinstance(payload.get("error_code"), int):
            error_code = int(payload["error_code"])
        if error_code == 429:
            retry_after: int | None = None
            parameters = payload.get("parameters") if payload is not None else None
            if isinstance(parameters, dict) and isinstance(parameters.get("retry_after"), int):
                retry_after = min(
                    _MAX_RETRY_AFTER_SECONDS,
                    max(1, int(parameters["retry_after"])),
                )
            raise TelegramFailure(
                "telegram_rate_limited",
                retryable=True,
                retry_after_seconds=retry_after,
            )
        if error_code >= 500:
            raise TelegramFailure("telegram_server_error", retryable=True)
        if error_code >= 400:
            code = {
                400: "telegram_bad_request",
                401: "telegram_unauthorized",
                403: "telegram_forbidden",
                404: "telegram_not_found",
            }.get(error_code, "telegram_client_error")
            raise TelegramFailure(code, retryable=False)
        if payload is None or payload.get("ok") is not True:
            raise TelegramFailure("telegram_malformed_response", retryable=False)
        result = payload.get("result")
        message_id = result.get("message_id") if isinstance(result, dict) else None
        if not isinstance(message_id, int):
            raise TelegramFailure("telegram_malformed_response", retryable=False)
        return message_id

    async def send_message(self, text: str, *, reply_to: int | None = None) -> int:
        payload: dict[str, object] = {
            "chat_id": self._chat_id,
            "text": text,
            "link_preview_options": {"is_disabled": True},
        }
        if reply_to is not None:
            payload["reply_parameters"] = {"message_id": int(reply_to)}
        return await self._post("sendMessage", json=payload)

    async def send_document(
        self,
        data: bytes,
        filename: str,
        *,
        caption: str,
        reply_to: int,
    ) -> int:
        fields = {
            "chat_id": str(self._chat_id),
            "caption": caption,
            "reply_parameters": json.dumps(
                {"message_id": int(reply_to)},
                separators=(",", ":"),
            ),
        }
        files = {"document": (filename, data, "text/plain; charset=utf-8")}
        return await self._post("sendDocument", data=fields, files=files)

    async def send_photo(self, data: bytes, *, caption: str, reply_to: int) -> int:
        fields = {
            "chat_id": str(self._chat_id),
            "caption": caption,
            "reply_parameters": json.dumps(
                {"message_id": int(reply_to)},
                separators=(",", ":"),
            ),
        }
        files = {"photo": ("generation-preview.png", data, "image/png")}
        return await self._post("sendPhoto", data=fields, files=files)


def _short_run_id(run_id: UUID) -> str:
    return str(run_id).split("-", 1)[0]


def _safe_label(value: str | None, *, limit: int) -> str:
    normalized = "".join(
        " " if unicodedata.category(char).startswith("C") else char
        for char in (value or "")
    )
    compact = " ".join(normalized.split()).strip()
    if not compact:
        return "без названия"
    if len(compact) <= limit:
        return compact
    return compact[: limit - 1].rstrip() + "…"


def _mode_label(mode: str) -> str:
    normalized = (mode or "").strip().lower()
    if normalized not in {"build", "edit"}:
        raise ValueError("generation Telegram report mode must be build or edit")
    return normalized.upper()


def build_start_delivery(
    *,
    run_id: UUID,
    mode: str,
    project_name: str,
    user_text: str,
) -> StartDelivery:
    """Build a plain-text start message from the exact persisted user turn."""

    short_id = _short_run_id(run_id)
    header = (
        f"🟡 {_mode_label(mode)} · Проект "
        f"«{_safe_label(project_name, limit=_PROJECT_LABEL_LIMIT)}» · #{short_id}\n"
        "Промпт:\n"
    )
    redacted = redact_provider_secrets(user_text or "")
    full_text = header + redacted
    if len(full_text) <= _MESSAGE_LIMIT:
        return StartDelivery(full_text, None, None)

    suffix = "…\n\nПолный промпт приложен файлом."
    available = max(0, _MESSAGE_LIMIT - len(header) - len(suffix))
    preview = redacted[:available].rstrip()
    return StartDelivery(
        text=header + preview + suffix,
        prompt_document=redacted.encode("utf-8"),
        prompt_filename=f"generation-{short_id}-prompt.txt",
    )


def sanitize_error(value: str | None) -> str:
    """Return a bounded diagnostic without credentials or external identifiers."""

    candidate = value or ""
    if "Traceback (most recent call last):" in candidate:
        return "[TRACEBACK REDACTED]"
    candidate = redact_provider_secrets(candidate)
    candidate = _URL_RE.sub("[URL REDACTED]", candidate)
    candidate = _EMAIL_RE.sub("[EMAIL REDACTED]", candidate)
    candidate = "".join(
        " " if unicodedata.category(char).startswith("C") else char
        for char in candidate
    )
    candidate = " ".join(candidate.split()).strip()
    if not candidate:
        candidate = "неизвестная ошибка"
    if len(candidate) <= _ERROR_LIMIT:
        return candidate
    return candidate[: _ERROR_LIMIT - 1].rstrip() + "…"


def _elapsed_text(elapsed_seconds: int) -> str:
    bounded = max(0, int(elapsed_seconds))
    minutes, seconds = divmod(bounded, 60)
    if minutes < 60:
        return f"{minutes:02d}:{seconds:02d}"
    hours, minutes = divmod(minutes, 60)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}"


def build_finish_text(
    *,
    run_id: UUID,
    mode: str,
    outcome: str,
    elapsed_seconds: int,
    stage: str,
    error: str | None,
    preview_error_code: str | None = None,
) -> str:
    """Build the one terminal text/caption for a generation report outcome."""

    label = _mode_label(mode)
    short_id = _short_run_id(run_id)
    elapsed = _elapsed_text(elapsed_seconds)
    if outcome == "completed":
        return f"✅ {label} завершён · {elapsed} · #{short_id}"
    if outcome == "completed_no_snapshot":
        return f"✅ {label} завершён без нового снимка · {elapsed} · #{short_id}"
    if outcome == "failed":
        safe_stage = _safe_label(stage, limit=40)
        return (
            f"❌ {label} упал · этап: {safe_stage} · {elapsed} · #{short_id}\n"
            f"Ошибка: {sanitize_error(error)}"
        )
    if outcome == "cancelled":
        return f"⚪ {label} отменён пользователем · {elapsed} · #{short_id}"
    if outcome == "preview_warning":
        reason = _safe_label(preview_error_code or "preview_timeout", limit=80)
        return (
            f"⚠️ {label} готов, но preview не получен · {elapsed} · #{short_id}\n"
            f"Причина: {reason}"
        )
    if outcome == "late_preview":
        return f"🖼 Preview появился позже · {label} · #{short_id}"
    raise ValueError("unknown generation Telegram report outcome")


__all__ = [
    "StartDelivery",
    "TelegramBotClient",
    "TelegramFailure",
    "build_finish_text",
    "build_start_delivery",
    "sanitize_error",
]

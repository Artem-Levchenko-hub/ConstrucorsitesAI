"""Closed evidence for a pre-execution LLMGW key rate-limit refusal.

This returns a candidate only. Routers may publish permission only after the
failed provider receipt commits. Missing monetary fields remain unknown.
"""

from __future__ import annotations

import json
import math
import re
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import Any

import httpx

TRUSTED_URL = "https://api.llmgw.ru/v1/chat/completions"
POLICY = "llmgw_key_rate_limit_v1"
_LIMIT = 16_384
_CODES = {"rate_limit_exceeded", "rate_limit_error"}
_MONEY_HEADERS = {"x-llmgw-cost-rub", "x-cost-rub", "x-cost-usd", "x-llmgw-cost", "x-cost"}
_DAY = r"(?:Mon|Tue|Wed|Thu|Fri|Sat|Sun)"
_MONTH = r"(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)"
_LONG_DAY = r"(?:Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday)"
_DATE_PATTERNS = (
    rf"{_DAY}, [0-9]{{2}} {_MONTH} [0-9]{{4}} [0-9]{{2}}:[0-9]{{2}}:[0-9]{{2}} GMT",
    rf"{_LONG_DAY}, [0-9]{{2}}-{_MONTH}-[0-9]{{2}} [0-9]{{2}}:[0-9]{{2}}:[0-9]{{2}} GMT",
    rf"{_DAY} {_MONTH} (?: [0-9]|[0-9]{{2}}) [0-9]{{2}}:[0-9]{{2}}:[0-9]{{2}} [0-9]{{4}}",
)


def bounded_error_body(response: httpx.Response) -> dict[str, Any] | None:
    if len(response.content) > _LIMIT:
        return None
    try:

        def unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
            result: dict[str, Any] = {}
            for key, value in pairs:
                if key in result:
                    raise ValueError("duplicate key")
                result[key] = value
            return result

        value = json.loads(response.content, object_pairs_hook=unique)
    except (ValueError, UnicodeError, RecursionError):
        return None
    return value if isinstance(value, dict) else None


def _delay(value: str, now: datetime) -> int | None:
    if not value or len(value) > 128:
        return None
    if re.fullmatch(r"[0-9]+", value):
        seconds = int(value)
    else:
        if not any(re.fullmatch(pattern, value) for pattern in _DATE_PATTERNS):
            return None
        try:
            date = parsedate_to_datetime(value)
            if date.tzinfo is None:
                date = date.replace(tzinfo=UTC)  # HTTP asctime is GMT.
            if date.utcoffset() != UTC.utcoffset(date):
                return None
            if re.fullmatch(_DATE_PATTERNS[1], value):
                # RFC 9110: obsolete two-digit dates over 50 years ahead refer
                # to the most recent past year with the same trailing digits.
                year = now.year // 100 * 100 + int(value.split("-")[2][:2])
                if year > now.year + 50:
                    year -= 100
                date = date.replace(year=year)
            seconds = max(0, math.ceil((date - now).total_seconds()))
        except (ValueError, TypeError, OverflowError):
            return None
    return seconds if 0 <= seconds <= 86_400 else None


def refusal_retry_candidate(
    response: httpx.Response, *, now: datetime | None = None
) -> dict[str, Any] | None:
    try:
        request = response.request
    except RuntimeError:
        return None
    if (
        response.status_code != 429
        or request.method != "POST"
        or str(request.url) != TRUSTED_URL
        or response.history
        or any(name in response.headers for name in _MONEY_HEADERS)
    ):
        return None
    body = bounded_error_body(response)
    if not body or set(body) - {"error", "id", "request_id"}:
        return None
    error = body.get("error")
    if not isinstance(error, dict) or set(error) - {"code", "type", "message", "param"}:
        return None
    codes = [error[key] for key in ("code", "type") if key in error]
    if not codes or any(not isinstance(code, str) or code not in _CODES for code in codes):
        return None
    if any(not isinstance(error[key], str) for key in ("message",) if key in error):
        return None
    if "param" in error and error["param"] is not None and not isinstance(error["param"], str):
        return None
    if any(not isinstance(body[key], str) for key in ("id", "request_id") if key in body):
        return None
    result: dict[str, Any] = {"retry_safe": True, "retry_policy": POLICY}
    if "retry-after" in response.headers:
        delay = _delay(response.headers["retry-after"], now or datetime.now(UTC))
        if delay is None:
            return None
        result["retry_after_seconds"] = delay
    return result

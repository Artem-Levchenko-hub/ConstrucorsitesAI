"""Explicitly armed, failure-only private wire evidence; never a provider retry.

No arm or unsafe filesystem means no diagnostics. This module deliberately has
no logging, configuration discovery, network client, or public error fields.
"""

from __future__ import annotations

import base64
import contextvars
import hashlib
import json
import os
import re
import stat
from collections.abc import Iterator
from contextlib import contextmanager, suppress
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import quote, quote_plus
from uuid import UUID

import httpx

_DIRECTORY = Path("/tmp/yleum-provider-validation")
_MAX_ARM_BYTES = 1024
_MAX_BODY_BYTES = 2 * 1024 * 1024
_SENSITIVE_HEADER = re.compile(r"authorization|cookie|api|token|key|secret", re.I)
_SENSITIVE_JSON = re.compile(
    r'("(?:authorization|cookie|set-cookie|api[_-]?key|access[_-]?token|refresh[_-]?token|'
    r'token|key|secret|password|client[_-]?secret)"\s*:\s*)"(?:[^"\\]|\\.)*"', re.I,
)


@dataclass(frozen=True)
class _Scope:
    project_id: str
    run_id: str
    call_id: str


_SCOPE: contextvars.ContextVar[_Scope | None] = contextvars.ContextVar(
    "provider_validation_diagnostic_scope", default=None,
)


def _uuid(value: object) -> str | None:
    if isinstance(value, UUID):
        return str(value)
    if not isinstance(value, str):
        return None
    try:
        parsed = UUID(value)
    except ValueError:
        return None
    return value if str(parsed) == value else None


@contextmanager
def provider_validation_scope(
    *, project_id: object, run_id: object, call_id: object,
) -> Iterator[None]:
    """Bind only a committed provider dispatch; nested/async contexts stay isolated."""
    project, run, call = _uuid(project_id), _uuid(run_id), _uuid(call_id)
    scope = _Scope(project, run, call) if project and run and call else None
    token = _SCOPE.set(scope)
    try:
        yield
    finally:
        _SCOPE.reset(token)


def _open_directory() -> int:
    # Linux-only security contract. Never silently replace dirfd/no-follow with
    # path-based I/O on another platform, and never create the directory here.
    if os.name != "posix" or not _DIRECTORY.is_absolute():
        raise OSError("Private diagnostic filesystem is unavailable")
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    descriptor = os.open("/", flags)
    try:
        for component in _DIRECTORY.parts[1:]:
            next_descriptor = os.open(component, flags, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = next_descriptor
        info = os.fstat(descriptor)
        if info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) != 0o700:
            raise OSError("Unsafe private diagnostic directory")
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise


def _read_arm(directory: int, scope: _Scope, now: datetime) -> str | None:
    descriptor = os.open("armed.json", os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
                         dir_fd=directory)
    try:
        before = os.fstat(descriptor)
        if (not stat.S_ISREG(before.st_mode) or before.st_uid != os.geteuid()
                or stat.S_IMODE(before.st_mode) != 0o600 or before.st_nlink != 1
                or not 0 < before.st_size <= _MAX_ARM_BYTES):
            return None
        raw = os.read(descriptor, _MAX_ARM_BYTES + 1)
        after = os.fstat(descriptor)
        stable = ("st_dev", "st_ino", "st_mode", "st_uid", "st_nlink", "st_size",
                  "st_mtime_ns", "st_ctime_ns")
        if any(getattr(after, field) != getattr(before, field) for field in stable) or len(raw) != before.st_size:
            return None
        arm = json.loads(raw)
    finally:
        os.close(descriptor)
    if not isinstance(arm, dict) or set(arm) != {"version", "project_id", "nonce", "expires_at"}:
        return None
    nonce = _uuid(arm["nonce"])
    if type(arm["version"]) is not int or arm["version"] != 1 or not nonce:
        return None
    if _uuid(arm["project_id"]) != scope.project_id or not isinstance(arm["expires_at"], str):
        return None
    expires = datetime.fromisoformat(arm["expires_at"])
    if expires.utcoffset() != timedelta(0) or not now < expires <= now + timedelta(hours=1):
        return None
    return nonce


def _secret_forms(secret: str) -> set[str]:
    if not secret:
        return set()
    raw = secret.encode("utf-8")
    forms = {
        secret, json.dumps(secret, ensure_ascii=True)[1:-1],
        json.dumps(secret, ensure_ascii=False)[1:-1], quote(secret, safe=""), quote_plus(secret),
        "".join(f"%{value:02X}" for value in raw),
        "".join(f"\\u{ord(character):04x}" for character in secret),
        base64.b64encode(raw).decode(), base64.urlsafe_b64encode(raw).decode(),
        raw.hex(), raw.hex().upper(),
    }
    forms |= {value.rstrip("=") for value in forms if value.endswith("=")}
    forms |= {re.sub(r"%[0-9A-F]{2}", lambda match: match[0].lower(), value) for value in forms}
    # JSON strings can carry an escaped URL, base64, or escaped-key spelling.
    forms |= {json.dumps(value)[1:-1] for value in forms}
    return forms


def _redact(value: str, secrets: set[str]) -> str:
    for secret in sorted(secrets, key=len, reverse=True):
        value = value.replace(secret, "[REDACTED]")
    return value


def _body(raw: bytes, secrets: set[str], *, within_budget: bool) -> dict[str, object]:
    result: dict[str, object] = {"sha256": hashlib.sha256(raw).hexdigest(), "bytes": len(raw)}
    if not within_budget:
        return {**result, "content_state": "withheld", "reason": "body_budget_exceeded"}
    try:
        text = raw.decode("utf-8", errors="strict")
    except UnicodeError:
        return {**result, "content_state": "withheld", "reason": "not_utf8"}
    cleaned = _SENSITIVE_JSON.sub(r'\1"[REDACTED]"', _redact(text, secrets))
    return {**result, "content_state": "redacted" if cleaned != text else "exact_utf8",
            "utf8": cleaned}


def _headers(headers: httpx.Headers, secrets: set[str]) -> list[list[str]]:
    return [[name, "[REDACTED]" if _SENSITIVE_HEADER.search(name) else _redact(value, secrets)]
            for name, value in headers.multi_items()]


def _write_new(directory: int, name: str, data: bytes) -> None:
    descriptor = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                         0o600, dir_fd=directory)
    try:
        info = os.fstat(descriptor)
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid()
                or stat.S_IMODE(info.st_mode) != 0o600 or info.st_nlink != 1):
            raise OSError("Unsafe diagnostic output")
        with os.fdopen(descriptor, "wb", closefd=False) as stream:
            stream.write(data)
            stream.flush()
            os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _capture(error: httpx.HTTPStatusError, credential: str) -> None:
    scope = _SCOPE.get()
    response = error.response
    request = response.request
    url = request.url
    if (scope is None or response.status_code != 422 or request.method != "POST"
            or url.scheme != "https" or url.host != "api.llmgw.ru" or url.port not in {None, 443}
            or url.path != "/v1/chat/completions" or url.query or url.userinfo):
        return
    directory = _open_directory()
    try:
        now = datetime.now(UTC)
        nonce = _read_arm(directory, scope, now)
        if nonce is None:
            return
        binding = {"version": 1, "nonce": nonce, "project_id": scope.project_id,
                   "run_id": scope.run_id, "call_id": scope.call_id, "captured_at": now.isoformat()}
        # Reserve first: any later sink failure consumes this arm, never retries
        # recording another call, and never affects the original provider error.
        _write_new(directory, f"claim-{nonce}.json", json.dumps(binding).encode("utf-8"))
        os.fsync(directory)
        secrets = _secret_forms(credential)
        for headers in (request.headers, response.headers):
            for name, value in headers.multi_items():
                if _SENSITIVE_HEADER.search(name):
                    secrets |= _secret_forms(value)
                    if name.lower() == "authorization" and " " in value:
                        secrets |= _secret_forms(value.split(" ", 1)[1])
        sent, received = request.content, response.content
        within_budget = len(sent) + len(received) <= _MAX_BODY_BYTES
        record = {**binding, "request": {"method": request.method, "url": str(url),
                  "headers": _headers(request.headers, secrets),
                  "body": _body(sent, secrets, within_budget=within_budget)},
                  "response": {"status": response.status_code,
                  "headers": _headers(response.headers, secrets),
                  "body": _body(received, secrets, within_budget=within_budget)}}
        data = json.dumps(record, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        temporary = f"pending-{nonce}.json"
        _write_new(directory, temporary, data)
        # Atomic, no-overwrite publication. Retain pending evidence on failure;
        # only unlink our successfully published hardlink, never arm/other files.
        os.link(temporary, f"capture-{nonce}.json", src_dir_fd=directory,
                dst_dir_fd=directory, follow_symlinks=False)
        os.unlink(temporary, dir_fd=directory)
        os.fsync(directory)
    finally:
        os.close(directory)


def capture_validation_failure(error: httpx.HTTPStatusError, *, credential: str) -> None:
    """Best effort, deliberately silent: provider exceptions/accounting win."""
    with suppress(Exception):
        _capture(error, credential)

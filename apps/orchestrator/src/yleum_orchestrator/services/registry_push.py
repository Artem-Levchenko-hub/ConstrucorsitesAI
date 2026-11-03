"""Bounded retry for public registry write fencing during safe maintenance."""

from __future__ import annotations

import base64
import math
import re
import time
from collections.abc import Callable
from typing import Any
from urllib.parse import quote

from yleum_orchestrator.services.project_machine import machine_remaining_seconds


class RegistryPushError(RuntimeError):
    def __init__(self, message: str, *, status_code: int = 500) -> None:
        super().__init__(message)
        self.status_code = status_code


def _safe_message(raw: object, auth: dict[str, str] | None) -> str:
    message = raw if isinstance(raw, str) else "registry push failed"
    credentials = auth or {}
    for key in ("password", "identitytoken", "registrytoken"):
        secret = credentials.get(key)
        if secret:
            message = message.replace(secret, "[redacted]").replace(
                quote(secret, safe=""), "[redacted]"
            )
    if credentials.get("password"):
        basic = base64.b64encode(
            f"{credentials.get('username', '')}:{credentials['password']}".encode()
        ).decode()
        message = message.replace(basic, "[redacted]")
    return message[:400]


def _temporary(line: dict[str, Any]) -> bool:
    detail = line.get("errorDetail")
    if isinstance(detail, dict) and detail.get("code") == "UNSUPPORTED":
        return True
    raw = line.get("error")
    if not isinstance(raw, str):
        return False
    text = raw.casefold()
    return any(value in text for value in ("read-only", "read only", "readonly")) or bool(
        re.search(r"\b(?:502|503|504)\b", text)
        and any(value in text for value in ("status", "gateway", "service unavailable"))
    )


def push_image(
    docker_client: Any,
    image_id: str,
    repository: str,
    tag: str,
    *,
    auth: dict[str, str] | None,
    maintenance_retry_seconds: float = 300,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> str:
    if not math.isfinite(maintenance_retry_seconds) or maintenance_retry_seconds < 0:
        raise ValueError("invalid registry maintenance retry duration")
    deadline = clock() + maintenance_retry_seconds
    docker_client.images.get(image_id).tag(repository, tag)
    while True:
        machine_remaining_seconds(1)
        stream = None
        try:
            stream = docker_client.images.push(
                repository,
                tag=tag,
                stream=True,
                decode=True,
                auth_config=auth,
            )
            retry = False
            for line in stream:
                if not isinstance(line, dict) or not line.get("error"):
                    continue
                if _temporary(line):
                    retry = True
                    break
                raise RegistryPushError(f"image push failed: {_safe_message(line['error'], auth)}")
            if not retry:
                return f"{repository}:{tag}"
        except RegistryPushError:
            raise
        except Exception as exc:
            response = getattr(exc, "response", None)
            status = getattr(exc, "status_code", None) or getattr(response, "status_code", None)
            if status not in {502, 503, 504}:
                raise RegistryPushError(
                    f"image push failed: {_safe_message(str(exc), auth)}"
                ) from None
        finally:
            close = getattr(stream, "close", None)
            if close is not None:
                try:
                    close()
                except Exception:
                    pass
        remaining = deadline - clock()
        if remaining <= 0:
            raise RegistryPushError("registry maintenance did not finish in time", status_code=503)
        machine_remaining_seconds(1)
        sleep(min(2.0, remaining))

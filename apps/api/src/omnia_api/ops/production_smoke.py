"""Dependency-free, bounded off-host production checks with safe diagnostics.

Run with PYTHONPATH=apps/api/src python -m omnia_api.ops.production_smoke.
Expected revisions are explicit per component; a shared image does not imply
that the independently deployed web image has the same revision.
"""

from __future__ import annotations

import json
import os
import queue
import re
import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Protocol
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

MAX_BODY_BYTES = 2 * 1024 * 1024
REQUEST_TIMEOUT = 20.0
TOTAL_DEADLINE = 150.0
ATTEMPTS = 3
READINESS_CHECKS = (
    "database",
    "redis",
    "worker",
    "generation_worker",
    "deploy_control_plane",
    "preview_storage",
)


@dataclass(frozen=True)
class Configuration:
    platform_url: str
    canary_url: str
    expected: dict[str, str]

    @classmethod
    def from_env(cls, env: Mapping[str, str]) -> Configuration:
        urls = []
        for key, label in (("PLATFORM_URL", "platform_url"), ("MAX_CANARY_URL", "canary_url")):
            value = env.get(key, "")
            try:
                parsed = urlsplit(value)
                valid = (
                    parsed.scheme in {"http", "https"}
                    and bool(parsed.hostname)
                    and parsed.username is None
                    and parsed.password is None
                    and not parsed.query
                    and not parsed.fragment
                    and not any(char.isspace() or ord(char) < 32 for char in value)
                )
                _ = parsed.port
            except ValueError:
                valid = False
            if not valid:
                raise ValueError(f"config.{label}")
            urls.append(value.rstrip("/"))
        expected = {}
        for component in ("web", "api", "worker", "orchestrator"):
            value = env.get(f"PRODUCTION_EXPECTED_{component.upper()}_RELEASE_SHA", "")
            if re.fullmatch(r"[0-9a-f]{40}", value) is None:
                raise ValueError(f"config.expected_{component}_release")
            expected[component] = value
        return cls(urls[0], urls[1], expected)


@dataclass(frozen=True)
class Reply:
    status: int
    body: bytes


class Transport(Protocol):
    def request(self, method: str, url: str, *, timeout: float) -> Reply: ...


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[no-untyped-def]
        return None


class HTTPClient:
    def request(self, method: str, url: str, *, timeout: float) -> Reply:
        """Bound DNS/connect/header/body time, including a slow-drip response.

        urllib's socket timeout alone is not a total deadline. A daemon request
        and bounded queue wait also cap DNS and repeated slow body reads; the CLI
        never waits for a timed-out transport thread during process shutdown.
        """
        results: queue.Queue[Reply | Exception] = queue.Queue(maxsize=1)

        def download() -> None:
            try:
                request = Request(url, method=method)
                if method == "POST":
                    request.data = b"{}"
                    request.add_header("Content-Type", "application/json")
                opener = build_opener(_NoRedirect())
                try:
                    response = opener.open(request, timeout=timeout)
                except HTTPError as error:
                    response = error
                with response:
                    result = Reply(response.code, response.read(MAX_BODY_BYTES + 1))
                results.put(result)
            except Exception as error:
                results.put(error)

        threading.Thread(target=download, daemon=True).start()
        try:
            result = results.get(timeout=timeout)
        except queue.Empty:
            raise TimeoutError from None
        if isinstance(result, Exception):
            raise OSError from None
        return result


def run_smoke(
    config: Configuration,
    transport: Transport,
    *,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> list[str]:
    deadline = clock() + TOTAL_DEADLINE
    failures: list[str] = []

    def probe(label: str, url: str, *, method: str = "GET", status: int = 200) -> bytes | None:
        for attempt in range(ATTEMPTS):
            remaining = deadline - clock()
            if remaining <= 0:
                failures.append(f"{label}.deadline")
                return None
            try:
                reply = transport.request(method, url, timeout=min(REQUEST_TIMEOUT, remaining))
            except (OSError, TimeoutError):
                code, retry = "transport_error", True
            else:
                if len(reply.body) > MAX_BODY_BYTES:
                    failures.append(f"{label}.body_too_large")
                    return None
                if reply.status == status:
                    return reply.body
                code, retry = f"http_{reply.status}", reply.status >= 500
            if not retry or attempt == ATTEMPTS - 1:
                failures.append(f"{label}.{code}")
                return None
            sleep(min(0.25, max(0, deadline - clock())))
        return None  # pragma: no cover - bounded loop always returns above

    def health(label: str, url: str) -> dict[str, object] | None:
        body = probe(label, url)
        if body is None:
            return None
        try:
            payload = json.loads(body)
        except (ValueError, UnicodeError):
            payload = None
        if not isinstance(payload, dict):
            failures.append(f"{label}.invalid_json")
            return None
        return payload

    def require(ok: bool, code: str) -> None:
        if not ok:
            failures.append(code)

    web = health("web", config.platform_url + "/web-health")
    if web is not None:
        require(web.get("status") == "ok", "web.status")
        require(web.get("service") == "web", "web.service")
        require(web.get("release_sha") == config.expected["web"], "web.release_mismatch")
    api = health("api", config.platform_url + "/api/health")
    if api is not None:
        require(api.get("status") == "ok", "api.status")
        require(api.get("service") == "api", "api.service")
        checks = api.get("checks")
        for name in READINESS_CHECKS:
            require(isinstance(checks, dict) and checks.get(name) == "ok", f"api.readiness.{name}")
        require(api.get("release_sha") == config.expected["api"], "api.release_mismatch")
        dependencies = api.get("dependencies")
        for component in ("worker", "generation_worker", "orchestrator"):
            expected = config.expected["worker" if component == "generation_worker" else component]
            require(
                isinstance(dependencies, dict)
                and dependencies.get(f"{component}_release_sha") == expected,
                f"{component}.release_mismatch",
            )
    mvp = probe("mvp", config.platform_url + "/mvp")
    if mvp is not None:
        require("Путь до полностью рабочего MVP".encode() in mvp, "mvp.text_missing")
    canary = health("max_health", config.canary_url + "/api/health")
    if canary is not None:
        require(canary.get("status") == "ok", "max_health.status")
        require(canary.get("platform") == "max-miniapp", "max_health.platform")
    probe("max_webhook", config.canary_url + "/api/max/webhook", method="POST", status=401)
    return failures


def main() -> int:
    try:
        config = Configuration.from_env(os.environ)
    except ValueError as error:
        print(f"FAIL {error}")
        return 1
    try:
        failures = run_smoke(config, HTTPClient())
    except Exception:
        print("FAIL smoke.internal_error")
        return 1
    for code in failures:
        print(f"FAIL {code}")
    if not failures:
        print("PASS production_smoke")
    return int(bool(failures))


if __name__ == "__main__":
    raise SystemExit(main())

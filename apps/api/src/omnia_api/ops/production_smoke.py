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
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
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
        # The generation worker is its own container. It may be pinned separately;
        # without its own variable it inherits the worker's revision, as before.
        generation = env.get("PRODUCTION_EXPECTED_GENERATION_WORKER_RELEASE_SHA", "")
        if generation and re.fullmatch(r"[0-9a-f]{40}", generation) is None:
            raise ValueError("config.expected_generation_worker_release")
        expected["generation_worker"] = generation or expected["worker"]
        return cls(urls[0], urls[1], expected)


_IDENTITY_FIELDS = ("web", "api", "worker", "generation_worker", "orchestrator")


def _release_of(payload: Mapping[str, object]) -> str | None:
    value = payload.get("release_sha")
    return value if isinstance(value, str) else None


@dataclass(frozen=True)
class ReleaseIdentity:
    """The five service revisions a smoke result is allowed to certify.

    Both workers run the same image but are separate containers: a generation worker
    left behind on an older revision must be reported on its own, not absorbed by the
    ordinary worker that happens to share the expected value.
    """

    web: str
    api: str
    worker: str
    generation_worker: str
    orchestrator: str

    def as_map(self) -> dict[str, str | None]:
        return {
            "web": self.web,
            "api": self.api,
            "worker": self.worker,
            "generation_worker": self.generation_worker,
            "orchestrator": self.orchestrator,
        }


def validate_smoke_identity(
    runner_sha: str,
    expected: ReleaseIdentity,
    observed: ReleaseIdentity,
) -> list[str]:
    """Return the identity failures of this run; never adopt what was observed.

    The runner's own revision is part of the verdict: a workflow checked out at an
    older commit cannot certify a newer release, however healthy production looks.
    """
    failures: list[str] = []
    if runner_sha != expected.api:
        failures.append("runner.release_mismatch")
    expected_map, observed_map = expected.as_map(), observed.as_map()
    for component in sorted(expected_map):
        if observed_map.get(component) != expected_map[component]:
            failures.append(f"{component}.release_mismatch")
    return failures


def smoke_artifact(
    *,
    runner_sha: str,
    expected: ReleaseIdentity,
    observed: ReleaseIdentity,
    failures: Sequence[str],
    started_at: str,
    finished_at: str,
) -> dict[str, object]:
    """The durable record of one smoke run, written whatever the outcome.

    The record does not judge: it stores what the run reported. Service revisions are
    compared once, inside the run itself; re-deciding them here produced mismatches for
    services that were never reached, which is a different claim from "they drifted".
    """
    unique = sorted(dict.fromkeys(failures))
    return {
        "runner_sha": runner_sha,
        "expected": expected.as_map(),
        "observed": observed.as_map(),
        "started_at": started_at,
        "finished_at": finished_at,
        "failures": unique,
        "status": "failed" if unique else "passed",
    }


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
    observed: dict[str, str | None] | None = None,
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

    def require_release(code: str, expected: str, actual: object) -> None:
        """Name the drifted revision without echoing an untrusted body.

        A mismatch alone cannot be acted on: it does not say whether the
        deployment or the expectation is stale. The expected value is our own
        configuration, and the observed value is published only when it still
        looks like a revision.
        """

        if actual == expected:
            return
        if isinstance(actual, str) and re.fullmatch(r"[0-9a-f]{7,40}", actual):
            reported = actual
        elif actual is None:
            reported = "missing"
        elif actual == "unknown":
            reported = "unknown"
        else:
            reported = "invalid"
        failures.append(f"{code} expected={expected} actual={reported}")

    web = health("web", config.platform_url + "/web-health")
    if web is not None:
        require(web.get("status") == "ok", "web.status")
        require(web.get("service") == "web", "web.service")
        require_release("web.release_mismatch", config.expected["web"], web.get("release_sha"))
        if observed is not None:
            observed["web"] = _release_of(web)
    api = health("api", config.platform_url + "/api/health")
    if api is not None:
        require(api.get("status") == "ok", "api.status")
        require(api.get("service") == "api", "api.service")
        checks = api.get("checks")
        for name in READINESS_CHECKS:
            require(isinstance(checks, dict) and checks.get(name) == "ok", f"api.readiness.{name}")
        require_release("api.release_mismatch", config.expected["api"], api.get("release_sha"))
        if observed is not None:
            observed["api"] = _release_of(api)
        dependencies = api.get("dependencies")
        for component in ("worker", "generation_worker", "orchestrator"):
            reported = dependencies.get(f"{component}_release_sha") if isinstance(
                dependencies, dict
            ) else None
            require_release(
                f"{component}.release_mismatch", config.expected[component], reported
            )
            if observed is not None:
                observed[component] = reported if isinstance(reported, str) else None
    mvp = probe("mvp", config.platform_url + "/mvp")
    if mvp is not None:
        require("Путь до полностью рабочего MVP".encode() in mvp, "mvp.text_missing")
    canary = health("max_health", config.canary_url + "/api/health")
    if canary is not None:
        require(canary.get("status") == "ok", "max_health.status")
        require(canary.get("platform") == "max-miniapp", "max_health.platform")
    probe("max_webhook", config.canary_url + "/api/max/webhook", method="POST", status=401)
    return failures


def _now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _write_artifact(path: str, artifact: Mapping[str, object]) -> None:
    """Best effort: a smoke run is not failed because its record could not be saved."""
    try:
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(artifact, handle, ensure_ascii=False, indent=1, sort_keys=True)
    except OSError as error:
        print(f"WARN smoke.artifact_unwritten {type(error).__name__}")


def main() -> int:
    try:
        config = Configuration.from_env(os.environ)
    except ValueError as error:
        print(f"FAIL {error}")
        return 1
    started_at = _now()
    seen: dict[str, str | None] = {}
    try:
        failures = run_smoke(config, HTTPClient(), observed=seen)
    except Exception:
        print("FAIL smoke.internal_error")
        failures = ["smoke.internal_error"]
    finished_at = _now()
    expected = ReleaseIdentity(**{name: config.expected[name] for name in _IDENTITY_FIELDS})
    observed = ReleaseIdentity(**{name: seen.get(name) for name in _IDENTITY_FIELDS})  # type: ignore[arg-type]
    # The revision the workflow itself is running from is part of the verdict: an older
    # runner cannot certify a newer release, however healthy production looks.
    runner_sha = os.environ.get("GITHUB_SHA", config.expected["api"])
    # Only the runner's own revision is decided here; the service revisions were already
    # compared against what was actually observed during the run.
    runner_failures = [
        code
        for code in validate_smoke_identity(runner_sha, expected, expected)
        if code == "runner.release_mismatch"
    ]
    artifact = smoke_artifact(
        runner_sha=runner_sha,
        expected=expected,
        observed=observed,
        failures=[*failures, *runner_failures],
        started_at=started_at,
        finished_at=finished_at,
    )
    _write_artifact(os.environ.get("SMOKE_ARTIFACT_PATH", "smoke.json"), artifact)
    reported = artifact["failures"]
    for code in reported if isinstance(reported, list) else []:
        print(f"FAIL {code}")
    if artifact["status"] == "passed":
        print("PASS production_smoke")
    return int(artifact["status"] != "passed")


if __name__ == "__main__":
    raise SystemExit(main())

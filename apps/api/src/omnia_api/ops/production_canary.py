from __future__ import annotations

import os
import random
import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any
from urllib.parse import SplitResult, parse_qs, urlsplit, urlunsplit
from uuid import UUID, uuid4

import httpx

from omnia_api.core.release import normalize_release_sha

COMPONENTS = ("web", "api", "worker", "generation_worker", "orchestrator")

STAGES = (
    "release_health",
    "login",
    "project_create",
    "build",
    "runtime_start",
    "preview",
    "edit",
    "final_release_health",
    "cleanup",
    "unknown",
)

DIAGNOSTIC_CODES = frozenset(
    {
        "release_mismatch",
        "release_changed",
        "release_unhealthy",
        "health_http_error",
        "health_invalid_json",
        "login_failed",
        "project_create_failed",
        "build_failed",
        "edit_failed",
        "runtime_failed",
        "preview_failed",
        "snapshot_failed",
        "api_http_error",
        "api_invalid_response",
        "timeout",
        "cleanup_failed",
        "configuration_invalid",
        "canary_failed",
    }
)

# One request helper serves every stage, so the stage decides which diagnostic
# code a transport or status failure reports.
_STAGE_REQUEST_CODES = {
    "release_health": ("health_http_error", "health_invalid_json"),
    "final_release_health": ("health_http_error", "health_invalid_json"),
    "login": ("login_failed", "api_invalid_response"),
    "project_create": ("project_create_failed", "api_invalid_response"),
    "build": ("build_failed", "api_invalid_response"),
    "edit": ("edit_failed", "api_invalid_response"),
    "runtime_start": ("runtime_failed", "api_invalid_response"),
    "preview": ("preview_failed", "api_invalid_response"),
}


class CanaryConfigurationError(ValueError):
    pass


class CanaryFailure(RuntimeError):
    """A bounded, safe-to-publish description of one canary failure.

    The message stays an internal English constant; `code`, `stage` and the
    optional detail fields are what the workflow log and the diagnostics
    artifact publish, so they are restricted to fixed vocabularies.
    """

    code = "canary_failed"
    public_message = "production canary failed"
    default_stage = "unknown"

    def __init__(
        self,
        message: str,
        *,
        stage: str | None = None,
        code: str | None = None,
        component: str | None = None,
        expected: str | None = None,
        actual: str | None = None,
        http_status: int | None = None,
    ) -> None:
        super().__init__(message)
        resolved_stage = stage if stage is not None else type(self).default_stage
        resolved_code = code if code is not None else type(self).code
        if resolved_stage not in STAGES:
            raise ValueError(f"unknown canary stage: {resolved_stage}")
        if resolved_code not in DIAGNOSTIC_CODES:
            raise ValueError(f"unknown canary diagnostic code: {resolved_code}")
        self.stage = resolved_stage
        self.code = resolved_code
        self.component = component
        self.expected = expected
        self.actual = actual
        self.http_status = http_status
        self.cleanup = "unknown"
        self.elapsed_seconds: float | None = None

    def diagnostics(self) -> dict[str, object]:
        report: dict[str, object] = {
            "stage": self.stage,
            "code": self.code,
            "cleanup": self.cleanup,
        }
        optional: tuple[tuple[str, object | None], ...] = (
            ("component", self.component),
            ("expected_sha", self.expected),
            ("actual_sha", self.actual),
            ("http_status", self.http_status),
            ("elapsed_seconds", self.elapsed_seconds),
        )
        for key, value in optional:
            if value is not None:
                report[key] = value
        return report


class CanaryCleanupFailure(CanaryFailure):
    code = "cleanup_failed"
    public_message = "production canary cleanup failed"
    default_stage = "cleanup"


BUILD_PROMPT = (
    "Создай компактное MAX Mini App для списка ежедневных дел: заголовок "
    "«Мой день», три демонстрационные задачи и заметная кнопка «Добавить задачу»."
)
EDIT_PROMPT = "Точечно измени заголовок на «Мой продуктивный день» и сохрани остальной интерфейс."


_PREVIEW_SIGNATURE_PATTERN = re.compile(r"[A-Za-z0-9_-]{43}")
_DNS_LABEL_PATTERN = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?")
_DNS_TLD_PATTERN = re.compile(r"[a-z]{2,63}")
_REQUEST_TIMEOUT_SECONDS = 30.0
_CLEANUP_TIMEOUT_SECONDS = 10.0
_CANCEL_WAIT_SECONDS = 120.0
# AV23.1: a 409/503 on DELETE means "something of ours is still finishing";
# the same delete is retried with backoff until this deadline, never a purge.
_CLEANUP_RETRY_DEADLINE_SECONDS = 180.0
_CLEANUP_RETRY_BASE_SECONDS = 2.0
_CLEANUP_RETRY_MAX_SECONDS = 20.0
# Second bound: a sleep that does not advance the clock must not spin forever.
_CLEANUP_RETRY_MAX_ATTEMPTS = 12
_CLEANUP_RETRYABLE_STATUSES = frozenset({409, 425, 429, 502, 503, 504})
_ACTIVE_GENERATION_STATUSES = frozenset(
    {"pending", "queued_for_capacity", "running", "cancel_requested"}
)
_TERMINAL_GENERATION_STATUSES = frozenset({"completed", "failed", "cancelled"})


def validate_preview_host_suffix(host_suffix: str) -> str:
    labels = host_suffix[1:].split(".") if host_suffix.startswith(".") else []
    if (
        len(host_suffix) > 253
        or len(labels) < 3
        or any(_DNS_LABEL_PATTERN.fullmatch(label) is None for label in labels)
        or _DNS_TLD_PATTERN.fullmatch(labels[-1]) is None
    ):
        raise CanaryConfigurationError(
            "PRODUCTION_CANARY_PREVIEW_HOST_SUFFIX must be a specific lowercase DNS suffix"
        )
    return host_suffix


def validate_preview_url(url: str, host_suffix: str) -> SplitResult:
    parsed = urlsplit(url)
    try:
        query = parse_qs(parsed.query, keep_blank_values=True, strict_parsing=True)
        port = parsed.port
    except ValueError as exc:
        raise CanaryFailure("preview session URL is invalid") from exc
    hostname = parsed.hostname
    if (
        parsed.scheme != "https"
        or hostname is None
        or hostname == host_suffix.lstrip(".")
        or not hostname.endswith(host_suffix)
        or parsed.username is not None
        or parsed.password is not None
        or port not in {None, 443}
        or parsed.path != "/api/omnia/preview-session"
        or parsed.fragment
        or set(query) != {"expires", "signature"}
        or len(query["expires"]) != 1
        or not query["expires"][0].isdigit()
        or len(query["signature"]) != 1
        or _PREVIEW_SIGNATURE_PATTERN.fullmatch(query["signature"][0]) is None
    ):
        raise CanaryFailure("preview session URL is invalid")
    return parsed


def _component_env(component: str) -> str:
    return f"PRODUCTION_EXPECTED_{component.upper()}_RELEASE_SHA"


def _expected_releases_from_env() -> dict[str, str]:
    """Read one allowed revision per component.

    Production deploys the web image independently of the API image, so a
    single shared expectation cannot describe a correct release. The API image
    also runs the worker and the generation worker, so the generation worker
    falls back to the worker expectation unless it is pinned separately.
    """

    expected: dict[str, str] = {}
    for component in ("web", "api", "worker", "orchestrator"):
        name = _component_env(component)
        value = os.getenv(name)
        if not value:
            raise CanaryConfigurationError(f"missing required environment: {name}")
        if normalize_release_sha(value) == "unknown":
            raise CanaryConfigurationError(f"{name} is invalid")
        expected[component] = value
    generation_worker = os.getenv(_component_env("generation_worker"))
    if generation_worker:
        if normalize_release_sha(generation_worker) == "unknown":
            raise CanaryConfigurationError(f"{_component_env('generation_worker')} is invalid")
        expected["generation_worker"] = generation_worker
    else:
        expected["generation_worker"] = expected["worker"]
    return expected


@dataclass(frozen=True)
class CanaryConfig:
    base_url: str
    email: str
    password: str
    expected_releases: dict[str, str]
    preview_host_suffix: str
    overall_timeout_seconds: int
    poll_seconds: float

    @classmethod
    def from_env(cls) -> CanaryConfig:
        required = (
            "PRODUCTION_CANARY_EMAIL",
            "PRODUCTION_CANARY_PASSWORD",
        )
        missing = [name for name in required if not os.getenv(name)]
        if missing:
            raise CanaryConfigurationError(f"missing required environment: {', '.join(missing)}")
        expected_releases = _expected_releases_from_env()
        base_url = os.getenv(
            "PRODUCTION_CANARY_BASE_URL",
            "https://constructor.lead-generator.ru",
        )
        parsed_base = urlsplit(base_url)
        if (
            parsed_base.scheme != "https"
            or not parsed_base.hostname
            or parsed_base.username is not None
            or parsed_base.password is not None
            or parsed_base.query
            or parsed_base.fragment
            or parsed_base.path not in {"", "/"}
        ):
            raise CanaryConfigurationError("PRODUCTION_CANARY_BASE_URL must be an HTTPS origin")
        try:
            overall_timeout_seconds = int(os.getenv("PRODUCTION_CANARY_TIMEOUT_SECONDS", "2700"))
            poll_seconds = float(os.getenv("PRODUCTION_CANARY_POLL_SECONDS", "5"))
        except ValueError as exc:
            raise CanaryConfigurationError("canary time bounds must be numeric") from exc
        if not 300 <= overall_timeout_seconds <= 3600:
            raise CanaryConfigurationError("PRODUCTION_CANARY_TIMEOUT_SECONDS is out of bounds")
        if not 1 <= poll_seconds <= 30:
            raise CanaryConfigurationError("PRODUCTION_CANARY_POLL_SECONDS is out of bounds")
        preview_host_suffix = validate_preview_host_suffix(
            os.getenv(
                "PRODUCTION_CANARY_PREVIEW_HOST_SUFFIX",
                ".preview.lead-generator.ru",
            )
        )
        return cls(
            base_url=base_url.rstrip("/"),
            email=os.environ["PRODUCTION_CANARY_EMAIL"],
            password=os.environ["PRODUCTION_CANARY_PASSWORD"],
            expected_releases=expected_releases,
            preview_host_suffix=preview_host_suffix,
            overall_timeout_seconds=overall_timeout_seconds,
            poll_seconds=poll_seconds,
        )


@dataclass(frozen=True)
class CanaryResult:
    releases: dict[str, str]
    project_id: str
    build_run_id: str
    edit_run_id: str
    build_snapshot_id: str
    edit_snapshot_id: str
    cleanup_complete: bool
    elapsed_seconds: float


EventEmitter = Callable[[dict[str, object]], None]


class ProductionCanary:
    def __init__(
        self,
        config: CanaryConfig,
        *,
        transport: httpx.BaseTransport | None = None,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
        emit: EventEmitter | None = None,
    ) -> None:
        self.config = config
        self._clock = clock
        self._sleep = sleep
        self._emit = emit or (lambda _event: None)
        self._client = httpx.Client(
            base_url=config.base_url,
            timeout=30,
            follow_redirects=False,
            transport=transport,
            headers={"User-Agent": "omnia-production-canary/1"},
        )
        self._started_at = 0.0
        self._deadline = 0.0
        self._stage = "unknown"

    def run(self) -> CanaryResult:
        self._started_at = self._clock()
        self._deadline = self._started_at + self.config.overall_timeout_seconds
        project_id: str | None = None
        logged_in = False
        result: CanaryResult | None = None
        caught: BaseException | None = None
        cleanup_failure: CanaryCleanupFailure | None = None
        try:
            self._stage = "release_health"
            releases = self._assert_release_health()
            self._stage = "login"
            self._request_json(
                "POST",
                "/api/auth/login",
                json={"email": self.config.email, "password": self.config.password},
            )
            logged_in = True
            self._event("login", "ok")

            self._stage = "project_create"
            project = self._request_json(
                "POST",
                "/api/projects",
                json={
                    "name": f"Production generation canary {uuid4().hex[:8]}",
                    "template": "max_miniapp",
                },
            )
            project_id = self._required_uuid(project, "id", code="project_create_failed")
            self._created_project_id = project_id  # the only project cleanup may delete
            seed_snapshot_id = self._required_uuid(
                project,
                "current_snapshot_id",
                code="project_create_failed",
            )
            self._event("project_create", "ok", project_id=project_id)

            self._stage = "build"
            build_run_id = self._start_prompt(project_id, BUILD_PROMPT, "build")
            build_run = self._poll_generation(project_id, build_run_id)
            if build_run.get("response_mode") != "build":
                raise self._fail(
                    "build run returned the wrong response mode",
                    code="build_failed",
                )
            build_snapshot_id = self._assert_new_snapshot(project_id, seed_snapshot_id)

            self._stage = "runtime_start"
            runtime = self._request_json(
                "POST",
                f"/api/projects/{project_id}/runtime/start",
            )
            if runtime.get("state") != "running":
                raise self._fail("runtime did not start", code="runtime_failed")
            self._event("runtime_start", "ok", project_id=project_id)

            self._stage = "preview"
            preview = self._request_json(
                "POST",
                f"/api/projects/{project_id}/max/preview-session",
            )
            bootstrap_url = preview.get("url")
            if not isinstance(bootstrap_url, str):
                raise self._fail(
                    "preview session response is invalid",
                    code="preview_failed",
                )
            self._verify_preview(bootstrap_url)
            self._event("preview", "ok", project_id=project_id)

            self._stage = "edit"
            edit_run_id = self._start_prompt(project_id, EDIT_PROMPT, "edit")
            edit_run = self._poll_generation(project_id, edit_run_id)
            if edit_run.get("response_mode") != "edit":
                raise self._fail(
                    "edit run returned the wrong response mode",
                    code="edit_failed",
                )
            edit_snapshot_id = self._assert_new_snapshot(project_id, build_snapshot_id)

            self._stage = "final_release_health"
            self._assert_release_health(baseline=releases)
            result = CanaryResult(
                releases=releases,
                project_id=project_id,
                build_run_id=build_run_id,
                edit_run_id=edit_run_id,
                build_snapshot_id=build_snapshot_id,
                edit_snapshot_id=edit_snapshot_id,
                cleanup_complete=True,
                elapsed_seconds=round(max(0.0, self._clock() - self._started_at), 3),
            )
        except BaseException as exc:
            caught = exc
        finally:
            self._stage = "cleanup"
            if project_id is not None:
                # An active run makes deletion return 409 and would keep building.
                self._cancel_active_generation(project_id)
                if not self._delete_project_with_retry(project_id):
                    cleanup_failure = CanaryCleanupFailure("project cleanup failed")
            if logged_in and not self._request_has_status(
                "POST",
                "/api/auth/logout",
                expected_status=204,
            ):
                cleanup_failure = CanaryCleanupFailure("logout cleanup failed")
                self._event("logout", "failed", error_code=cleanup_failure.code)
            elif logged_in:
                self._event("logout", "ok")
            self._client.close()

        if caught is not None:
            if isinstance(caught, CanaryFailure):
                self._finalize_failure(caught, cleanup_failed=cleanup_failure is not None)
            raise caught
        if cleanup_failure is not None:
            self._finalize_failure(cleanup_failure, cleanup_failed=True)
            raise cleanup_failure
        if result is None:
            raise self._fail("canary did not produce a result", code="canary_failed")
        self._event("canary", "ok", project_id=result.project_id)
        return result

    def _finalize_failure(self, failure: CanaryFailure, *, cleanup_failed: bool) -> None:
        """Stamp the outcome the workflow artifact reports for this run."""

        failure.cleanup = "failed" if cleanup_failed else "ok"
        failure.elapsed_seconds = round(max(0.0, self._clock() - self._started_at), 3)
        self._emit({"step": "canary", "status": "failed"} | failure.diagnostics())

    def _fail(
        self,
        message: str,
        *,
        code: str,
        component: str | None = None,
        expected: str | None = None,
        actual: str | None = None,
        http_status: int | None = None,
    ) -> CanaryFailure:
        return CanaryFailure(
            message,
            stage=self._stage,
            code=code,
            component=component,
            expected=expected,
            actual=actual,
            http_status=http_status,
        )

    def _request_json(
        self,
        method: str,
        path: str,
        *,
        json: object | None = None,
    ) -> dict[str, object]:
        try:
            response = self._client.request(
                method,
                path,
                json=json,
                timeout=self._request_timeout(),
            )
        except httpx.HTTPError as exc:
            raise self._fail("public API request failed", code=self._request_code()) from exc
        if not 200 <= response.status_code < 300:
            raise self._fail(
                "public API returned an unexpected status",
                code=self._request_code(),
                http_status=response.status_code,
            )
        try:
            payload: Any = response.json()
        except ValueError as exc:
            raise self._fail(
                "public API returned invalid JSON",
                code=self._request_code(invalid=True),
                http_status=response.status_code,
            ) from exc
        if not isinstance(payload, dict):
            raise self._fail(
                "public API returned an invalid payload",
                code=self._request_code(invalid=True),
                http_status=response.status_code,
            )
        return payload

    def _request_code(self, *, invalid: bool = False) -> str:
        codes = _STAGE_REQUEST_CODES.get(self._stage, ("api_http_error", "api_invalid_response"))
        return codes[1] if invalid else codes[0]

    def _request_has_status(
        self,
        method: str,
        path: str,
        *,
        expected_status: int,
    ) -> bool:
        try:
            response = self._client.request(
                method,
                path,
                timeout=_CLEANUP_TIMEOUT_SECONDS,
            )
        except httpx.HTTPError:
            return False
        return response.status_code == expected_status

    def _request_status(self, method: str, path: str) -> int | None:
        try:
            response = self._client.request(method, path, timeout=_CLEANUP_TIMEOUT_SECONDS)
        except httpx.HTTPError:
            return None
        return response.status_code

    def _delete_project_with_retry(self, project_id: str) -> bool:
        """AV23.1: retry the exact same DELETE while the API says "busy".

        409 (a release/restoration of this very project is still finishing) and
        503 ("удаление ещё не подтверждено, повторите") are retried with jittered
        backoff until a cleanup deadline; 404 means already gone. Anything else
        is a definitive refusal. Only the project this run created is ever
        deleted, and no broader purge is attempted."""
        if project_id != getattr(self, "_created_project_id", None):
            self._event(
                "project_delete", "refused", project_id=project_id,
                error_code="foreign_project",
            )
            return False
        deadline = self._clock() + _CLEANUP_RETRY_DEADLINE_SECONDS
        attempt = 0
        while True:
            attempt += 1
            status = self._request_status("DELETE", f"/api/projects/{project_id}")
            if status in {204, 404}:
                self._event("project_delete", "ok", project_id=project_id, attempts=attempt)
                return True
            retryable = status is None or status in _CLEANUP_RETRYABLE_STATUSES
            exhausted = self._clock() >= deadline or attempt >= _CLEANUP_RETRY_MAX_ATTEMPTS
            if not retryable or exhausted:
                self._event(
                    "project_delete", "failed", project_id=project_id,
                    error_code="cleanup_deadline" if retryable else "project_delete_rejected",
                    attempts=attempt, http_status=status,
                )
                return False
            if status == 409:
                # Our own unfinished work: stop it, then retry the same delete.
                self._cancel_active_generation(project_id)
                self._cancel_active_restoration(project_id)
            self._event(
                "project_delete", "retry", project_id=project_id,
                attempts=attempt, http_status=status,
            )
            backoff = min(
                _CLEANUP_RETRY_BASE_SECONDS * (2 ** (attempt - 1)), _CLEANUP_RETRY_MAX_SECONDS
            )
            self._sleep(backoff * (0.75 + 0.5 * random.random()))

    def _cancel_active_restoration(self, project_id: str) -> None:
        """Best effort: a QA restoration of this project blocks its deletion."""
        try:
            response = self._client.request(
                "GET", f"/api/projects/{project_id}/restorations", timeout=_CLEANUP_TIMEOUT_SECONDS,
            )
            payload = response.json() if response.status_code == 200 else None
        except (httpx.HTTPError, ValueError):
            return
        items = payload.get("items") if isinstance(payload, dict) else None
        for item in items or []:
            if isinstance(item, dict) and item.get("can_cancel") is True:
                cancelled = self._request_has_status(
                    "POST",
                    f"/api/projects/{project_id}/restorations/{item.get('id')}/cancel",
                    expected_status=200,
                )
                self._event(
                    "restoration_cancel",
                    "ok" if cancelled else "failed",
                    project_id=project_id,
                    error_code=None if cancelled else "restoration_cancel_rejected",
                )

    def _generation_status(self, project_id: str) -> object:
        try:
            response = self._client.request(
                "GET",
                f"/api/projects/{project_id}/generation",
                timeout=_CLEANUP_TIMEOUT_SECONDS,
            )
            payload = response.json() if response.status_code == 200 else None
        except (httpx.HTTPError, ValueError):
            return None
        return payload.get("status") if isinstance(payload, dict) else None

    def _cancel_active_generation(self, project_id: str) -> None:
        """Best effort: stop this canary's own run so its project can be deleted."""

        # Unknown statuses are cancelled too: only a known terminal run is safe to leave.
        if self._generation_status(project_id) in _TERMINAL_GENERATION_STATUSES | {None}:
            return
        if not self._request_has_status(
            "POST",
            f"/api/projects/{project_id}/generation/cancel",
            expected_status=202,
        ):
            self._event("generation_cancel", "failed", project_id=project_id)
            return
        waited_until = self._clock() + _CANCEL_WAIT_SECONDS
        while self._generation_status(project_id) not in _TERMINAL_GENERATION_STATUSES:
            if self._clock() >= waited_until:
                self._event("generation_cancel", "failed", project_id=project_id)
                return
            self._sleep(self.config.poll_seconds)
        self._event("generation_cancel", "ok", project_id=project_id)

    def _start_prompt(self, project_id: str, prompt: str, expected_mode: str) -> str:
        response = self._request_json(
            "POST",
            f"/api/projects/{project_id}/prompt",
            json={
                "prompt": prompt,
                "idempotency_key": str(uuid4()),
                "skip_clarify": True,
            },
        )
        run_id = self._required_uuid(response, "run_id", code=f"{expected_mode}_failed")
        if response.get("mode") != expected_mode:
            raise self._fail(
                "prompt returned the wrong generation mode",
                code=f"{expected_mode}_failed",
            )
        self._event(
            f"{expected_mode}_start",
            "ok",
            project_id=project_id,
            run_id=run_id,
        )
        return run_id

    def _poll_generation(self, project_id: str, run_id: str) -> dict[str, object]:
        while True:
            run = self._request_json("GET", f"/api/projects/{project_id}/generation")
            if run.get("id") != run_id:
                raise self._fail(
                    "latest generation run identity changed",
                    code=self._request_code(),
                )
            status = run.get("status")
            if status == "completed":
                self._event(
                    "generation",
                    "ok",
                    project_id=project_id,
                    run_id=run_id,
                )
                return run
            if status in {"failed", "cancelled"}:
                raise self._fail(
                    "generation reached a failed terminal status",
                    code=self._request_code(),
                )
            if status not in _ACTIVE_GENERATION_STATUSES:
                raise self._fail(
                    "generation returned an invalid status",
                    code=self._request_code(invalid=True),
                )
            remaining = self._remaining_seconds()
            if remaining <= 0:
                raise self._fail("generation deadline exceeded", code="timeout")
            self._sleep(min(self.config.poll_seconds, remaining))

    def _observe_releases(self) -> dict[str, str]:
        """Read the running revision of every checked component."""

        web = self._request_json("GET", "/web-health")
        api = self._request_json("GET", "/api/health")
        observed: dict[str, str] = {}
        for service, payload in (("web", web), ("api", api)):
            release_sha = payload.get("release_sha")
            if (
                payload.get("status") != "ok"
                or payload.get("service") != service
                or not isinstance(release_sha, str)
            ):
                raise self._fail(
                    "release health identity mismatch",
                    code="release_unhealthy",
                    component=service,
                )
            observed[service] = release_sha
        checks = api.get("checks")
        if not isinstance(checks, dict) or not checks:
            raise self._fail(
                "release dependency health mismatch",
                code="release_unhealthy",
                component="api",
            )
        for name, value in sorted(checks.items()):
            if value != "ok":
                raise self._fail(
                    "release dependency health mismatch",
                    code="release_unhealthy",
                    component=str(name),
                )
        dependencies = api.get("dependencies")
        if not isinstance(dependencies, dict):
            raise self._fail(
                "release dependency health mismatch",
                code="release_unhealthy",
                component="api",
            )
        for component in ("worker", "generation_worker", "orchestrator"):
            release_sha = dependencies.get(f"{component}_release_sha")
            if not isinstance(release_sha, str):
                raise self._fail(
                    "release dependency health mismatch",
                    code="release_unhealthy",
                    component=component,
                )
            observed[component] = release_sha
        return observed

    def _assert_release_health(self, baseline: dict[str, str] | None = None) -> dict[str, str]:
        observed = self._observe_releases()
        for component in COMPONENTS:
            if baseline is not None and observed[component] != baseline[component]:
                raise self._fail(
                    "release changed during canary",
                    code="release_changed",
                    component=component,
                    expected=baseline[component],
                    actual=observed[component],
                )
            expected = self.config.expected_releases[component]
            if observed[component] != expected:
                raise self._fail(
                    "release health identity mismatch",
                    code="release_mismatch",
                    component=component,
                    expected=expected,
                    actual=observed[component],
                )
        self._event(self._stage, "ok")
        return observed

    def _assert_new_snapshot(self, project_id: str, previous_snapshot_id: str) -> str:
        project = self._request_json("GET", f"/api/projects/{project_id}")
        snapshot_id = self._required_uuid(
            project,
            "current_snapshot_id",
            code="snapshot_failed",
        )
        if snapshot_id == previous_snapshot_id:
            raise self._fail(
                "generation did not advance the project snapshot",
                code="snapshot_failed",
            )
        snapshot = self._request_json(
            "GET",
            f"/api/projects/{project_id}/snapshots/{snapshot_id}",
        )
        if snapshot.get("id") != snapshot_id or snapshot.get("project_id") != project_id:
            raise self._fail("snapshot identity mismatch", code="snapshot_failed")
        files = snapshot.get("files")
        if not isinstance(files, dict) or not files:
            raise self._fail("generated snapshot has no files", code="snapshot_failed")
        self._event(
            "snapshot",
            "ok",
            project_id=project_id,
            snapshot_id=snapshot_id,
        )
        return snapshot_id

    def _verify_preview(self, bootstrap_url: str) -> None:
        try:
            parsed = validate_preview_url(bootstrap_url, self.config.preview_host_suffix)
        except CanaryFailure as exc:
            raise self._fail(str(exc), code="preview_failed") from exc
        try:
            bootstrap = self._client.get(
                bootstrap_url,
                timeout=self._request_timeout(),
            )
        except httpx.HTTPError as exc:
            raise self._fail("preview bootstrap request failed", code="preview_failed") from exc
        if bootstrap.status_code != 307 or bootstrap.headers.get("location") != "/":
            raise self._fail(
                "preview bootstrap contract failed",
                code="preview_failed",
                http_status=bootstrap.status_code,
            )
        origin_root = urlunsplit((parsed.scheme, parsed.netloc, "/", "", ""))
        try:
            preview = self._client.get(
                origin_root,
                timeout=self._request_timeout(),
            )
        except httpx.HTTPError as exc:
            raise self._fail("preview request failed", code="preview_failed") from exc
        if preview.status_code != 200:
            raise self._fail(
                "preview did not become ready",
                code="preview_failed",
                http_status=preview.status_code,
            )

    def _required_uuid(
        self,
        payload: dict[str, object],
        key: str,
        *,
        code: str,
    ) -> str:
        value = payload.get(key)
        if not isinstance(value, str):
            raise self._fail(f"{key} is not a valid identifier", code=code)
        try:
            return str(UUID(value))
        except ValueError as exc:
            raise self._fail(f"{key} is not a valid identifier", code=code) from exc

    def _remaining_seconds(self) -> float:
        return self._deadline - self._clock()

    def _request_timeout(self) -> float:
        remaining = self._remaining_seconds()
        if remaining <= 0:
            raise self._fail("canary deadline exceeded", code="timeout")
        return min(_REQUEST_TIMEOUT_SECONDS, remaining)

    def _event(
        self,
        step: str,
        status: str,
        *,
        project_id: str | None = None,
        run_id: str | None = None,
        snapshot_id: str | None = None,
        error_code: str | None = None,
        attempts: int | None = None,
        http_status: int | None = None,
    ) -> None:
        event: dict[str, object] = {
            "step": step,
            "status": status,
            "elapsed_seconds": round(max(0.0, self._clock() - self._started_at), 3),
        }
        if project_id is not None:
            event["project_id"] = project_id
        if run_id is not None:
            event["run_id"] = run_id
        if snapshot_id is not None:
            event["snapshot_id"] = snapshot_id
        if error_code is not None:
            event["error_code"] = error_code
        if attempts is not None:
            event["attempts"] = attempts
        if http_status is not None:
            event["http_status"] = http_status
        self._emit(event)

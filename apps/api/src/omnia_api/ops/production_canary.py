from __future__ import annotations

import os
import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any
from urllib.parse import SplitResult, parse_qs, urlsplit, urlunsplit
from uuid import UUID, uuid4

import httpx

from omnia_api.core.release import normalize_release_sha


class CanaryConfigurationError(ValueError):
    pass


class CanaryFailure(RuntimeError):
    code = "canary_failed"
    public_message = "production canary failed"


class CanaryCleanupFailure(CanaryFailure):
    code = "cleanup_failed"
    public_message = "production canary cleanup failed"


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


@dataclass(frozen=True)
class CanaryConfig:
    base_url: str
    email: str
    password: str
    expected_release_sha: str
    preview_host_suffix: str
    overall_timeout_seconds: int
    poll_seconds: float

    @classmethod
    def from_env(cls) -> CanaryConfig:
        required = (
            "PRODUCTION_CANARY_EMAIL",
            "PRODUCTION_CANARY_PASSWORD",
            "PRODUCTION_EXPECTED_RELEASE_SHA",
        )
        missing = [name for name in required if not os.getenv(name)]
        if missing:
            raise CanaryConfigurationError(f"missing required environment: {', '.join(missing)}")
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
        expected_release_sha = normalize_release_sha(os.environ["PRODUCTION_EXPECTED_RELEASE_SHA"])
        if expected_release_sha == "unknown":
            raise CanaryConfigurationError("PRODUCTION_EXPECTED_RELEASE_SHA is invalid")
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
            expected_release_sha=expected_release_sha,
            preview_host_suffix=preview_host_suffix,
            overall_timeout_seconds=overall_timeout_seconds,
            poll_seconds=poll_seconds,
        )


@dataclass(frozen=True)
class CanaryResult:
    release_sha: str
    project_id: str
    build_run_id: str
    edit_run_id: str
    build_snapshot_id: str
    edit_snapshot_id: str
    cleanup_complete: bool


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

    def run(self) -> CanaryResult:
        self._started_at = self._clock()
        self._deadline = self._started_at + self.config.overall_timeout_seconds
        project_id: str | None = None
        logged_in = False
        result: CanaryResult | None = None
        caught: BaseException | None = None
        cleanup_failure: CanaryCleanupFailure | None = None
        try:
            release_sha = self._assert_release_health()
            self._request_json(
                "POST",
                "/api/auth/login",
                json={"email": self.config.email, "password": self.config.password},
            )
            logged_in = True
            self._event("login", "ok")

            project = self._request_json(
                "POST",
                "/api/projects",
                json={
                    "name": f"Production generation canary {uuid4().hex[:8]}",
                    "template": "max_miniapp",
                },
            )
            project_id = self._required_uuid(project, "id", code="project_invalid")
            seed_snapshot_id = self._required_uuid(
                project,
                "current_snapshot_id",
                code="project_invalid",
            )
            self._event("project_create", "ok", project_id=project_id)

            build_run_id = self._start_prompt(project_id, BUILD_PROMPT, "build")
            build_run = self._poll_generation(project_id, build_run_id)
            if build_run.get("response_mode") != "build":
                raise CanaryFailure("build run returned the wrong response mode")
            build_snapshot_id = self._assert_new_snapshot(project_id, seed_snapshot_id)

            runtime = self._request_json(
                "POST",
                f"/api/projects/{project_id}/runtime/start",
            )
            if runtime.get("state") != "running":
                raise CanaryFailure("runtime did not start")
            self._event("runtime_start", "ok", project_id=project_id)

            preview = self._request_json(
                "POST",
                f"/api/projects/{project_id}/max/preview-session",
            )
            bootstrap_url = preview.get("url")
            if not isinstance(bootstrap_url, str):
                raise CanaryFailure("preview session response is invalid")
            self._verify_preview(bootstrap_url)
            self._event("preview", "ok", project_id=project_id)

            edit_run_id = self._start_prompt(project_id, EDIT_PROMPT, "edit")
            edit_run = self._poll_generation(project_id, edit_run_id)
            if edit_run.get("response_mode") != "edit":
                raise CanaryFailure("edit run returned the wrong response mode")
            edit_snapshot_id = self._assert_new_snapshot(project_id, build_snapshot_id)

            final_release_sha = self._assert_release_health()
            if final_release_sha != release_sha:
                raise CanaryFailure("release changed during canary")
            result = CanaryResult(
                release_sha=release_sha,
                project_id=project_id,
                build_run_id=build_run_id,
                edit_run_id=edit_run_id,
                build_snapshot_id=build_snapshot_id,
                edit_snapshot_id=edit_snapshot_id,
                cleanup_complete=True,
            )
        except BaseException as exc:
            caught = exc
        finally:
            if project_id is not None:
                if not self._request_has_status(
                    "DELETE",
                    f"/api/projects/{project_id}",
                    expected_status=204,
                ):
                    cleanup_failure = CanaryCleanupFailure("project cleanup failed")
                    self._event(
                        "project_delete",
                        "failed",
                        project_id=project_id,
                        error_code=cleanup_failure.code,
                    )
                else:
                    self._event("project_delete", "ok", project_id=project_id)
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

        if cleanup_failure is not None:
            raise cleanup_failure from caught
        if caught is not None:
            if isinstance(caught, CanaryFailure):
                self._event("canary", "failed", error_code=caught.code)
            raise caught
        if result is None:
            raise CanaryFailure("canary did not produce a result")
        self._event("canary", "ok", project_id=result.project_id)
        return result

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
            raise CanaryFailure("public API request failed") from exc
        if not 200 <= response.status_code < 300:
            raise CanaryFailure("public API returned an unexpected status")
        try:
            payload: Any = response.json()
        except ValueError as exc:
            raise CanaryFailure("public API returned invalid JSON") from exc
        if not isinstance(payload, dict):
            raise CanaryFailure("public API returned an invalid payload")
        return payload

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
        run_id = self._required_uuid(response, "run_id", code="generation_invalid")
        if response.get("mode") != expected_mode:
            raise CanaryFailure("prompt returned the wrong generation mode")
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
                raise CanaryFailure("latest generation run identity changed")
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
                raise CanaryFailure("generation reached a failed terminal status")
            if status not in {"pending", "running", "cancel_requested"}:
                raise CanaryFailure("generation returned an invalid status")
            remaining = self._remaining_seconds()
            if remaining <= 0:
                raise CanaryFailure("generation deadline exceeded")
            self._sleep(min(self.config.poll_seconds, remaining))

    def _assert_release_health(self) -> str:
        web = self._request_json("GET", "/web-health")
        api = self._request_json("GET", "/api/health")
        expected = self.config.expected_release_sha
        if (
            web.get("status") != "ok"
            or web.get("service") != "web"
            or web.get("release_sha") != expected
            or api.get("status") != "ok"
            or api.get("service") != "api"
            or api.get("release_sha") != expected
        ):
            raise CanaryFailure("release health identity mismatch")
        checks = api.get("checks")
        dependencies = api.get("dependencies")
        if (
            not isinstance(checks, dict)
            or not checks
            or any(value != "ok" for value in checks.values())
            or not isinstance(dependencies, dict)
            or dependencies.get("worker_release_sha") != expected
            or dependencies.get("orchestrator_release_sha") != expected
        ):
            raise CanaryFailure("release dependency health mismatch")
        self._event("release_health", "ok")
        return expected

    def _assert_new_snapshot(self, project_id: str, previous_snapshot_id: str) -> str:
        project = self._request_json("GET", f"/api/projects/{project_id}")
        snapshot_id = self._required_uuid(
            project,
            "current_snapshot_id",
            code="snapshot_invalid",
        )
        if snapshot_id == previous_snapshot_id:
            raise CanaryFailure("generation did not advance the project snapshot")
        snapshot = self._request_json(
            "GET",
            f"/api/projects/{project_id}/snapshots/{snapshot_id}",
        )
        if snapshot.get("id") != snapshot_id or snapshot.get("project_id") != project_id:
            raise CanaryFailure("snapshot identity mismatch")
        files = snapshot.get("files")
        if not isinstance(files, dict) or not files:
            raise CanaryFailure("generated snapshot has no files")
        self._event(
            "snapshot",
            "ok",
            project_id=project_id,
            snapshot_id=snapshot_id,
        )
        return snapshot_id

    def _verify_preview(self, bootstrap_url: str) -> None:
        parsed = validate_preview_url(bootstrap_url, self.config.preview_host_suffix)
        try:
            bootstrap = self._client.get(
                bootstrap_url,
                timeout=self._request_timeout(),
            )
        except httpx.HTTPError as exc:
            raise CanaryFailure("preview bootstrap request failed") from exc
        if bootstrap.status_code != 307 or bootstrap.headers.get("location") != "/":
            raise CanaryFailure("preview bootstrap contract failed")
        origin_root = urlunsplit((parsed.scheme, parsed.netloc, "/", "", ""))
        try:
            preview = self._client.get(
                origin_root,
                timeout=self._request_timeout(),
            )
        except httpx.HTTPError as exc:
            raise CanaryFailure("preview request failed") from exc
        if preview.status_code != 200:
            raise CanaryFailure("preview did not become ready")

    def _required_uuid(
        self,
        payload: dict[str, object],
        key: str,
        *,
        code: str,
    ) -> str:
        value = payload.get(key)
        if not isinstance(value, str):
            raise CanaryFailure(code)
        try:
            return str(UUID(value))
        except ValueError as exc:
            raise CanaryFailure(code) from exc

    def _remaining_seconds(self) -> float:
        return self._deadline - self._clock()

    def _request_timeout(self) -> float:
        remaining = self._remaining_seconds()
        if remaining <= 0:
            raise CanaryFailure("canary deadline exceeded")
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
        self._emit(event)

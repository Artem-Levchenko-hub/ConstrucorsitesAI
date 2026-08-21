from __future__ import annotations

import json
import os
import sys
import time
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

import httpx

from omnia_api.ops.production_canary import CanaryConfig, CanaryFailure

BUILD_PROMPT = (
    "Создай компактное MAX Mini App для списка ежедневных дел: заголовок "
    "«Мой день», три демонстрационные задачи и заметная кнопка «Добавить задачу»."
)
EDIT_PROMPT = "Точечно измени заголовок на «Мой продуктивный день» и сохрани остальной интерфейс."
CANCEL_PROMPT = "Измени текст кнопки добавления задачи на «Новая задача»."

_REQUEST_TIMEOUT_SECONDS = 30.0
_CLEANUP_TIMEOUT_SECONDS = 10.0
_ACTIVE_STATUSES = {"pending", "running", "cancel_requested"}
_TERMINAL_STATUSES = {"completed", "failed", "cancelled"}


class DevGenerationTelegramAcceptance:
    def __init__(
        self,
        config: CanaryConfig,
        *,
        include_cancel: bool = False,
        transport: httpx.BaseTransport | None = None,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
        wall_clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self.config = config
        self.include_cancel = include_cancel
        self._clock = clock
        self._sleep = sleep
        self._wall_clock = wall_clock
        self._client = httpx.Client(
            base_url=config.base_url,
            timeout=_REQUEST_TIMEOUT_SECONDS,
            follow_redirects=False,
            transport=transport,
            headers={"User-Agent": "omnia-dev-generation-telegram-acceptance/1"},
        )
        self._deadline = 0.0
        self.records: list[dict[str, object]] = []
        self.cleanup_complete = False

    def run(self) -> dict[str, object]:
        self._deadline = self._clock() + self.config.overall_timeout_seconds
        project_id: str | None = None
        logged_in = False
        caught: BaseException | None = None
        cleanup_failed = False
        try:
            self._assert_api_health()
            self._request_json(
                "POST",
                "/api/auth/login",
                json={"email": self.config.email, "password": self.config.password},
            )
            logged_in = True
            project = self._request_json(
                "POST",
                "/api/projects",
                json={
                    "name": f"Development Telegram acceptance {uuid4().hex[:8]}",
                    "template": "max_miniapp",
                },
            )
            project_id = self._required_uuid(project, "id")
            seed_snapshot_id = self._required_uuid(project, "current_snapshot_id")

            build_snapshot_id = self._exercise_generation(
                project_id,
                prompt=BUILD_PROMPT,
                expected_mode="build",
                expected_status="completed",
                previous_snapshot_id=seed_snapshot_id,
            )
            edit_snapshot_id = self._exercise_generation(
                project_id,
                prompt=EDIT_PROMPT,
                expected_mode="edit",
                expected_status="completed",
                previous_snapshot_id=build_snapshot_id,
            )
            if self.include_cancel:
                self._exercise_generation(
                    project_id,
                    prompt=CANCEL_PROMPT,
                    expected_mode="edit",
                    expected_status="cancelled",
                    previous_snapshot_id=edit_snapshot_id,
                )
        except BaseException as exc:
            caught = exc
        finally:
            if project_id is not None and not self._cleanup_request(
                "DELETE",
                f"/api/projects/{project_id}",
                expected_status=204,
            ):
                cleanup_failed = True
            if logged_in and not self._cleanup_request(
                "POST",
                "/api/auth/logout",
                expected_status=204,
            ):
                cleanup_failed = True
            self._client.close()
            self.cleanup_complete = project_id is not None and logged_in and not cleanup_failed

        if cleanup_failed:
            raise CanaryFailure("cleanup failed") from caught
        if caught is not None:
            if isinstance(caught, CanaryFailure):
                raise caught
            raise CanaryFailure("development Telegram acceptance failed") from caught
        if not self.cleanup_complete:
            raise CanaryFailure("cleanup failed")
        return self._summary()

    def _exercise_generation(
        self,
        project_id: str,
        *,
        prompt: str,
        expected_mode: str,
        expected_status: str,
        previous_snapshot_id: str,
    ) -> str:
        started_at = self._timestamp()
        response = self._request_json(
            "POST",
            f"/api/projects/{project_id}/prompt",
            json={
                "prompt": prompt,
                "idempotency_key": str(uuid4()),
                "skip_clarify": True,
            },
        )
        run_id = self._required_uuid(response, "run_id")
        if response.get("mode") != expected_mode:
            raise CanaryFailure("generation returned an unexpected mode")
        if expected_status == "cancelled":
            self._request_json("POST", f"/api/projects/{project_id}/generation/cancel")

        terminal = self._poll_generation(project_id, run_id)
        mode = terminal.get("response_mode")
        status = terminal.get("status")
        if mode != expected_mode or not isinstance(status, str):
            raise CanaryFailure("generation returned an unexpected mode")

        snapshot_id = previous_snapshot_id
        snapshot_ready = False
        preview_ready = False
        if status == "completed" and expected_status == "completed":
            snapshot_id, preview_ready = self._await_snapshot_preview(
                project_id,
                previous_snapshot_id,
            )
            snapshot_ready = True

        record = {
            "run_id": run_id,
            "mode": expected_mode,
            "terminal_status": status,
            "snapshot": snapshot_ready,
            "preview": preview_ready,
            "started_at": started_at,
            "finished_at": self._timestamp(),
        }
        self.records.append(record)
        if status != expected_status:
            raise CanaryFailure("generation reached an unexpected terminal state")
        return snapshot_id

    def _poll_generation(self, project_id: str, run_id: str) -> dict[str, object]:
        while True:
            run = self._request_json("GET", f"/api/projects/{project_id}/generation")
            if run.get("id") != run_id:
                raise CanaryFailure("generation identity changed")
            status = run.get("status")
            if status in _TERMINAL_STATUSES:
                return run
            if status not in _ACTIVE_STATUSES:
                raise CanaryFailure("generation returned an invalid state")
            self._bounded_sleep()

    def _await_snapshot_preview(
        self,
        project_id: str,
        previous_snapshot_id: str,
    ) -> tuple[str, bool]:
        snapshot_id: str | None = None
        while True:
            project = self._request_json("GET", f"/api/projects/{project_id}")
            candidate = self._required_uuid(project, "current_snapshot_id")
            if candidate != previous_snapshot_id:
                snapshot_id = candidate
                break
            self._bounded_sleep()

        while True:
            snapshot = self._request_json(
                "GET",
                f"/api/projects/{project_id}/snapshots/{snapshot_id}",
            )
            if snapshot.get("id") != snapshot_id or snapshot.get("project_id") != project_id:
                raise CanaryFailure("snapshot identity mismatch")
            preview_url = snapshot.get("preview_url")
            if isinstance(preview_url, str) and preview_url:
                return snapshot_id, True
            self._bounded_sleep()

    def _assert_api_health(self) -> None:
        health = self._request_json("GET", "/api/health")
        if (
            health.get("status") != "ok"
            or health.get("service") != "api"
            or health.get("release_sha") != self.config.expected_release_sha
        ):
            raise CanaryFailure("release health identity mismatch")

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

    def _cleanup_request(self, method: str, path: str, *, expected_status: int) -> bool:
        try:
            response = self._client.request(method, path, timeout=_CLEANUP_TIMEOUT_SECONDS)
        except httpx.HTTPError:
            return False
        return response.status_code == expected_status

    def _required_uuid(self, payload: dict[str, object], key: str) -> str:
        value = payload.get(key)
        if not isinstance(value, str):
            raise CanaryFailure("public API returned an invalid identifier")
        try:
            return str(UUID(value))
        except ValueError as exc:
            raise CanaryFailure("public API returned an invalid identifier") from exc

    def _bounded_sleep(self) -> None:
        remaining = self._remaining_seconds()
        if remaining <= 0:
            raise CanaryFailure("acceptance deadline exceeded")
        self._sleep(min(self.config.poll_seconds, remaining))

    def _remaining_seconds(self) -> float:
        return self._deadline - self._clock()

    def _request_timeout(self) -> float:
        remaining = self._remaining_seconds()
        if remaining <= 0:
            raise CanaryFailure("acceptance deadline exceeded")
        return min(_REQUEST_TIMEOUT_SECONDS, remaining)

    def _timestamp(self) -> str:
        return self._wall_clock().astimezone(UTC).isoformat()

    def _summary(self) -> dict[str, object]:
        return {"cleanup": self.cleanup_complete, "runs": list(self.records)}


def _env_flag(name: str) -> bool:
    return os.getenv(name, "").strip().lower() in {"1", "true", "yes", "on"}


def main() -> int:
    runner: DevGenerationTelegramAcceptance | None = None
    try:
        runner = DevGenerationTelegramAcceptance(
            CanaryConfig.from_env(),
            include_cancel=_env_flag("DEV_TELEGRAM_ACCEPTANCE_CANCEL"),
        )
        print(json.dumps(runner.run(), ensure_ascii=False, sort_keys=True))
        return 0
    except BaseException:
        payload = {
            "cleanup": bool(getattr(runner, "cleanup_complete", False)),
            "runs": list(getattr(runner, "records", [])),
        }
        print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
        print("development Telegram acceptance failed", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

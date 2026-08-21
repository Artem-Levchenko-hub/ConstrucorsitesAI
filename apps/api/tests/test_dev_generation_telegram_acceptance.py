from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import ClassVar

import httpx
import pytest

from omnia_api.ops.production_canary import CanaryConfig, CanaryFailure

PROJECT_ID = "10000000-0000-4000-8000-000000000001"
SEED_ID = "20000000-0000-4000-8000-000000000001"
BUILD_RUN = "30000000-0000-4000-8000-000000000001"
EDIT_RUN = "30000000-0000-4000-8000-000000000002"
CANCEL_RUN = "30000000-0000-4000-8000-000000000003"
BUILD_SNAPSHOT = "40000000-0000-4000-8000-000000000001"
EDIT_SNAPSHOT = "40000000-0000-4000-8000-000000000002"
RELEASE_SHA = "a" * 40
PRIVATE_PREVIEW = "https://storage.example/private.png?signature=never-emit"


def _config() -> CanaryConfig:
    return CanaryConfig(
        base_url="https://constructor.lead-generator.ru",
        email="private-canary@example.com",
        password="private-password",
        expected_release_sha=RELEASE_SHA,
        preview_host_suffix=".preview.lead-generator.ru",
        overall_timeout_seconds=300,
        poll_seconds=1,
    )


class _Scenario:
    def __init__(
        self,
        *,
        include_cancel: bool = False,
        build_status: str = "completed",
        delete_status: int = 204,
    ) -> None:
        self.include_cancel = include_cancel
        self.build_status = build_status
        self.delete_status = delete_status
        self.requests: list[tuple[str, str]] = []
        self.prompt_count = 0
        self.project_reads = 0

    def handler(self, request: httpx.Request) -> httpx.Response:
        method = request.method
        path = request.url.path
        self.requests.append((method, path))
        if path == "/api/health":
            return httpx.Response(
                200,
                json={"status": "ok", "service": "api", "release_sha": RELEASE_SHA},
            )
        if path == "/api/auth/login":
            body = json.loads(request.content)
            assert body == {
                "email": "private-canary@example.com",
                "password": "private-password",
            }
            return httpx.Response(
                200,
                json={"id": "50000000-0000-4000-8000-000000000001"},
                headers={"set-cookie": "omnia_session=private-cookie; Secure; HttpOnly"},
            )
        if path == "/api/projects" and method == "POST":
            return httpx.Response(
                201,
                json={"id": PROJECT_ID, "current_snapshot_id": SEED_ID},
            )
        if path == f"/api/projects/{PROJECT_ID}/prompt":
            self.prompt_count += 1
            body = json.loads(request.content)
            assert body["skip_clarify"] is True
            assert len(body["idempotency_key"]) == 36
            run_id, mode = {
                1: (BUILD_RUN, "build"),
                2: (EDIT_RUN, "edit"),
                3: (CANCEL_RUN, "edit"),
            }[self.prompt_count]
            return httpx.Response(202, json={"run_id": run_id, "mode": mode})
        if path == f"/api/projects/{PROJECT_ID}/generation":
            run_id, mode, status = {
                1: (BUILD_RUN, "build", self.build_status),
                2: (EDIT_RUN, "edit", "completed"),
                3: (CANCEL_RUN, "edit", "cancelled"),
            }[self.prompt_count]
            return httpx.Response(
                200,
                json={"id": run_id, "response_mode": mode, "status": status},
            )
        if path == f"/api/projects/{PROJECT_ID}" and method == "GET":
            self.project_reads += 1
            snapshot_id = BUILD_SNAPSHOT if self.project_reads == 1 else EDIT_SNAPSHOT
            return httpx.Response(
                200,
                json={"id": PROJECT_ID, "current_snapshot_id": snapshot_id},
            )
        if path in {
            f"/api/projects/{PROJECT_ID}/snapshots/{BUILD_SNAPSHOT}",
            f"/api/projects/{PROJECT_ID}/snapshots/{EDIT_SNAPSHOT}",
        }:
            return httpx.Response(
                200,
                json={
                    "id": path.rsplit("/", 1)[-1],
                    "project_id": PROJECT_ID,
                    "preview_url": PRIVATE_PREVIEW,
                    "files": {"private/source.tsx": "never emit source"},
                },
            )
        if path == f"/api/projects/{PROJECT_ID}/generation/cancel":
            return httpx.Response(202, json={"id": CANCEL_RUN, "status": "cancel_requested"})
        if path == f"/api/projects/{PROJECT_ID}" and method == "DELETE":
            return httpx.Response(self.delete_status)
        if path == "/api/auth/logout":
            return httpx.Response(204)
        raise AssertionError(f"unexpected request {method} {path}")


def _runner(
    scenario: _Scenario,
    *,
    include_cancel: bool = False,
    sleeps: list[float] | None = None,
):
    from scripts import dev_generation_telegram_acceptance as acceptance

    ticks = iter(range(1000))
    return acceptance.DevGenerationTelegramAcceptance(
        _config(),
        include_cancel=include_cancel,
        transport=httpx.MockTransport(scenario.handler),
        clock=lambda: float(next(ticks)),
        sleep=(sleeps.append if sleeps is not None else lambda _seconds: None),
        wall_clock=lambda: datetime(2026, 8, 21, 12, 0, tzinfo=UTC),
    )


def test_acceptance_runs_build_edit_and_emits_only_redacted_summary() -> None:
    scenario = _Scenario()
    sleeps: list[float] = []
    summary = _runner(scenario, sleeps=sleeps).run()

    assert summary["cleanup"] is True
    assert summary["runs"] == [
        {
            "run_id": BUILD_RUN,
            "mode": "build",
            "terminal_status": "completed",
            "snapshot": True,
            "preview": True,
            "started_at": "2026-08-21T12:00:00+00:00",
            "finished_at": "2026-08-21T12:00:00+00:00",
        },
        {
            "run_id": EDIT_RUN,
            "mode": "edit",
            "terminal_status": "completed",
            "snapshot": True,
            "preview": True,
            "started_at": "2026-08-21T12:00:00+00:00",
            "finished_at": "2026-08-21T12:00:00+00:00",
        },
    ]
    serialized = json.dumps(summary, ensure_ascii=False)
    for forbidden in (
        "private-canary@example.com",
        "private-password",
        "private-cookie",
        PRIVATE_PREVIEW,
        "never emit source",
        PROJECT_ID,
        "prompt",
        "token",
        "chat_id",
    ):
        assert forbidden not in serialized
    assert scenario.requests[-2:] == [
        ("DELETE", f"/api/projects/{PROJECT_ID}"),
        ("POST", "/api/auth/logout"),
    ]
    assert sleeps[-1] == 20.0


def test_acceptance_optional_cancel_is_requested_and_verified() -> None:
    scenario = _Scenario(include_cancel=True)
    summary = _runner(scenario, include_cancel=True).run()

    assert summary["runs"][-1]["run_id"] == CANCEL_RUN
    assert summary["runs"][-1]["mode"] == "edit"
    assert summary["runs"][-1]["terminal_status"] == "cancelled"
    assert summary["runs"][-1]["snapshot"] is False
    assert summary["runs"][-1]["preview"] is False
    assert (
        "POST",
        f"/api/projects/{PROJECT_ID}/generation/cancel",
    ) in scenario.requests


def test_unexpected_generation_state_returns_failure_and_still_cleans_up() -> None:
    scenario = _Scenario(build_status="failed")

    with pytest.raises(CanaryFailure, match="unexpected terminal state"):
        _runner(scenario).run()

    assert ("DELETE", f"/api/projects/{PROJECT_ID}") in scenario.requests
    assert ("POST", "/api/auth/logout") in scenario.requests


def test_cleanup_failure_is_nonzero_even_after_successful_generations() -> None:
    scenario = _Scenario(delete_status=503)

    with pytest.raises(CanaryFailure, match="cleanup failed"):
        _runner(scenario).run()

    assert ("DELETE", f"/api/projects/{PROJECT_ID}") in scenario.requests


def test_cli_emits_partial_redacted_json_and_nonzero_on_failure(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from scripts import dev_generation_telegram_acceptance as acceptance

    class _Failing:
        cleanup_complete = True
        records: ClassVar[list[dict[str, object]]] = [
            {
                "run_id": BUILD_RUN,
                "mode": "build",
                "terminal_status": "failed",
                "snapshot": False,
                "preview": False,
                "started_at": "2026-08-21T12:00:00+00:00",
                "finished_at": "2026-08-21T12:00:01+00:00",
            }
        ]

        def __init__(self, *_args: object, **_kwargs: object) -> None:
            return None

        def run(self) -> dict[str, object]:
            raise CanaryFailure("private response body")

    monkeypatch.setattr(acceptance, "DevGenerationTelegramAcceptance", _Failing)
    monkeypatch.setattr(acceptance.CanaryConfig, "from_env", lambda: _config())
    assert acceptance.main() == 1
    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert payload == {"cleanup": True, "runs": _Failing.records}
    assert captured.err == "development Telegram acceptance failed\n"
    assert "private response body" not in captured.out + captured.err

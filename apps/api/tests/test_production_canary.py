import json
import re
from pathlib import Path

import httpx
import pytest

from omnia_api.ops import production_canary as canary_module
from omnia_api.ops.production_canary import CanaryConfig, CanaryConfigurationError

RELEASE_SHA = "a7c4fc22"
PROJECT_ID = "10000000-0000-4000-8000-000000000001"
SEED_SNAPSHOT_ID = "20000000-0000-4000-8000-000000000001"
BUILD_RUN_ID = "30000000-0000-4000-8000-000000000001"
BUILD_SNAPSHOT_ID = "40000000-0000-4000-8000-000000000001"
EDIT_RUN_ID = "30000000-0000-4000-8000-000000000002"
EDIT_SNAPSHOT_ID = "40000000-0000-4000-8000-000000000002"
SIGNED_PREVIEW_URL = (
    "https://demo.preview.lead-generator.ru/api/omnia/preview-session"
    "?expires=1893456000&signature=aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
)


def _valid_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PRODUCTION_CANARY_EMAIL", "canary@example.com")
    monkeypatch.setenv("PRODUCTION_CANARY_PASSWORD", "secret-password")
    monkeypatch.setenv("PRODUCTION_EXPECTED_RELEASE_SHA", "a7c4fc22")


def test_config_rejects_missing_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PRODUCTION_CANARY_EMAIL", "canary@example.com")
    monkeypatch.delenv("PRODUCTION_CANARY_PASSWORD", raising=False)
    monkeypatch.setenv("PRODUCTION_EXPECTED_RELEASE_SHA", "a7c4fc22")

    with pytest.raises(CanaryConfigurationError, match="PRODUCTION_CANARY_PASSWORD"):
        CanaryConfig.from_env()


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("PRODUCTION_CANARY_BASE_URL", "http://constructor.example"),
        ("PRODUCTION_EXPECTED_RELEASE_SHA", "A7C4FC22"),
        ("PRODUCTION_CANARY_TIMEOUT_SECONDS", "299"),
        ("PRODUCTION_CANARY_TIMEOUT_SECONDS", "3601"),
        ("PRODUCTION_CANARY_TIMEOUT_SECONDS", "not-a-number"),
        ("PRODUCTION_CANARY_POLL_SECONDS", "0.5"),
        ("PRODUCTION_CANARY_POLL_SECONDS", "31"),
        ("PRODUCTION_CANARY_PREVIEW_HOST_SUFFIX", ""),
        ("PRODUCTION_CANARY_PREVIEW_HOST_SUFFIX", "preview.lead-generator.ru"),
        ("PRODUCTION_CANARY_PREVIEW_HOST_SUFFIX", ".com"),
        ("PRODUCTION_CANARY_PREVIEW_HOST_SUFFIX", ".lead-generator.ru"),
        ("PRODUCTION_CANARY_PREVIEW_HOST_SUFFIX", ".preview..example.com"),
        ("PRODUCTION_CANARY_PREVIEW_HOST_SUFFIX", ".Preview.example.com"),
        ("PRODUCTION_CANARY_PREVIEW_HOST_SUFFIX", ".preview.example.123"),
    ],
)
def test_config_rejects_unsafe_or_unbounded_values(
    monkeypatch: pytest.MonkeyPatch,
    name: str,
    value: str,
) -> None:
    _valid_env(monkeypatch)
    monkeypatch.setenv(name, value)

    with pytest.raises(CanaryConfigurationError):
        CanaryConfig.from_env()


def test_config_accepts_specific_dns_preview_suffix(monkeypatch: pytest.MonkeyPatch) -> None:
    _valid_env(monkeypatch)
    monkeypatch.setenv(
        "PRODUCTION_CANARY_PREVIEW_HOST_SUFFIX",
        ".preview.staging.example.com",
    )

    config = CanaryConfig.from_env()

    assert config.preview_host_suffix == ".preview.staging.example.com"


def test_preview_url_accepts_signed_https_canary_origin() -> None:
    parsed = canary_module.validate_preview_url(
        "https://demo.preview.lead-generator.ru/api/omnia/preview-session"
        "?expires=1893456000&signature=aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        ".preview.lead-generator.ru",
    )

    assert parsed.hostname == "demo.preview.lead-generator.ru"


@pytest.mark.parametrize(
    "url",
    [
        "http://demo.preview.lead-generator.ru/api/omnia/preview-session"
        "?expires=1893456000&signature=aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        "https://attacker.example/api/omnia/preview-session"
        "?expires=1893456000&signature=aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        "https://user@demo.preview.lead-generator.ru/api/omnia/preview-session"
        "?expires=1893456000&signature=aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        "https://demo.preview.lead-generator.ru/wrong"
        "?expires=1893456000&signature=aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        "https://demo.preview.lead-generator.ru/api/omnia/preview-session"
        "?expires=1893456000&signature=short",
        "https://demo.preview.lead-generator.ru/api/omnia/preview-session"
        "?expires=1893456000&signature=aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa&extra=bad",
    ],
)
def test_preview_url_rejects_unsafe_origins_and_signatures(url: str) -> None:
    with pytest.raises(canary_module.CanaryFailure):
        canary_module.validate_preview_url(url, ".preview.lead-generator.ru")


def _release_health(service: str) -> dict[str, object]:
    payload: dict[str, object] = {
        "status": "ok",
        "service": service,
        "release_sha": RELEASE_SHA,
    }
    if service == "api":
        payload["checks"] = {
            "database": "ok",
            "redis": "ok",
            "worker": "ok",
            "deploy_control_plane": "ok",
            "preview_storage": "ok",
        }
        payload["dependencies"] = {
            "worker_release_sha": RELEASE_SHA,
            "orchestrator_release_sha": RELEASE_SHA,
        }
    return payload


def test_canary_completes_build_preview_edit_and_mandatory_cleanup() -> None:
    requests: list[tuple[str, str]] = []
    events: list[dict[str, object]] = []
    active_run = "build"
    generation_polls = {"build": 0, "edit": 0}
    project_reads = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal active_run, project_reads
        requests.append((request.method, request.url.path))
        path = request.url.path
        if path == "/web-health":
            return httpx.Response(200, json=_release_health("web"))
        if path == "/api/health":
            return httpx.Response(200, json=_release_health("api"))
        if path == "/api/auth/login":
            assert json.loads(request.content) == {
                "email": "canary@example.com",
                "password": "secret-password",
            }
            return httpx.Response(
                200,
                json={"id": "50000000-0000-4000-8000-000000000001"},
                headers={"set-cookie": "omnia_session=session-secret; Secure; HttpOnly"},
            )
        if path == "/api/projects" and request.method == "POST":
            project_payload = json.loads(request.content)
            assert project_payload["template"] == "max_miniapp"
            assert re.fullmatch(
                r"Production generation canary [0-9a-f]{8}",
                project_payload["name"],
            )
            return httpx.Response(
                201,
                json={"id": PROJECT_ID, "current_snapshot_id": SEED_SNAPSHOT_ID},
            )
        if path == f"/api/projects/{PROJECT_ID}/prompt":
            payload = json.loads(request.content)
            assert payload["skip_clarify"] is True
            assert len(payload["idempotency_key"]) == 36
            if active_run == "build":
                assert payload["prompt"] == canary_module.BUILD_PROMPT
                return httpx.Response(
                    202,
                    json={"run_id": BUILD_RUN_ID, "mode": "build"},
                )
            assert payload["prompt"] == canary_module.EDIT_PROMPT
            return httpx.Response(202, json={"run_id": EDIT_RUN_ID, "mode": "edit"})
        if path == f"/api/projects/{PROJECT_ID}/generation":
            generation_polls[active_run] += 1
            run_id = BUILD_RUN_ID if active_run == "build" else EDIT_RUN_ID
            mode = active_run
            status = "running" if generation_polls[active_run] == 1 else "completed"
            return httpx.Response(
                200,
                json={"id": run_id, "status": status, "response_mode": mode},
            )
        if path == f"/api/projects/{PROJECT_ID}" and request.method == "GET":
            project_reads += 1
            snapshot_id = BUILD_SNAPSHOT_ID if project_reads == 1 else EDIT_SNAPSHOT_ID
            return httpx.Response(
                200,
                json={"id": PROJECT_ID, "current_snapshot_id": snapshot_id},
            )
        if path in {
            f"/api/projects/{PROJECT_ID}/snapshots/{BUILD_SNAPSHOT_ID}",
            f"/api/projects/{PROJECT_ID}/snapshots/{EDIT_SNAPSHOT_ID}",
        }:
            snapshot_id = path.rsplit("/", 1)[-1]
            return httpx.Response(
                200,
                json={
                    "id": snapshot_id,
                    "project_id": PROJECT_ID,
                    "files": {"src/app/page.tsx": "export default function Page() {}"},
                },
            )
        if path == f"/api/projects/{PROJECT_ID}/runtime/start":
            return httpx.Response(
                200,
                json={"state": "running", "dev_url": "https://demo.preview.example"},
            )
        if path == f"/api/projects/{PROJECT_ID}/max/preview-session":
            return httpx.Response(
                200,
                json={"url": SIGNED_PREVIEW_URL, "expires_at": "2030-01-01T00:00:00Z"},
            )
        if request.url.host == "demo.preview.lead-generator.ru" and path.endswith(
            "/preview-session"
        ):
            return httpx.Response(
                307,
                headers={
                    "location": "/",
                    "set-cookie": "max_preview=preview-secret; Path=/; Secure; HttpOnly",
                },
            )
        if request.url.host == "demo.preview.lead-generator.ru" and path == "/":
            assert "max_preview=preview-secret" in request.headers["cookie"]
            active_run = "edit"
            return httpx.Response(200, text="private preview body")
        if path == f"/api/projects/{PROJECT_ID}" and request.method == "DELETE":
            return httpx.Response(204)
        if path == "/api/auth/logout":
            return httpx.Response(204)
        raise AssertionError(f"unexpected request: {request.method} {request.url}")

    config = CanaryConfig(
        base_url="https://constructor.lead-generator.ru",
        email="canary@example.com",
        password="secret-password",
        expected_release_sha=RELEASE_SHA,
        preview_host_suffix=".preview.lead-generator.ru",
        overall_timeout_seconds=300,
        poll_seconds=1,
    )
    result = canary_module.ProductionCanary(
        config,
        transport=httpx.MockTransport(handler),
        sleep=lambda _seconds: None,
        emit=events.append,
    ).run()

    assert result.release_sha == RELEASE_SHA
    assert result.project_id == PROJECT_ID
    assert result.build_snapshot_id == BUILD_SNAPSHOT_ID
    assert result.edit_snapshot_id == EDIT_SNAPSHOT_ID
    assert result.cleanup_complete is True
    assert requests[-2:] == [
        ("DELETE", f"/api/projects/{PROJECT_ID}"),
        ("POST", "/api/auth/logout"),
    ]
    serialized = json.dumps([result.__dict__, events])
    for secret in (
        SIGNED_PREVIEW_URL,
        "session-secret",
        "preview-secret",
        "secret-password",
        "private preview body",
    ):
        assert secret not in serialized


class _FailureScenario:
    def __init__(
        self,
        *,
        generation_status: str = "completed",
        drift_release: bool = False,
        preview_status: int = 200,
        delete_status: int = 204,
    ) -> None:
        self.generation_status = generation_status
        self.drift_release = drift_release
        self.preview_status = preview_status
        self.delete_status = delete_status
        self.requests: list[tuple[str, str]] = []
        self.prompt_count = 0
        self.project_reads = 0
        self.web_health_calls = 0
        self.cleanup_timeouts: list[dict[str, float]] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        self.requests.append((request.method, path))
        if path == "/web-health":
            self.web_health_calls += 1
            payload = _release_health("web")
            if self.drift_release and self.web_health_calls > 1:
                payload["release_sha"] = "b7c4fc22"
            return httpx.Response(200, json=payload)
        if path == "/api/health":
            payload = _release_health("api")
            if self.drift_release and self.web_health_calls > 1:
                payload["release_sha"] = "b7c4fc22"
                dependencies = payload["dependencies"]
                assert isinstance(dependencies, dict)
                dependencies["worker_release_sha"] = "b7c4fc22"
                dependencies["orchestrator_release_sha"] = "b7c4fc22"
            return httpx.Response(200, json=payload)
        if path == "/api/auth/login":
            return httpx.Response(
                200,
                json={"id": "50000000-0000-4000-8000-000000000001"},
                headers={"set-cookie": "omnia_session=session-secret; Path=/; Secure"},
            )
        if path == "/api/projects" and request.method == "POST":
            return httpx.Response(
                201,
                json={"id": PROJECT_ID, "current_snapshot_id": SEED_SNAPSHOT_ID},
            )
        if path == f"/api/projects/{PROJECT_ID}/prompt":
            self.prompt_count += 1
            if self.prompt_count == 1:
                return httpx.Response(
                    202,
                    json={"run_id": BUILD_RUN_ID, "mode": "build"},
                )
            return httpx.Response(202, json={"run_id": EDIT_RUN_ID, "mode": "edit"})
        if path == f"/api/projects/{PROJECT_ID}/generation":
            is_build = self.prompt_count == 1
            return httpx.Response(
                200,
                json={
                    "id": BUILD_RUN_ID if is_build else EDIT_RUN_ID,
                    "status": self.generation_status,
                    "response_mode": "build" if is_build else "edit",
                },
            )
        if path == f"/api/projects/{PROJECT_ID}" and request.method == "GET":
            self.project_reads += 1
            return httpx.Response(
                200,
                json={
                    "id": PROJECT_ID,
                    "current_snapshot_id": (
                        BUILD_SNAPSHOT_ID if self.project_reads == 1 else EDIT_SNAPSHOT_ID
                    ),
                },
            )
        if "/snapshots/" in path:
            snapshot_id = path.rsplit("/", 1)[-1]
            return httpx.Response(
                200,
                json={
                    "id": snapshot_id,
                    "project_id": PROJECT_ID,
                    "files": {"src/app/page.tsx": "export default function Page() {}"},
                },
            )
        if path == f"/api/projects/{PROJECT_ID}/runtime/start":
            return httpx.Response(200, json={"state": "running"})
        if path == f"/api/projects/{PROJECT_ID}/max/preview-session":
            return httpx.Response(200, json={"url": SIGNED_PREVIEW_URL})
        if request.url.host == "demo.preview.lead-generator.ru" and path.endswith(
            "/preview-session"
        ):
            return httpx.Response(
                307,
                headers={"location": "/", "set-cookie": "preview=value; Path=/; Secure"},
            )
        if request.url.host == "demo.preview.lead-generator.ru" and path == "/":
            return httpx.Response(self.preview_status)
        if path == f"/api/projects/{PROJECT_ID}" and request.method == "DELETE":
            timeout = request.extensions.get("timeout")
            assert isinstance(timeout, dict)
            self.cleanup_timeouts.append(timeout)
            return httpx.Response(self.delete_status)
        if path == "/api/auth/logout":
            timeout = request.extensions.get("timeout")
            assert isinstance(timeout, dict)
            self.cleanup_timeouts.append(timeout)
            return httpx.Response(204)
        raise AssertionError(f"unexpected request: {request.method} {request.url}")


def _test_config() -> CanaryConfig:
    return CanaryConfig(
        base_url="https://constructor.lead-generator.ru",
        email="canary@example.com",
        password="secret-password",
        expected_release_sha=RELEASE_SHA,
        preview_host_suffix=".preview.lead-generator.ru",
        overall_timeout_seconds=300,
        poll_seconds=30,
    )


def _assert_delete_attempted(scenario: _FailureScenario) -> None:
    assert ("DELETE", f"/api/projects/{PROJECT_ID}") in scenario.requests


def test_canary_generation_deadline_still_deletes_project() -> None:
    scenario = _FailureScenario(generation_status="running")
    now = 0.0

    def clock() -> float:
        return now

    def sleep(seconds: float) -> None:
        nonlocal now
        now += seconds

    with pytest.raises(canary_module.CanaryFailure, match="deadline"):
        canary_module.ProductionCanary(
            _test_config(),
            transport=httpx.MockTransport(scenario.handler),
            clock=clock,
            sleep=sleep,
        ).run()

    _assert_delete_attempted(scenario)
    assert scenario.cleanup_timeouts
    assert all(
        value == 10
        for timeout in scenario.cleanup_timeouts
        for value in timeout.values()
    )


def test_canary_request_timeout_is_bounded_by_remaining_deadline() -> None:
    clock_calls = 0

    def clock() -> float:
        nonlocal clock_calls
        clock_calls += 1
        return 0.0 if clock_calls == 1 else 299.75

    def handler(request: httpx.Request) -> httpx.Response:
        timeout = request.extensions.get("timeout")
        assert isinstance(timeout, dict)
        assert all(0 < value <= 0.25 for value in timeout.values())
        return httpx.Response(503)

    with pytest.raises(canary_module.CanaryFailure, match="unexpected status"):
        canary_module.ProductionCanary(
            _test_config(),
            transport=httpx.MockTransport(handler),
            clock=clock,
        ).run()


def test_canary_terminal_generation_failure_still_deletes_project() -> None:
    scenario = _FailureScenario(generation_status="failed")

    with pytest.raises(canary_module.CanaryFailure, match="terminal"):
        canary_module.ProductionCanary(
            _test_config(),
            transport=httpx.MockTransport(scenario.handler),
        ).run()

    _assert_delete_attempted(scenario)


def test_canary_release_drift_still_deletes_project() -> None:
    scenario = _FailureScenario(drift_release=True)

    with pytest.raises(canary_module.CanaryFailure, match="release"):
        canary_module.ProductionCanary(
            _test_config(),
            transport=httpx.MockTransport(scenario.handler),
        ).run()

    _assert_delete_attempted(scenario)


def test_canary_preview_failure_still_deletes_project() -> None:
    scenario = _FailureScenario(preview_status=503)

    with pytest.raises(canary_module.CanaryFailure, match="preview"):
        canary_module.ProductionCanary(
            _test_config(),
            transport=httpx.MockTransport(scenario.handler),
        ).run()

    _assert_delete_attempted(scenario)


def test_canary_cleanup_failure_overrides_success() -> None:
    scenario = _FailureScenario(delete_status=503)

    with pytest.raises(canary_module.CanaryCleanupFailure):
        canary_module.ProductionCanary(
            _test_config(),
            transport=httpx.MockTransport(scenario.handler),
        ).run()

    _assert_delete_attempted(scenario)


def test_cli_prints_only_fixed_public_failure_without_traceback(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from scripts import production_generation_canary as cli

    class FailingCanary:
        def __init__(self, _config: object, *, emit: object) -> None:
            pass

        def run(self) -> None:
            raise canary_module.CanaryFailure("signed URL must stay private")

    monkeypatch.setattr(cli, "ProductionCanary", FailingCanary)
    monkeypatch.setattr(cli.CanaryConfig, "from_env", lambda: object())

    assert cli.main() == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == "production canary failed\n"
    assert "signed URL" not in captured.err
    assert "Traceback" not in captured.err


def test_cli_writes_safe_generation_failure_for_daily_report(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from scripts import production_generation_canary as cli

    class FailingCanary:
        def __init__(self, _config: object, *, emit: object) -> None:
            pass

        def run(self) -> None:
            raise canary_module.CanaryFailure("generation reached a failed terminal status")

    result_path = tmp_path / "production-canary-result.json"
    monkeypatch.setattr(cli, "ProductionCanary", FailingCanary)
    monkeypatch.setattr(cli.CanaryConfig, "from_env", lambda: object())
    monkeypatch.setenv("PRODUCTION_CANARY_RESULT_FILE", str(result_path))

    assert cli.main() == 1
    assert json.loads(result_path.read_text(encoding="utf-8")) == {
        "error": "Тестовая генерация завершилась с ошибкой.",
        "status": "failure",
    }


def test_cli_writes_success_for_daily_report(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from scripts import production_generation_canary as cli

    class SuccessfulCanary:
        def __init__(self, _config: object, *, emit: object) -> None:
            pass

        def run(self) -> None:
            return None

    result_path = tmp_path / "production-canary-result.json"
    monkeypatch.setattr(cli, "ProductionCanary", SuccessfulCanary)
    monkeypatch.setattr(cli.CanaryConfig, "from_env", lambda: object())
    monkeypatch.setenv("PRODUCTION_CANARY_RESULT_FILE", str(result_path))

    assert cli.main() == 0
    assert json.loads(result_path.read_text(encoding="utf-8")) == {"status": "success"}


@pytest.mark.parametrize(
    ("failure_reason", "report_error"),
    [
        (
            "generation deadline exceeded",
            "Тестовая генерация превысила лимит времени.",
        ),
        (
            "preview did not become ready",
            "Превью тестовой генерации не запустилось.",
        ),
        (
            "release dependency health mismatch",
            "Production-зависимости не прошли проверку здоровья.",
        ),
        (
            "public API returned an unexpected status",
            "Production API вернул ошибку.",
        ),
        (
            "project cleanup failed",
            "Не удалось удалить тестовый проект.",
        ),
    ],
)
def test_cli_reports_actionable_known_failure_category(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    failure_reason: str,
    report_error: str,
) -> None:
    from scripts import production_generation_canary as cli

    class FailingCanary:
        def __init__(self, _config: object, *, emit: object) -> None:
            pass

        def run(self) -> None:
            raise canary_module.CanaryFailure(failure_reason)

    result_path = tmp_path / "production-canary-result.json"
    monkeypatch.setattr(cli, "ProductionCanary", FailingCanary)
    monkeypatch.setattr(cli.CanaryConfig, "from_env", lambda: object())
    monkeypatch.setenv("PRODUCTION_CANARY_RESULT_FILE", str(result_path))

    assert cli.main() == 1
    assert json.loads(result_path.read_text(encoding="utf-8"))["error"] == report_error


def test_cli_writes_configuration_failure_for_daily_report(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from scripts import production_generation_canary as cli

    result_path = tmp_path / "production-canary-result.json"

    def fail_config() -> object:
        raise canary_module.CanaryConfigurationError("secret name must stay private")

    monkeypatch.setattr(cli.CanaryConfig, "from_env", fail_config)
    monkeypatch.setenv("PRODUCTION_CANARY_RESULT_FILE", str(result_path))

    assert cli.main() == 1
    assert json.loads(result_path.read_text(encoding="utf-8")) == {
        "error": "Не удалось запустить тестовые генерации: неверная конфигурация.",
        "status": "failure",
    }
    assert "secret name" not in result_path.read_text(encoding="utf-8")


def test_cli_writes_fixed_internal_failure_without_exception_detail(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from scripts import production_generation_canary as cli

    class BrokenCanary:
        def __init__(self, _config: object, *, emit: object) -> None:
            pass

        def run(self) -> None:
            raise RuntimeError("response body must stay private")

    result_path = tmp_path / "production-canary-result.json"
    monkeypatch.setattr(cli, "ProductionCanary", BrokenCanary)
    monkeypatch.setattr(cli.CanaryConfig, "from_env", lambda: object())
    monkeypatch.setenv("PRODUCTION_CANARY_RESULT_FILE", str(result_path))

    assert cli.main() == 1
    assert json.loads(result_path.read_text(encoding="utf-8")) == {
        "error": "Техническая ошибка production-canary.",
        "status": "failure",
    }
    assert "response body" not in result_path.read_text(encoding="utf-8")

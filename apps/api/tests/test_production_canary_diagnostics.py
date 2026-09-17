"""A red generation canary must say which stage failed and why.

Before this contract every failure collapsed into one `canary_failed` code, so
a run that stopped in the release preflight was indistinguishable from a broken
build. These tests pin the stage, the allowed diagnostic code and the safe
detail fields, and they pin what must never appear: passwords, cookies, request
bodies or signed preview URLs.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from omnia_api.ops import production_canary as canary_module
from omnia_api.ops.production_canary import CanaryConfig, CanaryFailure, ProductionCanary
from tests.production_canary_fakes import (
    OTHER_RELEASE_SHA,
    PROJECT_ID,
    FakeProduction,
    canary_env,
    matrix,
)


def _run(production: FakeProduction, events: list[dict[str, object]] | None = None) -> None:
    ProductionCanary(
        CanaryConfig.from_env(),
        transport=production.transport(),
        sleep=lambda _seconds: None,
        emit=(events.append if events is not None else None),
    ).run()


def _run_cli(
    production: FakeProduction,
    monkeypatch: pytest.MonkeyPatch,
    result_path: Path | None,
) -> int:
    from scripts import production_generation_canary as cli

    real = canary_module.ProductionCanary

    def factory(config: object, *, emit: object) -> object:
        return real(
            config,  # type: ignore[arg-type]
            transport=production.transport(),
            sleep=lambda _seconds: None,
            emit=emit,  # type: ignore[arg-type]
        )

    monkeypatch.setattr(cli, "ProductionCanary", factory)
    if result_path is None:
        monkeypatch.delenv("PRODUCTION_CANARY_RESULT_FILE", raising=False)
    else:
        monkeypatch.setenv("PRODUCTION_CANARY_RESULT_FILE", str(result_path))
    return cli.main()


def test_health_transport_error_is_its_own_cause_and_stops_before_generation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """C04/C06: a 502 preflight is a health failure, and nothing is generated."""

    canary_env(monkeypatch)
    production = FakeProduction(health_status=502)

    with pytest.raises(CanaryFailure) as failure:
        _run(production)

    assert failure.value.stage == "release_health"
    assert failure.value.code == "health_http_error"
    assert failure.value.http_status == 502
    assert production.paths("POST") == []


def test_malformed_health_payload_is_distinct_from_a_mismatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    canary_env(monkeypatch)
    production = FakeProduction(health_body="<html>bad gateway</html>")

    with pytest.raises(CanaryFailure) as failure:
        _run(production)

    assert failure.value.code == "health_invalid_json"
    assert failure.value.stage == "release_health"


def test_login_failure_names_the_login_stage_without_credentials(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """C07: the stage is login, and the password never reaches the report."""

    canary_env(monkeypatch)
    production = FakeProduction(login_status=401)
    result_path = tmp_path / "result.json"

    assert _run_cli(production, monkeypatch, result_path) == 1

    result = json.loads(result_path.read_text(encoding="utf-8"))
    assert result["stage"] == "login"
    assert result["code"] == "login_failed"
    assert result["http_status"] == 401
    assert "secret-password" not in result_path.read_text(encoding="utf-8")


def test_failed_build_is_distinguishable_from_a_health_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """C08: a real generation failure must not look like a preflight failure."""

    canary_env(monkeypatch)
    production = FakeProduction(build_generation_status="failed")

    with pytest.raises(CanaryFailure) as failure:
        _run(production)

    assert failure.value.stage == "build"
    assert failure.value.code == "build_failed"
    assert ("DELETE", f"/api/projects/{PROJECT_ID}") in production.calls


def test_primary_failure_survives_a_failing_cleanup(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """C09: deleting the test project failing too must not erase the real cause."""

    canary_env(monkeypatch)
    production = FakeProduction(build_generation_status="failed", delete_status=503)
    result_path = tmp_path / "result.json"

    assert _run_cli(production, monkeypatch, result_path) == 1

    result = json.loads(result_path.read_text(encoding="utf-8"))
    assert result["stage"] == "build"
    assert result["code"] == "build_failed"
    assert result["cleanup"] == "failed"


def test_cleanup_failure_alone_is_still_reported(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    canary_env(monkeypatch)
    production = FakeProduction(delete_status=503)
    result_path = tmp_path / "result.json"

    assert _run_cli(production, monkeypatch, result_path) == 1

    result = json.loads(result_path.read_text(encoding="utf-8"))
    assert result["stage"] == "cleanup"
    assert result["code"] == "cleanup_failed"
    assert result["cleanup"] == "failed"


def test_release_mismatch_result_carries_expected_and_actual(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """C10: the artifact holds enough to act on without opening production."""

    canary_env(monkeypatch)
    production = FakeProduction(releases=matrix(web=OTHER_RELEASE_SHA))
    result_path = tmp_path / "result.json"

    assert _run_cli(production, monkeypatch, result_path) == 1

    result = json.loads(result_path.read_text(encoding="utf-8"))
    assert result["status"] == "failure"
    assert result["code"] == "release_mismatch"
    assert result["component"] == "web"
    assert result["actual_sha"] == OTHER_RELEASE_SHA
    assert result["expected_sha"] != result["actual_sha"]


def test_successful_run_records_the_verified_release_matrix(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    canary_env(monkeypatch)
    production = FakeProduction()
    result_path = tmp_path / "result.json"

    assert _run_cli(production, monkeypatch, result_path) == 0

    result = json.loads(result_path.read_text(encoding="utf-8"))
    assert result["status"] == "success"
    assert result["cleanup"] == "ok"
    assert set(result["releases"]) == {
        "web",
        "api",
        "worker",
        "generation_worker",
        "orchestrator",
    }


def test_missing_result_path_does_not_turn_a_failure_into_a_pass(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """C11: no diagnostics file is still an unmistakable failure exit code."""

    canary_env(monkeypatch)
    production = FakeProduction(login_status=401)

    assert _run_cli(production, monkeypatch, None) == 1


def test_diagnostic_codes_and_stages_stay_inside_the_allowed_sets(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    canary_env(monkeypatch)

    for status, kwargs in (
        (502, {"health_status": 502}),
        (401, {"login_status": 401}),
        (None, {"build_generation_status": "failed"}),
        (None, {"runtime_state": "stopped"}),
        (None, {"preview_status": 500}),
    ):
        production = FakeProduction(**kwargs)  # type: ignore[arg-type]
        with pytest.raises(CanaryFailure) as failure:
            _run(production)
        assert failure.value.code in canary_module.DIAGNOSTIC_CODES
        assert failure.value.stage in canary_module.STAGES
        if status is not None:
            assert failure.value.http_status == status


def test_events_and_result_never_carry_secrets_or_signed_preview_urls(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    canary_env(monkeypatch)
    production = FakeProduction(edit_generation_status="failed")
    result_path = tmp_path / "result.json"

    assert _run_cli(production, monkeypatch, result_path) == 1

    published = capsys.readouterr().out + result_path.read_text(encoding="utf-8")
    for forbidden in ("secret-password", "signature=", "omnia_session", "canary@example.com"):
        assert forbidden not in published


def test_workflow_uploads_the_diagnostic_result_even_when_the_canary_fails() -> None:
    """A red run is the run whose artifact matters most."""

    workflow = Path(__file__).resolve().parents[3] / (
        ".github/workflows/production-generation-canary.yml"
    )
    content = workflow.read_text(encoding="utf-8")

    assert "actions/upload-artifact" in content
    assert "if-no-files-found: error" in content
    assert "if: always()" in content
    assert "PRODUCTION_EXPECTED_RELEASE_SHA" not in content
    for component in ("WEB", "API", "WORKER", "ORCHESTRATOR"):
        assert f"PRODUCTION_EXPECTED_{component}_RELEASE_SHA" in content


def test_capacity_queue_is_a_waiting_state_not_an_invalid_response(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Project Cell admission queues a run before it starts; that is not a failure."""

    canary_env(monkeypatch)
    production = FakeProduction(build_queue_polls=2)

    _run(production)

    assert ("DELETE", f"/api/projects/{PROJECT_ID}") in production.calls


def test_failed_run_cancels_its_active_generation_before_deleting_the_project(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An active run blocks project deletion; cleanup must not leave a paid build behind."""

    canary_env(monkeypatch)
    production = FakeProduction(build_generation_status="mystery")

    with pytest.raises(CanaryFailure) as failure:
        _run(production)

    cancel = ("POST", f"/api/projects/{PROJECT_ID}/generation/cancel")
    delete = ("DELETE", f"/api/projects/{PROJECT_ID}")
    assert failure.value.stage == "build"
    assert cancel in production.calls and delete in production.calls
    assert production.calls.index(cancel) < production.calls.index(delete)

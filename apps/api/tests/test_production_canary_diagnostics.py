"""A red generation canary must say which stage failed and why.

Before this contract every failure collapsed into one `canary_failed` code, so
a run that stopped in the release preflight was indistinguishable from a broken
build. These tests pin the stage, the allowed diagnostic code and the safe
detail fields, and they pin what must never appear: passwords, cookies, request
bodies or signed preview URLs.
"""

from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest

from tests.production_canary_fakes import (
    BUILD_RUN_ID,
    EDIT_RUN_ID,
    OTHER_RELEASE_SHA,
    PROJECT_ID,
    FakeProduction,
    canary_env,
    matrix,
)
from yleum_api.ops import production_canary as canary_module
from yleum_api.ops.production_canary import CanaryConfig, CanaryFailure, ProductionCanary


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


@pytest.mark.parametrize("stage,status", [("build", "failed"), ("edit", "cancelled")])
def test_terminal_generation_diagnostic_reaches_CLI_before_cleanup(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    stage: str,
    status: str,
) -> None:
    canary_env(monkeypatch)
    diagnostics = {
        "error_code": "model_unavailable", "error_origin": "provider", "http_status": 502,
        "upstream_http_status": 503, "provider_charge_ambiguous": True, "retry_safe": False,
    }
    failure = {"code": "provider_unavailable", "message": "private provider body",
               "retryable": False, "diagnostics": diagnostics}
    production = FakeProduction(**{f"{stage}_generation_status": status,
                                   f"{stage}_failure": failure}, delete_status=503)
    result_path = tmp_path / "result.json"
    assert _run_cli(production, monkeypatch, result_path) == 1
    result = json.loads(result_path.read_text(encoding="utf-8"))
    run_id = BUILD_RUN_ID if stage == "build" else EDIT_RUN_ID
    assert result["code"] == f"{stage}_failed" and result["stage"] == stage
    assert result["project_id"] == PROJECT_ID and result["run_id"] == run_id
    assert result["failure_code"] == "provider_unavailable"
    assert result["generation_diagnostics"] == diagnostics
    assert result["cleanup"] == "failed"
    events = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    failed = next(i for i, event in enumerate(events)
                  if event["step"] == "generation" and event["status"] == status)
    cleanup = next(i for i, event in enumerate(events) if event["step"] == "project_delete")
    assert failed < cleanup
    assert events[failed]["failure_code"] == result["failure_code"]
    assert events[failed]["generation_diagnostics"] == diagnostics
    assert "private provider body" not in json.dumps(result) + json.dumps(events)


@pytest.mark.parametrize("bad_failure", [None, [], "secret-password", {"code": "x" * 100_000},
    {"code": "provider_unavailable", "diagnostics": ["secret-password"]}])
def test_missing_malformed_or_huge_failure_keeps_safe_terminal_identity(
    monkeypatch: pytest.MonkeyPatch, bad_failure: object,
) -> None:
    canary_env(monkeypatch)
    production = FakeProduction(build_generation_status="failed", build_failure=bad_failure,
                                generation_extra={"error": "secret-password"})
    events: list[dict[str, object]] = []
    with pytest.raises(CanaryFailure) as caught:
        _run(production, events)
    report = caught.value.diagnostics()
    assert report["code"] == "build_failed"
    assert report["project_id"] == PROJECT_ID and report["run_id"] == BUILD_RUN_ID
    assert len(json.dumps(report)) < 1024
    assert "secret-password" not in json.dumps(report) + json.dumps(events)


def test_failure_diagnostics_drop_unknown_codes_origins_types_and_raw_secrets(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str],
) -> None:
    canary_env(monkeypatch)
    secret = "github_pat_injected_SECRET_should_never_reach_canary_output"
    production = FakeProduction(build_generation_status="failed", build_failure={
        "code": secret, "message": secret, "retryable": secret,
        "diagnostics": {"error_code": secret, "error_origin": secret, "http_status": True,
                        "upstream_http_status": 399, "provider_charge_ambiguous": 1,
                        "retry_safe": "false", "provider_body": secret}},
        generation_extra={"error": secret, "provider_body": secret})
    path = tmp_path / "result.json"
    assert _run_cli(production, monkeypatch, path) == 1
    report = json.loads(path.read_text(encoding="utf-8"))
    assert "failure_code" not in report
    assert report["generation_diagnostics"] == {"error_origin": "unknown"}
    assert secret not in path.read_text(encoding="utf-8") + capsys.readouterr().out


def test_canary_failure_code_allowlists_match_current_server_producers() -> None:
    source = Path(canary_module.__file__).parents[1]
    gateway = ast.parse((source / "services/llm_client.py").read_text(encoding="utf-8"))
    gateway_codes = next(ast.literal_eval(node.value) for node in gateway.body
                         if isinstance(node, ast.Assign)
                         and any(isinstance(target, ast.Name)
                                 and target.id == "_GATEWAY_ERROR_CODES"
                                 for target in node.targets))
    public = ast.parse((source / "services/generation_failure.py").read_text(encoding="utf-8"))
    public_codes = {node.value.value for node in ast.walk(public)
                    if isinstance(node, ast.Assign) and isinstance(node.value, ast.Constant)
                    and isinstance(node.value.value, str)
                    and any(isinstance(target, ast.Name) and target.id == "code"
                            for target in node.targets)}
    assert canary_module._GATEWAY_ERROR_CODES == gateway_codes
    assert canary_module._GENERATION_FAILURE_CODES == public_codes


@pytest.mark.parametrize("status,upstream", [(100, 400), (599, 599)])
def test_generation_diagnostics_preserve_only_exact_bounded_primitives(
    status: int, upstream: int,
) -> None:
    code, safe = canary_module._safe_generation_failure({
        "code": "verification", "diagnostics": {
            "error_code": "provider_output_invalid", "error_origin": "gateway",
            "http_status": status, "upstream_http_status": upstream,
            "provider_charge_ambiguous": False, "retry_safe": True,
        },
    })
    assert code == "verification"
    assert safe == {"error_code": "provider_output_invalid", "error_origin": "gateway",
                    "http_status": status, "upstream_http_status": upstream,
                    "provider_charge_ambiguous": False, "retry_safe": True}


@pytest.mark.parametrize("bad", [None, True, False, 99, 600, 502.0, "502", [], {}])
def test_generation_status_diagnostics_do_not_coerce_untrusted_types(bad: object) -> None:
    _, safe = canary_module._safe_generation_failure({"diagnostics": {
        "http_status": bad, "upstream_http_status": bad,
    }})
    assert safe == {"error_origin": "unknown"}


def test_primary_terminal_failure_survives_raised_cleanup_exception(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str],
) -> None:
    canary_env(monkeypatch)
    production = FakeProduction(build_generation_status="failed", build_failure={
        "code": "provider_unavailable", "diagnostics": {"http_status": 502},
    })
    def rejected_cleanup(self: ProductionCanary, project_id: str) -> bool:
        raise RuntimeError("secret-password-in-cleanup")
    monkeypatch.setattr(ProductionCanary, "_delete_project_with_retry", rejected_cleanup)
    path = tmp_path / "result.json"
    assert _run_cli(production, monkeypatch, path) == 1
    result = json.loads(path.read_text(encoding="utf-8"))
    assert result["code"] == "build_failed" and result["cleanup"] == "failed"
    assert result["project_id"] == PROJECT_ID and result["run_id"] == BUILD_RUN_ID
    assert result["failure_code"] == "provider_unavailable"
    assert result["generation_diagnostics"]["http_status"] == 502
    assert ("POST", "/api/auth/logout") in production.calls
    published = path.read_text(encoding="utf-8") + capsys.readouterr().out
    assert "secret-password-in-cleanup" not in published

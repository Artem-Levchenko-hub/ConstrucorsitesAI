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
import os
import time
from collections.abc import Callable
from pathlib import Path
from shutil import which
from subprocess import run
from typing import Any

import httpx
import pytest
import yaml

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
    *,
    clock: Callable[[], float] | None = None,
    sleep: Callable[[float], None] | None = None,
    auth_only: bool = False,
) -> int:
    from scripts import production_generation_canary as cli

    real = canary_module.ProductionCanary

    def factory(config: object, *, emit: object) -> object:
        return real(
            config,  # type: ignore[arg-type]
            transport=production.transport(),
            clock=clock if clock is not None else time.monotonic,
            sleep=sleep if sleep is not None else (lambda _seconds: None),
            emit=emit,  # type: ignore[arg-type]
        )

    monkeypatch.setattr(cli, "ProductionCanary", factory)
    if result_path is None:
        monkeypatch.delenv("PRODUCTION_CANARY_RESULT_FILE", raising=False)
    else:
        monkeypatch.setenv("PRODUCTION_CANARY_RESULT_FILE", str(result_path))
    return cli.main(["--auth-only"] if auth_only else [])


@pytest.mark.parametrize(("fault", "stage", "code", "paths"), [
    ("ok", "cleanup", "ok", ["login", "access", "logout"]),
    ("login_unauthorized", "login", "login_failed", ["login"]),
    ("login_redirect", "login", "login_failed", ["login"]),
    ("login_invalid_json", "login", "api_invalid_response", ["login", "logout"]),
    ("login_invalid_id", "login", "api_invalid_response", ["login", "logout"]),
    ("login_no_cookie", "login", "auth_session_missing", ["login", "logout"]),
    ("login_wrong_cookie_path", "login", "auth_session_missing", ["login", "logout"]),
    ("login_no_logout_cookie", "login", "auth_session_missing", ["login", "logout"]),
    ("login_different_logout_cookie", "login", "auth_session_missing", ["login", "logout"]),
    ("access_unauthorized", "max_access", "max_access_failed", ["login", "access", "logout"]),
    ("access_false", "max_access", "max_access_denied", ["login", "access", "logout"]),
    ("access_integer", "max_access", "api_invalid_response", ["login", "access", "logout"]),
    ("logout_unavailable", "cleanup", "cleanup_failed", ["login", "access", "logout"]),
])
def test_auth_only_verifies_the_same_session_and_max_access_without_generation(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str],
    fault: str, stage: str, code: str, paths: list[str],
) -> None:
    canary_env(monkeypatch, PRODUCTION_CANARY_BASE_URL="https://canary.example.com")
    for component in ("WEB", "API", "WORKER", "GENERATION_WORKER", "ORCHESTRATOR"):
        monkeypatch.delenv(f"PRODUCTION_EXPECTED_{component}_RELEASE_SHA", raising=False)
    identity = "50000000-0000-4000-8000-000000000001"
    cookie = "omnia_session=private-auth-canary-cookie"
    routes = {
        "/api/auth/login": "login",
        "/api/max/account/access": "access",
        "/api/auth/logout": "logout",
    }
    seen = []

    class AuthProduction(FakeProduction):
        def handle(self, request: httpx.Request) -> httpx.Response:
            route = routes[request.url.path]
            seen.append(route)
            assert request.url.scheme == "https"
            assert str(request.url).removesuffix(request.url.path) == os.environ[
                "PRODUCTION_CANARY_BASE_URL"
            ]
            if route in {"login", "logout"}:
                assert request.method == "POST"
                assert request.headers["Origin"] == os.environ["PRODUCTION_CANARY_BASE_URL"]
            else:
                assert request.method == "GET"
            if route == "login":
                assert "Cookie" not in request.headers
                assert json.loads(request.content) == {
                    "email": "canary@example.com", "password": "secret-password",
                }
                if fault == "login_unauthorized":
                    return httpx.Response(401, text=identity)
                if fault == "login_redirect":
                    return httpx.Response(307, headers={"Location": "https://unexpected.invalid"})
                path = {
                    "login_wrong_cookie_path": "/api/auth",
                    "login_no_logout_cookie": "/api/max",
                    "login_different_logout_cookie": "/api/max",
                }.get(fault, "/")
                headers: dict[str, str] | list[tuple[str, str]] = {
                    "Set-Cookie": f"{cookie}; Path={path}; Secure; HttpOnly",
                }
                if fault == "login_no_cookie":
                    headers = {"Set-Cookie": "other=unrelated; Path=/; Secure; HttpOnly"}
                if fault == "login_invalid_json":
                    return httpx.Response(200, headers=headers, text=identity)
                if fault == "login_invalid_id":
                    return httpx.Response(200, headers=headers, json={"id": "not-a-user"})
                if fault == "login_different_logout_cookie":
                    headers = [
                        ("Set-Cookie", f"{cookie}; Path={path}; Secure; HttpOnly"),
                        ("Set-Cookie", f"{other_cookie}; Path=/api/auth; Secure; HttpOnly"),
                    ]
                return httpx.Response(200, headers=headers, json={"id": identity})
            if route == "logout" and fault == "login_no_logout_cookie":
                assert "Cookie" not in request.headers
            elif route == "logout" and fault == "login_different_logout_cookie":
                assert request.headers["Cookie"] == other_cookie
            elif fault != "login_no_cookie":
                assert request.headers["Cookie"] == cookie
            assert "Authorization" not in request.headers
            if route == "access":
                if fault == "access_unauthorized":
                    return httpx.Response(401, text=identity)
                allowed = {"access_false": False, "access_integer": 1}.get(fault, True)
                return httpx.Response(200, json={"can_create_project": allowed, "other": identity})
            return httpx.Response(503 if fault == "logout_unavailable" else 204)

    other_cookie = "omnia_session=private-different-cookie"
    path = tmp_path / "auth-result.json"
    assert _run_cli(AuthProduction(), monkeypatch, path, auth_only=True) == (fault != "ok")
    raw = path.read_text(encoding="utf-8")
    report = json.loads(raw)
    assert (report["mode"], report["stage"], report["code"]) == ("auth_only", stage, code)
    assert report["status"] == ("success" if fault == "ok" else "failure")
    assert report["cleanup"] == ("failed" if fault in {
        "login_no_logout_cookie", "login_different_logout_cookie", "logout_unavailable",
    } else "ok")
    assert seen == paths
    output = capsys.readouterr()
    for sensitive in (
        "canary@example.com", "secret-password", cookie, other_cookie, identity, "not-a-user",
    ):
        assert sensitive not in raw + output.out + output.err


def test_auth_only_requires_protected_credentials_before_any_request(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    canary_env(monkeypatch, PRODUCTION_CANARY_PASSWORD="")
    production = FakeProduction()
    path = tmp_path / "auth-result.json"

    assert _run_cli(production, monkeypatch, path, auth_only=True) == 1

    report = json.loads(path.read_text(encoding="utf-8"))
    assert report["mode"] == "auth_only"
    assert report["status"] == "failure"
    assert report["code"] == "configuration_invalid"
    assert production.calls == []


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

    steps = _canary_workflow()["jobs"]["canary"]["steps"]
    upload = next(step for step in steps
                  if step.get("with", {}).get("name") == "production-canary-result")
    assert upload["uses"] == "actions/upload-artifact@v4"
    assert upload["if"] == "always()"
    assert upload["with"]["if-no-files-found"] == "error"
    assert upload["with"]["path"] == "${{ runner.temp }}/production-canary-result.json"
    canary = next(step for step in steps
                  if step.get("name") == "Run disposable build, preview, and edit canary")
    assert "PRODUCTION_EXPECTED_RELEASE_SHA" not in canary["env"]
    for component in ("WEB", "API", "WORKER", "ORCHESTRATOR"):
        name = f"PRODUCTION_EXPECTED_{component}_RELEASE_SHA"
        assert canary["env"][name] == "${{ vars." + name + " }}"


def _canary_workflow() -> dict[str, Any]:
    workflow = Path(__file__).resolve().parents[3] / (
        ".github/workflows/production-generation-canary.yml"
    )
    return yaml.safe_load(workflow.read_text(encoding="utf-8"))


@pytest.mark.parametrize("input_name", ["report_incident", "catalog_acceptance", "auth_only"])
def test_manual_canary_options_default_to_boolean_false(input_name) -> None:
    workflow = _canary_workflow()
    # PyYAML's YAML 1.1 loader reads GitHub's unquoted `on` key as True.
    triggers = workflow.get("on", workflow.get(True))
    dispatch = triggers.get("workflow_dispatch") or {}
    report = dispatch.get("inputs", {}).get(input_name, {})
    assert report.get("type") == "boolean"
    assert report.get("default") is False


def _workflow_decisions(expression, cases):
    node = which("node")
    if node is None:
        pytest.skip("Node is required to evaluate the workflow's boolean expression")
    # These status functions, boolean inputs, ==, && and || have the same
    # semantics in JavaScript and the GitHub expression subset used by this gate.
    expression = expression.removeprefix("${{").removesuffix("}}").strip()
    result = run([node, "-e", """
const {expression, cases} = JSON.parse(require("node:fs").readFileSync(0, "utf8"));
const evaluate = new Function("github", "inputs", "success", "failure", "always",
  `return (${expression});`);
console.log(JSON.stringify(cases.map(test => Boolean(evaluate(
  {event_name: test.event}, test.inputs || {},
  () => test.status === "success", () => test.status === "failure", () => true,
)))));
"""], input=json.dumps({"expression": expression, "cases": cases}),
        capture_output=True, text=True, check=False, timeout=10)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


@pytest.mark.parametrize(("step_name", "expected"), [
    ("Open one generation-canary incident", [False, True, False, False, False, False,
                                           False, True, False, False, False]),
    ("Close generation-canary incident after recovery", [True, False, False, False, False,
                                                        False, True, False, False, False, False]),
])
def test_manual_incident_mutations_require_explicit_opt_in(step_name, expected) -> None:
    workflow = _canary_workflow()
    step = next(step for step in workflow["jobs"]["canary"]["steps"]
                if step.get("name") == step_name)
    cases = [
        {"event": event, "inputs": {} if report is None else {"report_incident": report},
         "status": status}
        for event, report, status in (
            ("schedule", None, "success"), ("schedule", None, "failure"),
            ("workflow_dispatch", None, "success"), ("workflow_dispatch", None, "failure"),
            ("workflow_dispatch", False, "success"), ("workflow_dispatch", False, "failure"),
            ("workflow_dispatch", True, "success"), ("workflow_dispatch", True, "failure"),
            ("workflow_dispatch", True, "cancelled"),
        )
    ]
    cases.extend(
        {"event": "workflow_dispatch", "status": status,
         "inputs": {"report_incident": True, "auth_only": True, "catalog_acceptance": True}}
        for status in ("success", "failure")
    )
    assert _workflow_decisions(step["if"], cases) == expected


@pytest.mark.parametrize(("event", "catalog", "auth_only", "expected_cli"), [
    ("schedule", False, False, "production_generation_canary.py"),
    ("schedule", True, True, "production_generation_canary.py"),
    ("workflow_dispatch", False, False, "production_generation_canary.py"),
    ("workflow_dispatch", True, False, "production_catalog_canary.py"),
    ("workflow_dispatch", False, True, "production_generation_canary.py --auth-only"),
    ("workflow_dispatch", True, True, "production_generation_canary.py --auth-only"),
])
@pytest.mark.parametrize("exit_code", [0, 17])
def test_workflow_routes_only_explicit_manual_modes_and_preserves_failure(
    event, catalog, auth_only, expected_cli, exit_code,
):
    workflow = _canary_workflow()
    step = next(step for step in workflow["jobs"]["canary"]["steps"]
                if step.get("name") == "Run disposable build, preview, and edit canary")
    cases = [{
        "event": event, "inputs": {"catalog_acceptance": catalog, "auth_only": auth_only},
        "status": "success",
    }]
    enabled = _workflow_decisions(step["env"].get("CATALOG_ACCEPTANCE", "false"), cases)[0]
    auth_enabled = _workflow_decisions(step["env"].get("AUTH_ONLY", "false"), cases)[0]
    bash = which("bash")
    git = which("git")
    if os.name == "nt" and git:
        git_bash = Path(git).resolve().parents[1] / "bin/bash.exe"
        if git_bash.is_file():
            bash = str(git_bash)
    if bash is None:
        pytest.skip("Bash is required to execute the fixed workflow routing script")
    result = run([bash, "-c", """
uv() { printf '%s\\n' "$*"; return "$TEST_CLI_EXIT"; }
""" + step["run"]], env={**os.environ, "CATALOG_ACCEPTANCE": str(enabled).lower(),
                         "AUTH_ONLY": str(auth_enabled).lower(),
                         "TEST_CLI_EXIT": str(exit_code)},
        capture_output=True, text=True, check=False, timeout=10)
    assert result.stdout.splitlines() == [f"run --frozen python scripts/{expected_cli}"]
    assert result.returncode == exit_code


def test_catalog_browser_and_evidence_are_manual_only_and_keep_the_production_boundary():
    workflow = _canary_workflow()
    job = workflow["jobs"]["canary"]
    assert job["needs"] == "authorize-ref"
    assert job["environment"] == "production"
    assert job["timeout-minutes"] == 50
    rejection = workflow["jobs"]["authorize-ref"]["steps"][0]
    assert rejection["if"] == "github.ref != 'refs/heads/main'"
    assert "exit 1" in rejection["run"]
    steps = job["steps"]
    browser = next((step for step in steps if "playwright install" in step.get("run", "")), None)
    assert browser is not None, "Catalog acceptance must install the frozen Chromium browser"
    assert browser["run"] == "uv run --frozen playwright install --with-deps chromium"
    assert browser["working-directory"] == "apps/api"
    evidence = next((step for step in steps if step.get("with", {}).get("name")
                     == "production-catalog-evidence"), None)
    assert evidence is not None, "Catalog acceptance must upload its sanitized evidence"
    assert evidence["uses"] == "actions/upload-artifact@v4"
    assert evidence["with"]["path"].splitlines() == [
        "${{ runner.temp }}/production-catalog-evidence/evidence.json",
        "${{ runner.temp }}/production-catalog-evidence/*.png",
    ]
    assert evidence["with"]["if-no-files-found"] == "error"
    cases = [{"event": event, "inputs": {"catalog_acceptance": catalog}, "status": status}
             for event, catalog, status in (
                 ("schedule", True, "success"), ("workflow_dispatch", False, "success"),
                 ("workflow_dispatch", True, "success"), ("workflow_dispatch", True, "failure"),
             )]
    assert _workflow_decisions(browser["if"], cases[:3]) == [False, False, True]
    assert _workflow_decisions(evidence["if"], cases) == [False, False, True, True]
    auth_override = [{
        "event": "workflow_dispatch", "inputs": {"catalog_acceptance": True, "auth_only": True},
        "status": status,
    } for status in ("success", "failure")]
    assert _workflow_decisions(browser["if"], auth_override) == [False, False]
    assert _workflow_decisions(evidence["if"], auth_override) == [False, False]
    canary = next(step for step in steps
                  if step.get("name") == "Run disposable build, preview, and edit canary")
    for name in ("PRODUCTION_CANARY_EMAIL", "PRODUCTION_CANARY_PASSWORD"):
        assert canary["env"][name] == "${{ secrets." + name + " }}"
    assert canary["env"]["PRODUCTION_CANARY_RESULT_FILE"] == (
        "${{ runner.temp }}/production-canary-result.json"
    )
    assert canary["env"]["PRODUCTION_CATALOG_ARTIFACT_DIR"] == (
        "${{ runner.temp }}/production-catalog-evidence"
    )


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


class ObservationClock:
    def __init__(self) -> None:
        self.value = 0.0
        self.delays: list[float] = []

    def __call__(self) -> float:
        return self.value

    def sleep(self, seconds: float) -> None:
        self.delays.append(seconds)
        self.value += seconds


def test_cli_observation_transport_failure_has_context_and_survives_cleanup_failure(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str],
) -> None:
    canary_env(monkeypatch)
    secret = "do-not-publish-request-url-headers-or-exception"
    errors = [httpx.ReadTimeout(secret, request=httpx.Request(
        "GET", f"https://secret.invalid/{secret}?token={secret}",
        headers={"authorization": f"Bearer {secret}"},
    )) for _ in range(3)]
    production = FakeProduction(edit_observations=errors, delete_status=503)
    path = tmp_path / "result.json"
    assert _run_cli(production, monkeypatch, path) == 1
    report = json.loads(path.read_text(encoding="utf-8"))
    assert report["stage"] == "edit" and report["code"] == "edit_failed"
    assert report["project_id"] == PROJECT_ID and report["run_id"] == EDIT_RUN_ID
    assert report["request_method"] == "GET" and report["request_route"] == "project_generation"
    assert report["request_failure"] == "transport" and report["transport_error"] == "read_timeout"
    assert report["observation_attempts"] == 3 and report["cleanup"] == "failed"
    stdout = capsys.readouterr().out
    events = [json.loads(line) for line in stdout.splitlines()]
    failed = next(i for i, row in enumerate(events) if row["step"] == "stage_failure")
    cleanup = next(i for i, row in enumerate(events) if row["step"] == "project_delete")
    assert failed < cleanup and events[failed]["run_id"] == EDIT_RUN_ID
    retries = [row["observation_attempts"] for row in events if row["step"] == "observation_retry"]
    assert retries == [1, 2]
    assert production.paths("POST").count(f"/api/projects/{PROJECT_ID}/prompt") == 2
    assert secret not in path.read_text(encoding="utf-8") + stdout


@pytest.mark.parametrize("outcome,reason,code", [
    (httpx.Response(503, text="private body"), "http_status", "edit_failed"),
    (httpx.Response(200, text="private body"), "invalid_json", "api_invalid_response"),
    (httpx.Response(200, json=["private body"]), "invalid_payload", "api_invalid_response"),
    (httpx.Response(200, json={"id": "private body", "status": "running"}),
     "generation_identity", "edit_failed"),
    (httpx.Response(200, json={"id": EDIT_RUN_ID, "status": ["private body"]}),
     "generation_status", "api_invalid_response"),
])
def test_cli_observation_response_failure_has_closed_cause_and_ids(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str],
    outcome: httpx.Response, reason: str, code: str,
) -> None:
    canary_env(monkeypatch)
    attempts = 3 if reason == "http_status" else 1
    production = FakeProduction(edit_observations=[outcome] * attempts)
    path = tmp_path / "result.json"
    assert _run_cli(production, monkeypatch, path) == 1
    report = json.loads(path.read_text(encoding="utf-8"))
    assert report["stage"] == "edit" and report["code"] == code
    assert report["request_failure"] == reason and report["observation_attempts"] == attempts
    assert report["project_id"] == PROJECT_ID and report["run_id"] == EDIT_RUN_ID
    assert report["request_method"] == "GET" and report["request_route"] == "project_generation"
    assert report["cleanup"] == "ok"
    assert "private body" not in path.read_text(encoding="utf-8") + capsys.readouterr().out


@pytest.mark.parametrize("error", [httpx.ConnectTimeout, httpx.ReadTimeout,
                                  httpx.ConnectError, httpx.RemoteProtocolError])
def test_transient_observation_retries_are_reads_only_and_recover_to_real_completion(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, error: type[httpx.HTTPError],
) -> None:
    canary_env(monkeypatch)
    clock = ObservationClock()
    production = FakeProduction(edit_observations=[error("private"), error("private")])
    assert _run_cli(production, monkeypatch, tmp_path / "result.json",
                    clock=clock, sleep=clock.sleep) == 0
    assert clock.delays == [1.0, 2.0]
    assert production.paths("POST").count(f"/api/projects/{PROJECT_ID}/prompt") == 2
    assert production.paths("DELETE") == [f"/api/projects/{PROJECT_ID}"]


@pytest.mark.parametrize("status", [429, 502, 503, 504])
def test_transient_HTTP_observation_recovers_without_new_mutation(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, status: int,
) -> None:
    canary_env(monkeypatch)
    production = FakeProduction(edit_observations=[httpx.Response(status)])
    assert _run_cli(production, monkeypatch, tmp_path / "result.json") == 0
    assert production.paths("POST").count(f"/api/projects/{PROJECT_ID}/prompt") == 2


@pytest.mark.parametrize("error,name", [(httpx.LocalProtocolError, "local_protocol_error"),
                                       (httpx.UnsupportedProtocol, "unsupported_protocol")])
def test_deterministic_transport_failures_are_never_retried(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, error: type[httpx.HTTPError], name: str,
) -> None:
    canary_env(monkeypatch)
    production = FakeProduction(edit_observations=[error("private")])
    path = tmp_path / "result.json"
    assert _run_cli(production, monkeypatch, path) == 1
    result = json.loads(path.read_text(encoding="utf-8"))
    assert result["observation_attempts"] == 1 and result["transport_error"] == name


def test_ambiguous_mutating_prompt_is_not_replayed_and_does_not_borrow_build_run_id(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    canary_env(monkeypatch)
    production = FakeProduction(edit_prompt_error=httpx.ReadTimeout("private"))
    path = tmp_path / "result.json"
    assert _run_cli(production, monkeypatch, path) == 1
    report = json.loads(path.read_text(encoding="utf-8"))
    assert report["request_method"] == "POST" and report["request_route"] == "project_prompt"
    assert report["project_id"] == PROJECT_ID and "run_id" not in report
    assert "observation_attempts" not in report
    assert production.paths("POST").count(f"/api/projects/{PROJECT_ID}/prompt") == 2


def test_observation_retry_honors_remaining_deadline_without_sleep_or_extra_GET(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    canary_env(monkeypatch, PRODUCTION_CANARY_TIMEOUT_SECONDS="300")
    clock = ObservationClock()

    class NearDeadline(FakeProduction):
        def handle(self, request: httpx.Request) -> httpx.Response:
            if request.url.path.endswith("/prompt") and self._edit_started:
                clock.value = 299.5
            return super().handle(request)

    production = NearDeadline(edit_observations=[httpx.ReadTimeout("private")])
    path = tmp_path / "result.json"
    assert _run_cli(production, monkeypatch, path, clock=clock, sleep=clock.sleep) == 1
    report = json.loads(path.read_text(encoding="utf-8"))
    assert report["observation_attempts"] == 1 and report["request_failure"] == "transport"
    assert report["run_id"] == EDIT_RUN_ID and clock.delays == [] and clock.value == 299.5


def test_expired_deadline_keeps_current_run_without_dispatching_another_observation(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    canary_env(monkeypatch, PRODUCTION_CANARY_TIMEOUT_SECONDS="300")
    clock = ObservationClock()

    class Expired(FakeProduction):
        def handle(self, request: httpx.Request) -> httpx.Response:
            if request.url.path.endswith("/prompt") and self._edit_started:
                clock.value = 300.0
            return super().handle(request)

    production = Expired()
    path = tmp_path / "result.json"
    assert _run_cli(production, monkeypatch, path, clock=clock, sleep=clock.sleep) == 1
    report = json.loads(path.read_text(encoding="utf-8"))
    assert report["code"] == "timeout" and report["request_failure"] == "deadline"
    assert report["project_id"] == PROJECT_ID and report["run_id"] == EDIT_RUN_ID
    assert report["request_route"] == "project_generation" and clock.delays == []
    assert report["observation_attempts"] == 0
    # Build observation and cleanup only; no edit observation after the deadline.
    assert production.paths("GET").count(f"/api/projects/{PROJECT_ID}/generation") == 2


def test_terminal_generation_failure_never_triggers_observation_retry(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str],
) -> None:
    canary_env(monkeypatch)
    production = FakeProduction(edit_generation_status="failed", edit_failure={
        "code": "provider_unavailable", "diagnostics": {"http_status": 502},
    })
    path = tmp_path / "result.json"
    assert _run_cli(production, monkeypatch, path) == 1
    report = json.loads(path.read_text(encoding="utf-8"))
    assert report["request_failure"] == "generation_terminal"
    assert report["observation_attempts"] == 1
    assert report["failure_code"] == "provider_unavailable" and report["cleanup"] == "ok"
    assert not any(json.loads(line)["step"] == "observation_retry"
                   for line in capsys.readouterr().out.splitlines())


def test_request_diagnostics_never_accept_raw_or_coerced_values() -> None:
    secret = "secret-url-or-exception"
    failure = CanaryFailure("fixed", request_diagnostics={
        "request_method": secret, "request_route": secret, "request_failure": secret,
        "transport_error": secret, "observation_attempts": True,
    })
    report = failure.diagnostics()
    assert not any(key in report for key in ("request_method", "request_route", "request_failure",
                                            "transport_error", "observation_attempts"))
    assert secret not in json.dumps(report)


@pytest.mark.parametrize("failing_step", ["stage_failure", "canary"])
@pytest.mark.parametrize("cleanup_status", [204, 503])
def test_cli_event_sink_failure_keeps_primary_request_cause_and_cleanup_result(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str],
    failing_step: str, cleanup_status: int,
) -> None:
    from scripts import production_generation_canary as cli

    canary_env(monkeypatch)
    original_emit = cli._emit
    secret = "secret-from-failing-event-sink"

    def failing_emit(event: dict[str, object]) -> None:
        if event["step"] == failing_step:
            raise OSError(secret)
        original_emit(event)

    monkeypatch.setattr(cli, "_emit", failing_emit)
    production = FakeProduction(edit_observations=[httpx.Response(502)] * 3,
                                delete_status=cleanup_status)
    path = tmp_path / "result.json"
    assert _run_cli(production, monkeypatch, path) == 1
    report = json.loads(path.read_text(encoding="utf-8"))
    assert report["stage"] == "edit" and report["code"] == "edit_failed"
    assert report["project_id"] == PROJECT_ID and report["run_id"] == EDIT_RUN_ID
    assert report["http_status"] == 502 and report["request_failure"] == "http_status"
    assert report["diagnostic_event_emission_failed"] is True
    assert report["cleanup"] == ("ok" if cleanup_status == 204 else "failed")
    assert secret not in path.read_text(encoding="utf-8") + capsys.readouterr().out

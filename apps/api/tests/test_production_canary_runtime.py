"""The off-host probe follows the public asynchronous runtime contract."""

import httpx
import pytest

from tests.production_canary_fakes import PROJECT_ID, FakeProduction, matrix
from yleum_api.ops.production_canary import CanaryConfig, CanaryFailure, ProductionCanary
from yleum_api.schemas.runtime import RuntimeStatus


def run_runtime(*, start="provisioning", probes=("provisioning", "running"),
                start_status=200, probe_status=200, start_elapsed=0, overall=600):
    production = FakeProduction()
    clock = [0.0]
    timeouts = []
    remaining_probes = iter(probes)
    runtime_path = f"/api/projects/{PROJECT_ID}/runtime"

    def handler(request):
        path = request.url.path
        if path not in {runtime_path, runtime_path + "/start"}:
            return production.handle(request)
        production.calls.append((request.method, path))
        timeouts.append(request.extensions["timeout"]["read"])
        if request.method == "POST":
            clock[0] += start_elapsed
            state, status = start, start_status
        else:
            state, status = next(remaining_probes, "provisioning"), probe_status
        if status != 200:
            return httpx.Response(status, json={"error": {"code": "conflict"}})
        payload = (RuntimeStatus(state=state).model_dump(mode="json")
                   if state in {"provisioning", "running", "paused", "stopped", "failed"}
                   else {"state": state})
        return httpx.Response(200, json=payload)

    def sleep(seconds):
        clock[0] += seconds

    config = CanaryConfig(
        base_url="https://constructor.example.com", email="canary@example.com",
        password="unit-secret", expected_releases=matrix(),
        preview_host_suffix=".preview.lead-generator.ru",
        overall_timeout_seconds=overall, poll_seconds=5,
    )
    probe = ProductionCanary(config, transport=httpx.MockTransport(handler),
                             clock=lambda: clock[0], sleep=sleep)
    return probe, production, clock, timeouts


def test_async_start_is_observed_until_running_before_preview_and_edit():
    probe, production, clock, timeouts = run_runtime(start_elapsed=120)
    result = probe.run()
    runtime_path = f"/api/projects/{PROJECT_ID}/runtime"
    assert production.paths("POST").count(runtime_path + "/start") == 1
    assert production.paths("GET").count(runtime_path) == 2
    assert production.calls.index(("GET", runtime_path)) < production.calls.index(
        ("POST", f"/api/projects/{PROJECT_ID}/max/preview-session")
    )
    assert timeouts == [330, 30, 30]
    assert clock[0] == 130
    assert result.cleanup_complete is True


def test_already_running_start_needs_no_runtime_poll():
    probe, production, _, _ = run_runtime(start="running")
    assert probe.run().cleanup_complete is True
    assert f"/api/projects/{PROJECT_ID}/runtime" not in production.paths("GET")


@pytest.mark.parametrize("state", ["stopped", "paused", "failed", "unknown", None])
@pytest.mark.parametrize("where", ["start", "poll"])
def test_failed_or_invalid_runtime_state_never_reaches_preview(state, where):
    probe, production, _, _ = run_runtime(
        start=state if where == "start" else "provisioning", probes=(state,),
    )
    with pytest.raises(CanaryFailure) as caught:
        probe.run()
    assert caught.value.stage == "runtime_start"
    assert caught.value.code == "runtime_failed"
    assert caught.value.cleanup == "ok"
    assert f"/api/projects/{PROJECT_ID}/max/preview-session" not in production.paths("POST")


@pytest.mark.parametrize("status", [401, 409, 503])
@pytest.mark.parametrize("where", ["start", "poll"])
def test_http_failures_are_reported_without_repeating_start(status, where):
    probe, production, _, _ = run_runtime(
        start_status=status if where == "start" else 200,
        probe_status=status if where == "poll" else 200,
    )
    with pytest.raises(CanaryFailure) as caught:
        probe.run()
    assert caught.value.http_status == status
    assert caught.value.stage == "runtime_start"
    assert caught.value.cleanup == "ok"
    assert production.paths("POST").count(f"/api/projects/{PROJECT_ID}/runtime/start") == 1


@pytest.mark.parametrize("overall,expected", [(600, 330), (300, 300)])
def test_provisioning_has_bounded_stage_and_overall_budgets(overall, expected):
    probe, production, clock, timeouts = run_runtime(probes=(), overall=overall)
    with pytest.raises(CanaryFailure) as caught:
        probe.run()
    assert caught.value.stage == "runtime_start"
    assert caught.value.code == "timeout"
    assert caught.value.cleanup == "ok"
    assert clock[0] == expected
    assert timeouts[0] == expected
    assert production.paths("POST").count(f"/api/projects/{PROJECT_ID}/runtime/start") == 1

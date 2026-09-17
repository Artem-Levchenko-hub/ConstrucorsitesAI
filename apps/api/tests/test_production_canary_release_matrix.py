"""The generation canary must accept independently deployed components.

Production deploys web and the API image separately, so one shared expected
revision cannot describe a correct release. These tests pin the per-component
contract: every checked component has its own allowed revision, an unexpected
revision on any of them fails with the component named, and a revision that
changes mid-run is still a delivery-identity failure.
"""

from __future__ import annotations

import pytest

from omnia_api.ops.production_canary import (
    CanaryConfig,
    CanaryConfigurationError,
    CanaryFailure,
    ProductionCanary,
)
from tests.production_canary_fakes import (
    OTHER_RELEASE_SHA,
    RELEASE_SHA,
    FakeProduction,
    canary_env,
    matrix,
)

WEB_SHA = "c0ffee1"
API_SHA = "d15ea5e"


def _run(production: FakeProduction) -> None:
    ProductionCanary(
        CanaryConfig.from_env(),
        transport=production.transport(),
        sleep=lambda _seconds: None,
    ).run()


def test_mixed_web_and_api_revisions_pass_when_both_are_expected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """C01: web on A and the API image on B is a correct release, not a fault."""

    canary_env(
        monkeypatch,
        PRODUCTION_EXPECTED_WEB_RELEASE_SHA=WEB_SHA,
        PRODUCTION_EXPECTED_API_RELEASE_SHA=API_SHA,
        PRODUCTION_EXPECTED_WORKER_RELEASE_SHA=API_SHA,
        PRODUCTION_EXPECTED_ORCHESTRATOR_RELEASE_SHA=API_SHA,
    )
    production = FakeProduction(
        releases=matrix(
            web=WEB_SHA,
            api=API_SHA,
            worker=API_SHA,
            generation_worker=API_SHA,
            orchestrator=API_SHA,
        )
    )

    _run(production)

    assert ("DELETE", "/api/projects/10000000-0000-4000-8000-000000000001") in production.calls


@pytest.mark.parametrize("component", ["web", "api", "worker", "orchestrator"])
def test_unexpected_component_revision_names_that_component(
    monkeypatch: pytest.MonkeyPatch,
    component: str,
) -> None:
    """C02: the failure says which component drifted, and from what to what."""

    canary_env(monkeypatch)
    production = FakeProduction(releases=matrix(**{component: OTHER_RELEASE_SHA}))

    with pytest.raises(CanaryFailure) as failure:
        _run(production)

    assert failure.value.code == "release_mismatch"
    assert failure.value.stage == "release_health"
    assert failure.value.component == component
    assert failure.value.expected == RELEASE_SHA
    assert failure.value.actual == OTHER_RELEASE_SHA
    assert "/api/auth/login" not in production.paths("POST")


def test_stale_generation_worker_fails_while_the_plain_worker_is_healthy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """C03: the generation worker is checked in its own right."""

    canary_env(monkeypatch)
    production = FakeProduction(releases=matrix(generation_worker=OTHER_RELEASE_SHA))

    with pytest.raises(CanaryFailure) as failure:
        _run(production)

    assert failure.value.code == "release_mismatch"
    assert failure.value.component == "generation_worker"
    assert failure.value.actual == OTHER_RELEASE_SHA


def test_generation_worker_expectation_can_be_pinned_separately(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A separate expectation wins over the shared API image default."""

    canary_env(
        monkeypatch,
        PRODUCTION_EXPECTED_GENERATION_WORKER_RELEASE_SHA=OTHER_RELEASE_SHA,
    )
    production = FakeProduction(releases=matrix(generation_worker=OTHER_RELEASE_SHA))

    _run(production)

    prompt_path = "/api/projects/10000000-0000-4000-8000-000000000001/prompt"
    assert production.paths("POST").count(prompt_path) == 2


def test_generation_worker_defaults_to_the_worker_expectation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """api, worker and generation-worker share one image in the production compose."""

    canary_env(monkeypatch)

    config = CanaryConfig.from_env()

    assert config.expected_releases["generation_worker"] == RELEASE_SHA
    assert config.expected_releases["worker"] == RELEASE_SHA


def test_release_change_during_the_run_fails_delivery_identity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """C05: a component replaced mid-canary invalidates the whole result."""

    canary_env(monkeypatch)
    production = FakeProduction(
        releases=matrix(),
        releases_after_edit=matrix(orchestrator=OTHER_RELEASE_SHA),
    )

    with pytest.raises(CanaryFailure) as failure:
        _run(production)

    assert failure.value.code == "release_changed"
    assert failure.value.component == "orchestrator"
    assert failure.value.stage == "final_release_health"


@pytest.mark.parametrize(
    "name",
    [
        "PRODUCTION_EXPECTED_WEB_RELEASE_SHA",
        "PRODUCTION_EXPECTED_API_RELEASE_SHA",
        "PRODUCTION_EXPECTED_WORKER_RELEASE_SHA",
        "PRODUCTION_EXPECTED_ORCHESTRATOR_RELEASE_SHA",
    ],
)
def test_each_component_expectation_is_required(
    monkeypatch: pytest.MonkeyPatch,
    name: str,
) -> None:
    canary_env(monkeypatch, **{name: ""})

    with pytest.raises(CanaryConfigurationError, match=name):
        CanaryConfig.from_env()


def test_shared_expectation_alone_is_no_longer_accepted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The old single-SHA contract must not silently come back."""

    for name in (
        "PRODUCTION_EXPECTED_WEB_RELEASE_SHA",
        "PRODUCTION_EXPECTED_API_RELEASE_SHA",
        "PRODUCTION_EXPECTED_WORKER_RELEASE_SHA",
        "PRODUCTION_EXPECTED_ORCHESTRATOR_RELEASE_SHA",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("PRODUCTION_CANARY_EMAIL", "canary@example.com")
    monkeypatch.setenv("PRODUCTION_CANARY_PASSWORD", "secret-password")
    monkeypatch.setenv("PRODUCTION_EXPECTED_RELEASE_SHA", RELEASE_SHA)

    with pytest.raises(CanaryConfigurationError):
        CanaryConfig.from_env()


@pytest.mark.parametrize("value", ["A7C4FC22", "nothex", "", "0" * 41])
def test_component_expectation_must_be_a_release_sha(
    monkeypatch: pytest.MonkeyPatch,
    value: str,
) -> None:
    canary_env(monkeypatch)
    monkeypatch.setenv("PRODUCTION_EXPECTED_WEB_RELEASE_SHA", value)

    with pytest.raises(CanaryConfigurationError):
        CanaryConfig.from_env()

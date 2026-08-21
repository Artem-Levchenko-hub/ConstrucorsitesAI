"""Deploy-attestation gate defaults + production fail-closed wiring."""

from __future__ import annotations

import inspect

from omnia_api.core.config import Settings
from omnia_api.services.deploy_attestation import blocking_required


def test_deploy_gate_advisory_by_default() -> None:
    assert Settings.model_fields["use_deploy_attestation_gate"].default is True


def test_deploy_gate_does_not_block_by_default() -> None:
    assert Settings.model_fields["deploy_attestation_blocking"].default is False


def test_project_memory_defaults_on() -> None:
    assert Settings.model_fields["use_project_memory"].default is True


def test_deploy_gate_is_always_blocking_in_production() -> None:
    settings = Settings.model_construct(env="prod", deploy_attestation_blocking=False)
    assert blocking_required(settings)


def test_trigger_deploy_consults_the_attestation() -> None:
    from omnia_api.routers import runtime

    src = inspect.getsource(runtime.trigger_deploy)
    assert "resolve_deploy_proof" in src
    assert "blocking_required" in src
    assert "DEPLOY-GATE" in src


def test_production_compose_enables_blocking_by_default() -> None:
    from pathlib import Path

    compose = (Path(__file__).parents[2] / "llm-gateway/deploy/full/docker-compose.yml").read_text()
    assert "DEPLOY_ATTESTATION_BLOCKING: ${DEPLOY_ATTESTATION_BLOCKING:-true}" in compose
    assert "USE_AGENTIC_BUILDER: ${USE_AGENTIC_BUILDER:-true}" in compose
    assert "USE_RUNTIME_GATES: ${USE_RUNTIME_GATES:-true}" in compose
    assert "AGENT_REQUIRE_GREEN_BEFORE_DONE: ${AGENT_REQUIRE_GREEN_BEFORE_DONE:-true}" in compose

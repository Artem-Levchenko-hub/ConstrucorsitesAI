"""Defaults the production compose must keep enabled.

The legacy deploy-attestation gate left with the publication path it guarded:
a MAX app publishes through its cell, where the proof is checked by identity
(proof key, workspace revision, migration digest, fencing epoch) before the
release is promoted — a stricter check than the advisory attestation ever was.
"""

from __future__ import annotations

from yleum_api.core.config import Settings


def test_project_memory_defaults_on() -> None:
    assert Settings.model_fields["use_project_memory"].default is True


def test_production_compose_enables_blocking_by_default() -> None:
    from pathlib import Path

    compose = (Path(__file__).parents[2] / "llm-gateway/deploy/full/docker-compose.yml").read_text()
    assert "USE_AGENTIC_BUILDER: ${USE_AGENTIC_BUILDER:-true}" in compose
    assert "USE_RUNTIME_GATES: ${USE_RUNTIME_GATES:-true}" in compose
    assert "AGENT_REQUIRE_GREEN_BEFORE_DONE: ${AGENT_REQUIRE_GREEN_BEFORE_DONE:-true}" in compose

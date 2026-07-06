"""Deploy-attestation gate defaults + wiring — pure, no DB.

Safe-rollout invariant: the gate is ADVISORY by default (logs the build's verdict
at deploy) and does NOT block. Blocking must be an explicit opt-in — a regression
that defaults it on would refuse every deploy of a project built before
attestations existed.
"""

from __future__ import annotations

import inspect

from omnia_api.core.config import Settings


def test_deploy_gate_advisory_by_default() -> None:
    assert Settings.model_fields["use_deploy_attestation_gate"].default is True


def test_deploy_gate_does_not_block_by_default() -> None:
    assert Settings.model_fields["deploy_attestation_blocking"].default is False


def test_trigger_deploy_consults_the_attestation() -> None:
    # Guard the wiring: the deploy handler must look up the attestation + honour the
    # blocking flag (defence against a refactor that drops the gate).
    from omnia_api.routers import runtime

    src = inspect.getsource(runtime.trigger_deploy)
    assert "Attestation" in src
    assert "deploy_attestation_blocking" in src
    assert "DEPLOY-GATE" in src

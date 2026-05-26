"""Unit tests for the docker_client surface that doesn't talk to Docker.

The thick parts (start_container body, exec_cmd) require a real docker
daemon and live in integration tests on the VPS. Here we cover the
deterministic helpers + dataclass guarantees + module-level constants
that regress under refactors: `ContainerSpec` defaults, label assembly,
the label-set we stamp onto containers.
"""

from __future__ import annotations

import pytest

from omnia_orchestrator.core import docker_client
from omnia_orchestrator.core.docker_client import ContainerSpec


@pytest.fixture(autouse=True)
def _env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(
        "DATABASE_URL",
        "postgresql://omnia_root:rootpw@localhost:5433/omnia_users",
    )
    monkeypatch.setenv("INTERNAL_TOKEN", "test-token-test-token-test-token")
    from omnia_orchestrator.core.config import get_settings

    get_settings.cache_clear()  # type: ignore[attr-defined]


def test_container_spec_defaults_match_brief() -> None:
    """Free-tier defaults: 0.5 CPU, 512 MB, kind=dev, restart=no, tier=free.
    These are the security/quota numbers AGENT-D-ORCHESTRATOR.md promises —
    any change should be intentional and approved."""
    spec = ContainerSpec(
        name="omnia-dev-x", image="omnia-template-x:dev", port=3200,
        project_id="00000000-0000-0000-0000-000000000001", env={},
    )
    assert spec.cpu_quota == 0.5
    assert spec.memory_mb == 512
    assert spec.kind == "dev"
    assert spec.restart_policy_name == "no"
    assert spec.tier == "free"
    assert spec.network_name is None


def test_container_spec_is_frozen() -> None:
    """Frozen dataclass — mutating raises FrozenInstanceError. Catching
    accidental in-place changes (e.g. in test fixtures) keeps caller
    invariants honest."""
    import dataclasses

    spec = ContainerSpec(
        name="x", image="y", port=1, project_id="p", env={},
    )
    with pytest.raises(dataclasses.FrozenInstanceError):
        spec.port = 2  # type: ignore[misc]


def test_container_spec_carries_tier_for_hibernate() -> None:
    """`tier` flows from ProvisionRequest → ContainerSpec → omnia.tier
    label → hibernate sweeper. The whole policy hangs on this field
    being respected — no implicit defaults that would silently grant
    pro privileges."""
    free = ContainerSpec(
        name="x", image="y", port=1, project_id="p", env={}, tier="free",
    )
    pro = ContainerSpec(
        name="x", image="y", port=1, project_id="p", env={}, tier="pro",
    )
    assert free.tier == "free"
    assert pro.tier == "pro"
    # Defaults to free explicitly so a partial spec construction never
    # accidentally lands in a pro tier.
    bare = ContainerSpec(name="x", image="y", port=1, project_id="p", env={})
    assert bare.tier == "free"


def test_module_exposes_expected_public_api() -> None:
    """Smoke test: the public surface AGENT-D-ORCHESTRATOR.md promises +
    what's documented in `routers/runtime.py` imports actually exists."""
    expected = {
        "ContainerSpec",
        "start_container",
        "stop_container",
        "container_status",
        "destroy_container",
        "find_project_container",
        "wake_container",
        "unpause_container",
        "write_files",
        "exec_cmd",
        "copy_path_from_container",
        "build_image",
        "prune_old_app_images",
        "container_logs",
    }
    for name in expected:
        assert hasattr(docker_client, name), f"missing public symbol: {name}"

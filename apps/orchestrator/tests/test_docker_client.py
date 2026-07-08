"""Unit tests for the docker_client surface that doesn't talk to Docker.

The thick parts (start_container body, exec_cmd) require a real docker
daemon and live in integration tests on the VPS. Here we cover the
deterministic helpers + dataclass guarantees + module-level constants
that regress under refactors: `ContainerSpec` defaults, label assembly,
the label-set we stamp onto containers.
"""

from __future__ import annotations

from typing import Any

import docker  # type: ignore[import-untyped]
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


class _FakeContainer:
    def __init__(self, cid: str, image: str, status: str = "running") -> None:
        self.id = cid
        self.status = status
        self.attrs = {"Config": {"Image": image}}
        self.removed = False
        self.started = False

    def reload(self) -> None:
        pass

    def start(self) -> None:
        self.started = True
        self.status = "running"

    def unpause(self) -> None:
        self.status = "running"

    def remove(self, force: bool = False) -> None:
        self.removed = True


class _FakeContainers:
    def __init__(self, existing: _FakeContainer | None) -> None:
        self._existing = existing
        self.run_image: str | None = None
        self.run_called = False
        self.run_kwargs: dict[str, Any] = {}

    def get(self, name: str) -> _FakeContainer:
        if self._existing is None:
            raise docker.errors.NotFound(name)
        return self._existing

    def run(self, *, image: str, **kwargs: Any) -> _FakeContainer:
        self.run_called = True
        self.run_image = image
        self.run_kwargs = kwargs
        return _FakeContainer("new-container-id", image)


class _FakeNetworks:
    def __init__(self, existing: set[str] | None = None) -> None:
        self.existing = existing or set()
        self.created: list[str] = []

    def get(self, name: str) -> object:
        if name not in self.existing:
            raise docker.errors.NotFound(name)
        return object()

    def create(self, name: str, **_: Any) -> object:
        self.created.append(name)
        self.existing.add(name)
        return object()


class _FakeClient:
    def __init__(self, existing: _FakeContainer | None) -> None:
        self.containers = _FakeContainers(existing)
        self.networks = _FakeNetworks()


def _spec(image: str) -> ContainerSpec:
    return ContainerSpec(
        name="omnia-dev-x", image=image, port=3200,
        project_id="00000000-0000-0000-0000-000000000001", env={},
    )


async def test_start_container_recreates_on_image_change(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Stack switch (drizzle→nextjs-entities) re-provisions with a new image.
    The stale container must be removed and a fresh one created from the new
    image — otherwise generated entity code runs against the wrong template's
    kit and 500s on `@/components/ui/*`. Regression for the stack-switch bug."""
    stale = _FakeContainer("old-id", "omnia-template-nextjs-postgres-drizzle:dev")
    client = _FakeClient(stale)
    monkeypatch.setattr(docker_client, "_get_client", lambda: client)

    cid = await docker_client.start_container(_spec("omnia-template-nextjs-entities:dev"))

    assert stale.removed is True, "stale container must be removed on image change"
    assert client.containers.run_called is True
    assert client.containers.run_image == "omnia-template-nextjs-entities:dev"
    assert cid == "new-container-id"


async def test_start_container_reuses_when_image_matches(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Same image tag (incl. a same-tag rebuild) → reuse the running container,
    never recreate. A rebuild must not disturb live apps mid-session."""
    same = _FakeContainer("live-id", "omnia-template-nextjs-entities:dev", status="running")
    client = _FakeClient(same)
    monkeypatch.setattr(docker_client, "_get_client", lambda: client)

    cid = await docker_client.start_container(_spec("omnia-template-nextjs-entities:dev"))

    assert same.removed is False
    assert client.containers.run_called is False
    assert cid == "live-id"


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


# ── Sandbox hardening (Phase 1) ─────────────────────────────────────────────


def test_container_spec_hardening_defaults_off() -> None:
    """Hardening knobs default to OFF so a bare spec produces today's exact
    run kwargs — the feature ships dark (R-10). A change here means prod
    container behaviour changed and must be intentional."""
    spec = ContainerSpec(name="x", image="y", port=1, project_id="p", env={})
    assert spec.runtime == ""
    assert spec.harden is False
    assert spec.pids_limit == 0


async def test_start_container_default_adds_no_security_kwargs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With default spec, start_container passes NO runtime/security_opt/
    pids_limit — byte-identical to pre-Phase-1 so OFF is a true no-op."""
    client = _FakeClient(None)
    monkeypatch.setattr(docker_client, "_get_client", lambda: client)

    await docker_client.start_container(_spec("omnia-template-x:dev"))

    assert client.containers.run_called is True
    assert "runtime" not in client.containers.run_kwargs
    assert "security_opt" not in client.containers.run_kwargs
    assert "pids_limit" not in client.containers.run_kwargs


async def test_start_container_applies_hardening_when_enabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When the spec carries gVisor runtime + harden, those land on the
    docker run kwargs: runtime=runsc, no-new-privileges, PID ceiling."""
    client = _FakeClient(None)
    monkeypatch.setattr(docker_client, "_get_client", lambda: client)

    spec = ContainerSpec(
        name="omnia-dev-x", image="omnia-template-x:dev", port=3200,
        project_id="00000000-0000-0000-0000-000000000001", env={},
        runtime="runsc", harden=True, pids_limit=512,
    )
    await docker_client.start_container(spec)

    kw = client.containers.run_kwargs
    assert kw.get("runtime") == "runsc"
    assert kw.get("security_opt") == ["no-new-privileges:true"]
    assert kw.get("pids_limit") == 512


async def test_start_container_harden_without_pids_limit_omits_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """harden on but pids_limit=0 → no-new-privileges applies, pids_limit is
    omitted (0 must not become a real, accidental ceiling)."""
    client = _FakeClient(None)
    monkeypatch.setattr(docker_client, "_get_client", lambda: client)

    spec = ContainerSpec(
        name="omnia-dev-x", image="omnia-template-x:dev", port=3200,
        project_id="00000000-0000-0000-0000-000000000001", env={},
        harden=True, pids_limit=0,
    )
    await docker_client.start_container(spec)

    kw = client.containers.run_kwargs
    assert kw.get("security_opt") == ["no-new-privileges:true"]
    assert "pids_limit" not in kw
    assert "runtime" not in kw  # runtime empty → omitted


async def test_start_container_default_creates_no_network(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Default spec (network_name None) → shared runtime net, NO per-project
    network created — byte-identical to pre-Phase-1."""
    client = _FakeClient(None)
    monkeypatch.setattr(docker_client, "_get_client", lambda: client)

    await docker_client.start_container(_spec("omnia-template-x:dev"))

    assert client.networks.created == []


async def test_start_container_creates_per_project_network_when_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When the spec names a per-project network, start_container ensures it
    exists (idempotent) and runs the container on it (isolation)."""
    client = _FakeClient(None)
    monkeypatch.setattr(docker_client, "_get_client", lambda: client)

    spec = ContainerSpec(
        name="omnia-dev-x", image="omnia-template-x:dev", port=3200,
        project_id="00000000-0000-0000-0000-000000000001", env={},
        network_name="omnia-proj-1",
    )
    await docker_client.start_container(spec)

    assert "omnia-proj-1" in client.networks.created
    assert client.containers.run_kwargs.get("network") == "omnia-proj-1"


# ── _wake_if_stopped (wake-on-agent-op; 2026-07-08 hibernate-mid-build fix) ──


class _WakeFakeContainer:
    """Container double for the wake helper: status transitions on start/unpause."""

    def __init__(self, status: str = "exited", *, fail_start: bool = False) -> None:
        self.status = status
        self._fail_start = fail_start
        self.start_calls = 0
        self.unpause_calls = 0

    def reload(self) -> None:  # status is already current on the fake
        pass

    def start(self) -> None:
        self.start_calls += 1
        if self._fail_start:
            raise docker.errors.APIError("boom")
        self.status = "running"

    def unpause(self) -> None:
        self.unpause_calls += 1
        self.status = "running"


def test_wake_if_stopped_starts_exited_container() -> None:
    c = _WakeFakeContainer(status="exited")
    docker_client._wake_if_stopped(c, "omnia-dev-x")
    assert c.start_calls == 1
    assert c.status == "running"


def test_wake_if_stopped_unpauses_paused_container() -> None:
    c = _WakeFakeContainer(status="paused")
    docker_client._wake_if_stopped(c, "omnia-dev-x")
    assert c.unpause_calls == 1
    assert c.status == "running"


def test_wake_if_stopped_running_is_noop() -> None:
    c = _WakeFakeContainer(status="running")
    docker_client._wake_if_stopped(c, "omnia-dev-x")
    assert c.start_calls == 0
    assert c.unpause_calls == 0


def test_wake_if_stopped_raises_structured_409_when_wake_fails() -> None:
    from omnia_orchestrator.core.errors import OrchestratorError

    c = _WakeFakeContainer(status="exited", fail_start=True)
    with pytest.raises(OrchestratorError) as ei:
        docker_client._wake_if_stopped(c, "omnia-dev-x")
    assert ei.value.code == "container_not_running"
    assert ei.value.status_code == 409

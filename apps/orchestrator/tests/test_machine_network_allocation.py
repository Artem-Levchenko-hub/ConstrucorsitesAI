import importlib
import importlib.util

import pytest


def allocator():
    name = "omnia_orchestrator.services.machine_network_allocation"
    assert importlib.util.find_spec(name) is not None, "explicit cell subnet allocation is missing"
    return importlib.import_module(name).choose_subnet


def test_allocator_uses_only_the_configured_pool_and_skips_existing_networks():
    allocate = allocator()
    first = allocate("10.253.240.0/24", [], "project-one")
    second = allocate("10.253.240.0/24", [first], "project-one")
    assert first != second
    assert first.startswith("10.253.240.")
    assert first.endswith("/28")
    assert allocate("10.253.240.0/24", [], "project-one") == first


def test_allocator_rejects_overlapping_host_routes_and_exhaustion():
    allocate = allocator()
    with pytest.raises(ValueError, match="exhausted"):
        allocate("10.253.240.0/28", ["10.253.240.0/28"], "one")
    with pytest.raises(ValueError, match="exhausted"):
        allocate("10.253.240.0/24", ["10.253.0.0/16"], "one")
    with pytest.raises(ValueError, match="private"):
        allocate("8.8.8.0/24", [], "one")


async def test_existing_cell_network_creation_uses_explicit_ipam_before_machine_start():
    from types import SimpleNamespace

    from omnia_orchestrator.services.docker_py_cell_backend import DockerPyCellBackend
    from tests.test_docker_py_cell_backend import _labels

    created = []

    class Networks:
        def list(self):
            return []

        def create(self, name, **kwargs):
            created.append(kwargs)
            return SimpleNamespace(id="network-one", name=name, attrs={"Labels": kwargs["labels"]})

    client = SimpleNamespace(networks=Networks(), ping=lambda: True)
    backend = DockerPyCellBackend(
        "unix:///test/docker.sock",
        "helper@sha256:" + "a" * 64,
        client_factory=lambda _: client,
        network_pool="10.253.240.0/24",
    )
    result = await backend.create_network("new-cell-internal", _labels("internal"), internal=True)
    assert result.internal is True
    assert created[0]["ipam"]["Config"][0]["Subnet"].startswith("10.253.240.")
    assert created[0]["internal"] is True


def test_host_and_none_docker_networks_have_null_ipam_config():
    from types import SimpleNamespace

    from omnia_orchestrator.services.machine_network_allocation import docker_network_subnets

    client = SimpleNamespace(
        networks=SimpleNamespace(
            list=lambda: [
                SimpleNamespace(attrs={"IPAM": {"Config": None}}),
                SimpleNamespace(attrs={"IPAM": {"Config": [{"Subnet": "172.17.0.0/16"}]}}),
            ]
        )
    )
    assert docker_network_subnets(client) == ["172.17.0.0/16"]


def test_parallel_pool_collision_rescans_and_retries_only_overlap():
    from types import SimpleNamespace

    import docker

    from omnia_orchestrator.services import machine_network_allocation as allocation

    occupied = []
    attempts = []

    class Networks:
        def list(self):
            return [
                SimpleNamespace(attrs={"IPAM": {"Config": [{"Subnet": value}]}})
                for value in occupied
            ]

        def create(self, name, **kwargs):
            subnet = kwargs["ipam"]["Config"][0]["Subnet"]
            attempts.append(subnet)
            if len(attempts) == 1:
                occupied.append(subnet)  # Another project won between scan and create.
                raise docker.errors.APIError("Pool overlaps with other one on this address space")
            return "created"

    assert (
        allocation.create_pool_network(
            SimpleNamespace(networks=Networks()), "10.253.240.0/24", "new-cell", internal=True
        )
        == "created"
    )
    assert len(attempts) == 2 and attempts[0] != attempts[1]


def test_pool_collision_retry_is_bounded_and_other_errors_are_not_retried():
    from types import SimpleNamespace

    import docker

    from omnia_orchestrator.services import machine_network_allocation as allocation

    attempts = []

    def create(*args, **kwargs):
        attempts.append(kwargs)
        raise docker.errors.APIError("Pool overlaps with other one on this address space")

    client = SimpleNamespace(networks=SimpleNamespace(list=lambda: [], create=create))
    with pytest.raises(docker.errors.APIError, match="overlap"):
        allocation.create_pool_network(client, "10.253.240.0/24", "new-cell")
    assert len(attempts) == 8

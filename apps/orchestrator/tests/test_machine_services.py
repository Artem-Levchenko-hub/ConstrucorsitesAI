import importlib
import importlib.util

import pytest

from omnia_orchestrator.core.project_machine import MachineManifest
from tests.test_project_machine import fixture
from tests.test_project_machine_manifest import payload


def module():
    name = "omnia_orchestrator.services.machine_services"
    assert importlib.util.find_spec(name) is not None, "machine supervision is missing"
    return importlib.import_module(name)


class ServicesBackend:
    def __init__(self):
        self.names = []
        self.healthy = True

    def start_service(self, service, epoch):
        self.names.append(service.name)

    def service_status(self, service, epoch):
        return {
            "name": service.name,
            "state": "running" if self.healthy else "failed",
            "ready": self.healthy,
            "log_tail": "started",
        }

    def stop_services(self):
        self.names.clear()


async def test_services_start_in_dependency_order_and_report_readiness(tmp_path):
    api = module()
    machine, _backend, _lease, mutation = fixture(tmp_path)
    manifest = MachineManifest.model_validate(payload())
    await machine.ensure(manifest, mutation)
    backend = ServicesBackend()
    services = api.MachineServices(machine, backend)
    status = await services.reconcile(manifest, mutation)
    assert backend.names == ["api", "worker"]
    assert all(item.ready for item in status)
    assert (await services.logs("api", mutation)) == "started"


async def test_unhealthy_dependency_stops_launching_downstream_services(tmp_path):
    api = module()
    machine, _backend, _lease, mutation = fixture(tmp_path)
    manifest = MachineManifest.model_validate(payload())
    await machine.ensure(manifest, mutation)
    backend = ServicesBackend()
    backend.healthy = False
    services = api.MachineServices(machine, backend)
    with pytest.raises(api.MachineServiceFailed, match="api"):
        await services.reconcile(manifest, mutation)
    assert backend.names == ["api"]


async def test_stale_fence_prevents_service_start(tmp_path):
    api = module()
    machine, _backend, lease, mutation = fixture(tmp_path)
    manifest = MachineManifest.model_validate(payload())
    await machine.ensure(manifest, mutation)
    lease["epoch"] = 8
    backend = ServicesBackend()
    services = api.MachineServices(machine, backend)
    with pytest.raises(RuntimeError):
        await services.reconcile(manifest, mutation)
    assert backend.names == []

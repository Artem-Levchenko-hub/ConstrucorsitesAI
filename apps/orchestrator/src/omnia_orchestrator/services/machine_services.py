"""Manifest service reconciliation over the fenced project machine."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel

from omnia_orchestrator.core.cell_resources import CellResourceError, LifecycleMutation
from omnia_orchestrator.core.project_machine import MachineManifest
from omnia_orchestrator.services.project_machine import ProjectMachine, machine_effect


class MachineServiceFailed(CellResourceError):
    pass


class ServiceStatus(BaseModel):
    name: str
    state: str
    ready: bool
    log_tail: str = ""


class MachineServices:
    def __init__(self, machine: ProjectMachine, backend: Any) -> None:
        self.machine = machine
        self.backend = backend

    async def reconcile(
        self, manifest: MachineManifest, mutation: LifecycleMutation
    ) -> list[ServiceStatus]:
        await self.machine.ensure(manifest, mutation)
        services = {item.name: item for item in manifest.services}
        statuses = []
        for name in manifest.service_order():
            await self.machine.assert_fence(mutation)
            service = services[name]
            await machine_effect(self.backend.start_service, service, mutation.fencing_epoch)
            status = ServiceStatus.model_validate(
                await machine_effect(
                    self.backend.service_status,
                    service,
                    mutation.fencing_epoch,
                )
            )
            statuses.append(status)
            if not status.ready:
                raise MachineServiceFailed(f"service {name} readiness failed: {status.log_tail}")
        return statuses

    async def status(self, mutation: LifecycleMutation) -> list[ServiceStatus]:
        state = await self.machine.assert_fence(mutation)
        manifest = MachineManifest.model_validate(state["manifest"])
        return [
            ServiceStatus.model_validate(
                await machine_effect(
                    self.backend.service_status,
                    service,
                    mutation.fencing_epoch,
                )
            )
            for service in manifest.services
        ]

    async def stop(self, mutation: LifecycleMutation) -> None:
        await self.machine.assert_fence(mutation)
        await machine_effect(self.backend.stop_services)

    async def logs(self, service_name: str, mutation: LifecycleMutation) -> str:
        for item in await self.status(mutation):
            if item.name == service_name:
                return item.log_tail
        raise MachineServiceFailed("unknown service")

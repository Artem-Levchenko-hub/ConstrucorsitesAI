"""Production reservations: separate caps, same host admission and accounting."""

from dataclasses import replace
from typing import Any

from omnia_orchestrator.core.cell_resources import CellResourceError
from omnia_orchestrator.services.cell_admission import CellAdmissionGate
from omnia_orchestrator.services.docker_cell_resources import DockerCellResourceManager
from omnia_orchestrator.services.machine_adapter import MachineAdapter


def production_manager(
    source: DockerCellResourceManager,
    settings: Any,
) -> DockerCellResourceManager:
    if not source.profile.is_v2:
        raise CellResourceError("publication requires the portable v2 resource profile")
    # MAX core DB/Redis serve one app, not generation. The product keeps its
    # normal RAM budget; CPU is separately bounded for concurrent editing.
    # min preserves explicitly smaller disposable QA resource profiles.
    limits = {
        "bundle_cpu_cores": ("cell_public_bundle_cpu_cores", 1.0),
        "bundle_memory_bytes": ("cell_public_bundle_memory_bytes", 1024**3),
        "active_machine_cpu_cores": ("cell_public_machine_cpu_cores", 0.5),
        "active_machine_memory_bytes": ("cell_public_machine_memory_bytes", 2 * 1024**3),
        "managed_core_cpu_cores": ("cell_public_core_cpu_cores", 0.2),
        "managed_core_memory_bytes": ("cell_public_core_memory_bytes", 768 * 1024**2),
        "helper_cpu_cores": ("cell_public_helper_cpu_cores", 0.2),
    }
    profile = replace(
        source.profile,
        **{
            name: min(getattr(source.profile, name), getattr(settings, field, default))
            for name, (field, default) in limits.items()
        },
    )
    # Proxy + namespace guard + public gateway have fixed Docker caps
    # (0.10 + 0.05 + 0.05 CPU), even for a smaller source QA profile.
    profile = replace(profile, helper_cpu_cores=max(0.2, profile.helper_cpu_cores))
    manager = replace(source, profile=profile, admission_gate=CellAdmissionGate(profile))
    manager.machine_runtime = MachineAdapter(manager, settings)
    return manager

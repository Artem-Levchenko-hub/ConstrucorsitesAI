"""Deliver managed integration SDK and session bootstrap through the generation lease."""

from omnia_api.services.max_project_kit import _template_file
from omnia_api.services.project_cell_executor import ProjectCellExecutorHandle

INTEGRATION_SDK_PATH = "src/lib/omnia/integration-client.ts"
MANAGED_BROWSER_PATHS = (INTEGRATION_SDK_PATH, "src/components/MaxAppProvider.tsx")


async def refresh_integration_sdk(handle: ProjectCellExecutorHandle) -> None:
    """Stage managed browser files; preserve product files and executor dirty state."""
    if not handle.is_portable():
        return
    current = await handle.snapshot_files()
    patch = {
        path: canonical
        for path in MANAGED_BROWSER_PATHS
        if current.get(path) != (canonical := _template_file(path))
    }
    if patch:
        await handle.stage_patch(patch, ())

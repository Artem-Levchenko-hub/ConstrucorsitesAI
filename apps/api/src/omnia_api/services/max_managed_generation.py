"""Deliver the canonical browser SDK through the active generation lease."""

from omnia_api.services.max_project_kit import _template_file
from omnia_api.services.project_cell_executor import ProjectCellExecutorHandle

INTEGRATION_SDK_PATH = "src/lib/omnia/integration-client.ts"


async def refresh_integration_sdk(handle: ProjectCellExecutorHandle) -> None:
    """Stage only the SDK; preserve product files and the executor's dirty state."""
    if not handle.is_portable():
        return
    canonical = _template_file(INTEGRATION_SDK_PATH)
    current = await handle.snapshot_files()
    if current.get(INTEGRATION_SDK_PATH) != canonical:
        await handle.stage_patch({INTEGRATION_SDK_PATH: canonical}, ())

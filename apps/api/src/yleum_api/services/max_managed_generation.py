"""Deliver managed integration SDK and session bootstrap through the generation lease."""

from yleum_api.services.max_project_kit import MAX_RETIRED_MANAGED_FILES, _template_file
from yleum_api.services.project_cell_executor import ProjectCellExecutorHandle

INTEGRATION_SDK_PATH = "src/lib/omnia/integration-client.ts"
MANAGED_BROWSER_PATHS = (
    INTEGRATION_SDK_PATH, "src/components/MaxAppProvider.tsx", "src/components/YleumCompliance.tsx",
)


def managed_browser_files(max_config_source: str | None = None) -> dict[str, str]:
    files = {path: _template_file(path) for path in MANAGED_BROWSER_PATHS}
    if max_config_source is not None:
        files["src/lib/omnia/max-config.ts"] = max_config_source
    return files


async def refresh_integration_sdk(
    handle: ProjectCellExecutorHandle, *, max_config_source: str | None = None,
) -> None:
    """Stage managed browser files; preserve product files and executor dirty state."""
    current = await handle.snapshot_files()
    patch = {
        path: canonical
        for path, canonical in managed_browser_files(max_config_source).items()
        if current.get(path) != canonical
    }
    # Retired platform files (encrypted CRUD, kit v18) leave existing projects.
    retired = tuple(sorted(path for path in MAX_RETIRED_MANAGED_FILES if path in current))
    if patch or retired:
        await handle.stage_patch(patch, retired)

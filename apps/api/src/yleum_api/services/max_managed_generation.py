"""Deliver the managed application SDK through the generation lease."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from yleum_api.core.errors import ApiError
from yleum_api.models.max_project_config import MaxProjectConfig
from yleum_api.schemas.max_studio import MaxProjectConfigPayload
from yleum_api.services.max_project_kit import (
    MAX_MANAGEMENT_RUNTIME_FILES,
    MAX_RETIRED_MANAGED_FILES,
    _template_file,
    enable_catalog_runtime_manifest,
)

if TYPE_CHECKING:
    from yleum_api.services.project_cell_executor import ProjectCellExecutorHandle

INTEGRATION_SDK_PATH = "src/lib/omnia/integration-client.ts"
MANAGED_GENERATION_PATHS = (
    *MAX_MANAGEMENT_RUNTIME_FILES,
    "next.config.ts",
    INTEGRATION_SDK_PATH, "src/lib/omnia/client.ts",
    "src/lib/omnia/activity.ts",
    "src/lib/omnia/catalog-server.ts", "src/lib/omnia/catalog-actions.ts",
    "src/lib/omnia/catalog-client.ts",
    "src/components/MaxAppProvider.tsx", "src/components/YleumCompliance.tsx",
    "src/lib/max/owner-preview-renewal.ts",
    "src/app/support/page.tsx",
)


async def load_saved_business_config(
    factory: async_sessionmaker[AsyncSession], *, project_id: UUID, owner_id: UUID,
    default_name: str | None = None,
) -> tuple[MaxProjectConfigPayload | None, int]:
    """Read canonical platform metadata; generated source never supplies this seed."""
    async with factory() as session:
        record = await session.get(MaxProjectConfig, project_id)
    if record is None:
        if default_name is None:
            return None, 0
        # Only an unsaved project's label needs a valid neutral brief. Never
        # normalize or replace the owner's persisted configuration.
        label = default_name.strip()[:100] or "Мини-приложение"
        return MaxProjectConfigPayload.default_for(label), 0
    if record.owner_id != owner_id:
        raise ApiError("conflict", "MAX configuration ownership mismatch", 409)
    return MaxProjectConfigPayload.model_validate(record.config), record.config_version


def managed_generation_files(max_config_source: str | None = None) -> dict[str, str]:
    files = {path: _template_file(path) for path in MANAGED_GENERATION_PATHS}
    if max_config_source is not None:
        files["src/lib/omnia/max-config.ts"] = max_config_source
    return files


async def refresh_integration_sdk(
    handle: ProjectCellExecutorHandle, *, max_config_source: str | None = None,
    catalog_runtime: bool = False,
) -> None:
    """Stage one SDK import closure; preserve product files and executor dirty state."""
    current = await handle.snapshot_files()
    from yleum_api.services.max_management_contract import parse_management_contract

    raw_management = current.get(".omnia/management.json")
    management_enabled = bool(
        raw_management and parse_management_contract(json.loads(raw_management)).resources
    )
    files = managed_generation_files(max_config_source)
    migration_path = "drizzle/0003_owner_management.sql"
    if migration_path in current and current[migration_path] != files[migration_path]:
        raise ApiError(
            "conflict", "Управляемые миграции приложения изменены", 409,
            details={"reason": "managed_migration_immutable"},
        )
    patch = {
        path: canonical
        for path, canonical in files.items()
        if path != "drizzle/0003_owner_management.sql" or management_enabled
        if current.get(path) != canonical
    }
    manifest = current.get(".omnia/cell.json")
    if catalog_runtime and manifest is not None:
        upgraded = enable_catalog_runtime_manifest(manifest)
        if upgraded != manifest:
            patch[".omnia/cell.json"] = upgraded
    # Retired platform files (encrypted CRUD, kit v18) leave existing projects.
    retired = tuple(sorted(path for path in MAX_RETIRED_MANAGED_FILES if path in current))
    if patch or retired:
        await handle.stage_patch(patch, retired)

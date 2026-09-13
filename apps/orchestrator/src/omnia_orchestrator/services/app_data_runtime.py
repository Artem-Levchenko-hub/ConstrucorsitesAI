"""Connect host key management to the immutable MAX core, never the product guest."""

from pathlib import Path
from typing import Any
from uuid import UUID

from omnia_orchestrator.services.app_data_keys import (
    AppDataKeyError,
    AppDataKeyManager,
    VaultDataKeyConfig,
)
from omnia_orchestrator.services.cell_state import _read_plain_json_file
from omnia_orchestrator.services.project_machine import write_controller_json

CORE_KEY_FILE = "/run/omnia-data/keys.json"


def key_manager(settings: Any) -> AppDataKeyManager:
    ca_file = getattr(settings, "cell_data_vault_ca_file", "")
    return AppDataKeyManager(VaultDataKeyConfig(
        address=settings.cell_data_vault_address,
        token_file=Path(settings.cell_data_vault_token_file),
        mount=settings.cell_data_vault_mount,
        key_name=settings.cell_data_vault_key,
        wrapped_root=Path(settings.cell_data_wrapped_key_root),
        runtime_root=Path(settings.cell_data_runtime_key_root),
        ca_file=Path(ca_file) if ca_file else None,
    ))


def _has_records(postgres: Any, project_id: str) -> bool:
    if postgres is None:
        raise AppDataKeyError("Managed database key inventory is unavailable")

    def query(sql: str) -> str:
        try:
            result = postgres.exec_run([
                "psql", "-X", "-q", "-A", "-t", "-U", "postgres", "-d", "postgres",
                "-v", "ON_ERROR_STOP=1", "-c", sql,
            ], user="postgres")
            if result.exit_code != 0:
                raise AppDataKeyError("Managed database key inventory failed")
            value: str = bytes(result.output).decode("ascii").strip()
            if value not in {"0", "1"}:
                raise AppDataKeyError("Managed database key inventory failed")
            return value
        except AppDataKeyError:
            raise
        except Exception:
            raise AppDataKeyError("Managed database key inventory failed") from None

    if query("SELECT CASE WHEN to_regclass('omnia_secure_records') IS NULL "
             "THEN 0 ELSE 1 END") == "0":
        return False
    return query("SELECT CASE WHEN EXISTS(SELECT 1 FROM omnia_secure_records "
                 f"WHERE project_id='{project_id}') THEN 1 ELSE 0 END") == "1"


def prepare_core_keys(
    settings: Any, project_id: UUID, postgres: Any, marker: Path | None,
) -> Path | None:
    if not getattr(settings, "cell_data_vault_address", ""):
        return None
    project = str(UUID(str(project_id)))
    established = False
    if marker is not None and (marker.exists() or marker.is_symlink()):
        value = _read_plain_json_file(marker)
        if value != {"project_id": project}:
            raise AppDataKeyError("Managed key binding identity mismatch")
        established = True
    allow_create = not established and not _has_records(postgres, project)
    path = key_manager(settings).prepare(project, allow_create=allow_create)
    if marker is not None and not established:
        write_controller_json(marker, {"project_id": project})
    return path

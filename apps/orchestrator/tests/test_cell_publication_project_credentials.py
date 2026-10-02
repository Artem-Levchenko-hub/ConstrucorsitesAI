"""Lost or mismatched published authority cannot silently rekey a running database."""

from __future__ import annotations

import base64
from types import SimpleNamespace
from uuid import UUID

import pytest

from yleum_orchestrator.core.cell_resources import CellResourceError
from yleum_orchestrator.services.cell_publication import CellPublicationService
from yleum_orchestrator.services.project_database_credentials import ProjectDatabaseCredentialStore

PROJECT = UUID("a344f247-a0f3-4efd-84da-337d991f8c2b")
PRODUCTION = UUID("e39ab95c-3a25-40ee-9c29-1eafde7d1179")


def placement(*, installed: bool, dsn: str | None = None):
    def get(_version, kind, name, _namespace):
        if kind == "StatefulSet":
            return {
                "spec": {
                    "template": {
                        "metadata": {
                            "annotations": {
                                "omnia.project-db.role-protocol": "1" if installed else "0"
                            }
                        }
                    }
                }
            }
        if kind == "Secret":
            return {"data": {"DATABASE_URL": base64.b64encode((dsn or "").encode()).decode()}}
        raise AssertionError("unexpected object read")

    return SimpleNamespace(
        runtime=SimpleNamespace(api=SimpleNamespace(get=get)),
        namespace=lambda project: f"app-{project}",
    )


async def credentials(root, target):
    service = object.__new__(CellPublicationService)
    return await service._kubernetes_project_credentials(
        target, SimpleNamespace(root=root), PRODUCTION, PROJECT
    )


@pytest.mark.asyncio
async def test_first_publication_allocates_a_separate_persistent_authority(tmp_path):
    preview = ProjectDatabaseCredentialStore(tmp_path / "project-db-credentials").load_or_create(
        PROJECT
    )
    value = await credentials(tmp_path, placement(installed=False))
    assert value != preview
    assert value == await credentials(tmp_path, placement(installed=False))


@pytest.mark.asyncio
async def test_installed_database_requires_existing_private_authority(tmp_path):
    with pytest.raises(RuntimeError, match="authority unavailable"):
        await credentials(tmp_path, placement(installed=True))
    assert not (tmp_path / "project-db-credentials" / f"{PRODUCTION}.json").exists()


@pytest.mark.asyncio
async def test_lost_installed_authority_is_not_regenerated(tmp_path):
    store = ProjectDatabaseCredentialStore(tmp_path / "project-db-credentials")
    store.load_or_create(PRODUCTION)
    record = store.root / f"{PRODUCTION}.json"
    record.unlink()
    with pytest.raises(RuntimeError, match="authority unavailable"):
        await credentials(tmp_path, placement(installed=True))
    assert not record.exists()


@pytest.mark.asyncio
async def test_installed_database_reuses_exact_matching_authority(tmp_path):
    value = ProjectDatabaseCredentialStore(tmp_path / "project-db-credentials").load_or_create(
        PRODUCTION
    )
    dsn = f"postgresql://omnia_project_runtime:{value.runtime_password}@project-postgres:5432/postgres"
    assert value == await credentials(tmp_path, placement(installed=True, dsn=dsn))


@pytest.mark.asyncio
async def test_installed_database_rejects_legacy_or_mismatched_secret_without_rekey(tmp_path):
    store = ProjectDatabaseCredentialStore(tmp_path / "project-db-credentials")
    before = store.load_or_create(PRODUCTION)
    with pytest.raises(CellResourceError, match="credential mismatch"):
        await credentials(
            tmp_path,
            placement(
                installed=True,
                dsn="postgresql://postgres:synthetic-legacy@project-postgres:5432/postgres",
            ),
        )
    assert store.load(PRODUCTION) == before

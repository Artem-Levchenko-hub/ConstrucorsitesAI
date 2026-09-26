"""Scaffold materialization and the kit route, without the frozen static trees.

The four static scaffolds (blank / landing / portfolio / blog) and the fullstack
one left with the site builder together with their golden byte fixture: a MAX
project has no api-side scaffold at all — `init_repo` is handed a directory that
does not exist and must produce an empty first commit. What stayed is the shared
kit the cell preview serves over `/api/kit/<file>`, and the export/rollback path
the owner uses to download a project.
"""

import hashlib
import io
import zipfile
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI

from yleum_api.services import repo
from yleum_api.services.template_materialization import materialize_template

SHARED_ASSETS = Path(__file__).parents[1] / "src/yleum_api/templates/shared-assets"


def _scaffold(root: Path) -> Path:
    """A throwaway scaffold with the shape `init_repo` accepts."""
    source = root / "scaffold"
    (source / "assets").mkdir(parents=True)
    (source / "index.html").write_text("<h1>Стартовая страница</h1>\n", encoding="utf-8")
    (source / "assets/style.css").write_text("body { margin: 0 }\n", encoding="utf-8")
    return source


def test_missing_scaffold_still_creates_empty_commit(tmp_path: Path) -> None:
    """The MAX path: no api-side template dir, so the first commit is empty."""
    project_id = uuid4()
    commit = repo.init_repo(project_id, tmp_path / "missing", "max_miniapp")
    assert repo.read_files(project_id, commit) == {}


def test_external_scaffold_and_dotfile_are_not_augmented(tmp_path: Path) -> None:
    source = tmp_path / "custom"
    source.mkdir()
    (source / ".user-config").write_text("custom")
    project_id = uuid4()
    commit = repo.init_repo(project_id, source, "custom")
    assert repo.read_files(project_id, commit) == {".user-config": "custom"}


@pytest.mark.parametrize("already_exists", [False, True])
def test_materialization_creates_a_standalone_tree(tmp_path: Path, already_exists: bool) -> None:
    source = _scaffold(tmp_path)
    destination = tmp_path / "project"
    if already_exists:
        destination.mkdir()

    materialize_template(source, destination)

    assert all(not path.is_symlink() for path in destination.rglob("*"))
    assert {
        path.relative_to(destination).as_posix(): path.read_bytes()
        for path in destination.rglob("*")
        if path.is_file()
    } == {
        path.relative_to(source).as_posix(): path.read_bytes()
        for path in source.rglob("*")
        if path.is_file()
    }


@pytest.mark.parametrize("existing_path", [".user-config", "assets/style.css", "index.html"])
def test_populated_destination_is_rejected_without_partial_writes(
    tmp_path: Path, existing_path: str,
) -> None:
    source = _scaffold(tmp_path)
    destination = tmp_path / "project"
    existing = destination / existing_path
    existing.parent.mkdir(parents=True)
    user_bytes = b"owner content must survive\r\n"
    existing.write_bytes(user_bytes)
    before = sorted(path.relative_to(destination).as_posix() for path in destination.rglob("*"))

    with pytest.raises(ValueError, match="destination must be empty"):
        materialize_template(source, destination)

    assert sorted(
        path.relative_to(destination).as_posix() for path in destination.rglob("*")
    ) == before
    assert existing.read_bytes() == user_bytes


async def test_kit_http_contract_serves_the_shipped_bytes() -> None:
    """`/api/kit/<file>` is what a cell's draft preview proxies the inspector from."""
    from yleum_api.core.errors import ApiError, api_error_handler
    from yleum_api.routers.public import kit_router

    app = FastAPI()
    app.include_router(kit_router)
    app.add_exception_handler(ApiError, api_error_handler)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://testserver",
    ) as client:
        for name, mime in (
            ("omnia-kit.css", "text/css; charset=utf-8"),
            ("omnia-kit.js", "application/javascript; charset=utf-8"),
            ("anime.min.js", "application/javascript; charset=utf-8"),
        ):
            response = await client.get(f"/api/kit/{name}")
            assert response.status_code == 200
            shipped = (SHARED_ASSETS / name).read_bytes()
            assert hashlib.sha256(response.content).hexdigest() == hashlib.sha256(
                shipped
            ).hexdigest()
            assert response.headers["content-type"] == mime
            assert response.headers["cache-control"] == "public, max-age=3600"
            assert response.headers["access-control-allow-origin"] == "*"
        inspector = await client.get("/api/kit/omnia-inspector.js")
        assert inspector.status_code == 200
        missing = await client.get("/api/kit/not-a-kit-file.js")
        assert missing.status_code == 404
        assert missing.json()["error"]["code"] == "not_found"


async def test_committed_files_survive_http_export_and_rollback(tmp_path: Path) -> None:
    """Real Git and HTTP ZIP; only identity/metadata and object storage are fixtures."""
    from yleum_api.core.db import get_session
    from yleum_api.core.deps import get_current_user
    from yleum_api.models.project import Project
    from yleum_api.models.snapshot import Snapshot
    from yleum_api.routers.projects import router

    project_id, owner_id, snapshot_id = uuid4(), uuid4(), uuid4()
    source = _scaffold(tmp_path)
    initial = repo.init_repo(project_id, source, "max_miniapp")
    starter = repo.read_files(project_id, initial)
    custom = {
        "index.html": "<h1>Правка владельца</h1>\n",
        ".owner-config": "persist this custom file\n",
    }
    edited = repo.commit_files(project_id, custom, "Owner edits", parent_sha=initial)
    project = SimpleNamespace(
        id=project_id, owner_id=owner_id, current_snapshot_id=snapshot_id,
        slug="owner-app", template="max_miniapp",
    )
    snapshot = SimpleNamespace(commit_sha=edited)

    class MetadataSession:
        async def get(self, model, identity):
            if model is Project and identity == project_id:
                return project
            if model is Snapshot and identity == snapshot_id:
                return snapshot
            raise AssertionError(f"unexpected metadata lookup: {model}")

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_session] = lambda: MetadataSession()
    app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id=owner_id)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://testserver",
    ) as client:
        async def exported_files() -> dict[str, bytes]:
            response = await client.get(f"/api/projects/{project_id}/download")
            assert response.status_code == 200, response.text
            assert response.headers["content-type"] == "application/zip"
            with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
                assert all(info.external_attr >> 16 == 0o100644 for info in archive.infolist())
                return {name: archive.read(name) for name in archive.namelist()}

        edited_export = await exported_files()
        for path, content in custom.items():
            assert edited_export[path] == content.encode("utf-8")
        # A MAX export also carries the orchestrator's app template, so the
        # owner's files are a subset, not the whole archive.
        assert set(starter) | {".owner-config"} <= set(edited_export)

        snapshot.commit_sha = repo.checkout(project_id, initial)
        restored = await exported_files()
        assert ".owner-config" not in restored
        assert {path: restored[path].decode("utf-8") for path in starter} == starter
        # Restoring old output neither rewrites history nor loses the newer edits.
        preserved = repo.read_files(project_id, edited)
        assert all(preserved[path] == value for path, value in custom.items())
        snapshot.commit_sha = repo.checkout(project_id, edited)
        assert await exported_files() == edited_export

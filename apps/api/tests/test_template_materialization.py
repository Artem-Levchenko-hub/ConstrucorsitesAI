"""Frozen Linux/Git scaffold bytes, captured before the Task 5 refactor."""

import hashlib
import io
import json
import zipfile
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI

from omnia_api.services import repo
from omnia_api.services.template_materialization import materialize_template

TEMPLATES = Path(__file__).parents[1] / "src/omnia_api/templates"
GOLDEN = json.loads(
    (Path(__file__).parent / "fixtures/static_templates_810f0fbb.json").read_text()
)["templates"]


@pytest.mark.parametrize("name", list(GOLDEN))
def test_real_initial_commit_matches_frozen_tree(name: str) -> None:
    project_id = uuid4()
    commit = repo.init_repo(project_id, TEMPLATES / name, name)
    files = repo.read_files(project_id, commit)
    actual = {
        path: hashlib.sha256(content.encode("utf-8")).hexdigest()
        for path, content in files.items()
    }
    assert actual == {path: entry["sha256"] for path, entry in GOLDEN[name].items()}
    with repo._open_workdir(project_id, must_exist=True) as workdir:
        import pygit2

        repository = pygit2.Repository(str(workdir))
        tree = repository[commit].tree
        for path, entry in GOLDEN[name].items():
            assert f"{tree[path].filemode:o}" == entry["mode"]


def test_missing_scaffold_still_creates_empty_commit(tmp_path: Path) -> None:
    project_id = uuid4()
    commit = repo.init_repo(project_id, tmp_path / "missing", "missing")
    assert repo.read_files(project_id, commit) == {}


def test_external_blank_scaffold_and_dotfile_are_not_augmented(tmp_path: Path) -> None:
    source = tmp_path / "blank"
    source.mkdir()
    (source / ".user-config").write_text("custom")
    project_id = uuid4()
    commit = repo.init_repo(project_id, source, "blank")
    assert repo.read_files(project_id, commit) == {".user-config": "custom"}


@pytest.mark.parametrize("already_exists", [False, True])
@pytest.mark.parametrize("name", ["blank", "landing", "portfolio", "blog"])
def test_materialization_creates_standalone_frozen_tree(
    tmp_path: Path, name: str, already_exists: bool,
) -> None:
    destination = tmp_path / "project"
    if already_exists:
        destination.mkdir()
    materialize_template(TEMPLATES / name, destination)
    files = [path for path in destination.rglob("*") if path.is_file()]
    assert all(not path.is_symlink() for path in destination.rglob("*"))
    assert {
        path.relative_to(destination).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in files
    } == {path: entry["sha256"] for path, entry in GOLDEN[name].items()}


@pytest.mark.parametrize("existing_path", [".user-config", "assets/omnia-kit.css", "index.html"])
def test_populated_destination_is_rejected_without_partial_writes(
    tmp_path: Path, existing_path: str,
) -> None:
    destination = tmp_path / "project"
    existing = destination / existing_path
    existing.parent.mkdir(parents=True)
    user_bytes = b"owner content must survive\r\n"
    existing.write_bytes(user_bytes)
    before = sorted(path.relative_to(destination).as_posix() for path in destination.rglob("*"))

    with pytest.raises(ValueError, match="destination must be empty"):
        materialize_template(TEMPLATES / "blank", destination)

    assert sorted(
        path.relative_to(destination).as_posix() for path in destination.rglob("*")
    ) == before
    assert existing.read_bytes() == user_bytes


async def test_kit_http_contract_uses_frozen_bytes() -> None:
    from omnia_api.core.errors import ApiError, api_error_handler
    from omnia_api.routers.public import kit_router

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
            assert hashlib.sha256(response.content).hexdigest() == GOLDEN["blank"][
                f"assets/{name}"
            ]["sha256"]
            assert response.headers["content-type"] == mime
            assert response.headers["cache-control"] == "public, max-age=3600"
            assert response.headers["access-control-allow-origin"] == "*"
        missing = await client.get("/api/kit/not-a-kit-file.js")
        assert missing.status_code == 404
        assert missing.json()["error"]["code"] == "not_found"


async def test_committed_custom_kit_survives_http_export_and_rollback() -> None:
    """Real Git and HTTP ZIP; only identity/metadata and object storage are fixtures."""
    from omnia_api.core.db import get_session
    from omnia_api.core.deps import get_current_user
    from omnia_api.models.project import Project
    from omnia_api.models.snapshot import Snapshot
    from omnia_api.routers.projects import router

    project_id, owner_id, snapshot_id = uuid4(), uuid4(), uuid4()
    initial = repo.init_repo(project_id, TEMPLATES / "blank", "blank")
    custom = {
        "assets/omnia-kit.css": "/* owner CSS */\nbody { color: red; }\n",
        "assets/omnia-kit.js": "window.ownerVersion = 'custom';\n",
        ".owner-config": "persist this custom file\n",
    }
    edited = repo.commit_files(project_id, custom, "Owner kit edits", parent_sha=initial)
    project = SimpleNamespace(
        id=project_id, owner_id=owner_id, current_snapshot_id=snapshot_id,
        slug="owner-static", template="blank",
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
        assert set(edited_export) == set(GOLDEN["blank"]) | {".owner-config"}

        snapshot.commit_sha = repo.checkout(project_id, initial)
        restored = await exported_files()
        assert {path: hashlib.sha256(value).hexdigest() for path, value in restored.items()} == {
            path: entry["sha256"] for path, entry in GOLDEN["blank"].items()
        }
        # Restoring old output neither rewrites history nor loses the user's newer kit.
        preserved = repo.read_files(project_id, edited)
        assert all(preserved[path] == value for path, value in custom.items())
        snapshot.commit_sha = repo.checkout(project_id, edited)
        assert await exported_files() == edited_export

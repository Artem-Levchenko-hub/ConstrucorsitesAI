import io
import tarfile
from collections.abc import Iterator
from pathlib import Path
from uuid import UUID

import pytest

from omnia_api.services import repo

PROJECT_ID = UUID("00000000-0000-0000-0000-000000000123")


@pytest.fixture(autouse=True)
def isolated_project_repo_storage(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    objects: dict[str, bytes] = {}

    def upload(project_id: UUID, source: Path) -> None:
        payload = io.BytesIO()
        with tarfile.open(fileobj=payload, mode="w:gz") as archive:
            archive.add(source, arcname=".")
        objects[str(project_id)] = payload.getvalue()

    def try_download(project_id: UUID, destination: Path) -> bool:
        payload = objects.get(str(project_id))
        if payload is None:
            return False
        with tarfile.open(fileobj=io.BytesIO(payload), mode="r:gz") as archive:
            archive.extractall(destination)
        return True

    monkeypatch.setattr(repo, "_upload", upload)
    monkeypatch.setattr(repo, "_try_download", try_download)
    yield


def test_init_rejects_paths_outside_project_workspace() -> None:
    with pytest.raises(ValueError, match="invalid repository path"):
        repo.init_from_files(PROJECT_ID, {"../host.txt": "escape"}, "seed")


def test_init_rejects_repo_dot_segments() -> None:
    with pytest.raises(ValueError, match="invalid repository path"):
        repo.init_from_files(PROJECT_ID, {".git/config": "escape"}, "seed")


def test_commit_rejects_paths_outside_project_workspace() -> None:
    sha = repo.init_from_files(PROJECT_ID, {"src/page.tsx": "safe"}, "seed")

    with pytest.raises(ValueError, match="invalid repository path"):
        repo.commit_files(PROJECT_ID, {"../host.txt": "escape"}, "bad", sha)

    assert repo.read_files(PROJECT_ID, sha) == {"src/page.tsx": "safe"}


def test_incremental_commit_enforces_project_wide_file_quota(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(repo, "MAX_FILES", 2)
    sha = repo.init_from_files(PROJECT_ID, {"a.ts": "a", "b.ts": "b"}, "seed")

    with pytest.raises(ValueError, match="too many files in commit"):
        repo.commit_files(PROJECT_ID, {"c.ts": "c"}, "overflow", sha)

    assert repo.read_files(PROJECT_ID, sha) == {"a.ts": "a", "b.ts": "b"}


def test_incremental_commit_enforces_project_wide_byte_quota(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(repo, "MAX_REPO_BYTES", 5)
    sha = repo.init_from_files(PROJECT_ID, {"a.ts": "abc"}, "seed")

    with pytest.raises(ValueError, match="repository text exceeds"):
        repo.commit_files(PROJECT_ID, {"b.ts": "def"}, "overflow", sha)

    assert repo.read_files(PROJECT_ID, sha) == {"a.ts": "abc"}


def test_initial_import_enforces_batch_file_quota(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(repo, "MAX_FILES", 1)

    with pytest.raises(ValueError, match="too many files"):
        repo.init_from_files(PROJECT_ID, {"a.ts": "a", "b.ts": "b"}, "seed")


def test_initial_import_enforces_project_wide_byte_quota(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(repo, "MAX_REPO_BYTES", 5)

    with pytest.raises(ValueError, match="repository text exceeds"):
        repo.init_from_files(PROJECT_ID, {"a.ts": "abc", "b.ts": "def"}, "seed")


def test_exact_commit_preserves_verified_tree_despite_different_git_baseline() -> None:
    parent = repo.init_from_files(
        PROJECT_ID, {"src/page.tsx": "old", "removed.ts": "obsolete"}, "seed",
    )
    # Another detached commit leaves unrelated paths in the persisted index.
    repo.commit_files(PROJECT_ID, {"other-branch.ts": "unrelated"}, "other", parent)
    verified = {
        "src/page.tsx": "new",
        ".omnia/cell.json": '{"version": 1}',
        "src/helper.ts": "already existed in the live workspace",
        "empty.txt": "",
    }
    sha = repo.commit_files(PROJECT_ID, verified, "verified", parent, exact_tree=True)
    assert repo.read_files(PROJECT_ID, sha) == verified
    assert repo.read_files(PROJECT_ID, parent) == {
        "src/page.tsx": "old", "removed.ts": "obsolete",
    }


def test_exact_commit_can_remove_all_files() -> None:
    parent = repo.init_from_files(PROJECT_ID, {"old.txt": "old"}, "seed")
    sha = repo.commit_files(PROJECT_ID, {}, "empty tree", parent, exact_tree=True)
    assert repo.read_files(PROJECT_ID, sha) == {}

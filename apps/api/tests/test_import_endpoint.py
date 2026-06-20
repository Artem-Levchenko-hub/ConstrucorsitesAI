"""B3 — POST /api/projects/import endpoint tests.

These tests monkeypatch repo_import.fetch_repo_tarball and
repo_import.tarball_to_files so no real network calls are made.
The DB fixtures from conftest.py (client, db_session) are used.
"""

from __future__ import annotations

import io
import tarfile
import uuid
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from omnia_api.models.project import Project
from omnia_api.models.snapshot import Snapshot
from omnia_api.services.repo_import import ImportResult


def _make_tiny_tar(files: dict[str, str]) -> bytes:
    """Build a GitHub-style tarball (top-level wrapper dir stripped by tarball_to_files)."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        for path, content in files.items():
            raw = content.encode()
            info = tarfile.TarInfo(name=f"owner-repo-abc123/{path}")
            info.size = len(raw)
            tf.addfile(info, io.BytesIO(raw))
    return buf.getvalue()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

async def _register(client: httpx.AsyncClient, email: str) -> None:
    r = await client.post("/api/auth/register", json={"email": email, "password": "secret123"})
    assert r.status_code == 201


# ---------------------------------------------------------------------------
# Happy path: public repo, anon user
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_import_project_anon_public_repo(client: httpx.AsyncClient, db_session) -> None:
    """Anon import of a public repo creates project with source='imported'
    and snapshot with prompt_text='' (empty string, not None)."""
    client.cookies.clear()

    fixed_result = ImportResult(
        files={"index.html": "<html><body>hello</body></html>", "README.md": "# hello"},
        template="blank",
        truncated=False,
        skipped_binaries=0,
    )

    with (
        patch(
            "omnia_api.services.repo_import.fetch_repo_tarball",
            new=AsyncMock(return_value=_make_tiny_tar(fixed_result.files)),
        ),
        patch(
            "omnia_api.services.repo_import.tarball_to_files",
            return_value=fixed_result,
        ),
    ):
        r = await client.post(
            "/api/projects/import",
            json={"repo_url": "https://github.com/owner/myrepo"},
        )

    assert r.status_code == 201, r.text
    body = r.json()

    assert body["source"] == "imported"
    assert body["external_repo_url"] == "https://github.com/owner/myrepo"
    assert body["template"] == "blank"
    assert "id" in body

    # Session cookie issued for anon user
    assert "omnia_session" in r.cookies

    # Verify DB state
    pid = body["id"]
    proj = await db_session.get(Project, uuid.UUID(pid))
    assert proj is not None
    assert proj.source == "imported"
    assert proj.is_imported is True

    snap = await db_session.get(Snapshot, proj.current_snapshot_id)
    assert snap is not None
    # prompt_text MUST be '' (empty string) — this makes is_first_build=False
    assert snap.prompt_text == ""


# ---------------------------------------------------------------------------
# Happy path: authenticated user, custom name
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_import_project_authenticated_custom_name(
    client: httpx.AsyncClient, db_session
) -> None:
    await _register(client, "importer@example.com")

    fixed_result = ImportResult(
        files={"main.py": "print('hello')"},
        template="code",
        truncated=False,
        skipped_binaries=0,
    )

    with (
        patch(
            "omnia_api.services.repo_import.fetch_repo_tarball",
            new=AsyncMock(return_value=b"fake"),
        ),
        patch(
            "omnia_api.services.repo_import.tarball_to_files",
            return_value=fixed_result,
        ),
    ):
        r = await client.post(
            "/api/projects/import",
            json={"repo_url": "owner/myrepo", "name": "My Custom Import"},
        )

    assert r.status_code == 201, r.text
    body = r.json()
    assert body["name"] == "My Custom Import"
    assert body["source"] == "imported"
    assert body["template"] == "code"


# ---------------------------------------------------------------------------
# Error cases
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_import_bad_url_returns_400(client: httpx.AsyncClient) -> None:
    r = await client.post(
        "/api/projects/import",
        json={"repo_url": "not-a-github-url"},
    )
    # The Pydantic validator on ProjectImportRequest fires first (422),
    # or our parse_github_url raises ValueError mapped to 400.
    # Either is acceptable — we just confirm it's not 2xx.
    assert r.status_code in (400, 422), r.text


@pytest.mark.asyncio
async def test_import_not_found_returns_404(client: httpx.AsyncClient) -> None:
    with patch(
        "omnia_api.services.repo_import.fetch_repo_tarball",
        new=AsyncMock(side_effect=FileNotFoundError("repo not found")),
    ):
        r = await client.post(
            "/api/projects/import",
            json={"repo_url": "owner/nonexistent"},
        )
    assert r.status_code == 404, r.text
    assert r.json()["error"]["code"] == "import_not_found"


@pytest.mark.asyncio
async def test_import_forbidden_returns_403(client: httpx.AsyncClient) -> None:
    with patch(
        "omnia_api.services.repo_import.fetch_repo_tarball",
        new=AsyncMock(side_effect=PermissionError("forbidden")),
    ):
        r = await client.post(
            "/api/projects/import",
            json={"repo_url": "owner/private-repo"},
        )
    assert r.status_code == 403, r.text
    assert r.json()["error"]["code"] == "import_forbidden"


@pytest.mark.asyncio
async def test_import_empty_repo_returns_400(client: httpx.AsyncClient) -> None:
    empty_result = ImportResult(files={}, template="code", truncated=False, skipped_binaries=5)
    with (
        patch(
            "omnia_api.services.repo_import.fetch_repo_tarball",
            new=AsyncMock(return_value=b"fake"),
        ),
        patch(
            "omnia_api.services.repo_import.tarball_to_files",
            return_value=empty_result,
        ),
    ):
        r = await client.post(
            "/api/projects/import",
            json={"repo_url": "owner/empty-repo"},
        )
    assert r.status_code == 400, r.text
    assert r.json()["error"]["code"] == "import_empty"

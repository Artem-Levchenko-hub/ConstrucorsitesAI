from collections.abc import Iterator
from uuid import UUID

import httpx
import pytest
from cryptography.fernet import Fernet
from pydantic import SecretStr
from sqlalchemy.ext.asyncio import AsyncSession

from omnia_api.core import crypto
from omnia_api.core.config import Settings, get_settings
from omnia_api.core.crypto import decrypt_token, encrypt_token
from omnia_api.core.security import create_oauth_state
from omnia_api.models.github_connection import GithubConnection
from omnia_api.models.project import Project
from omnia_api.models.user import User
from omnia_api.services import github_client, repo


@pytest.fixture
def github_config(monkeypatch: pytest.MonkeyPatch) -> Iterator[Settings]:
    """Configure the GitHub OAuth App + a fresh Fernet key for the test."""
    s = get_settings()
    monkeypatch.setattr(s, "github_oauth_client_id", "test-client-id")
    monkeypatch.setattr(s, "github_oauth_client_secret", SecretStr("test-secret"))
    monkeypatch.setattr(s, "github_token_enc_key", SecretStr(Fernet.generate_key().decode()))
    crypto._fernet.cache_clear()
    yield s
    crypto._fernet.cache_clear()


async def _register(client: httpx.AsyncClient, email: str = "u@example.com") -> UUID:
    r = await client.post("/api/auth/register", json={"email": email, "password": "secret123"})
    assert r.status_code == 201
    return UUID(r.json()["id"])


async def test_status_not_connected(client: httpx.AsyncClient) -> None:
    await _register(client)
    r = await client.get("/api/integrations/github/status")
    assert r.status_code == 200
    assert r.json() == {
        "connected": False,
        "github_username": None,
        "scopes": None,
        "connected_at": None,
    }


async def test_connect_returns_authorize_url(
    client: httpx.AsyncClient, github_config: Settings
) -> None:
    await _register(client)
    r = await client.get("/api/integrations/github/connect")
    assert r.status_code == 200
    url = r.json()["authorize_url"]
    assert "client_id=test-client-id" in url
    assert "state=" in url


async def test_connect_unconfigured_returns_503(client: httpx.AsyncClient) -> None:
    await _register(client)
    r = await client.get("/api/integrations/github/connect")
    assert r.status_code == 503
    assert r.json()["error"]["code"] == "github_unavailable"


async def test_callback_stores_encrypted_token(
    client: httpx.AsyncClient,
    db_session: AsyncSession,
    github_config: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user_id = await _register(client)

    async def fake_exchange(code: str) -> tuple[str, str]:
        return ("gho_secrettoken", "repo")

    async def fake_user(token: str) -> dict[str, object]:
        return {"login": "octocat", "id": 42}

    monkeypatch.setattr(github_client, "exchange_code_for_token", fake_exchange)
    monkeypatch.setattr(github_client, "get_authenticated_user", fake_user)

    state = create_oauth_state(user_id)
    r = await client.get(f"/api/integrations/github/callback?code=abc&state={state}")
    assert r.status_code == 302
    assert "github=connected" in r.headers["location"]

    conn = await db_session.get(GithubConnection, user_id)
    assert conn is not None
    assert conn.github_username == "octocat"
    # The crucial security property: token is encrypted at rest, not plaintext.
    assert conn.access_token_encrypted != "gho_secrettoken"
    assert decrypt_token(conn.access_token_encrypted) == "gho_secrettoken"


async def test_callback_bad_state_creates_no_connection(
    client: httpx.AsyncClient, db_session: AsyncSession, github_config: Settings
) -> None:
    user_id = await _register(client)
    r = await client.get("/api/integrations/github/callback?code=abc&state=forged")
    assert r.status_code == 302
    assert "github=error" in r.headers["location"]
    assert await db_session.get(GithubConnection, user_id) is None


async def test_export_requires_connection(
    client: httpx.AsyncClient, db_session: AsyncSession, github_config: Settings
) -> None:
    user_id = await _register(client)
    project = Project(owner_id=user_id, name="My Site", slug="my-site", template="blank")
    db_session.add(project)
    await db_session.commit()

    r = await client.post(f"/api/projects/{project.id}/export/github", json={})
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "github_not_connected"


async def test_export_creates_repo_and_links_project(
    client: httpx.AsyncClient,
    db_session: AsyncSession,
    github_config: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user_id = await _register(client)
    conn = GithubConnection(
        user_id=user_id,
        access_token_encrypted=encrypt_token("gho_x"),
        github_username="octocat",
        scopes="repo",
    )
    project = Project(owner_id=user_id, name="My Site", slug="my-site", template="blank")
    db_session.add_all([conn, project])
    await db_session.commit()

    async def fake_get_repo(token: str, full_name: str) -> dict[str, object] | None:
        return None

    async def fake_create_repo(
        token: str, *, name: str, private: bool, description: str | None
    ) -> dict[str, object]:
        return {
            "full_name": f"octocat/{name}",
            "html_url": f"https://github.com/octocat/{name}",
            "default_branch": "main",
        }

    def fake_push(
        project_id: UUID, remote_url: str, token: str, target_branch: str = "main"
    ) -> str:
        return "deadbeef"

    async def fake_publish(*args: object, **kwargs: object) -> None:
        return None

    monkeypatch.setattr(github_client, "get_repo", fake_get_repo)
    monkeypatch.setattr(github_client, "create_repo", fake_create_repo)
    monkeypatch.setattr(repo, "push_to_remote", fake_push)
    monkeypatch.setattr("omnia_api.routers.github_export.publish_event", fake_publish)

    r = await client.post(f"/api/projects/{project.id}/export/github", json={"private": True})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["repo_full_name"] == "octocat/my-site"
    assert body["repo_url"] == "https://github.com/octocat/my-site"

    await db_session.refresh(project)
    assert project.github_repo_url == "https://github.com/octocat/my-site"


async def test_export_others_project_returns_404(
    client: httpx.AsyncClient, db_session: AsyncSession, github_config: Settings
) -> None:
    await _register(client, email="me@example.com")
    other = User(email="other@example.com", password_hash="x")
    db_session.add(other)
    await db_session.flush()
    project = Project(owner_id=other.id, name="Theirs", slug="theirs", template="blank")
    db_session.add(project)
    await db_session.commit()

    r = await client.post(f"/api/projects/{project.id}/export/github", json={})
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "not_found"

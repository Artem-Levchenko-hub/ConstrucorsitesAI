"""V4.1a — anonymous-project seam (viewer→creator backend contract).

Falsifiable gate (CONTINUOUS-PLAN §5★ V4.1a):
  * POST /api/projects WITHOUT an auth cookie/JWT succeeds, returns a project
    owned by an ephemeral anonymous principal, and hands the caller a session
    cookie so the anon project stays reachable+editable pre-auth.
  * POST /api/projects/{id}/claim binds an anon-owned project to a real
    authenticated user; the project's source rows (snapshots) are unchanged.
  * Adversary: claiming a *real* user's project (not anon) is forbidden — the
    seam never lets a stranger steal an account-bound project.
"""

import httpx
from sqlalchemy import func, select

from omnia_api.models.project import Project
from omnia_api.models.snapshot import Snapshot
from omnia_api.models.user import User


async def _register(client: httpx.AsyncClient, email: str) -> None:
    r = await client.post(
        "/api/auth/register", json={"email": email, "password": "secret123"}
    )
    assert r.status_code == 201


async def test_create_project_without_auth_creates_anon_owned_project(
    client: httpx.AsyncClient, db_session
) -> None:
    client.cookies.clear()
    r = await client.post("/api/projects", json={"name": "Anon site", "template": "blank"})
    assert r.status_code == 201, r.text
    body = r.json()
    assert "id" in body
    # The seam issues a session so the anon principal can keep editing.
    assert "omnia_session" in r.cookies

    owner_id = (
        await db_session.execute(
            select(Project.owner_id).where(Project.id == body["id"])
        )
    ).scalar_one()
    owner = await db_session.get(User, owner_id)
    assert owner is not None
    assert owner.is_anon is True
    assert owner.email is None
    assert owner.password_hash is None


async def test_anon_project_reachable_and_editable_pre_auth(
    client: httpx.AsyncClient,
) -> None:
    client.cookies.clear()
    create = await client.post("/api/projects", json={"name": "Anon edit", "template": "blank"})
    assert create.status_code == 201
    pid = create.json()["id"]

    # The issued anon session cookie is now on the client → the project is
    # reachable (owner-scoped GET) and editable (PATCH) without ever signing up.
    got = await client.get(f"/api/projects/{pid}")
    assert got.status_code == 200
    assert got.json()["id"] == pid

    listed = await client.get("/api/projects")
    assert listed.status_code == 200
    assert any(p["id"] == pid for p in listed.json())

    patched = await client.patch(f"/api/projects/{pid}", json={"image_gen_enabled": False})
    assert patched.status_code == 200
    assert patched.json()["image_gen_enabled"] is False


async def test_claim_transfers_anon_project_to_real_user_without_data_loss(
    client: httpx.AsyncClient, db_session
) -> None:
    client.cookies.clear()
    create = await client.post("/api/projects", json={"name": "Claim me", "template": "blank"})
    assert create.status_code == 201
    pid = create.json()["id"]

    # Snapshot fingerprint BEFORE claim (source rows must survive untouched).
    snaps_before = (
        await db_session.execute(
            select(Snapshot.id, Snapshot.commit_sha)
            .where(Snapshot.project_id == pid)
            .order_by(Snapshot.id)
        )
    ).all()
    assert len(snaps_before) == 1

    # A real user signs up (fresh cookie replaces the anon session).
    client.cookies.clear()
    await _register(client, "claimer@example.com")

    claim = await client.post(f"/api/projects/{pid}/claim")
    assert claim.status_code == 200, claim.text
    assert claim.json()["id"] == pid

    # Ownership now binds to the real user; snapshot rows are byte-identical.
    real = (
        await db_session.execute(select(User).where(User.email == "claimer@example.com"))
    ).scalar_one()
    project = await db_session.get(Project, pid)
    assert project.owner_id == real.id

    snaps_after = (
        await db_session.execute(
            select(Snapshot.id, Snapshot.commit_sha)
            .where(Snapshot.project_id == pid)
            .order_by(Snapshot.id)
        )
    ).all()
    assert snaps_after == snaps_before


async def test_claim_foreign_real_users_project_is_forbidden(
    client: httpx.AsyncClient,
) -> None:
    # User one creates a project while authenticated (real owner, not anon).
    await _register(client, "owner@example.com")
    create = await client.post("/api/projects", json={"name": "Owned", "template": "blank"})
    assert create.status_code == 201
    pid = create.json()["id"]

    # User two tries to claim it — must be refused (real owner ≠ anon).
    client.cookies.clear()
    await _register(client, "thief@example.com")
    claim = await client.post(f"/api/projects/{pid}/claim")
    assert claim.status_code == 403
    assert claim.json()["error"]["code"] == "forbidden"


async def test_claim_own_project_is_idempotent(client: httpx.AsyncClient) -> None:
    await _register(client, "self@example.com")
    create = await client.post("/api/projects", json={"name": "Mine", "template": "blank"})
    pid = create.json()["id"]
    claim = await client.post(f"/api/projects/{pid}/claim")
    assert claim.status_code == 200
    assert claim.json()["id"] == pid


async def test_authenticated_create_still_binds_real_owner(
    client: httpx.AsyncClient, db_session
) -> None:
    """Regression: an authed create must NOT mint an anon user."""
    anon_before = (
        await db_session.execute(select(func.count()).where(User.is_anon.is_(True)))
    ).scalar_one()

    await _register(client, "real@example.com")
    create = await client.post("/api/projects", json={"name": "Real", "template": "blank"})
    assert create.status_code == 201
    pid = create.json()["id"]

    real = (
        await db_session.execute(select(User).where(User.email == "real@example.com"))
    ).scalar_one()
    project = await db_session.get(Project, pid)
    assert project.owner_id == real.id

    anon_after = (
        await db_session.execute(select(func.count()).where(User.is_anon.is_(True)))
    ).scalar_one()
    assert anon_after == anon_before

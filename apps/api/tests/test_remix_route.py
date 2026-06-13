"""V4.1b-UI-fullstack — same-origin ``GET /p/<slug>/remix`` viral fork entry.

A deployed container app lives on a different origin than apps/api, and the
``SameSite=lax`` session cookie is NOT sent on a cross-origin ``fetch`` — so the
in-page CTA there can't reuse the static page's same-origin fork ``fetch``.
This route is the top-level-navigation primitive that unblocks it: a plain
``<a href>`` GET forks the shared app server-side and 302-redirects the visitor
into their own editable workspace, carrying (or minting) an anon session cookie.

Falsifiable gate:
  1. Stranger (no cookie) GET → 302 to ``/projects/<fork-id>`` with a distinct
     id, ``forked_from`` lineage, anon owner, and a fresh ``omnia_session``.
  2. Authed GET → fork bound to the caller, no anon principal minted.
  3. Unknown slug → 404 (no fork created).
  4. Isolation: reuses ``perform_fork`` → source rows are untouched.
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


async def _create_source(client: httpx.AsyncClient, name: str) -> dict:
    r = await client.post("/api/projects", json={"name": name, "template": "blank"})
    assert r.status_code == 201, r.text
    return r.json()


async def test_remix_without_auth_redirects_to_fork_workspace(
    client: httpx.AsyncClient, db_session
) -> None:
    await _register(client, "remix-src@example.com")
    source = await _create_source(client, "Sushi bar")
    source_id = source["id"]
    slug = source["slug"]

    # A stranger opens the share link's remix entry — top-level GET, no cookie.
    client.cookies.clear()
    r = await client.get(f"/p/{slug}/remix", follow_redirects=False)

    assert r.status_code == 302, r.text
    location = r.headers["location"]
    assert location.startswith("/projects/")
    fork_id = location.removeprefix("/projects/")
    # Distinct project (a fork, not the source).
    assert fork_id != source_id
    # Anon session issued so the forker keeps editing without signing up.
    assert "omnia_session" in r.cookies

    fork_row = await db_session.get(Project, fork_id)
    assert fork_row is not None
    assert str(fork_row.forked_from) == source_id
    owner = await db_session.get(User, fork_row.owner_id)
    assert owner.is_anon is True
    assert owner.email is None
    # Carries a HEAD snapshot so the workspace is immediately previewable.
    assert fork_row.current_snapshot_id is not None


async def test_remix_while_authed_binds_caller(
    client: httpx.AsyncClient, db_session
) -> None:
    await _register(client, "remix-owner@example.com")
    source = await _create_source(client, "Fitness studio")
    # Caller stays authed (cookie kept) and remixes.
    r = await client.get(f"/p/{source['slug']}/remix", follow_redirects=False)
    assert r.status_code == 302, r.text
    fork_id = r.headers["location"].removeprefix("/projects/")

    fork_row = await db_session.get(Project, fork_id)
    owner = await db_session.get(User, fork_row.owner_id)
    # Bound to the real, signed-in caller — no anon principal minted.
    assert owner.is_anon is False
    assert owner.email == "remix-owner@example.com"


async def test_remix_unknown_slug_is_404_and_creates_nothing(
    client: httpx.AsyncClient, db_session
) -> None:
    before = (await db_session.execute(select(func.count()).select_from(Project))).scalar()
    r = await client.get("/p/does-not-exist-zzz/remix", follow_redirects=False)
    assert r.status_code == 404
    after = (await db_session.execute(select(func.count()).select_from(Project))).scalar()
    assert before == after


async def test_remix_leaves_source_rows_untouched(
    client: httpx.AsyncClient, db_session
) -> None:
    await _register(client, "remix-iso@example.com")
    source = await _create_source(client, "Clinic site")
    source_id = source["id"]

    def _snap_count() -> int:
        return (
            select(func.count())
            .select_from(Snapshot)
            .where(Snapshot.project_id == source_id)
        )  # type: ignore[return-value]

    snaps_before = (await db_session.execute(_snap_count())).scalar()

    client.cookies.clear()
    r = await client.get(f"/p/{source['slug']}/remix", follow_redirects=False)
    assert r.status_code == 302

    # Source's snapshot rows are byte-untouched by a fork (new rows keyed to the
    # fork's id; the source is never written).
    snaps_after = (await db_session.execute(_snap_count())).scalar()
    assert snaps_before == snaps_after
    src_row = await db_session.get(Project, source_id)
    assert src_row is not None
    assert src_row.forked_from is None

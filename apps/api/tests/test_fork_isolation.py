"""V4.1b — zero-signup instant fork ("Remix this") + fork-isolation invariant.

Falsifiable gate (CONTINUOUS-PLAN §5★ V4.1b — 3 pinned machine extractions, NOT
a folded property, because isolation is the quietest thing that breaks virality:
an anon fork that mutates the source corrupts the referrer's *live* app):

  1. ``fork.project_id != source.project_id`` (distinct UUID + ``forked_from``
     lineage points back at the source).
  2. The source's snapshot-count and project-row-count are UNCHANGED before vs.
     after fork + an edit committed on the fork (deep copy, zero shared rows).
  3. Writing to the fork's repo leaves the source's repo byte-identical (the
     fork got its own MinIO key, not a shared reference).

Plus the seam itself: forking with no auth lands an anon-owned, immediately
editable copy with a session cookie; forking while authed binds the caller;
forking a missing project is 404.
"""

import httpx
from sqlalchemy import func, select

from omnia_api.models.project import Project
from omnia_api.models.snapshot import Snapshot
from omnia_api.models.user import User
from omnia_api.services import repo as repo_svc


async def _register(client: httpx.AsyncClient, email: str) -> None:
    r = await client.post(
        "/api/auth/register", json={"email": email, "password": "secret123"}
    )
    assert r.status_code == 201


async def _create_source(client: httpx.AsyncClient, name: str = "Source app") -> str:
    """Create a project (authed caller) and return its id."""
    r = await client.post("/api/projects", json={"name": name, "template": "blank"})
    assert r.status_code == 201, r.text
    return r.json()["id"]


async def test_fork_without_auth_creates_anon_owned_copy(
    client: httpx.AsyncClient, db_session
) -> None:
    await _register(client, "src-owner@example.com")
    source_id = await _create_source(client, "Cafe landing")

    # A stranger (no cookie) forks the shared app.
    client.cookies.clear()
    r = await client.post(f"/api/projects/{source_id}/fork")
    assert r.status_code == 201, r.text
    fork = r.json()
    # Issued a session so the anon forker can keep editing without signing up.
    assert "omnia_session" in r.cookies

    # Isolation assert (1): distinct id + lineage (exposed in the response AND
    # persisted, so the client can show provenance / a "back to original" edge).
    assert fork["id"] != source_id
    assert fork["forked_from"] == source_id
    fork_row = await db_session.get(Project, fork["id"])
    assert str(fork_row.forked_from) == source_id

    owner = await db_session.get(User, fork_row.owner_id)
    assert owner.is_anon is True
    assert owner.email is None

    # Preserves the source's template/preset; carries a HEAD snapshot.
    assert fork["template"] == "blank"
    assert fork["current_snapshot_id"] is not None


async def test_fork_while_authed_binds_caller(
    client: httpx.AsyncClient, db_session
) -> None:
    await _register(client, "owner2@example.com")
    source_id = await _create_source(client)

    # A second real user forks it.
    client.cookies.clear()
    await _register(client, "forker@example.com")
    r = await client.post(f"/api/projects/{source_id}/fork")
    assert r.status_code == 201, r.text
    fork = r.json()

    forker = (
        await db_session.execute(select(User).where(User.email == "forker@example.com"))
    ).scalar_one()
    fork_row = await db_session.get(Project, fork["id"])
    assert fork_row.owner_id == forker.id
    assert forker.is_anon is False


async def test_fork_edit_leaves_source_byte_identical(
    client: httpx.AsyncClient, db_session
) -> None:
    """Isolation asserts (2) + (3): editing the fork never touches the source."""
    await _register(client, "iso-owner@example.com")
    source_id = await _create_source(client, "Isolated source")

    source_head = await db_session.get(
        Snapshot,
        (await db_session.get(Project, source_id)).current_snapshot_id,
    )
    source_sha = source_head.commit_sha
    source_files_before = repo_svc.read_files(source_id, source_sha)

    snaps_before = (
        await db_session.execute(
            select(func.count())
            .select_from(Snapshot)
            .where(Snapshot.project_id == source_id)
        )
    ).scalar_one()
    projects_before = (
        await db_session.execute(select(func.count()).select_from(Project))
    ).scalar_one()

    # Fork (anon) and commit a divergent edit directly on the fork's repo.
    client.cookies.clear()
    r = await client.post(f"/api/projects/{source_id}/fork")
    assert r.status_code == 201, r.text
    fork = r.json()
    fork_head = await db_session.get(Snapshot, fork["current_snapshot_id"])

    new_sha = repo_svc.commit_files(
        fork["id"],
        {"REMIX_DIVERGENCE.md": "# forked + diverged\n"},
        "fork edit",
        parent_sha=fork_head.commit_sha,
    )
    fork_files = repo_svc.read_files(fork["id"], new_sha)
    assert "REMIX_DIVERGENCE.md" in fork_files  # the fork really diverged

    # (3) Source repo is byte-identical after the fork edit.
    source_files_after = repo_svc.read_files(source_id, source_sha)
    assert source_files_after == source_files_before
    assert "REMIX_DIVERGENCE.md" not in source_files_after

    # (2) Source snapshot-count and project-row-count for the source are unchanged
    # (the new project + snapshot belong to the fork, never the source).
    snaps_after = (
        await db_session.execute(
            select(func.count())
            .select_from(Snapshot)
            .where(Snapshot.project_id == source_id)
        )
    ).scalar_one()
    assert snaps_after == snaps_before

    # Exactly one new project row exists (the fork); the source row still exists.
    projects_after = (
        await db_session.execute(select(func.count()).select_from(Project))
    ).scalar_one()
    assert projects_after == projects_before + 1
    assert await db_session.get(Project, source_id) is not None


async def test_fork_inherits_design_preset_and_discovery_spec(
    client: httpx.AsyncClient, db_session
) -> None:
    """V4.2b-finish leg (B) — the remix carries the source's design identity.

    The viral return-edge promise is "remix → refined niche, not a blank
    onboarding": a forked project must land in the composer already steered by
    the source's onboarding answers, so the first prompt re-generates from a
    real brief (``discovery_spec``) under the same preset instead of re-asking.

    Inheritance is performed once, at fork time (``perform_fork`` copies both
    fields onto the fork row), and the generation worker reads the fork's own
    copied ``design_preset_id`` / ``discovery_spec`` — there is deliberately no
    second ``forked_from`` lookup in the worker (that would be a divergent
    source of truth). This test pins that single copy so it can never silently
    regress to an empty onboarding for the remixer.
    """
    await _register(client, "preset-owner@example.com")
    source_id = await _create_source(client, "Sushi bar landing")

    # Reify the source's onboarding identity (what a real first-build interview
    # would have persisted) so we can prove the fork inherits it.
    source_spec = {
        "tone": "playful",
        "primary_family": "sans",
        "dark_mode": True,
        "sections": ["hero", "menu", "contact"],
    }
    source = await db_session.get(Project, source_id)
    source.design_preset_id = "aurora"
    source.discovery_spec = source_spec
    await db_session.commit()

    # A stranger remixes it.
    client.cookies.clear()
    r = await client.post(f"/api/projects/{source_id}/fork")
    assert r.status_code == 201, r.text
    fork = r.json()

    # Response surfaces the inherited preset + the lineage edge.
    assert fork["design_preset_id"] == "aurora"
    assert fork["forked_from"] == source_id

    # The persisted fork row carries the source's full onboarding spec verbatim,
    # so the worker's context-loader steers generation from the inherited brief.
    fork_row = await db_session.get(Project, fork["id"])
    assert fork_row.design_preset_id == "aurora"
    assert fork_row.discovery_spec == source_spec

    # Source identity is untouched (isolation holds for the design fields too).
    source_after = await db_session.get(Project, source_id)
    await db_session.refresh(source_after)
    assert source_after.design_preset_id == "aurora"
    assert source_after.discovery_spec == source_spec


async def test_fork_nonexistent_project_is_404(client: httpx.AsyncClient) -> None:
    client.cookies.clear()
    r = await client.post("/api/projects/00000000-0000-0000-0000-000000000000/fork")
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "not_found"

"""V4.7-SYNTHETIC — the viral loop proven at fork DEPTH 2 (money-free, 0 LLM).

CONTINUOUS-PLAN §5★ V4.7: virality is *branching*, not a single hop. The
depth-1 isolation in ``test_fork_isolation.py`` proves A→B; the real viral 10×
is a tree — A shares, B forks, B diverges and shares, C forks *the fork*. The
guarantees that hold at depth 1 must hold TRANSITIVELY at depth 2, and a linear
chain never exercises them:

  • lineage integrity — ``C.forked_from -> B``, ``B.forked_from -> A`` is a
    walkable chain back to the root; every node has a distinct ``project_id``.
  • transitive isolation — editing the MIDDLE node B leaks neither UP to its
    ancestor A nor DOWN to its already-branched descendant C; editing the LEAF
    C leaks into neither B nor A. Each node owns its repo key.
  • transitive niche inheritance — the source's onboarding identity
    (``design_preset_id`` + ``discovery_spec``) flows A→B→C, so a fork-of-a-fork
    still lands in the refined niche, not a blank onboarding.
  • transitive composition floor — the V3.3 compose gate that guards the shared
    surface must still pass two forks deep when the root is composed, and must
    still BITE two forks deep when the root is catastrophically flat (the
    adversarial half: a green assert that can never fail is worthless).

This runs the REAL fork endpoint at each edge — no production code changes, it
pins that the shipped fork machinery is transitively sound. The LLM-divergence
half of V4.7 (a real edit diverging each node on ≥1 axis) is the paid owner-run;
everything proven here is deterministic and money-free.
"""

import httpx
from sqlalchemy import func, select

from omnia_api.models.project import Project
from omnia_api.models.snapshot import Snapshot
from omnia_api.services import compose_gate
from omnia_api.services import repo as repo_svc

# A composed, awwwards-floor page (clears the V3.3 compose gate by a wide
# margin: many type sizes, several landmark sections, a hero).
_COMPOSED_PAGE = """<!doctype html><html><head><style>
h1{font-size:3.5rem} h2{font-size:2rem} .sub{font-size:1.25rem} p{font-size:1rem}
</style></head><body>
<header class="hero"><h1>Aurora Studio</h1><p class="sub">We design the future</p></header>
<section class="features"><h2>Features</h2><p>Fast and beautiful.</p></section>
<section class="pricing"><h2>Pricing</h2><p>Fair and simple.</p></section>
<footer class="footer"><h3>Contact</h3><p>hello@aurora.studio</p></footer>
</body></html>"""

# A catastrophically flat page — one type size, no sections, no hero. The
# compose floor MUST reject it (adversarial fixture for the transitive gate).
_FLAT_PAGE = """<!doctype html><html><head></head><body>
<div>Welcome</div><div>Some text here</div><div>More text below</div>
</body></html>"""


async def _register(client: httpx.AsyncClient, email: str) -> None:
    r = await client.post(
        "/api/auth/register", json={"email": email, "password": "secret123"}
    )
    assert r.status_code == 201


async def _create_source(client: httpx.AsyncClient, name: str) -> str:
    r = await client.post("/api/projects", json={"name": name, "template": "blank"})
    assert r.status_code == 201, r.text
    return r.json()["id"]


async def _fork_as_stranger(client: httpx.AsyncClient, source_id: str) -> dict:
    """Fork ``source_id`` as a brand-new anon visitor (fresh cookie jar), the
    way a real stranger lands on a shared ``/p/<slug>`` and clicks Remix."""
    client.cookies.clear()
    r = await client.post(f"/api/projects/{source_id}/fork")
    assert r.status_code == 201, r.text
    return r.json()


async def _head_sha(db_session, snapshot_id) -> str:
    snap = await db_session.get(Snapshot, snapshot_id)
    return snap.commit_sha


async def _seed_composed_head(db_session, project_id, html: str) -> str:
    """Commit ``html`` as the project's new HEAD snapshot, mirroring what the
    generation worker persists, so a later fork carries this page verbatim."""
    proj = await db_session.get(Project, project_id)
    head = await db_session.get(Snapshot, proj.current_snapshot_id)
    new_sha = repo_svc.commit_files(
        project_id, {"index.html": html}, "seed head", parent_sha=head.commit_sha
    )
    snap = Snapshot(
        project_id=project_id,
        commit_sha=new_sha,
        prompt_text="seed head",
        model_id=None,
        preview_key=head.preview_key,
        parent_id=head.id,
    )
    db_session.add(snap)
    await db_session.flush()
    proj.current_snapshot_id = snap.id
    await db_session.commit()
    return new_sha


async def test_depth2_lineage_and_transitive_isolation(
    client: httpx.AsyncClient, db_session
) -> None:
    """A → fork B → fork-of-fork C: lineage chain is walkable and editing any
    node leaks into no other node, transitively."""
    await _register(client, "tree-root@example.com")
    source_id = await _create_source(client, "Cafe landing")
    a_sha = await _head_sha(
        db_session, (await db_session.get(Project, source_id)).current_snapshot_id
    )

    projects_before = (
        await db_session.execute(select(func.count()).select_from(Project))
    ).scalar_one()
    a_snaps_before = (
        await db_session.execute(
            select(func.count())
            .select_from(Snapshot)
            .where(Snapshot.project_id == source_id)
        )
    ).scalar_one()

    # Edge A→B, then edge B→C (a fork OF the fork).
    fork_b = await _fork_as_stranger(client, source_id)
    fork_c = await _fork_as_stranger(client, fork_b["id"])

    # ── lineage integrity ──────────────────────────────────────────────────
    ids = {source_id, fork_b["id"], fork_c["id"]}
    assert len(ids) == 3  # three distinct project_ids
    assert fork_b["forked_from"] == source_id
    assert fork_c["forked_from"] == fork_b["id"]

    # Walk the persisted ``forked_from`` chain C → B → A back to the root.
    c_row = await db_session.get(Project, fork_c["id"])
    b_row = await db_session.get(Project, str(c_row.forked_from))
    a_row = await db_session.get(Project, str(b_row.forked_from))
    assert str(b_row.id) == fork_b["id"]
    assert str(a_row.id) == source_id
    assert a_row.forked_from is None  # the root has no ancestor

    # Distinct anon owners at each fork edge (a stranger per hop).
    assert b_row.owner_id != a_row.owner_id
    assert c_row.owner_id != b_row.owner_id

    # Exactly two new project rows (B and C); the source row survives.
    projects_after = (
        await db_session.execute(select(func.count()).select_from(Project))
    ).scalar_one()
    assert projects_after == projects_before + 2

    # ── transitive isolation: edit the MIDDLE node B ───────────────────────
    b_head = await _head_sha(db_session, fork_b["current_snapshot_id"])
    c_head = await _head_sha(db_session, fork_c["current_snapshot_id"])
    a_files_before = repo_svc.read_files(source_id, a_sha)
    c_files_before = repo_svc.read_files(fork_c["id"], c_head)

    b_sha2 = repo_svc.commit_files(
        fork_b["id"], {"B_DIVERGE.md": "# B diverged\n"}, "B edit", parent_sha=b_head
    )
    assert "B_DIVERGE.md" in repo_svc.read_files(fork_b["id"], b_sha2)

    # Ancestor A is byte-identical (no leak UP the tree).
    assert repo_svc.read_files(source_id, a_sha) == a_files_before
    # Descendant C — already branched off B's pre-edit bytes — is byte-identical
    # (no leak DOWN to a fork that left before the edit; C owns its repo key).
    assert repo_svc.read_files(fork_c["id"], c_head) == c_files_before
    assert "B_DIVERGE.md" not in repo_svc.read_files(fork_c["id"], c_head)

    # ── transitive isolation: edit the LEAF node C ─────────────────────────
    b_files_at2 = repo_svc.read_files(fork_b["id"], b_sha2)
    c_sha2 = repo_svc.commit_files(
        fork_c["id"], {"C_DIVERGE.md": "# C diverged\n"}, "C edit", parent_sha=c_head
    )
    assert "C_DIVERGE.md" in repo_svc.read_files(fork_c["id"], c_sha2)
    # Editing the leaf touches neither its parent B nor the root A.
    assert repo_svc.read_files(fork_b["id"], b_sha2) == b_files_at2
    assert "C_DIVERGE.md" not in b_files_at2
    assert repo_svc.read_files(source_id, a_sha) == a_files_before

    # The source's own snapshot count never moved through any of this.
    a_snaps_after = (
        await db_session.execute(
            select(func.count())
            .select_from(Snapshot)
            .where(Snapshot.project_id == source_id)
        )
    ).scalar_one()
    assert a_snaps_after == a_snaps_before


async def test_depth2_niche_inheritance_is_transitive(
    client: httpx.AsyncClient, db_session
) -> None:
    """The source's onboarding identity flows A→B→C: a fork-of-a-fork still
    lands in the refined niche, never a blank onboarding."""
    await _register(client, "niche-root@example.com")
    source_id = await _create_source(client, "Sushi bar landing")

    spec = {
        "tone": "playful",
        "primary_family": "sans",
        "dark_mode": True,
        "sections": ["hero", "menu", "contact"],
    }
    source = await db_session.get(Project, source_id)
    source.design_preset_id = "aurora"
    source.discovery_spec = spec
    await db_session.commit()

    fork_b = await _fork_as_stranger(client, source_id)
    fork_c = await _fork_as_stranger(client, fork_b["id"])

    # The grand-fork C carries the root's preset + full discovery spec verbatim,
    # inherited through B (perform_fork copies both fields at each edge).
    assert fork_b["design_preset_id"] == "aurora"
    assert fork_c["design_preset_id"] == "aurora"
    c_row = await db_session.get(Project, fork_c["id"])
    assert c_row.design_preset_id == "aurora"
    assert c_row.discovery_spec == spec

    # The root's identity is untouched by either fork.
    source_after = await db_session.get(Project, source_id)
    await db_session.refresh(source_after)
    assert source_after.design_preset_id == "aurora"
    assert source_after.discovery_spec == spec


async def test_depth2_compose_floor_holds_for_composed_root(
    client: httpx.AsyncClient, db_session
) -> None:
    """A composed root stays composed two forks deep — the V3.3 compose floor
    passes on the surface a fork-of-a-fork actually serves."""
    await _register(client, "compose-ok@example.com")
    source_id = await _create_source(client, "Aurora studio")
    await _seed_composed_head(db_session, source_id, _COMPOSED_PAGE)

    fork_b = await _fork_as_stranger(client, source_id)
    fork_c = await _fork_as_stranger(client, fork_b["id"])

    for label, fork in (("B", fork_b), ("C", fork_c)):
        head = await _head_sha(db_session, fork["current_snapshot_id"])
        report = compose_gate.scan(repo_svc.read_files(fork["id"], head))
        assert report.passed is True, f"compose floor regressed at fork {label}"


async def test_depth2_compose_floor_bites_for_flat_root(
    client: httpx.AsyncClient, db_session
) -> None:
    """Adversarial half: a catastrophically flat root is STILL caught two forks
    deep. A transitive gate that can't fail would be worthless."""
    await _register(client, "compose-flat@example.com")
    source_id = await _create_source(client, "Flat page")
    await _seed_composed_head(db_session, source_id, _FLAT_PAGE)

    fork_b = await _fork_as_stranger(client, source_id)
    fork_c = await _fork_as_stranger(client, fork_b["id"])

    c_head = await _head_sha(db_session, fork_c["current_snapshot_id"])
    report = compose_gate.scan(repo_svc.read_files(fork_c["id"], c_head))
    assert report.passed is False
    # Every catastrophe class still names itself at depth 2.
    assert set(report.classes) == set(compose_gate.COMPOSE_CLASSES)

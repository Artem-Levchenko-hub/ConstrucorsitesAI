"""V4 #3 — transitive remix lineage: ``GET /api/projects/<id>`` resolves the
source project's *name* + *slug* onto the fork's ``ProjectPublic`` so the
workspace remix badge can attribute it ("ремикс <name>") and link to
``/p/<slug>``.

Falsifiable gate:
  1. A fork carries ``forked_from_name`` / ``forked_from_slug`` equal to the
     source's name / slug.
  2. An organic project carries both as ``None`` (no badge → no attribution).

(A fork whose source is deleted has ``forked_from`` itself nulled by the
``ON DELETE SET NULL`` FK, so the badge cleanly vanishes; the resolver's
``if source is not None`` guard is defensive belt-and-suspenders.)
"""

import httpx


async def _register(client: httpx.AsyncClient, email: str) -> None:
    r = await client.post(
        "/api/auth/register", json={"email": email, "password": "secret123"}
    )
    assert r.status_code == 201


async def _create(client: httpx.AsyncClient, name: str) -> dict:
    r = await client.post("/api/projects", json={"name": name, "template": "blank"})
    assert r.status_code == 201, r.text
    return r.json()


async def test_fork_resolves_source_name_and_slug(
    client: httpx.AsyncClient,
) -> None:
    await _register(client, "lineage-resolve@example.com")
    source = await _create(client, "Кофейня на углу")

    # Owner remixes their own project — authed POST /fork.
    fr = await client.post(f"/api/projects/{source['id']}/fork")
    assert fr.status_code == 201, fr.text
    fork = fr.json()
    assert fork["forked_from"] == source["id"]

    # The fork's public read carries the source attribution.
    gr = await client.get(f"/api/projects/{fork['id']}")
    assert gr.status_code == 200, gr.text
    body = gr.json()
    assert body["forked_from_name"] == source["name"]
    assert body["forked_from_slug"] == source["slug"]


async def test_organic_project_has_no_lineage(
    client: httpx.AsyncClient,
) -> None:
    await _register(client, "lineage-organic@example.com")
    organic = await _create(client, "Просто проект")

    gr = await client.get(f"/api/projects/{organic['id']}")
    assert gr.status_code == 200, gr.text
    body = gr.json()
    assert body["forked_from"] is None
    assert body["forked_from_name"] is None
    assert body["forked_from_slug"] is None

"""Single-source resolution of a user dev container's container-to-container URL.

User dev containers serve Next.js on ``:3000`` inside the container. Their host
bind is ``127.0.0.1``-only, so the worker can't reach them via the host port or
the public nginx URL (hairpin NAT). It reaches them container-to-container by
name over the shared ``omnia-runtime_default`` network (the worker joins it in
``deploy/full/docker-compose.yml``) — the same path user containers use for
MinIO. This is the ONLY internal address the worker can render against without
public egress.

R-04 single source: both the preview-render job (screenshots the live app) and
the entity composition gate (V1.6 16/5 — scores the live app's awwwards floor)
build this URL from the same place, so the "internal dev URL = http://
<container_name>:3000" fact lives in exactly one function.
"""

from __future__ import annotations

from uuid import UUID

from omnia_api.services import orchestrator_client

#: Internal port a user dev container's Next.js server listens on.
DEV_CONTAINER_PORT = 3000


def _normalize_route(route: str) -> str:
    """Render a ``route`` as the suffix appended to the bare container URL.

    The bare URL (``http://<name>:3000``) already addresses ``/`` — so the root
    route contributes *no* suffix, keeping the default-route URL byte-identical
    to the historical value (the preview job + its tests rely on this). Any other
    route is returned with a single leading slash. Single-source so the
    "internal URL = base + route" math lives in exactly one place (R-04)."""
    r = (route or "/").strip()
    if r in ("", "/"):
        return ""
    return r if r.startswith("/") else f"/{r}"


async def resolve_live_url(project_id: UUID, route: str = "/") -> str | None:
    """Container-to-container URL of a *running* dev preview, or ``None`` if it
    isn't up / can't be located. Fail-soft (R-10): any orchestrator hiccup →
    ``None`` → the caller just skips this round (a later build / edit re-enqueues
    once the container is warm).

    ``route`` (V1.6 16/5d) lets the composition gate target the WOW/content
    surface instead of the bare ``/`` (which for an auth-gated entity app is a
    login card, the wrong surface for the landing rubric). The default ``/``
    keeps every existing caller byte-identical."""
    try:
        status = await orchestrator_client.get_status(project_id)
    except Exception:
        return None
    if status.get("state") != "running":
        return None
    name = status.get("container_name")
    if not isinstance(name, str) or not name:
        return None
    return f"http://{name}:{DEV_CONTAINER_PORT}{_normalize_route(route)}"

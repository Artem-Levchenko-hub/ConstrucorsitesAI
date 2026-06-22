"""HTTP client to the V2 orchestrator (`apps/orchestrator` on :8003).

apps/api is a thin authenticated proxy: it owns the JWT cookie and the
ownership check, then forwards the request to orchestrator with a shared
X-Internal-Token header. Errors from orchestrator are translated into our
ApiError taxonomy so the public response shape stays consistent.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

import httpx
import structlog

from omnia_api.core.config import get_settings
from omnia_api.core.errors import ApiError

log = structlog.get_logger(__name__)


class OrchestratorUnavailable(ApiError):
    """Orchestrator is offline or returns a network/5xx error."""

    def __init__(self, message: str, details: dict[str, Any] | None = None) -> None:
        super().__init__(
            code="orchestrator_unavailable",
            message=message,
            status_code=503,
            details=details,
        )


class OrchestratorBadRequest(ApiError):
    """Orchestrator rejected the request (4xx) — pass the reason through."""

    def __init__(self, message: str, status_code: int = 400, details: dict[str, Any] | None = None) -> None:
        super().__init__(
            code="orchestrator_rejected",
            message=message,
            status_code=status_code,
            details=details,
        )


async def _request(
    method: str,
    path: str,
    *,
    json: dict[str, Any] | None = None,
    params: dict[str, Any] | None = None,
    timeout: float = 30.0,
) -> dict[str, Any]:
    """Internal call to orchestrator. Returns parsed JSON body or raises ApiError.

    Note: orchestrator routes are all under `/internal/...` and require the
    `X-Internal-Token` header. The shared secret comes from settings — same
    string sits in /opt/omnia-runtime/.env.orchestrator on prod.

    `timeout` defaults to 30s for normal requests. Long-running jobs (e.g.
    /build-exe which invokes PyInstaller + NSIS) should pass a higher value —
    see `build_exe()` which uses 360s.
    """
    settings = get_settings()
    token = (
        settings.orchestrator_internal_token.get_secret_value()
        if settings.orchestrator_internal_token
        else ""
    )
    if not token:
        # Not configured — fail fast, don't silently 200 with bogus data.
        raise OrchestratorUnavailable(
            "Orchestrator token is not configured (set ORCHESTRATOR_INTERNAL_TOKEN)."
        )

    url = f"{settings.orchestrator_url.rstrip('/')}{path}"
    headers = {"X-Internal-Token": token, "Content-Type": "application/json"}

    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.request(method, url, json=json, params=params, headers=headers)
    except httpx.RequestError as exc:
        log.exception("orchestrator.network_error", path=path, err=str(exc))
        raise OrchestratorUnavailable(f"Cannot reach orchestrator at {url}") from exc

    if resp.status_code >= 500:
        log.error("orchestrator.upstream_5xx", path=path, status=resp.status_code, body=resp.text[:300])
        raise OrchestratorUnavailable(
            f"Orchestrator returned {resp.status_code}",
            details={"upstream_body": resp.text[:300]},
        )
    if resp.status_code >= 400:
        try:
            detail = resp.json()
        except Exception:
            detail = {"raw": resp.text[:300]}
        log.warning("orchestrator.4xx", path=path, status=resp.status_code, detail=detail)
        raise OrchestratorBadRequest(
            f"Orchestrator rejected request: {detail.get('detail', 'unknown')}",
            status_code=resp.status_code,
            details=detail if isinstance(detail, dict) else {"detail": detail},
        )

    try:
        return resp.json()
    except Exception as exc:
        raise OrchestratorUnavailable(
            f"Orchestrator returned non-JSON ({resp.status_code})"
        ) from exc


# --- Public API ---------------------------------------------------------


async def get_status(project_id: UUID) -> dict[str, Any]:
    """GET /internal/projects/<uuid>/status — current runtime state."""
    return await _request("GET", f"/internal/projects/{project_id}/status")


async def wake(project_id: UUID) -> dict[str, Any]:
    """POST /internal/projects/wake — start (or unpause) a previously provisioned project."""
    return await _request("POST", "/internal/projects/wake", json={"project_id": str(project_id)})


async def provision(
    *,
    project_id: UUID,
    slug: str,
    template: str,
    tier: str = "free",
    initial_env: dict[str, str] | None = None,
) -> dict[str, Any]:
    """POST /internal/projects/provision — first-time scaffold + start."""
    payload: dict[str, Any] = {
        "project_id": str(project_id),
        "slug": slug,
        "template": template,
        "tier": tier,
    }
    if initial_env:
        payload["initial_env"] = initial_env
    return await _request("POST", "/internal/projects/provision", json=payload)


async def stop(project_id: UUID, *, pause: bool = True) -> dict[str, Any]:
    """POST /internal/projects/stop — pause or full stop of dev container."""
    return await _request(
        "POST",
        "/internal/projects/stop",
        json={"project_id": str(project_id), "pause": pause},
    )


async def deploy(project_id: UUID, *, commit_sha: str | None = None) -> dict[str, Any]:
    """POST /internal/projects/deploy — build prod image + swap traffic."""
    payload: dict[str, Any] = {"project_id": str(project_id)}
    if commit_sha:
        payload["commit_sha"] = commit_sha
    return await _request("POST", "/internal/projects/deploy", json=payload)


async def get_deploy(project_id: UUID) -> dict[str, Any]:
    """GET /internal/projects/<uuid>/deploy — last-known deploy record.

    Returns the orchestrator's `DeployResponse` shape:
    `{project_id, phase, prod_url, image_tag, started_at, finished_at, error}`.
    `phase` is one of `queued | building | swapping | done | failed`. For a
    project that has never been deployed the orchestrator returns
    `phase=queued` with no prod_url.
    """
    return await _request("GET", f"/internal/projects/{project_id}/deploy")


async def destroy(project_id: UUID, slug: str) -> dict[str, Any]:
    """POST /internal/projects/<uuid>/destroy?slug=<slug> — full teardown.

    Removes dev+prod containers, releases ports, archives the per-project
    Postgres schema, removes nginx vhosts. Idempotent on the orchestrator side,
    so a retry after a partial failure is safe. `slug` is required as a query
    param (orchestrator looks containers up by `omnia-dev-<slug>`)."""
    return await _request(
        "POST",
        f"/internal/projects/{project_id}/destroy",
        params={"slug": slug},
    )


async def get_logs(
    project_id: UUID, *, tail: int = 200, kind: str = "dev"
) -> dict[str, Any]:
    """GET /internal/projects/<uuid>/logs — tail container stdout+stderr.

    Returns `{"project_id", "container_name", "tail", "logs": "<text>"}`.
    `logs` is a single UTF-8 string with newline-separated lines.
    """
    return await _request(
        "GET",
        f"/internal/projects/{project_id}/logs",
        params={"tail": tail, "kind": kind},
    )


async def compile_status(
    project_id: UUID, *, slug: str | None = None
) -> dict[str, Any]:
    """GET /internal/projects/<uuid>/compile-status — does the dev build fail?

    Returns `{"project_id", "ok": bool, "error": str|None, "file": str|None}`.
    `ok=True` when the Next.js dev server is compiling cleanly (or has no
    outstanding error). Used right after a hot-reload to surface a compile
    failure as a chat card. Fail-soft on the orchestrator side: a missing
    container returns `ok=True`, never a 404.
    """
    params = {"slug": slug} if slug else None
    return await _request(
        "GET",
        f"/internal/projects/{project_id}/compile-status",
        params=params,
    )


async def runtime_status(
    project_id: UUID, *, slug: str | None = None, path: str = "/"
) -> dict[str, Any]:
    """GET /internal/projects/<uuid>/runtime-status — does the running app 5xx?

    Returns `{"project_id", "ok": bool, "status_code": int|None, "error": str|None,
    "file": str|None}`. `ok=False` only when the rendered route returns 5xx — a
    compile-clean app that still crashes on render. Used right after a build,
    after the compile probe comes back clean. Fail-soft on the orchestrator side:
    a missing/paused container returns `ok=True`, never a 404.
    """
    params: dict[str, str] = {"path": path}
    if slug:
        params["slug"] = slug
    return await _request(
        "GET",
        f"/internal/projects/{project_id}/runtime-status",
        params=params,
    )


async def read_container_file(
    project_id: UUID, slug: str, path: str
) -> str | None:
    """GET /internal/projects/{id}/read-file — read a whitelisted fixed file
    (e.g. ``src/app/globals.css``) straight from the running dev container.

    The project git repo only tracks AI-generated files; the template's fixed
    files live solely in the container image. Returns the file content, or
    ``None`` if it isn't present / the container is down (caller falls back).
    """
    resp = await _request(
        "GET",
        f"/internal/projects/{project_id}/read-file",
        params={"slug": slug, "path": path},
    )
    if not resp.get("found"):
        return None
    content = resp.get("content")
    return content if isinstance(content, str) else None


# ── Agentic builder tools (Phase 0) ─────────────────────────────────────────
# Thin wrappers the api-side agent loop (services/agent_builder.py) calls to act
# on the live dev container. Each maps to a /agent/* orchestrator endpoint.


async def agent_read_file(
    project_id: UUID, slug: str, path: str
) -> str | None:
    """Read ANY file under /app from the dev container; None if missing/down."""
    resp = await _request(
        "GET",
        f"/internal/projects/{project_id}/agent/read-file",
        params={"slug": slug, "path": path},
    )
    if not resp.get("found"):
        return None
    content = resp.get("content")
    return content if isinstance(content, str) else None


async def agent_list_dir(project_id: UUID, slug: str, path: str = ".") -> str:
    """List a directory under /app; returns the ls output (or an error line)."""
    resp = await _request(
        "GET",
        f"/internal/projects/{project_id}/agent/list-dir",
        params={"slug": slug, "path": path},
    )
    detail = resp.get("detail")
    return detail if isinstance(detail, str) else ""


async def agent_grep(
    project_id: UUID, slug: str, *, pattern: str, path: str = "src"
) -> str:
    """Recursive text search under /app; returns matches (or '(no matches)')."""
    resp = await _request(
        "GET",
        f"/internal/projects/{project_id}/agent/grep",
        params={"slug": slug, "pattern": pattern, "path": path},
    )
    detail = resp.get("detail")
    return detail if isinstance(detail, str) else ""


async def agent_build(project_id: UUID, slug: str) -> dict[str, Any]:
    """Run the container typecheck; returns {ok: bool, detail/error: str}."""
    return await _request(
        "POST",
        f"/internal/projects/{project_id}/agent/build",
        params={"slug": slug},
    )


async def agent_exec(project_id: UUID, slug: str, cmd: str) -> dict[str, Any]:
    """Run a shell command in the dev container (agent `bash` tool)."""
    return await _request(
        "POST",
        f"/internal/projects/{project_id}/agent/exec",
        params={"slug": slug, "cmd": cmd},
    )


async def hot_reload(
    project_id: UUID, slug: str, files: dict[str, str]
) -> dict[str, Any]:
    """POST /internal/projects/hot-reload — write AI-generated files into the
    dev container; orchestrator additionally runs `drizzle-kit push` if the
    diff touches `src/lib/db/schema.ts` or `src/lib/db/migrations/*`.

    `slug` is required as a query param because orchestrator's container
    lookup is `omnia-dev-<slug>` (no project_id ↔ container_name registry
    yet, PoC). apps/api always has the slug at hand from its own Project row.
    """
    return await _request(
        "POST",
        "/internal/projects/hot-reload",
        json={"project_id": str(project_id), "files": files},
        params={"slug": slug},
    )


async def build_exe(
    name: str,
    files: dict[str, str],
    pyinstaller_args: list[str],
    installer_nsi: str,
    requirements: str | None,
) -> dict[str, Any]:
    """POST /build-exe — package a Python project into a Windows .exe + NSIS
    Setup installer.

    The orchestrator side spawns an ``omnia-exe-builder`` sidecar container
    that runs PyInstaller + NSIS and returns the artefacts as base-64 blobs.
    A full build typically takes 60–300s, so we override the default 30s
    socket timeout with 360s. Returns ``{"ok": bool, "log": str,
    "setup_b64": str, "exe_b64": str | null}``.
    """
    return await _request(
        "POST",
        "/build-exe",
        json={
            "name": name,
            "files": files,
            "pyinstaller_args": pyinstaller_args,
            "installer_nsi": installer_nsi,
            "requirements": requirements,
        },
        timeout=360.0,
    )

import asyncio
import html
import logging
from mimetypes import guess_type
from pathlib import Path
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query, Response, status
from fastapi.responses import RedirectResponse
from sqlalchemy import select

from omnia_api.core.deps import SessionDep
from omnia_api.core.errors import ApiError
from omnia_api.models.project import Project
from omnia_api.models.snapshot import Snapshot
from omnia_api.services import orchestrator_client
from omnia_api.services import repo as repo_svc

log = logging.getLogger("omnia_api.public")

router = APIRouter(prefix="/p", tags=["public"], include_in_schema=False)

# Select-mode inspector — single source of truth is static/omnia-inspector.js (a
# synced copy ships in the orchestrator Next.js template; a drift test keeps them
# identical). Inlined into the workspace preview only when ?inspect=1 is present,
# so public share links (/p/<slug>) stay clean. Read once at import — a missing
# file fails loudly at startup rather than silently per-request.
_INSPECTOR_JS = (
    Path(__file__).resolve().parent.parent / "static" / "omnia-inspector.js"
).read_text(encoding="utf-8")
_INSPECTOR_TAG = (
    b'<script id="omnia-inspector">' + _INSPECTOR_JS.encode("utf-8") + b"</script>"
)

# Canonical kit assets served slug-independently at /api/kit/<file> so the
# streaming-preview iframe (apps/web) can load the SAME omnia-kit.css the
# committed /p/<slug> page uses — styling parity while the AI is still typing
# (the streaming iframe has no project slug yet, esp. on the first prompt).
# Mounted under /api (nginx already proxies that to this service). Read once at
# import; filenames whitelisted (no path traversal). The JS is exposed too for
# future use, but the streaming bootstrap loads ONLY the CSS — the kit's
# DOM-mutating JS animations conflict with morphdom's live patching.
_KIT_ASSETS_DIR = (
    Path(__file__).resolve().parent.parent / "templates" / "blank" / "assets"
)
_KIT_ASSET_MIME = {
    "omnia-kit.css": "text/css; charset=utf-8",
    "omnia-kit.js": "application/javascript; charset=utf-8",
    "anime.min.js": "application/javascript; charset=utf-8",
}
_KIT_ASSETS: dict[str, bytes] = {
    name: (_KIT_ASSETS_DIR / name).read_bytes() for name in _KIT_ASSET_MIME
}

kit_router = APIRouter(prefix="/api/kit", tags=["kit"], include_in_schema=False)


@kit_router.get("/{file}", response_class=Response)
async def get_kit_asset(file: str) -> Response:
    """Serve a whitelisted Omnia-kit asset for the streaming preview."""
    data = _KIT_ASSETS.get(file)
    if data is None:
        raise ApiError(
            "not_found", f"kit asset {file} not found", status.HTTP_404_NOT_FOUND
        )
    return Response(
        content=data,
        media_type=_KIT_ASSET_MIME[file],
        headers={
            "Cache-Control": "public, max-age=3600",
            "Access-Control-Allow-Origin": "*",
        },
    )


async def _resolve_snapshot(
    session: SessionDep,
    slug: str,
    snapshot_id: UUID | None = None,
) -> tuple[Project, Snapshot]:
    """Look up project by slug and return (project, snapshot).

    If `snapshot_id` is given, return that specific historical snapshot —
    but only if it belongs to the project. Otherwise return HEAD.
    """
    res = await session.execute(select(Project).where(Project.slug == slug))
    project = res.scalar_one_or_none()
    if project is None:
        raise ApiError("not_found", "project not found", status.HTTP_404_NOT_FOUND)

    target_id = snapshot_id if snapshot_id is not None else project.current_snapshot_id
    if target_id is None:
        raise ApiError("not_found", "snapshot not found", status.HTTP_404_NOT_FOUND)

    snapshot = await session.get(Snapshot, target_id)
    if snapshot is None or snapshot.project_id != project.id:
        raise ApiError("not_found", "snapshot not found", status.HTTP_404_NOT_FOUND)
    return project, snapshot


def _file_response(path: str, content: bytes) -> Response:
    mime, _ = guess_type(path)
    headers = {
        # Allow the workspace iframe (same origin) to embed this preview.
        "X-Frame-Options": "SAMEORIGIN",
        # Don't let stale HEADs linger — every navigation re-fetches.
        "Cache-Control": "no-cache",
    }
    return Response(
        content=content,
        media_type=mime or "application/octet-stream",
        headers=headers,
    )


def _inject_inspector(content: bytes) -> bytes:
    """Inline the select-mode inspector before the last </body> (fallback: append).

    Bytes in, bytes out — we never decode the page, so its charset is preserved.
    """
    marker = b"</body>"
    idx = content.rfind(marker)
    if idx != -1:
        return content[:idx] + _INSPECTOR_TAG + content[idx:]
    return content + _INSPECTOR_TAG


def _inject_base_href(content: bytes, slug: str) -> bytes:
    """Inject ``<base href="/p/{slug}/">`` so the page's relative asset paths
    resolve to ``/p/{slug}/assets/…`` (which the ``get_file`` route serves)
    instead of ``/p/assets/…`` (which 404s because the slug-less route does
    not exist).

    Phase K fix (2026-05-27). Background: ``/p/{slug}`` is served without a
    trailing slash; browsers resolve relative URLs from the parent
    ``/p/``, so ``<link href="assets/omnia-kit.css">`` requests
    ``/p/assets/omnia-kit.css`` which has no matching route. The
    omnia-kit (.depth-*, .reveal, .cursor-blob, scroll-driven animations)
    silently never loaded on share links — every generated site looked
    static / un-animated to the public.

    Idempotent: skips when a ``<base>`` already exists. Bytes in, bytes
    out so charset is preserved.
    """
    head_slice = content[:2048].lower()
    # Already has a <base>? Don't add a second — browsers honour only the first.
    if b"<base " in head_slice or b"<base\t" in head_slice or b"<base>" in head_slice:
        return content
    base_tag = f'\n<base href="/p/{slug}/">'.encode("utf-8")
    head_open = content.find(b"<head")
    if head_open == -1:
        return content
    # Insert right after the closing ``>`` of the <head ...> open tag.
    head_close = content.find(b">", head_open)
    if head_close == -1:
        return content
    ins = head_close + 1
    return content[:ins] + base_tag + content[ins:]


async def _serve_file(project: Project, snapshot: Snapshot, path: str) -> Response:
    content = await asyncio.to_thread(
        repo_svc.read_file, project.id, snapshot.commit_sha, path
    )
    if content is None:
        raise ApiError("not_found", f"file {path} not found", status.HTTP_404_NOT_FOUND)
    return _file_response(path, content)


# Branded, self-contained (no CDN) placeholder so the preview iframe is NEVER
# blank. Full-stack projects have no static index.html — their live preview is
# the dev container shown in the workspace — and brand-new projects may not have
# committed one yet. Inline styles only so it renders even if Tailwind's CDN is
# blocked. Placeholders are filled by `_preview_shell` (values are escaped).
_SHELL_HTML = """<!doctype html>
<html lang="ru"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>__NAME__ — Omnia.AI</title>
<style>
  *{box-sizing:border-box}html,body{height:100%;margin:0}
  body{display:flex;align-items:center;justify-content:center;padding:24px;
    font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;
    color:#e2e8f0;background:radial-gradient(1200px 600px at 50% -10%,#1e293b,#0f172a)}
  main{max-width:30rem;text-align:center}
  .brand{font-weight:700;letter-spacing:-.02em;font-size:14px;text-transform:uppercase;
    background:linear-gradient(90deg,#818cf8,#c084fc);-webkit-background-clip:text;
    background-clip:text;color:transparent;margin-bottom:20px}
  .dot{display:inline-block;width:8px;height:8px;border-radius:9999px;background:#818cf8;
    margin-right:8px;vertical-align:middle;animation:pulse 1.6s ease-in-out infinite}
  h1{font-size:24px;line-height:1.25;margin:0 0 12px;color:#f8fafc;font-weight:600}
  p{font-size:15px;line-height:1.6;color:#94a3b8;margin:0}
  @keyframes pulse{0%,100%{opacity:.35;transform:scale(.85)}50%{opacity:1;transform:scale(1)}}
  @media (prefers-reduced-motion:reduce){.dot{animation:none}}
</style></head>
<body><main>
  <div class="brand">Omnia.AI</div>
  <h1><span class="dot"></span>__TITLE__</h1>
  <p>__MESSAGE__</p>
</main></body></html>"""


def _preview_shell(project: Project) -> Response:
    """Never-blank placeholder for projects without a renderable static index.html."""
    name = html.escape(project.name or "Проект")
    if project.template == "fullstack":
        title = "Приложение запускается"
        message = (
            "Это full-stack приложение на Next.js. Живое превью открывается в "
            "рабочей области Omnia — там для проекта поднимается dev-контейнер."
        )
    else:
        title = "Главная страница ещё не создана"
        message = (
            "Отправьте первый промпт — Omnia сгенерирует index.html, и сайт "
            "появится здесь."
        )
    body = (
        _SHELL_HTML.replace("__NAME__", name)
        .replace("__TITLE__", html.escape(title))
        .replace("__MESSAGE__", html.escape(message))
    )
    return Response(
        content=body.encode("utf-8"),
        media_type="text/html; charset=utf-8",
        headers={"X-Frame-Options": "SAMEORIGIN", "Cache-Control": "no-cache"},
    )


# Where a static entrypoint might live. Models don't always honour "root
# index.html" — they sometimes drop it in Next.js/Vite conventional spots
# (public/, dist/, out/). We probe these in order so the preview shows the
# real page wherever it landed, instead of falling through to the shell. Note:
# a nested index.html with sibling assets may have broken relative links, but
# most AI-generated static pages are single-file (Tailwind CDN), so this still
# renders a real site far more often than a hard 404 would.
_INDEX_CANDIDATES = (
    "index.html",
    "public/index.html",
    "dist/index.html",
    "out/index.html",
)


async def _fullstack_redirect(project: Project) -> Response | None:
    """For a fullstack project that has a finished deploy, redirect /p/<slug>
    to the live prod URL so visitors actually see the running app — not the
    "Приложение запускается" stub. Returns None if no deploy is available, so
    the caller can fall through to `_preview_shell`.

    Best-effort: any orchestrator failure logs and returns None — the shell is
    a perfectly safe fallback. The redirect is 302 (not 301) so a future deploy
    URL change is picked up without browser cache lock-in. `?snapshot=...` and
    `?inspect=1` are NEVER forwarded — those are dev/workspace-only concepts.
    """
    if project.template != "fullstack":
        return None
    try:
        deploy = await orchestrator_client.get_deploy(project.id)
    except Exception as exc:
        # Best-effort: any orchestrator failure (timeout, 5xx, network) must
        # NEVER block /p. Log + fall through to the shell.
        log.warning("public.deploy_lookup_failed slug=%s err=%s", project.slug, exc)
        return None
    if deploy.get("phase") == "done" and deploy.get("prod_url"):
        return RedirectResponse(
            url=str(deploy["prod_url"]),
            status_code=status.HTTP_302_FOUND,
            headers={"Cache-Control": "no-cache"},
        )
    return None


@router.get("/{slug}", response_class=Response)
async def get_index(
    slug: str,
    session: SessionDep,
    snapshot: Annotated[UUID | None, Query()] = None,
    inspect: Annotated[str | None, Query()] = None,
) -> Response:
    project, snap = await _resolve_snapshot(session, slug, snapshot)
    for candidate in _INDEX_CANDIDATES:
        content = await asyncio.to_thread(
            repo_svc.read_file, project.id, snap.commit_sha, candidate
        )
        if content is not None:
            # Rewrite the document base so relative asset URLs
            # (``assets/omnia-kit.css``, ``./logo.svg``, etc.) resolve to
            # ``/p/{slug}/…`` instead of ``/p/…``. Without this the
            # generated omnia-kit never loads on public share links.
            content = _inject_base_href(content, slug)
            # Workspace preview opts in with ?inspect=1 to enable select-mode.
            if inspect == "1":
                content = _inject_inspector(content)
            return _file_response("index.html", content)
    # No static entrypoint. For deployed full-stack projects we redirect to
    # the live prod URL so /p/<slug> behaves as a real public share link, not
    # an eternal "Приложение запускается" stub. Workspace preview (inspect=1)
    # never redirects — it stays in-iframe so select-mode can talk to it.
    if inspect != "1":
        redirect = await _fullstack_redirect(project)
        if redirect is not None:
            return redirect
    # Fall through: pre-deploy fullstack OR a static project with no committed
    # index.html yet → branded shell instead of a raw 404 / blank iframe.
    return _preview_shell(project)


@router.get("/{slug}/{file_path:path}", response_class=Response)
async def get_file(
    slug: str,
    file_path: str,
    session: SessionDep,
    snapshot: Annotated[UUID | None, Query()] = None,
) -> Response:
    project, snap = await _resolve_snapshot(session, slug, snapshot)
    return await _serve_file(project, snap, file_path)

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

from omnia_api.core.deps import OptionalUserDep, SessionDep
from omnia_api.core.errors import ApiError
from omnia_api.models.project import Project
from omnia_api.models.snapshot import Snapshot
from omnia_api.routers.projects import perform_fork
from omnia_api.schemas.project import is_fullstack
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
    base_tag = f'\n<base href="/p/{slug}/">'.encode()
    head_open = content.find(b"<head")
    if head_open == -1:
        return content
    # Insert right after the closing ``>`` of the <head ...> open tag.
    head_close = content.find(b">", head_open)
    if head_close == -1:
        return content
    ins = head_close + 1
    return content[:ins] + base_tag + content[ins:]


# Viral "Remix this" seam (V4.1b-UI). A stranger on a public share link can fork
# the app into their OWN editable copy with one click and ZERO signup: the button
# POSTs to /api/projects/<id>/fork (which mints an anon principal + session cookie
# via _ensure_anon_user, deep-copies the repo, and returns the fork's id), then we
# navigate the visitor straight into the workspace at /projects/<fork.id>. The
# omnia_session cookie just set rides the same-origin redirect (samesite=lax sends
# it on the top-level GET) so the (app) layout's getSession resolves the anon user
# and the workspace renders without a login wall — closing pillar 4's viral loop.
# Injected ONLY into static share pages and ONLY for public viewers (never the
# workspace's own ?inspect=1 preview, which has its own remix affordances).
# Self-contained (no CDN), reduced-motion-safe, brand-gradient pill, bottom-right.
# Authored as a readable UTF-8 str (real Cyrillic, no \x escapes) and encoded to
# bytes once at import so the rest of the page pipeline stays bytes-in/bytes-out.
_REMIX_CTA_TEMPLATE = (
    '<style id="omnia-remix-style">'
    "#omnia-remix-cta{position:fixed;right:20px;bottom:20px;z-index:2147483000;"
    "font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif}"
    "#omnia-remix-btn{display:inline-flex;align-items:center;gap:9px;border:0;"
    "cursor:pointer;padding:13px 20px;border-radius:9999px;font-size:15px;"
    "font-weight:600;letter-spacing:-.01em;color:#fff;"
    "background:linear-gradient(90deg,#818cf8,#c084fc);"
    "box-shadow:0 8px 28px rgba(99,102,241,.42),0 2px 8px rgba(0,0,0,.18);"
    "transition:transform .18s ease,box-shadow .18s ease;"
    "animation:omnia-remix-in .5s cubic-bezier(.16,1,.3,1) both}"
    "#omnia-remix-btn:hover{transform:translateY(-2px) scale(1.02);"
    "box-shadow:0 12px 34px rgba(99,102,241,.5),0 3px 10px rgba(0,0,0,.2)}"
    "#omnia-remix-btn:active{transform:translateY(0) scale(.99)}"
    "#omnia-remix-btn[disabled]{opacity:.7;cursor:default;transform:none}"
    "#omnia-remix-btn .omnia-remix-spark{font-size:16px;line-height:1;"
    "animation:omnia-remix-spin 4s linear infinite}"
    "@keyframes omnia-remix-in{from{opacity:0;transform:translateY(14px) scale(.96)}"
    "to{opacity:1;transform:none}}"
    "@keyframes omnia-remix-spin{to{transform:rotate(360deg)}}"
    "@media (prefers-reduced-motion:reduce){#omnia-remix-btn,"
    "#omnia-remix-btn .omnia-remix-spark{animation:none}"
    "#omnia-remix-btn:hover{transform:none}}"
    "</style>"
    '<div id="omnia-remix-cta">'
    '<button type="button" id="omnia-remix-btn" '
    'aria-label="Сделать свою версию этого приложения на Omnia.AI">'
    '<span class="omnia-remix-spark" aria-hidden="true">✦</span>'
    '<span class="omnia-remix-label">Сделать свою версию</span>'
    "</button></div>"
    '<script id="omnia-remix-js">(function(){'
    'var b=document.getElementById("omnia-remix-btn");if(!b)return;'
    'b.addEventListener("click",function(){'
    "if(b.disabled)return;b.disabled=true;"
    'var l=b.querySelector(".omnia-remix-label");var prev=l?l.textContent:"";'
    'if(l)l.textContent="Создаём копию…";'
    'fetch("/api/projects/__PROJECT_ID__/fork",{method:"POST",'
    'credentials:"include",headers:{Accept:"application/json"}})'
    '.then(function(r){if(!r.ok)throw new Error("fork "+r.status);return r.json();})'
    '.then(function(p){window.location.href="/projects/"+p.id;})'
    '.catch(function(e){b.disabled=false;if(l)l.textContent=prev;'
    'console.error("[omnia-remix]",e);'
    'alert("Не удалось создать копию. Попробуйте ещё раз.");});'
    "});})();</script>"
    # Viral seed badge (#VIRAL-WATERMARK, pillar 4). A subtle "Сделано на
    # Omnia.AI" credit bottom-LEFT (opposite the loud remix pill). Click opens a
    # branded popover that replays the "design being born" reveal on demand
    # (window.__omniaReplayBrief, baked by services/brief_narration alongside the
    # narration script) and offers a fresh "Создать свой сайт" CTA → the
    # same-origin Omnia landing ("/" — absolute, unaffected by the page's <base
    # href="/p/<slug>/">). Mirrors the container watermark in the template's
    # omnia-remix-cta.js so the seed looks identical whichever surface served it.
    '<style id="omnia-wm-style">'
    "#omnia-wm{position:fixed;left:18px;bottom:18px;z-index:2147482999;"
    "font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif}"
    "#omnia-wm-badge{display:inline-flex;align-items:center;gap:7px;cursor:pointer;"
    "border:1px solid rgba(255,255,255,.16);background:rgba(15,16,28,.62);"
    "-webkit-backdrop-filter:blur(10px) saturate(1.25);backdrop-filter:blur(10px) saturate(1.25);"
    "color:rgba(255,255,255,.88);padding:7px 13px 7px 11px;border-radius:9999px;"
    "font-size:12.5px;font-weight:600;letter-spacing:-.01em;line-height:1;"
    "box-shadow:0 6px 22px rgba(0,0,0,.3);"
    "transition:transform .18s ease,box-shadow .18s ease,border-color .18s ease;"
    "animation:omnia-wm-in .6s cubic-bezier(.16,1,.3,1) both}"
    "#omnia-wm-badge:hover{transform:translateY(-2px);border-color:rgba(192,132,252,.55);"
    "box-shadow:0 10px 30px rgba(99,102,241,.34)}"
    "#omnia-wm-spark{font-size:13px;line-height:1;"
    "background:linear-gradient(90deg,#818cf8,#c084fc);-webkit-background-clip:text;"
    "background-clip:text;color:transparent}"
    "#omnia-wm-name{background:linear-gradient(90deg,#a5b4fc,#e9d5ff);"
    "-webkit-background-clip:text;background-clip:text;color:transparent}"
    "#omnia-wm-pop{position:absolute;left:0;bottom:calc(100% + 10px);width:288px;"
    "max-width:calc(100vw - 36px);padding:18px;border-radius:18px;"
    "border:1px solid rgba(255,255,255,.12);background:rgba(17,18,30,.92);"
    "-webkit-backdrop-filter:blur(16px) saturate(1.3);backdrop-filter:blur(16px) saturate(1.3);"
    "box-shadow:0 24px 64px rgba(0,0,0,.5),0 2px 10px rgba(0,0,0,.3);"
    "color:#e9eaf3;opacity:0;transform:translateY(8px) scale(.97);transform-origin:bottom left;"
    "pointer-events:none;transition:opacity .2s ease,transform .24s cubic-bezier(.16,1,.3,1)}"
    "#omnia-wm-pop::before{content:'';position:absolute;inset:0;border-radius:18px;padding:1px;"
    "background:linear-gradient(135deg,rgba(129,140,248,.5),rgba(192,132,252,.18) 55%,transparent);"
    "-webkit-mask:linear-gradient(#000 0 0) content-box,linear-gradient(#000 0 0);"
    "-webkit-mask-composite:xor;mask-composite:exclude;pointer-events:none}"
    "#omnia-wm.open #omnia-wm-pop{opacity:1;transform:none;pointer-events:auto}"
    "#omnia-wm.open #omnia-wm-badge{border-color:rgba(192,132,252,.55)}"
    "#omnia-wm-pop h4{margin:0 0 6px;font-size:14px;font-weight:700;letter-spacing:-.01em;"
    "display:flex;align-items:center;gap:7px;color:#fff}"
    "#omnia-wm-pop h4 b{background:linear-gradient(90deg,#a5b4fc,#e9d5ff);"
    "-webkit-background-clip:text;background-clip:text;color:transparent;font-weight:700}"
    "#omnia-wm-pop p{margin:0 0 14px;font-size:12.5px;line-height:1.5;color:rgba(233,234,243,.72)}"
    "#omnia-wm-replay{display:inline-flex;align-items:center;gap:7px;width:100%;"
    "justify-content:center;cursor:pointer;border:1px solid rgba(255,255,255,.14);"
    "background:rgba(255,255,255,.04);color:#e9eaf3;padding:9px 12px;border-radius:11px;"
    "font-size:12.5px;font-weight:600;margin-bottom:8px;"
    "transition:background .16s ease,border-color .16s ease}"
    "#omnia-wm-replay:hover{background:rgba(255,255,255,.09);border-color:rgba(192,132,252,.45)}"
    "#omnia-wm-make{display:flex;align-items:center;justify-content:center;gap:7px;width:100%;"
    "box-sizing:border-box;cursor:pointer;border:0;text-decoration:none;color:#fff;"
    "padding:11px 14px;border-radius:11px;font-size:13.5px;font-weight:700;letter-spacing:-.01em;"
    "background:linear-gradient(90deg,#818cf8,#c084fc);"
    "box-shadow:0 8px 24px rgba(99,102,241,.4);"
    "transition:transform .16s ease,box-shadow .16s ease}"
    "#omnia-wm-make:hover{transform:translateY(-1px);box-shadow:0 12px 30px rgba(99,102,241,.5)}"
    "@keyframes omnia-wm-in{from{opacity:0;transform:translateY(12px) scale(.94)}"
    "to{opacity:1;transform:none}}"
    "@media (max-width:479px){#omnia-wm-made{display:none}}"
    "@media (prefers-reduced-motion:reduce){#omnia-wm-badge,#omnia-wm-pop,#omnia-wm-make{"
    "animation:none!important;transition:none!important}#omnia-wm-badge:hover{transform:none}}"
    "</style>"
    '<div id="omnia-wm">'
    '<button type="button" id="omnia-wm-badge" aria-haspopup="dialog" '
    'aria-expanded="false" aria-label="Сделано на Omnia.AI — создать свой сайт">'
    '<span id="omnia-wm-spark" aria-hidden="true">✦</span>'
    '<span id="omnia-wm-made">Сделано на </span>'
    '<span id="omnia-wm-name">Omnia.AI</span></button>'
    '<div id="omnia-wm-pop" role="dialog" aria-label="Создано на Omnia.AI">'
    '<h4><span aria-hidden="true">✦</span><b>Omnia.AI</b></h4>'
    "<p>Этот сайт собран искусственным интеллектом из одного промпта — "
    "за пару минут.</p>"
    '<button type="button" id="omnia-wm-replay" style="display:none">'
    '<span aria-hidden="true">▶</span>'
    "<span>Посмотреть, как он родился</span></button>"
    '<a id="omnia-wm-make" href="/" target="_blank" rel="noopener">'
    "<span>Создать свой сайт</span>"
    '<span aria-hidden="true">→</span></a>'
    "</div></div>"
    '<script id="omnia-wm-js">(function(){'
    'var w=document.getElementById("omnia-wm");if(!w)return;'
    'var b=document.getElementById("omnia-wm-badge");'
    'var rp=document.getElementById("omnia-wm-replay");'
    'function onDoc(e){if(!w.contains(e.target))close();}'
    'function onKey(e){if(e.key==="Escape"||e.keyCode===27)close();}'
    'function open(){w.classList.add("open");b.setAttribute("aria-expanded","true");'
    'document.addEventListener("click",onDoc,true);document.addEventListener("keydown",onKey);}'
    'function close(){w.classList.remove("open");b.setAttribute("aria-expanded","false");'
    'document.removeEventListener("click",onDoc,true);document.removeEventListener("keydown",onKey);}'
    'b.addEventListener("click",function(e){e.stopPropagation();'
    'if(w.classList.contains("open"))close();else open();});'
    'if(rp&&typeof window.__omniaReplayBrief==="function"){rp.style.display="";'
    'rp.addEventListener("click",function(){close();'
    'try{window.__omniaReplayBrief();}catch(_){}});}'
    "})();</script>"
).encode()


def _inject_remix_cta(content: bytes, project_id: object) -> bytes:
    """Inline the zero-signup "Remix this" CTA before the last </body>.

    Bytes in, bytes out — the page charset is preserved. Idempotent: a page that
    already carries the CTA (e.g. re-injection) is returned untouched. The
    project id is a DB-issued UUID (never user input), so direct substitution is
    safe. Fallback appends when the page has no </body>.
    """
    if b'id="omnia-remix-cta"' in content:
        return content
    tag = _REMIX_CTA_TEMPLATE.replace(
        b"__PROJECT_ID__", str(project_id).encode("ascii")
    )
    marker = b"</body>"
    idx = content.rfind(marker)
    if idx != -1:
        return content[:idx] + tag + content[idx:]
    return content + tag


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

# Deploy phases that mean an image build / traffic swap is actively in flight.
# While in one of these (and with no prior prod_url), a fullstack share link
# must NOT leak the half-built dev container to strangers — it falls through to
# the "запускается" stub instead. Phase contract lives in
# `orchestrator_client.get_deploy` (queued | building | swapping | done | failed).
_DEPLOY_IN_FLIGHT_PHASES = frozenset({"building", "swapping"})


async def _fullstack_redirect(project: Project) -> Response | None:
    """For a container project (fullstack / nextjs_entities), redirect /p/<slug>
    to the LIVE app so visitors see it running — not the "Приложение
    запускается" stub. Preference order:
      1) a finished prod deploy → its prod_url (the canonical public site);
      2) otherwise, if the project has a provisioned dev container → its public
         dev-preview URL. This holds even when the container is HIBERNATED
         (state stopped/paused): the dev vhost wakes it on the first hit
         (scale-from-zero, Phase 0.3), so a stranger opening the share link of a
         sleeping app gets the waking page → live app, not a dead shell.
    Returns None only when the project has no container at all (or a dead one),
    so the caller falls through to `_preview_shell`.

    Best-effort: any orchestrator failure logs and returns None — the shell is a
    safe fallback. Redirects are 302 (not 301) so the URL can change later (e.g.
    dev-preview → prod after a deploy) without browser cache lock-in. `?snapshot`
    / `?inspect=1` are NEVER forwarded — those are dev/workspace-only concepts.
    """
    if not is_fullstack(project.template):
        return None
    # 1) Canonical public URL once deployed.
    try:
        deploy = await orchestrator_client.get_deploy(project.id)
        phase = deploy.get("phase")
        if phase == "done" and deploy.get("prod_url"):
            return RedirectResponse(
                url=str(deploy["prod_url"]),
                status_code=status.HTTP_302_FOUND,
                headers={"Cache-Control": "no-cache"},
            )
        # First build still in flight (image building / traffic swapping) and no
        # canonical prod_url yet → the dev container is a half-built shell. A
        # share-link stranger must see the tasteful "запускается" stub, NOT the
        # in-flight dev container leaking through (V4.6 link-served-before-done).
        # A REDEPLOY of an already-live app keeps its prod_url here, so this
        # never stubs a site that is actually live — only a true first build.
        if phase in _DEPLOY_IN_FLIGHT_PHASES and not deploy.get("prod_url"):
            return None
    except Exception as exc:
        # Best-effort: any orchestrator failure (timeout, 5xx, network) must
        # NEVER block /p. Log + fall through to the dev-preview check / shell.
        log.warning("public.deploy_lookup_failed slug=%s err=%s", project.slug, exc)
    # 2) Not deployed yet — send visitors to the dev preview (publicly
    #    reachable) so the share link isn't an eternal "запускается" stub.
    #    A provisioned container reports a dev_url even while hibernated; its
    #    nginx vhost wakes it on the first request (Phase 0.3), so any wakeable
    #    state redirects there. Only a project with no container (dev_url None)
    #    or a dead one (state failed) falls through to the shell.
    _WAKEABLE_STATES = {"running", "stopped", "paused", "provisioning"}
    try:
        st = await orchestrator_client.get_status(project.id)
        if st.get("dev_url") and st.get("state") in _WAKEABLE_STATES:
            return RedirectResponse(
                url=str(st["dev_url"]),
                status_code=status.HTTP_302_FOUND,
                headers={"Cache-Control": "no-cache"},
            )
    except Exception as exc:
        log.warning("public.status_lookup_failed slug=%s err=%s", project.slug, exc)
    return None


@router.get("/{slug}", response_class=Response)
async def get_index(
    slug: str,
    session: SessionDep,
    snapshot: Annotated[UUID | None, Query()] = None,
    inspect: Annotated[str | None, Query()] = None,
) -> Response:
    project, snap = await _resolve_snapshot(session, slug, snapshot)
    # Container projects (fullstack / nextjs_entities) are served by their LIVE
    # dev/prod app, never a static file — redirect FIRST, before the index-candidate
    # scan. Otherwise a stray index.html the AI mistakenly committed (Next.js ignores
    # it) would hijack /p with a stale static page whose relative links resolve to
    # THIS domain (the "login → constructor/signin" + broken-layout bug). Workspace
    # preview (inspect=1) stays in-iframe and never redirects.
    if inspect != "1" and is_fullstack(project.template):
        redirect = await _fullstack_redirect(project)
        if redirect is not None:
            return redirect
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
            # Public share viewers (inspect != "1") instead get the zero-signup
            # "Remix this" CTA — the two are mutually exclusive (the workspace
            # already owns the project, strangers don't).
            if inspect == "1":
                content = _inject_inspector(content)
            else:
                content = _inject_remix_cta(content, project.id)
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


@router.get("/{slug}/remix", response_class=Response)
async def remix_project(
    slug: str,
    session: SessionDep,
    response: Response,
    current_user: OptionalUserDep,
) -> Response:
    """Same-origin, no-JS viral "Remix this" entry — top-level navigation forks
    the shared app and drops the visitor into their own editable workspace.

    The in-page CTA on a *static* /p/<slug> can fork with a same-origin
    ``fetch`` (apps/api owns that origin). A *deployed container* (fullstack /
    nextjs_entities) lives on a different origin, and the ``SameSite=lax``
    session cookie is NOT sent on a cross-origin ``fetch`` — so its CTA links
    here instead. A top-level GET navigation DOES carry the lax cookie (or none
    → an anon owner is minted), so the fork lands on the right principal with no
    CORS and no credentials dance. Reuses ``perform_fork`` (R-04) so isolation
    behaviour is identical to POST ``/fork``.

    GET is deliberate: a plain ``<a href>`` keeps the loop zero-friction (no JS,
    works from email/Slack). A link-prefetcher could create a stray anon fork,
    but anon forks are owner-scoped, zero-balance, and disposable — they never
    touch the source — so the friction-free win outweighs it.
    """
    project, _ = await _resolve_snapshot(session, slug, None)
    fork = await perform_fork(session, response, project, current_user)
    redirect = RedirectResponse(
        url=f"/projects/{fork.id}",
        status_code=status.HTTP_302_FOUND,
        headers={"Cache-Control": "no-cache"},
    )
    # perform_fork set the anon session cookie on the injected `response`; carry
    # it onto the redirect we actually return so the remixer stays signed in to
    # their new ephemeral principal across the navigation.
    for cookie in response.headers.getlist("set-cookie"):
        redirect.raw_headers.append((b"set-cookie", cookie.encode("latin-1")))
    return redirect


@router.get("/{slug}/{file_path:path}", response_class=Response)
async def get_file(
    slug: str,
    file_path: str,
    session: SessionDep,
    snapshot: Annotated[UUID | None, Query()] = None,
) -> Response:
    project, snap = await _resolve_snapshot(session, slug, snapshot)
    return await _serve_file(project, snap, file_path)

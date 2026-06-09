"""Public wake-on-request ingress (`/_omnia/wake`).

Unlike `routers/runtime.py`, this surface is **token-free**: the shared nginx
reaches it over loopback when a per-project vhost 502s (its upstream container
is hibernated). nginx forwards the original `Host`, and we map that hostname
back to the project's container, boot it, and return a self-refreshing "waking
up" page. The browser keeps reloading the original URL; once Next.js is up the
vhost proxies straight through and this endpoint is never hit again.

Safety: we only ever `start` a container that ALREADY exists (provisioned for
this exact host). An unknown host gets a static 404 page and starts nothing, so
a crawler hitting random subdomains can't spin up containers.
"""

from __future__ import annotations

import html
import re
from typing import Annotated

import structlog
from fastapi import APIRouter, Header, Query
from fastapi.responses import HTMLResponse

from omnia_orchestrator.core.config import get_settings
from omnia_orchestrator.core.docker_client import container_status, wake_container
from omnia_orchestrator.core.errors import OrchestratorError
from omnia_orchestrator.services.hibernate import record_activity

router = APIRouter(tags=["ingress"])

log = structlog.get_logger("omnia_orchestrator.ingress")

# A single DNS label: the slug part of "<label>.<suffix>". No dots — a wildcard
# cert / vhost covers exactly one label, so anything with a dot isn't ours.
_LABEL_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")


def _resolve_container(host: str) -> str | None:
    """Map a request Host to its dev/prod container name, or None if the host
    isn't a recognizable omnia preview/prod host.

    `<slug>-dev.<suffix>` → `omnia-dev-<slug>` (live preview);
    `<slug>.<suffix>`     → `omnia-app-<slug>` (deployed prod). Mirrors
    `nginx_writer.dev_host` / `prod_host` exactly.
    """
    suffix = get_settings().runtime_host_suffix
    host = host.split(":", 1)[0].strip().lower()  # drop any :port, normalize
    if not host.endswith("." + suffix):
        return None
    label = host[: -(len(suffix) + 1)]
    if not _LABEL_RE.match(label):
        return None
    if label.endswith("-dev"):
        slug = label[: -len("-dev")]
        return f"omnia-dev-{slug}" if slug else None
    return f"omnia-app-{label}"


@router.get("/_omnia/wake", response_class=HTMLResponse)
async def wake_on_request(
    host_query: Annotated[str | None, Query(alias="host")] = None,
    host_header: Annotated[str | None, Header(alias="host")] = None,
) -> HTMLResponse:
    """Boot the hibernated container behind `Host` and return a waking page.

    In production nginx forwards the original `Host` header; `?host=` is a
    direct-call / test fallback. Waking is fail-soft — a docker hiccup still
    returns the interstitial, so the browser retries rather than seeing a 5xx.
    """
    effective_host = (host_query or host_header or "").strip()
    name = _resolve_container(effective_host)
    if name is None:
        return _not_found_page(effective_host)

    info = await container_status(name)
    state = info["state"]
    project_id = info.get("project_id") or ""

    if state == "not_found":
        # Provisioned vhost but no container — destroyed or never built.
        return _not_found_page(effective_host)

    if state in ("exited", "created", "paused"):
        try:
            await wake_container(name)
            log.info("ingress.woke", host=effective_host, container=name, was=state)
        except OrchestratorError as exc:
            # Don't 5xx — the page will retry; surface the cause in logs.
            log.warning(
                "ingress.wake_failed",
                host=effective_host,
                container=name,
                err=exc.message,
            )

    # Reset the idle timer either way: running means the app is still compiling
    # after a just-issued start, so keep it off the next hibernate sweep.
    if project_id:
        await record_activity(project_id)

    return _waking_page(effective_host)


def _waking_page(host: str) -> HTMLResponse:
    """Self-refreshing interstitial. JS reloads the ORIGINAL url (this page is
    served in its place by nginx error_page), counting attempts in
    sessionStorage to soften the copy if a cold start runs long. `<noscript>`
    falls back to a plain meta-refresh."""
    safe_host = html.escape(host)
    body = f"""<!doctype html>
<html lang="ru">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex">
<title>Запускаем приложение…</title>
<noscript><meta http-equiv="refresh" content="6"></noscript>
<style>
  :root {{ color-scheme: dark; }}
  * {{ box-sizing: border-box; }}
  body {{
    margin: 0; min-height: 100vh; display: grid; place-items: center;
    font-family: ui-sans-serif, system-ui, -apple-system, "Segoe UI", Roboto, sans-serif;
    background: radial-gradient(120% 120% at 50% 0%, #14142b 0%, #0a0a14 60%);
    color: #e7e7f0;
  }}
  .card {{ text-align: center; padding: 2.5rem 1.5rem; max-width: 30rem; }}
  .ring {{
    width: 56px; height: 56px; margin: 0 auto 1.5rem; border-radius: 50%;
    border: 3px solid rgba(255,255,255,.12); border-top-color: #7c6cff;
    animation: spin 0.9s linear infinite;
  }}
  @keyframes spin {{ to {{ transform: rotate(360deg); }} }}
  @media (prefers-reduced-motion: reduce) {{ .ring {{ animation: none; }} }}
  h1 {{ font-size: 1.25rem; font-weight: 650; margin: 0 0 .5rem; letter-spacing: -.01em; }}
  p {{ margin: 0; color: #9a9ab0; font-size: .95rem; line-height: 1.5; }}
  .host {{ margin-top: 1.25rem; font-size: .8rem; color: #5a5a72; word-break: break-all; }}
</style>
</head>
<body>
  <main class="card">
    <div class="ring" aria-hidden="true"></div>
    <h1 id="t">Запускаем приложение…</h1>
    <p id="s">Оно отдыхало — будим контейнер. Это займёт несколько секунд.</p>
    <div class="host">{safe_host}</div>
  </main>
<script>
  (function () {{
    var k = "omnia-wake-" + location.host;
    var n = (parseInt(sessionStorage.getItem(k) || "0", 10) || 0) + 1;
    sessionStorage.setItem(k, String(n));
    if (n >= 6) {{
      document.getElementById("t").textContent = "Почти готово…";
      document.getElementById("s").textContent =
        "Первый запуск компилируется чуть дольше. Спасибо за терпение.";
    }}
    setTimeout(function () {{ location.reload(); }}, 6000);
  }})();
</script>
</body>
</html>"""
    # 200 so the browser renders the page; Retry-After hints well-behaved bots.
    return HTMLResponse(content=body, status_code=200, headers={"Retry-After": "6"})


def _not_found_page(host: str) -> HTMLResponse:
    safe_host = html.escape(host or "—")
    body = f"""<!doctype html>
<html lang="ru">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex">
<title>Приложение не найдено</title>
<style>
  :root {{ color-scheme: dark; }}
  body {{
    margin: 0; min-height: 100vh; display: grid; place-items: center;
    font-family: ui-sans-serif, system-ui, -apple-system, "Segoe UI", Roboto, sans-serif;
    background: #0a0a14; color: #e7e7f0;
  }}
  .card {{ text-align: center; padding: 2.5rem 1.5rem; max-width: 30rem; }}
  h1 {{ font-size: 1.25rem; margin: 0 0 .5rem; }}
  p {{ margin: 0; color: #9a9ab0; }}
  .host {{ margin-top: 1.25rem; font-size: .8rem; color: #5a5a72; word-break: break-all; }}
</style>
</head>
<body>
  <main class="card">
    <h1>Приложение не найдено</h1>
    <p>Похоже, этот проект ещё не создан или был удалён.</p>
    <div class="host">{safe_host}</div>
  </main>
</body>
</html>"""
    return HTMLResponse(content=body, status_code=404)

"""Per-project nginx reverse-proxy sites + per-host TLS.

R-01 (deep module): the whole surface is `dev_host` / `prod_host` /
`publish(host, port)` / `unpublish(host)`. Callers never see nginx config
text, sudo, or certbot.

Layout: we write `<host>.conf` into `settings.nginx_sites_dir`
(/opt/omnia-runtime/nginx/sites-enabled, owned by the orchestrator user, so
no sudo for the file write). The system nginx includes that directory via
/etc/nginx/conf.d/omnia-runtime.conf, which also defines the
`$omnia_connection_upgrade` map used here for WebSocket/HMR upgrades.

TLS strategy (R-10 fail-soft): write an HTTP(:80) block first so the site is
immediately reachable and can answer ACME http-01 challenges, then try to
issue a Let's Encrypt cert via `certbot --webroot`. On success rewrite the
block with :443 + redirect; on ANY failure leave the HTTP block in place.
A site is never left in a state that fails `nginx -t` — if our own block
breaks the config we remove it and restore nginx rather than take the box
down (it is shared with other tenants).
"""

from __future__ import annotations

import os
import re
from pathlib import Path

import structlog

from omnia_orchestrator.core.config import get_settings
from omnia_orchestrator.core.errors import OrchestratorError
from omnia_orchestrator.core.shell import CmdResult, run

log = structlog.get_logger("omnia_orchestrator.nginx")

_HOST_RE = re.compile(r"^[a-z0-9]([a-z0-9.-]{0,253}[a-z0-9])?$")


def dev_host(slug: str) -> str:
    """Public hostname for a project's live dev preview."""
    return f"{slug}-dev.{get_settings().runtime_host_suffix}"


def prod_host(slug: str) -> str:
    """Public hostname for a project's deployed prod site."""
    return f"{slug}.{get_settings().runtime_host_suffix}"


def _scheme() -> str:
    return "https" if get_settings().enable_tls else "http"


def dev_url(slug: str) -> str:
    """Expected dev preview URL (actual scheme confirmed by `publish`)."""
    return f"{_scheme()}://{dev_host(slug)}"


def prod_url(slug: str) -> str:
    """Expected prod URL (actual scheme confirmed by `publish`)."""
    return f"{_scheme()}://{prod_host(slug)}"


def _site_path(host: str) -> Path:
    return Path(get_settings().nginx_sites_dir) / f"{host}.conf"


def _wildcard_cert_dir(host: str) -> str | None:
    """A pre-issued WILDCARD cert covering `host`, if one exists.

    Preview/prod hosts are `<single-label>.<suffix>` (e.g.
    `myslug-dev.preview.lead-generator.ru`), all covered by one `*.<suffix>`
    cert living at `<live_root>/<suffix>/`. When that cert is present we point
    the host's HTTPS block straight at it and SKIP per-host acme — which is the
    flaky bit: a failed issuance leaves an EMPTY fullchain, so `ensure_tls`
    never writes the :443 block and the preview falls through to the catch-all
    `*.preview` vhost → 502 "no live project". The wildcard is instant + can't
    rate-limit or half-fail. Falls back to per-host acme when no wildcard exists
    (e.g. sslip.io hosts). `live_root` overridable via OMNIA_WILDCARD_CERT_ROOT.
    """
    parent = host.split(".", 1)[1] if "." in host else ""
    if not parent:
        return None
    root = os.getenv("OMNIA_WILDCARD_CERT_ROOT", "/etc/letsencrypt/live")
    cert_dir = Path(root) / parent
    try:
        if (cert_dir / "fullchain.pem").exists() and (cert_dir / "privkey.pem").exists():
            return str(cert_dir)
    except OSError:
        return None
    return None


def _proxy_location(port: int) -> str:
    # `$omnia_connection_upgrade` is defined once in conf.d/omnia-runtime.conf.
    # X-Frame-Options is hidden so the workspace can embed the preview iframe.
    return f"""\
    location / {{
        proxy_pass http://127.0.0.1:{port};
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection $omnia_connection_upgrade;
        proxy_read_timeout 86400;
        proxy_hide_header X-Frame-Options;
    }}"""


def _acme_location() -> str:
    return f"""\
    location /.well-known/acme-challenge/ {{
        root {get_settings().acme_webroot};
    }}"""


def _http_block(host: str, port: int) -> str:
    return f"""\
# omnia auto-generated — {host} (HTTP)
server {{
    listen 80;
    listen [::]:80;
    server_name {host};

{_acme_location()}

{_proxy_location(port)}
}}
"""


def _https_block(host: str, port: int) -> str:
    # Prefer a pre-issued wildcard cert (instant, reliable); else the per-host
    # acme cert dir.
    cert_dir = _wildcard_cert_dir(host) or f"{get_settings().acme_certs_dir}/{host}"
    return f"""\
# omnia auto-generated — {host} (HTTPS)
server {{
    listen 80;
    listen [::]:80;
    server_name {host};

{_acme_location()}

    location / {{ return 301 https://$host$request_uri; }}
}}

server {{
    listen 443 ssl;
    listen [::]:443 ssl;
    server_name {host};

    ssl_certificate     {cert_dir}/fullchain.pem;
    ssl_certificate_key {cert_dir}/privkey.pem;
    add_header Strict-Transport-Security "max-age=31536000" always;

{_proxy_location(port)}
}}
"""


async def _reload() -> CmdResult:
    """`nginx -t` then reload. Returns the first failing step (or the reload)."""
    test = await run(["sudo", "-n", "nginx", "-t"], timeout=20)
    if not test.ok:
        return test
    return await run(["sudo", "-n", "systemctl", "reload", "nginx"], timeout=25)


async def _issue_cert(host: str) -> bool:
    """Issue + install a Let's Encrypt cert for `host` via acme.sh (webroot
    http-01). We use acme.sh, NOT the system certbot (2.1.0 is broken on this
    box with `AttributeError: can't set attribute`).

    `acme.sh --issue` returns non-zero when a still-valid cert already exists,
    so we don't gate on its exit code — `--install-cert` copies whatever cert
    acme.sh holds, and success is "the installed files exist afterwards".
    """
    # A pre-issued wildcard cert covers this host → use it, skip acme entirely.
    # This is the reliable path for `*.preview.<domain>` previews; per-host acme
    # is only for hosts without a covering wildcard (e.g. sslip.io).
    if _wildcard_cert_dir(host):
        log.info("nginx.cert_wildcard", host=host)
        return True
    s = get_settings()
    acme = os.path.expanduser("~/.acme.sh/acme.sh")
    # acme.sh's default working dir is ~/.acme.sh, which the unit's
    # ProtectHome=read-only makes unwritable → "Cannot create domain key" and no
    # cert (so HTTPS for the per-project preview never comes up). Redirect --home
    # to a writable runtime dir (seeded once from ~/.acme.sh so the LE account +
    # config carry over — no re-registration). Overridable via env.
    acme_home = os.getenv("OMNIA_ACME_HOME", "/opt/omnia-runtime/acme-home")
    # acme.sh treats LOG_LEVEL/DEBUG as integers; the orchestrator sets
    # LOG_LEVEL=INFO, which makes acme.sh's `[ "$LOG_LEVEL" -ge 2 ]` abort with
    # "integer expression expected". Strip them for the acme.sh subprocess.
    acme_env = {k: v for k, v in os.environ.items() if k not in ("LOG_LEVEL", "DEBUG")}
    cert_dir = Path(s.acme_certs_dir) / host
    cert_dir.mkdir(parents=True, exist_ok=True)
    fullchain = cert_dir / "fullchain.pem"
    privkey = cert_dir / "privkey.pem"

    # Short-circuit: a valid cert is already present (e.g. a symlink to a
    # pre-issued wildcard cert covering this host). Skip the acme.sh round-trip
    # — it would waste rate-limit, time, and a network call for nothing.
    try:
        if fullchain.exists() and privkey.exists() and "BEGIN CERTIFICATE" in fullchain.read_text(errors="ignore"):
            log.info("nginx.cert_short_circuit", host=host)
            return True
    except OSError:
        pass

    await run(
        [
            acme, "--home", acme_home, "--issue", "-d", host,
            "-w", s.acme_webroot,
            "--server", "letsencrypt",
            "--keylength", "ec-256",
        ],
        timeout=180,
        env=acme_env,
    )
    install = await run(
        [
            acme, "--home", acme_home, "--install-cert", "-d", host, "--ecc",
            "--key-file", str(privkey),
            "--fullchain-file", str(fullchain),
            "--reloadcmd", "sudo -n systemctl reload nginx",
        ],
        timeout=60,
        env=acme_env,
    )
    # Gate on a real installed cert — empty/garbage files would crash nginx.
    ok = False
    if fullchain.exists() and privkey.exists():
        try:
            ok = "BEGIN CERTIFICATE" in fullchain.read_text(errors="ignore")
        except OSError:
            ok = False
    if not ok:
        log.warning("nginx.cert_failed", host=host, stderr=install.stderr[-400:])
    return ok


def _validate_host(host: str) -> None:
    if not _HOST_RE.match(host):
        raise OrchestratorError(
            code="validation_failed",
            message=f"refusing to write nginx site for unsafe host: {host!r}",
            status_code=400,
        )


async def publish_http(host: str, port: int) -> None:
    """Write the HTTP(:80) block for `host` and reload nginx (fast, ~1-2s).

    Makes the site reachable over HTTP immediately and able to answer the
    ACME http-01 challenge. Raises (after rolling back) only if our own block
    breaks the shared nginx config — we never leave the box in a failing state.
    """
    _validate_host(host)
    path = _site_path(host)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_http_block(host, port), encoding="utf-8")
    res = await _reload()
    if not res.ok:
        path.unlink(missing_ok=True)
        await _reload()
        raise OrchestratorError(
            code="container_failure",
            message=f"nginx rejected site for {host}: {res.stderr[-300:]}",
            status_code=500,
        )
    log.info("nginx.published_http", host=host, port=port)


async def ensure_tls(host: str, port: int) -> bool:
    """Issue/refresh a cert and swap the site to HTTPS. Returns True iff live.

    Slow (cert issuance is ~30-60s). Fail-soft: any failure leaves the HTTP
    block in place. Safe to call repeatedly (cert reuse via certbot).
    """
    _validate_host(host)
    if not get_settings().enable_tls:
        return False
    if not await _issue_cert(host):
        return False
    path = _site_path(host)
    path.write_text(_https_block(host, port), encoding="utf-8")
    res = await _reload()
    if res.ok:
        log.info("nginx.published_https", host=host, port=port)
        return True
    # Cert exists but the HTTPS block won't load — revert to HTTP.
    log.warning("nginx.https_reload_failed", host=host, stderr=res.stderr[-300:])
    path.write_text(_http_block(host, port), encoding="utf-8")
    await _reload()
    return False


async def publish(host: str, port: int) -> str:
    """Full publish: HTTP block, then HTTPS upgrade. Returns the actual URL.

    Blocking on cert issuance — call from a background task (deploy), not from
    a request that must answer within the api timeout. For the fast path use
    `publish_http` + `publish_tls_in_background`.
    """
    await publish_http(host, port)
    if await ensure_tls(host, port):
        return f"https://{host}"
    return f"http://{host}"


# Keep references so background TLS tasks aren't garbage-collected mid-flight.
_bg_tasks: set[object] = set()


def publish_tls_in_background(host: str, port: int) -> None:
    """Fire-and-forget the (slow) TLS upgrade after a fast `publish_http`.

    Used by provision so the api call returns in ~2s while the cert is issued
    out of band; the optimistic `https://` URL the caller returns starts
    working as soon as the cert lands (overlaps Next.js cold start anyway).
    """
    import asyncio

    async def _go() -> None:
        try:
            await ensure_tls(host, port)
        except Exception as exc:  # never let a bg task crash silently-loud
            log.warning("nginx.bg_tls_failed", host=host, err=str(exc))

    task = asyncio.create_task(_go())
    _bg_tasks.add(task)
    task.add_done_callback(_bg_tasks.discard)


async def unpublish(host: str) -> None:
    """Remove a site and reload nginx. Missing site is a no-op."""
    path = _site_path(host)
    if path.exists():
        path.unlink(missing_ok=True)
        await _reload()
        log.info("nginx.unpublished", host=host)

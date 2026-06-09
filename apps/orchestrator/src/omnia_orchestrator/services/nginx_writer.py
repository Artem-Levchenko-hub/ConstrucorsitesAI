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

import asyncio
import os
import re
from contextlib import suppress
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
    """The pre-issued WILDCARD cert dir covering `host`, or None.

    Dev/prod hosts are `<single-label>.<runtime_host_suffix>` (e.g.
    `myslug-dev.preview.lead-generator.ru`), all covered by one
    `*.<suffix>` cert. Pointing the HTTPS block straight at that cert and
    SKIPPING per-host acme removes the flaky bit: a half-failed acme issuance
    leaves an EMPTY fullchain, so `ensure_tls` never writes the :443 block and
    the preview falls through to the catch-all `*.preview` vhost → 502 "no live
    project". The wildcard is instant and can't rate-limit or half-fail.

    OPT-IN via env `OMNIA_WILDCARD_CERT_ROOT` (the letsencrypt *live* dir, e.g.
    `/etc/letsencrypt/live`); unset → keep per-host acme (back-compat, and the
    only option for hosts without a covering wildcard, e.g. sslip.io). We do NOT
    stat the cert: the orchestrator user typically can't read /etc/letsencrypt
    (root:ssl-cert 0750), but nginx can — so a wrong/missing path is caught by
    `nginx -t` on reload and `ensure_tls` fails soft back to the HTTP block.
    The cert dir is `<root>/<suffix>` (the standard certbot/acme layout).
    """
    root = os.getenv("OMNIA_WILDCARD_CERT_ROOT", "").rstrip("/")
    if not root:
        return None
    suffix = get_settings().runtime_host_suffix
    if not host.endswith("." + suffix):
        return None
    label = host[: -(len(suffix) + 1)]
    if not label or "." in label:  # a *.<suffix> wildcard covers exactly ONE label
        return None
    return f"{root}/{suffix}"


def _proxy_location(port: int) -> str:
    # `$omnia_connection_upgrade` is defined once in conf.d/omnia-runtime.conf.
    # X-Frame-Options is hidden so the workspace can embed the preview iframe.
    #
    # Wake-on-request: a hibernated container leaves nothing on 127.0.0.1:<port>,
    # so the proxy_pass connection is refused → 502. `proxy_intercept_errors`
    # routes that to @omnia_waking, which boots the container and returns a
    # self-refreshing "waking up" page instead of a raw Bad Gateway. Once the
    # app is up the proxy_pass succeeds and @omnia_waking is never reached.
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
        proxy_intercept_errors on;
        error_page 502 503 504 = @omnia_waking;
    }}

{_wake_location()}"""


def _wake_location() -> str:
    """Internal fallback that boots a hibernated upstream. Reached only when
    `location /` 502s. Forwards the original Host so the orchestrator can map
    the hostname back to its dev/prod container (see routers/ingress.py)."""
    target = get_settings().orchestrator_wake_target
    return f"""\
    location @omnia_waking {{
        proxy_pass http://{target}/_omnia/wake;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Omnia-Forwarded-Uri $request_uri;
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

    async def _go() -> None:
        try:
            await ensure_tls(host, port)
        except Exception as exc:  # never let a bg task crash silently-loud
            log.warning("nginx.bg_tls_failed", host=host, err=str(exc))

    task = asyncio.create_task(_go())
    _bg_tasks.add(task)
    task.add_done_callback(_bg_tasks.discard)


_SERVER_NAME_RE = re.compile(r"server_name\s+([^;\s]+)\s*;")
_PROXY_PORT_RE = re.compile(r"proxy_pass\s+http://127\.0\.0\.1:(\d+)\s*;")


def _rebuild_conf(text: str) -> str | None:
    """Rebuild a vhost's text with the current template, preserving its host,
    upstream port and TLS mode. Returns None when the file isn't a recognizable
    omnia proxy vhost (no host / no upstream port) — caller skips it untouched.
    """
    host_m = _SERVER_NAME_RE.search(text)
    port_m = _PROXY_PORT_RE.search(text)
    if not host_m or not port_m:
        return None
    host = host_m.group(1)
    port = int(port_m.group(1))
    is_tls = "listen 443" in text
    return _https_block(host, port) if is_tls else _http_block(host, port)


def _rewrite_legacy_confs(sites_dir: Path) -> dict[Path, str]:
    """Rewrite every legacy conf in `sites_dir` in place; return {path: old}
    backups for the ones changed. Sync (blocking FS) — call via a thread.

    Idempotent: a conf already carrying `@omnia_waking`, or one we can't parse,
    is left untouched and not backed up.
    """
    backups: dict[Path, str] = {}
    if not sites_dir.is_dir():
        return backups
    for path in sorted(sites_dir.glob("*.conf")):
        try:
            old = path.read_text(encoding="utf-8")
        except OSError:
            continue
        if "@omnia_waking" in old:
            continue  # already on the new template
        new = _rebuild_conf(old)
        if new is None or new == old:
            continue
        try:
            path.write_text(new, encoding="utf-8")
            backups[path] = old
        except OSError as exc:
            log.warning("nginx.refresh_write_failed", path=str(path), err=str(exc))
    return backups


def _restore_confs(backups: dict[Path, str]) -> None:
    """Revert each conf to its backed-up bytes. Sync — call via a thread."""
    for path, old in backups.items():
        with suppress(OSError):
            path.write_text(old, encoding="utf-8")


async def refresh_vhosts() -> int:
    """Re-render every existing omnia vhost with the current template, so a
    template change (e.g. wake-on-request) reaches previews provisioned before
    the upgrade — without re-running provision or re-issuing certs.

    Idempotent: a conf already carrying `@omnia_waking` is skipped, so repeat
    startups are no-ops. Fail-soft (R-10): files are rewritten first, then a
    single `nginx -t` gates the reload; if the config is invalid EVERY file is
    restored to its prior bytes and nginx is reloaded back to the known-good
    state. A broken template therefore can never take the shared box down —
    worst case is "no upgrade this round".

    Returns the number of vhosts upgraded.
    """
    sites_dir = Path(get_settings().nginx_sites_dir)
    backups = await asyncio.to_thread(_rewrite_legacy_confs, sites_dir)
    if not backups:
        return 0

    res = await _reload()
    if res.ok:
        log.info("nginx.refresh_vhosts", upgraded=len(backups))
        return len(backups)

    # Invalid config — roll every file back and reload to the prior good state.
    await asyncio.to_thread(_restore_confs, backups)
    await _reload()
    log.warning("nginx.refresh_vhosts_rolled_back", stderr=res.stderr[-300:])
    return 0


async def unpublish(host: str) -> None:
    """Remove a site and reload nginx. Missing site is a no-op."""
    path = _site_path(host)
    if path.exists():
        path.unlink(missing_ok=True)
        await _reload()
        log.info("nginx.unpublished", host=host)

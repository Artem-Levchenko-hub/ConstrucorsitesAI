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
import ipaddress
import os
import re
from contextlib import suppress
from pathlib import Path
from urllib.parse import urlsplit

import structlog

from omnia_orchestrator.core.config import get_settings
from omnia_orchestrator.core.errors import OrchestratorError
from omnia_orchestrator.core.shell import CmdResult, run

log = structlog.get_logger("omnia_orchestrator.nginx")

_HOST_RE = re.compile(r"^[a-z0-9]([a-z0-9.-]{0,253}[a-z0-9])?$")
_ASSET_TARGET_RE = re.compile(r"^127\.0\.0\.1:\d{1,5}/[A-Za-z0-9_./-]+$")
_VHOST_TEMPLATE_MARKER = "# omnia vhost template: html-no-store-v3"
_RFC1918_V4_NETWORKS = (
    ipaddress.IPv4Network("10.0.0.0/8"),
    ipaddress.IPv4Network("172.16.0.0/12"),
    ipaddress.IPv4Network("192.168.0.0/16"),
)


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


def _is_dev_host(host: str) -> bool:
    """True only for the private/live editor hostname, never deployed prod."""
    suffix = "." + get_settings().runtime_host_suffix
    label = host[: -len(suffix)] if host.endswith(suffix) else host
    return label.endswith("-dev")


def _workspace_origin() -> str:
    """Return a safe HTTP(S) origin for the injected postMessage allowlist."""
    raw = get_settings().workspace_origin.rstrip("/")
    parsed = urlsplit(raw)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.netloc
        or parsed.path
        or parsed.query
        or parsed.fragment
        or "'" in raw
        or '"' in raw
    ):
        raise OrchestratorError(
            code="validation_failed",
            message="WORKSPACE_ORIGIN must be a plain http(s) origin",
            status_code=500,
        )
    return raw


def _inspector_asset_target() -> str:
    """Validate the loopback-only canonical inspector upstream."""
    target = get_settings().inspector_asset_target
    if not _ASSET_TARGET_RE.fullmatch(target):
        raise OrchestratorError(
            code="validation_failed",
            message="INSPECTOR_ASSET_TARGET must be a 127.0.0.1 host:port/path",
            status_code=500,
        )
    port = int(target.split(":", 1)[1].split("/", 1)[0])
    if not 1 <= port <= 65535:
        raise OrchestratorError(
            code="validation_failed",
            message="INSPECTOR_ASSET_TARGET port is out of range",
            status_code=500,
        )
    return target


def _inspector_location() -> str:
    """Same-origin route for the canonical, platform-controlled inspector."""
    target = _inspector_asset_target()
    return f"""\
    location = /_omnia/inspector.js {{
        proxy_pass http://{target};
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_hide_header Cache-Control;
        add_header Cache-Control "no-store" always;
        add_header X-Content-Type-Options "nosniff" always;
    }}"""


def _validate_upstream_host(upstream_host: str) -> str:
    """Allow only canonical IPv4 loopback or RFC1918 upstreams."""
    try:
        addr = ipaddress.IPv4Address(upstream_host)
    except ipaddress.AddressValueError as exc:
        raise OrchestratorError(
            code="validation_failed",
            message=f"refusing to write nginx site for unsafe upstream host: {upstream_host!r}",
            status_code=400,
        ) from exc
    if str(addr) != upstream_host:
        raise OrchestratorError(
            code="validation_failed",
            message=(
                "refusing to write nginx site for non-canonical upstream host: "
                f"{upstream_host!r}"
            ),
            status_code=400,
        )
    if upstream_host == "127.0.0.1":
        return upstream_host
    if any(addr in network for network in _RFC1918_V4_NETWORKS):
        return upstream_host
    raise OrchestratorError(
        code="validation_failed",
        message=f"refusing to write nginx site for non-private upstream host: {upstream_host!r}",
        status_code=400,
    )


def _proxy_location(
    port: int,
    *,
    inject_inspector: bool = False,
    upstream_host: str = "127.0.0.1",
) -> str:
    # `$omnia_connection_upgrade` is defined once in conf.d/omnia-runtime.conf.
    # X-Frame-Options is hidden so the workspace can embed the preview iframe.
    #
    # Wake-on-request: a hibernated container leaves nothing on 127.0.0.1:<port>,
    # so the proxy_pass connection is refused → 502. `proxy_intercept_errors`
    # routes that to @omnia_waking, which boots the container and returns a
    # self-refreshing "waking up" page instead of a raw Bad Gateway. Once the
    # app is up the proxy_pass succeeds and @omnia_waking is never reached.
    upstream_host = _validate_upstream_host(upstream_host)
    inspector_filter = ""
    if inject_inspector:
        # This is deliberately platform-owned and injected into EVERY HTML
        # document on the *dev* hostname. It remains dormant until a trusted
        # workspace command arrives. Unlike `?inspect=1`-only injection, it
        # survives client-side route changes and full document navigations.
        #
        # Clear upstream compression on loopback so nginx's sub_filter always
        # sees HTML bytes. Downstream gzip/brotli may still compress the response.
        origin = _workspace_origin()
        tag = (
            '<script src="/_omnia/inspector.js" data-omnia-platform-inspector="1" '
            f'data-omnia-parent-origin="{origin}"></script>'
        )
        inspector_filter = f"""\
        proxy_set_header Accept-Encoding "";
        sub_filter_once on;
        sub_filter '</body>' '{tag}</body>';
"""
    return f"""\
    # Next.js content-hashes these assets, so their upstream immutable cache
    # policy is safe across deployments and should remain untouched.
    location ^~ /_next/static/ {{
        proxy_pass http://{upstream_host}:{port};
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

    location / {{
        proxy_pass http://{upstream_host}:{port};
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection $omnia_connection_upgrade;
        proxy_read_timeout 86400;
        proxy_hide_header X-Frame-Options;
        # Next.js may mark a prerendered app shell cacheable for a year. This
        # default route covers HTML, APIs and public shell assets; only the
        # content-hashed /_next/static location above keeps immutable caching.
        proxy_hide_header Cache-Control;
        add_header Cache-Control "no-store" always;
{inspector_filter.rstrip()}
        proxy_intercept_errors on;
        error_page 502 503 504 = @omnia_waking;
    }}

{_wake_location()}"""


def _wake_location() -> str:
    """Internal fallback that boots a hibernated upstream. Reached only when
    `location /` 502s. Forwards the original Host so the orchestrator can map
    the hostname back to its dev/prod container (see routers/ingress.py).

    nginx forbids a literal URI part in `proxy_pass` inside a NAMED location,
    so the target is built into a variable — the variable form is allowed and
    proxies to exactly that address (no $uri appended, which is what we want:
    the orchestrator keys on Host, not path). 127.0.0.1 needs no resolver."""
    target = get_settings().orchestrator_wake_target
    return f"""\
    location @omnia_waking {{
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Omnia-Forwarded-Uri $request_uri;
        set $omnia_wake_target http://{target}/_omnia/wake;
        proxy_pass $omnia_wake_target;
    }}"""


def _acme_location() -> str:
    return f"""\
    location /.well-known/acme-challenge/ {{
        root {get_settings().acme_webroot};
    }}"""


def _private_cell_assets() -> str:
    """Private cells cannot remix; suppress its floating CTA and watermark.

    Serve a platform-owned no-op at ingress so existing workspaces are covered
    without changing accepted source files or disabling the editor inspector.
    """
    return """\
    location = /omnia-remix-cta.js {
        types { }
        default_type application/javascript;
        add_header Cache-Control "no-store" always;
        add_header X-Content-Type-Options "nosniff" always;
        return 200 "/* Public remix is unavailable for private cells. */";
    }"""


def _http_block(
    host: str, port: int, *, upstream_host: str = "127.0.0.1", private_cell: bool = False,
) -> str:
    dev = _is_dev_host(host)
    inspector = f"\n{_inspector_location()}\n" if dev else ""
    return f"""\
{_VHOST_TEMPLATE_MARKER}
# omnia auto-generated — {host} (HTTP)
server {{
    listen 80;
    listen [::]:80;
    server_name {host};

{_acme_location()}
{inspector}
{_private_cell_assets() if private_cell else ""}

{_proxy_location(port, inject_inspector=dev, upstream_host=upstream_host)}
}}
"""


def _https_block(
    host: str, port: int, *, upstream_host: str = "127.0.0.1", private_cell: bool = False,
) -> str:
    # Prefer a pre-issued wildcard cert (instant, reliable); else the per-host
    # acme cert dir.
    cert_dir = _wildcard_cert_dir(host) or f"{get_settings().acme_certs_dir}/{host}"
    dev = _is_dev_host(host)
    inspector = f"\n{_inspector_location()}\n" if dev else ""
    return f"""\
{_VHOST_TEMPLATE_MARKER}
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
{inspector}
{_private_cell_assets() if private_cell else ""}

{_proxy_location(port, inject_inspector=dev, upstream_host=upstream_host)}
}}
"""


async def _reload() -> CmdResult:
    """`nginx -t` then reload. Returns the first failing step (or the reload)."""
    test = await run(["sudo", "-n", "nginx", "-t"], timeout_seconds=20)
    if not test.ok:
        return test
    return await run(
        ["sudo", "-n", "systemctl", "reload", "nginx"],
        timeout_seconds=25,
    )


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
    acme = os.path.expanduser("~/.acme.sh/acme.sh")  # noqa: ASYNC240
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
        if fullchain.exists() and privkey.exists():
            has_cert = "BEGIN CERTIFICATE" in fullchain.read_text(errors="ignore")
            if has_cert:
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
        timeout_seconds=180,
        env=acme_env,
    )
    install = await run(
        [
            acme, "--home", acme_home, "--install-cert", "-d", host, "--ecc",
            "--key-file", str(privkey),
            "--fullchain-file", str(fullchain),
            "--reloadcmd", "sudo -n systemctl reload nginx",
        ],
        timeout_seconds=60,
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


async def publish_http(
    host: str, port: int, *, upstream_host: str = "127.0.0.1", private_cell: bool = False,
) -> None:
    """Write the HTTP(:80) block for `host` and reload nginx (fast, ~1-2s).

    Preserves HTTPS for an already upgraded site, including upstream changes.
    Makes a new site reachable over HTTP immediately and able to answer the
    ACME http-01 challenge. Raises (after rolling back) only if our own block
    breaks the shared nginx config — we never leave the box in a failing state.
    """
    _validate_host(host)
    upstream_host = _validate_upstream_host(upstream_host)
    path = _site_path(host)
    path.parent.mkdir(parents=True, exist_ok=True)
    previous = path.read_text(encoding="utf-8") if path.exists() else None
    render = _https_block if previous and "listen 443" in previous else _http_block
    desired = render(host, port, upstream_host=upstream_host, private_cell=private_cell)
    if desired == previous:
        return
    path.write_text(desired, encoding="utf-8")
    res = await _reload()
    if not res.ok:
        if previous is None:
            path.unlink(missing_ok=True)
        else:
            path.write_text(previous, encoding="utf-8")
        await _reload()
        raise OrchestratorError(
            code="container_failure",
            message=f"nginx rejected site for {host}: {res.stderr[-300:]}",
            status_code=500,
        )
    log.info("nginx.published_http", host=host, port=port)


async def ensure_tls(
    host: str, port: int, *, upstream_host: str = "127.0.0.1", private_cell: bool = False,
) -> bool:
    """Issue/refresh a cert and swap the site to HTTPS. Returns True iff live.

    Slow (cert issuance is ~30-60s). Fail-soft: a reload failure restores the
    previous block, preserving any existing HTTPS site. Safe to call repeatedly.
    """
    _validate_host(host)
    upstream_host = _validate_upstream_host(upstream_host)
    if not get_settings().enable_tls:
        return False
    if not await _issue_cert(host):
        return False
    path = _site_path(host)
    path.parent.mkdir(parents=True, exist_ok=True)
    previous = path.read_text(encoding="utf-8") if path.exists() else None
    path.write_text(
        _https_block(host, port, upstream_host=upstream_host, private_cell=private_cell),
        encoding="utf-8",
    )
    res = await _reload()
    if res.ok:
        log.info("nginx.published_https", host=host, port=port)
        return True
    # Never downgrade a previously working HTTPS site on a failed refresh.
    log.warning("nginx.https_reload_failed", host=host, stderr=res.stderr[-300:])
    path.write_text(
        previous if previous is not None else _http_block(
            host, port, upstream_host=upstream_host, private_cell=private_cell,
        ),
        encoding="utf-8",
    )
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

    Idempotent: a conf carrying the current template marker, or one we can't
    parse, is left untouched and not backed up. A versioned marker is important:
    older vhosts may already have wake-on-request but still lack the universal
    inspector, and must therefore be re-rendered on orchestrator startup.
    """
    backups: dict[Path, str] = {}
    if not sites_dir.is_dir():
        return backups
    for path in sorted(sites_dir.glob("*.conf")):
        try:
            old = path.read_text(encoding="utf-8")
        except OSError:
            continue
        if _VHOST_TEMPLATE_MARKER in old:
            continue  # already on the current template
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


async def unpublish(host: str, *, http_only: bool = False) -> None:
    """Remove a site; HTTP-only cleanup preserves previously working TLS."""
    path = _site_path(host)
    if path.exists():
        if http_only and "listen 443" in path.read_text(encoding="utf-8"):
            return
        path.unlink(missing_ok=True)
        await _reload()
        log.info("nginx.unpublished", host=host)

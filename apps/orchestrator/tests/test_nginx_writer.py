"""Unit tests for `services.nginx_writer` host validation + content shape.

The slow parts (cert issuance, nginx reload subprocess) are integration —
we cover the deterministic parts that protect us from path traversal,
config drift, and the most common nginx-rejected misconfig.
"""

from __future__ import annotations

import pytest

from omnia_orchestrator.core.errors import OrchestratorError
from omnia_orchestrator.services import nginx_writer


@pytest.fixture(autouse=True)
def _env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(
        "DATABASE_URL",
        "postgresql://omnia_root:rootpw@localhost:5433/omnia_users",
    )
    monkeypatch.setenv("INTERNAL_TOKEN", "test-token-test-token-test-token")
    monkeypatch.setenv("RUNTIME_HOST_SUFFIX", "preview.omniadevelop.ru")
    monkeypatch.setenv("ENABLE_TLS", "true")
    from omnia_orchestrator.core.config import get_settings

    get_settings.cache_clear()  # type: ignore[attr-defined]


# ---------- host derivation ----------


def test_dev_host_appends_suffix() -> None:
    assert nginx_writer.dev_host("my-app") == "my-app-dev.preview.omniadevelop.ru"


def test_prod_host_no_dev_segment() -> None:
    assert nginx_writer.prod_host("my-app") == "my-app.preview.omniadevelop.ru"


def test_dev_url_uses_https_when_tls_enabled() -> None:
    assert nginx_writer.dev_url("x").startswith("https://")


def test_dev_url_uses_http_when_tls_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ENABLE_TLS", "false")
    from omnia_orchestrator.core.config import get_settings

    get_settings.cache_clear()  # type: ignore[attr-defined]
    assert nginx_writer.dev_url("x").startswith("http://")


# ---------- host validation (path traversal / injection) ----------


@pytest.mark.parametrize(
    "bad",
    [
        "../etc/passwd",  # path traversal
        "../../root",
        "host with space",  # shell metas
        "host;injection",  # command-substitution-ish
        "host\nnewline",
        "",  # empty
        ".leading-dot",  # invalid TLD
        "host_with_underscore",  # underscores forbidden by RFC + our regex
        "UPPER.case",  # we only accept lowercase
    ],
)
def test_validate_host_rejects_unsafe(bad: str) -> None:
    with pytest.raises(OrchestratorError) as excinfo:
        nginx_writer._validate_host(bad)
    assert excinfo.value.code == "validation_failed"


@pytest.mark.parametrize(
    "good",
    [
        "site.example.com",
        "x-y-z.preview.omniadevelop.ru",
        "abc123.app.omniadevelop.ru",
        "a.b",
        "single",
    ],
)
def test_validate_host_accepts_safe(good: str) -> None:
    # No exception = pass.
    nginx_writer._validate_host(good)


# ---------- block content shape ----------


def test_http_block_contains_proxy_pass_and_acme_path() -> None:
    """HTTP-only block must (1) reverse-proxy to the loopback port, (2) leave
    the .well-known path uncovered for ACME http-01. Both are tested by
    grepping the rendered text — any refactor that drops them will fail
    here loudly."""
    block = nginx_writer._http_block("test.example.com", 3200)
    assert "server_name test.example.com" in block
    assert "proxy_pass http://127.0.0.1:3200" in block
    assert "/.well-known/acme-challenge/" in block
    assert "listen 80" in block
    assert "listen 443" not in block  # plain HTTP first; HTTPS is the upgrade


def test_https_block_redirects_80_to_443() -> None:
    """After cert issuance the HTTPS block must redirect bare HTTP. Without
    this every public link would silently degrade to the plaintext nginx."""
    block = nginx_writer._https_block("test.example.com", 3200)
    assert "listen 443 ssl" in block
    assert "return 301 https://$host$request_uri" in block
    # HSTS preserves the redirect across visits.
    assert "Strict-Transport-Security" in block


def test_proxy_location_carries_websocket_upgrade() -> None:
    """HMR breaks without Connection: $omnia_connection_upgrade — the live
    Next.js dev container can't push file-change events back through the
    proxy."""
    block = nginx_writer._proxy_location(3200)
    assert "Upgrade $http_upgrade" in block
    assert "Connection $omnia_connection_upgrade" in block
    assert "X-Forwarded-Proto $scheme" in block
    # X-Frame-Options hidden so the workspace can iframe the preview.
    assert "proxy_hide_header X-Frame-Options" in block


def test_proxy_location_disables_html_cache_only() -> None:
    """A deployed app shell must refresh while hashed assets keep their policy."""
    block = nginx_writer._proxy_location(3200)
    assert "location ^~ /_next/static/" in block
    assert "proxy_hide_header Cache-Control" in block
    assert 'add_header Cache-Control "no-store" always' in block
    static_location, default_location = block.split("    location / {", maxsplit=1)
    assert "proxy_hide_header Cache-Control" not in static_location
    assert "proxy_hide_header Cache-Control" in default_location


def test_blocks_accept_private_ipv4_upstream() -> None:
    """Selected-cell previews may proxy to a private bridge IP, not loopback."""
    for block in (
        nginx_writer._http_block(
            "test.example.com", 3200, upstream_host="10.23.45.67"
        ),
        nginx_writer._https_block(
            "test.example.com", 3200, upstream_host="192.168.44.12"
        ),
    ):
        assert "proxy_pass http://127.0.0.1:3200" not in block
    assert "proxy_pass http://10.23.45.67:3200" in nginx_writer._http_block(
        "test.example.com", 3200, upstream_host="10.23.45.67"
    )
    assert "proxy_pass http://192.168.44.12:3200" in nginx_writer._https_block(
        "test.example.com", 3200, upstream_host="192.168.44.12"
    )


@pytest.mark.parametrize(
    "bad",
    [
        "",
        "localhost",
        "0.0.0.0",
        "8.8.8.8",
        "127.0.0.2",
        "169.254.10.20",
        "10.0.0.1:3000",
        "10.0.0.1/path",
        "http://10.0.0.1",
        "010.0.0.1",
        "::1",
    ],
)
def test_proxy_location_rejects_unsafe_upstream_host(bad: str) -> None:
    with pytest.raises(OrchestratorError) as excinfo:
        nginx_writer._proxy_location(3200, upstream_host=bad)
    assert excinfo.value.code == "validation_failed"


def test_dev_vhost_injects_platform_inspector_for_old_projects() -> None:
    """The proxy—not project source—must deliver the picker to every dev HTML."""
    block = nginx_writer._https_block(
        "old-project-dev.preview.omniadevelop.ru", 3200
    )
    assert "location = /_omnia/inspector.js" in block
    assert "127.0.0.1:8200/api/kit/omnia-inspector.js" in block
    assert 'data-omnia-platform-inspector="1"' in block
    assert (
        'data-omnia-parent-origin="https://constructor.lead-generator.ru"' in block
    )
    assert 'proxy_set_header Accept-Encoding ""' in block
    assert "sub_filter '</body>'" in block


def test_prod_vhost_does_not_inject_editor_runtime() -> None:
    """Published customer sites stay byte-for-byte free of editor machinery."""
    block = nginx_writer._https_block("project.preview.omniadevelop.ru", 4200)
    assert "/_omnia/inspector.js" not in block
    assert "sub_filter" not in block
    assert "data-omnia-platform-inspector" not in block


# ---------- wake-on-request (scale-from-zero) ----------


def test_proxy_location_intercepts_502_to_wake() -> None:
    """A hibernated upstream 502s; without intercept the user sees Bad Gateway
    instead of the waking page."""
    block = nginx_writer._proxy_location(3200)
    assert "proxy_intercept_errors on" in block
    assert "error_page 502 503 504 = @omnia_waking" in block
    assert "location @omnia_waking" in block
    assert "/_omnia/wake" in block


def test_blocks_embed_wake_fallback() -> None:
    """Both HTTP-only and HTTPS blocks must carry the @omnia_waking location so
    wake-on-request works before AND after the TLS upgrade."""
    for block in (
        nginx_writer._http_block("t.example.com", 3200),
        nginx_writer._https_block("t.example.com", 3200),
    ):
        assert "location @omnia_waking" in block
        assert "/_omnia/wake" in block


def test_rebuild_conf_upgrades_legacy_vhost() -> None:
    """A pre-wake conf is re-rendered with the wake fallback, preserving host,
    port and TLS mode."""
    legacy = (
        "server {\n"
        "    listen 443 ssl;\n"
        "    server_name old-app-dev.preview.omniadevelop.ru;\n"
        "    location / { proxy_pass http://127.0.0.1:3271; }\n"
        "}\n"
    )
    out = nginx_writer._rebuild_conf(legacy)
    assert out is not None
    assert "server_name old-app-dev.preview.omniadevelop.ru" in out
    assert "proxy_pass http://127.0.0.1:3271" in out
    assert "listen 443 ssl" in out  # TLS mode preserved
    assert "@omnia_waking" in out
    assert "data-omnia-platform-inspector" in out


def test_rebuild_conf_skips_unrecognized() -> None:
    """A conf with no upstream port isn't ours — leave it untouched (None)."""
    assert nginx_writer._rebuild_conf("server { listen 80; }\n") is None


async def test_refresh_vhosts_idempotent_and_upgrades(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """refresh_vhosts upgrades legacy confs once and skips already-upgraded
    ones; the reload is gated, so we stub it green."""
    import omnia_orchestrator.services.nginx_writer as nw
    from omnia_orchestrator.core.shell import CmdResult

    monkeypatch.setenv("NGINX_SITES_DIR", str(tmp_path))
    from omnia_orchestrator.core.config import get_settings

    get_settings.cache_clear()  # type: ignore[attr-defined]

    async def _ok() -> CmdResult:
        return CmdResult(rc=0, stdout="", stderr="")

    monkeypatch.setattr(nw, "_reload", _ok)

    legacy = tmp_path / "a-dev.preview.omniadevelop.ru.conf"
    legacy.write_text(
        "server {\n  listen 80;\n  server_name a-dev.preview.omniadevelop.ru;\n"
        "  location / { proxy_pass http://127.0.0.1:3300; }\n}\n",
        encoding="utf-8",
    )

    first = await nw.refresh_vhosts()
    assert first == 1
    assert "@omnia_waking" in legacy.read_text(encoding="utf-8")

    # Second pass is a no-op (already carries the fallback).
    second = await nw.refresh_vhosts()
    assert second == 0


async def test_refresh_vhosts_upgrades_wake_enabled_but_inspectorless_conf(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """c389a2e-era vhosts already had wake logic; they still need this migration."""
    import omnia_orchestrator.services.nginx_writer as nw
    from omnia_orchestrator.core.shell import CmdResult

    monkeypatch.setenv("NGINX_SITES_DIR", str(tmp_path))
    from omnia_orchestrator.core.config import get_settings

    get_settings.cache_clear()  # type: ignore[attr-defined]

    async def _ok() -> CmdResult:
        return CmdResult(rc=0, stdout="", stderr="")

    monkeypatch.setattr(nw, "_reload", _ok)
    conf = tmp_path / "old-dev.preview.omniadevelop.ru.conf"
    conf.write_text(
        "server {\n  listen 80;\n"
        "  server_name old-dev.preview.omniadevelop.ru;\n"
        "  location / { proxy_pass http://127.0.0.1:3302; }\n"
        "  location @omnia_waking { return 200; }\n}\n",
        encoding="utf-8",
    )

    assert await nw.refresh_vhosts() == 1
    upgraded = conf.read_text(encoding="utf-8")
    assert "data-omnia-platform-inspector" in upgraded
    assert nw._VHOST_TEMPLATE_MARKER in upgraded


async def test_refresh_vhosts_rolls_back_on_bad_config(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """If nginx -t rejects the new render, every file reverts to its prior
    bytes — a broken template can't take the shared box down."""
    import omnia_orchestrator.services.nginx_writer as nw
    from omnia_orchestrator.core.shell import CmdResult

    monkeypatch.setenv("NGINX_SITES_DIR", str(tmp_path))
    from omnia_orchestrator.core.config import get_settings

    get_settings.cache_clear()  # type: ignore[attr-defined]

    calls = {"n": 0}

    async def _fail_then_ok() -> CmdResult:
        # First call (the gate) fails; the rollback reload succeeds.
        calls["n"] += 1
        if calls["n"] == 1:
            return CmdResult(rc=1, stdout="", stderr="nginx: bad config")
        return CmdResult(rc=0, stdout="", stderr="")

    monkeypatch.setattr(nw, "_reload", _fail_then_ok)

    conf = tmp_path / "b-dev.preview.omniadevelop.ru.conf"
    original = (
        "server {\n  listen 80;\n  server_name b-dev.preview.omniadevelop.ru;\n"
        "  location / { proxy_pass http://127.0.0.1:3301; }\n}\n"
    )
    conf.write_text(original, encoding="utf-8")

    upgraded = await nw.refresh_vhosts()
    assert upgraded == 0
    # Reverted byte-for-byte.
    assert conf.read_text(encoding="utf-8") == original


async def test_publish_http_keeps_legacy_loopback_default(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Legacy callers still render loopback upstreams without passing the kwarg."""
    import omnia_orchestrator.services.nginx_writer as nw
    from omnia_orchestrator.core.shell import CmdResult

    monkeypatch.setenv("NGINX_SITES_DIR", str(tmp_path))
    from omnia_orchestrator.core.config import get_settings

    get_settings.cache_clear()  # type: ignore[attr-defined]

    async def _ok() -> CmdResult:
        return CmdResult(rc=0, stdout="", stderr="")

    monkeypatch.setattr(nw, "_reload", _ok)

    await nw.publish_http("legacy.example.com", 3305)
    conf = (tmp_path / "legacy.example.com.conf").read_text(encoding="utf-8")
    assert "proxy_pass http://127.0.0.1:3305" in conf


async def test_publish_http_rejects_invalid_upstream_before_write(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Unsafe upstreams must fail before we touch the vhost file."""
    import omnia_orchestrator.services.nginx_writer as nw

    monkeypatch.setenv("NGINX_SITES_DIR", str(tmp_path))
    from omnia_orchestrator.core.config import get_settings

    get_settings.cache_clear()  # type: ignore[attr-defined]

    with pytest.raises(OrchestratorError) as excinfo:
        await nw.publish_http("unsafe.example.com", 3306, upstream_host="8.8.8.8")
    assert excinfo.value.code == "validation_failed"
    assert not (tmp_path / "unsafe.example.com.conf").exists()


async def test_ensure_tls_writes_private_upstream(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """TLS publish preserves a validated private bridge IP upstream."""
    import omnia_orchestrator.services.nginx_writer as nw
    from omnia_orchestrator.core.shell import CmdResult

    monkeypatch.setenv("NGINX_SITES_DIR", str(tmp_path))
    from omnia_orchestrator.core.config import get_settings

    get_settings.cache_clear()  # type: ignore[attr-defined]

    async def _ok() -> CmdResult:
        return CmdResult(rc=0, stdout="", stderr="")

    async def _cert_ok(_host: str) -> bool:
        return True

    monkeypatch.setattr(nw, "_reload", _ok)
    monkeypatch.setattr(nw, "_issue_cert", _cert_ok)

    assert await nw.ensure_tls(
        "private.example.com", 3310, upstream_host="172.16.5.9"
    )
    conf = (tmp_path / "private.example.com.conf").read_text(encoding="utf-8")
    assert "listen 443 ssl" in conf
    assert "proxy_pass http://172.16.5.9:3310" in conf


async def test_ensure_tls_rejects_invalid_upstream_before_cert_issue(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Unsafe upstreams must fail before any cert issuance side effect."""
    import omnia_orchestrator.services.nginx_writer as nw

    monkeypatch.setenv("NGINX_SITES_DIR", str(tmp_path))
    from omnia_orchestrator.core.config import get_settings

    get_settings.cache_clear()  # type: ignore[attr-defined]

    called = False

    async def _boom(_host: str) -> bool:
        nonlocal called
        called = True
        raise AssertionError("_issue_cert should not run for invalid upstreams")

    monkeypatch.setattr(nw, "_issue_cert", _boom)

    with pytest.raises(OrchestratorError) as excinfo:
        await nw.ensure_tls("unsafe.example.com", 3307, upstream_host="8.8.8.8")
    assert excinfo.value.code == "validation_failed"
    assert called is False

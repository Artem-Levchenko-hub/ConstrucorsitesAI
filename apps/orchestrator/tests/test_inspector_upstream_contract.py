"""Exact public-asset relay; these checks do not certify a live Commerce host."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest

from yleum_orchestrator.core.errors import OrchestratorError
from yleum_orchestrator.services import nginx_writer as writer

ROOT = Path(__file__).resolve().parents[3]


def verifier():
    spec = importlib.util.spec_from_file_location(
        "inspector_verifier", ROOT / "infra/release/verify-inspector-relay.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("failure", [
    None, "foreign_host", "wrong_revision", "writable", "foreign_uid", "symlink",
])
def test_actual_cli_resolves_trusted_controller_configuration(tmp_path, failure):
    """Fresh interpreter + real Settings, no service env in CLI; no network/daemon."""
    import json
    import os
    import subprocess
    import sys

    if os.name != "posix" or os.geteuid() != 0:
        pytest.skip("requires isolated Linux root file-ownership fixture")
    release = "a" * 40
    state = tmp_path / "state"
    state.mkdir(mode=0o755)
    config = state / "config.json"
    config.write_text(json.dumps({"version": 1, "host": "commerce", "release_sha": release,
                                  "api_image": None, "ssh_user": "root"}))
    proc = tmp_path / "proc" / "123"
    proc.mkdir(parents=True)
    (proc / "cmdline").write_bytes(b"python\0-m\0uvicorn\0yleum_orchestrator.main:app\0")
    (proc / "stat").write_text("123 (python) S " + " ".join(["0"] * 18 + ["12345"]))
    (proc / "cwd").symlink_to(ROOT / "apps/orchestrator", target_is_directory=True)
    (proc / "environ").write_bytes(
        b"DATABASE_URL=postgresql://fixture.invalid/unused\0INTERNAL_TOKEN=synthetic\0"
        + b"WORKSPACE_ORIGIN=https://platform.example.org\0OMNIA_RELEASE_SHA="
        + release.encode() + b"\0"
    )
    if failure in {"foreign_host", "wrong_revision"}:
        data = json.loads(config.read_text())
        data["host" if failure == "foreign_host" else "release_sha"] = (
            "core" if failure == "foreign_host" else "b" * 40
        )
        config.write_text(json.dumps(data))
    elif failure == "writable":
        config.chmod(0o666)
    elif failure == "foreign_uid":
        os.chown(config, 65534, 65534)
    elif failure == "symlink":
        target = state / "other.json"
        config.rename(target)
        config.symlink_to(target)
    verifier_path = str(ROOT / "infra/release/verify-inspector-relay.py")
    code = f'''
import hashlib, importlib.util, json, sys
from pathlib import Path
spec = importlib.util.spec_from_file_location("verifier", {verifier_path!r})
m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
m.INVENTORY_CONFIG = Path({str(config)!r}); m.PROC = Path({str(tmp_path / 'proc')!r})
m.command = lambda *args: "123"
m.check_owner = lambda expected: None
m.verify = lambda origin, release, asset: {{"ok": True,
    "origin_matches": origin == "https://platform.example.org"}}
asset_path = m.ROOT / "apps/api/src/yleum_api/static/omnia-inspector.js"
asset = hashlib.sha256(asset_path.read_bytes()).hexdigest()
sys.argv = ["verify", "--expected-sha", {release!r}, "--asset-sha256", asset]
try:
    m.main()
except Exception as error:
    print(json.dumps({{"ok": False, "error_class": type(error).__name__}})); sys.exit(1)
'''
    result = subprocess.run([sys.executable, "-B", "-c", code], cwd=tmp_path,
                            env={"PATH": os.environ["PATH"], "PYTHONDONTWRITEBYTECODE": "1"},
                            text=True, capture_output=True, timeout=20)
    assert "synthetic" not in result.stdout + result.stderr
    assert result.returncode == (0 if failure is None else 1), result.stdout + result.stderr
    if failure is None:
        assert json.loads(result.stdout) == {"ok": True, "origin_matches": True}


@pytest.mark.parametrize(
    "path",
    [
        "/api/private",
        "/api/kit/../private",
        "//api/kit/omnia-inspector.js",
        "/api/kit/omnia-inspector.js/extra",
    ],
)
def test_target_is_exact_public_asset(monkeypatch, path):
    monkeypatch.setattr(
        writer,
        "get_settings",
        lambda: SimpleNamespace(inspector_asset_target="127.0.0.1:8200" + path),
    )
    with pytest.raises(OrchestratorError):
        writer._inspector_asset_target()


@pytest.mark.parametrize(
    "origin",
    [
        "http://example.org",
        "https://user@example.org",
        "https://127.0.0.1",
        "https://169.254.169.254",
        "https://[::1]",
        "https://example.org/path",
        "https://example.org?x",
        "https://example.org#x",
        "https://example.org;bad",
        "https://example.org\nfoo",
        "https://localhost",
        "https://example.org:444",
    ],
)
def test_relay_origin_rejects_unsafe_input(origin):
    with pytest.raises((ValueError, OrchestratorError)):
        writer._render_inspector_relay(origin)


def test_relay_has_fixed_tls_sni_and_no_private_headers():
    rendered = writer._render_inspector_relay("https://platform.example.org")
    for exact in [
        "listen 127.0.0.1:8200;",
        "location = /api/kit/omnia-inspector.js",
        "proxy_ssl_verify on;",
        "proxy_ssl_server_name on;",
        "proxy_ssl_name platform.example.org;",
        "proxy_set_header Host platform.example.org;",
        "proxy_pass_request_headers off;",
        "proxy_pass_request_body off;",
        "error_page 301 302 303 307 308 = @inspector_bad_redirect;",
        "return 404;",
    ]:
        assert exact in rendered
    assert "$host" not in rendered


def test_listener_unknown_is_never_owned():
    module = verifier()
    with pytest.raises(ValueError):
        module.validate_listener(
            'LISTEN 0 10 0.0.0.0:8200 0.0.0.0:* users:(("nginx",pid=12,fd=8))',
            owned_config=True,
            nginx_pids={12},
        )
    with pytest.raises(ValueError):
        module.validate_listener(
            'LISTEN 0 10 127.0.0.1:8200 0.0.0.0:* users:(("python",pid=12,fd=8))',
            owned_config=True,
            nginx_pids={12},
        )
    with pytest.raises(ValueError):
        module.validate_listener(
            'LISTEN 0 10 127.0.0.1:8200 0.0.0.0:* users:(("nginx",pid=12,fd=8))',
            owned_config=False,
            nginx_pids={12},
        )


def test_verifier_rejects_redirect_html_and_wrong_digest():
    module = verifier()
    import hashlib

    body = b"void 0;"
    expected = hashlib.sha256(body).hexdigest()
    module.validate_asset(200, "application/javascript; charset=utf-8", body, expected)
    for status, mime, data in [
        (302, "application/javascript", body),
        (200, "text/html", body),
        (200, "application/javascript", b"changed"),
    ]:
        with pytest.raises(ValueError):
            module.validate_asset(status, mime, data, expected)


@pytest.mark.parametrize(
    "raw,config,pids,accepted",
    [
        ("", False, set(), True),
        ('LISTEN 0 10 127.0.0.1:8200 0.0.0.0:* users:(("nginx",pid=12,fd=8))', True, {12}, True),
        ('LISTEN 0 10 127.0.0.1:8200 0.0.0.0:* users:(("nginx",pid=13,fd=8))', True, {12}, False),
        ('LISTEN 0 10 [::]:8200 [::]:* users:(("nginx",pid=12,fd=8))', True, {12}, False),
        ("LISTEN 0 10 127.0.0.1:8200 0.0.0.0:*", True, {12}, False),
    ],
)
def test_listener_complete_owner_contract(raw, config, pids, accepted):
    module = verifier()
    if accepted:
        module.validate_listener(raw, owned_config=config, nginx_pids=pids)
    else:
        with pytest.raises(ValueError):
            module.validate_listener(raw, owned_config=config, nginx_pids=pids)


def test_foreign_config_denies_before_any_command(monkeypatch):
    module = verifier()
    monkeypatch.setattr(module, "config_bytes", lambda: b"foreign config")
    monkeypatch.setattr(module, "command", lambda *_args: pytest.fail("must not touch nginx"))
    with pytest.raises(ValueError, match="relay_config_conflict"):
        module.check_owner(b"owned config")


def test_redirect_is_not_followed_and_no_ambient_proxy(monkeypatch):
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    calls = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            calls.append(self.path)
            self.send_response(302)
            self.send_header("Location", "/must-not-follow")
            self.end_headers()

        def log_message(self, *_args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:1")
    try:
        assert verifier().fetch(f"http://127.0.0.1:{server.server_port}/asset")[0] == 302
        assert calls == ["/asset"]
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_failed_reload_removes_only_own_new_config(tmp_path, monkeypatch):
    import hashlib
    import os

    if not hasattr(os, "geteuid") or os.geteuid() != 0:
        pytest.skip("Linux root-owned isolated filesystem fixture")
    module = verifier()
    monkeypatch.setattr(module, "CONFIG", tmp_path / "owned.conf")
    monkeypatch.setattr(module, "LOCK", tmp_path / "owned.lock")
    monkeypatch.setattr(module, "check_owner", lambda _expected: None)
    body = b"void 0;"
    monkeypatch.setattr(
        module,
        "fetch",
        lambda url: (
            (200, "application/javascript", body)
            if url.endswith(".js")
            else (
                200,
                "application/json",
                b'{"release_sha":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"}',
            )
        ),
    )
    calls = []

    def command(*args):
        calls.append(args)
        if len(calls) == 2:
            raise ValueError("reload failed")
        return ""

    monkeypatch.setattr(module, "command", command)
    with pytest.raises(ValueError, match="reload failed"):
        module.install(
            b"owned", "https://platform.example.org", "a" * 40, hashlib.sha256(body).hexdigest()
        )
    assert not module.CONFIG.exists()
    assert calls == [
        ("nginx", "-t"),
        ("systemctl", "reload", "nginx"),
        ("nginx", "-t"),
        ("systemctl", "reload", "nginx"),
    ]


def test_canonical_deploy_relay_stays_within_api_drain():
    source = (ROOT / "infra/release/deploy-prod.sh").read_text()
    relay = source.index('say "commerce: canonical inspector relay')
    assert (
        source.index('say "оркестраторы: core')
        < relay
        < source.index("reader_control commerce clear")
    )
    section = source[relay : source.index("\nfi", relay)]
    assert "StrictHostKeyChecking=yes commerce" in section
    assert "--install --expected-sha $SHA --asset-sha256 $INSPECTOR_ASSET_SHA" in section
    assert "git -C /opt/omnia show $SHA:apps/api/src/yleum_api/static/omnia-inspector.js" in section


def test_actual_nginx_refused_upstream_then_verified_tls_relay(tmp_path, monkeypatch):
    """Opt-in isolated Linux fixture; binds8200, never a production config."""
    import hashlib
    import os
    import shutil
    import socket
    import ssl
    import subprocess
    import threading
    import time
    import urllib.error
    import urllib.request
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    nginx = shutil.which("nginx")
    openssl = shutil.which("openssl")
    if not nginx or not openssl or os.environ.get("OMNIA_TEST_INSPECTOR_NGINX") != "1":
        pytest.skip("requires explicitly isolated real NGINX+OpenSSL runtime; not a template PASS")
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 8200))  # unknown listener must fail, never kill/reassign
    cert, key = tmp_path / "cert.pem", tmp_path / "key.pem"
    subprocess.run(
        [
            openssl,
            "req",
            "-x509",
            "-newkey",
            "rsa:2048",
            "-nodes",
            "-days",
            "1",
            "-subj",
            "/CN=platform.example.org",
            "-addext",
            "subjectAltName=DNS:platform.example.org",
            "-keyout",
            str(key),
            "-out",
            str(cert),
        ],
        check=True,
        capture_output=True,
    )
    body = b"window.__inspector_fixture = true;"
    observed = []
    sni = []
    upstream_status = [200]

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            observed.append(dict(self.headers))
            self.send_response(upstream_status[0])
            if upstream_status[0] == 302:
                self.send_header("Location", "/must-not-follow")
            self.send_header("Content-Type", "application/javascript")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_args):
            pass

    upstream = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(cert, key)
    context.set_servername_callback(lambda _socket, name, _context: sni.append(name))
    upstream.socket = context.wrap_socket(upstream.socket, server_side=True)
    thread = threading.Thread(target=upstream.serve_forever, daemon=True)
    thread.start()
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        preview_port = sock.getsockname()[1]
    monkeypatch.setattr(
        writer,
        "get_settings",
        lambda: SimpleNamespace(inspector_asset_target="127.0.0.1:8200/api/kit/omnia-inspector.js"),
    )
    base = (
        f"daemon off; pid {tmp_path}/nginx.pid; error_log {tmp_path}/error.log; events {{}} "
        f"http {{ access_log off; upstream platform.example.org "
        f"{{ server 127.0.0.1:{upstream.server_port}; }} "
        f"server {{ listen 127.0.0.1:{preview_port}; {writer._inspector_location()} }} @RELAY@ }}"
    )
    config = tmp_path / "nginx.conf"
    processes = []
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), verifier().NoRedirect())

    def get(path, *, method="GET", headers=None, port=8200):
        request = urllib.request.Request(
            f"http://127.0.0.1:{port}{path}", headers=headers or {}, method=method
        )
        try:
            response = opener.open(request, timeout=5)
        except urllib.error.HTTPError as error:
            response = error
        with response:
            return response.status, response.headers.get("Content-Type", ""), response.read()

    def start(relay):
        config.write_text(base.replace("@RELAY@", relay))
        subprocess.run(
            [nginx, "-p", str(tmp_path), "-c", str(config), "-t"], check=True, capture_output=True
        )
        process = subprocess.Popen(
            [nginx, "-p", str(tmp_path), "-c", str(config)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        processes.append(process)
        for _ in range(100):
            if process.poll() is not None:
                pytest.fail("owned fixture NGINX exited before readiness")
            try:
                with socket.create_connection(("127.0.0.1", preview_port), timeout=0.1):
                    return process
            except OSError:
                time.sleep(0.02)
        pytest.fail("owned fixture NGINX failed to listen")

    try:
        process = start("")
        assert get("/_omnia/inspector.js", port=preview_port)[0] == 502
        process.terminate()
        process.wait(timeout=5)
        relay = writer._render_inspector_relay("https://platform.example.org").replace(
            "/etc/ssl/certs/ca-certificates.crt", str(cert)
        )
        process = start(relay)
        verifier().validate_asset(
            *get("/_omnia/inspector.js", port=preview_port), hashlib.sha256(body).hexdigest()
        )
        assert (
            get(
                "/api/kit/omnia-inspector.js",
                headers={
                    "Host": "foreign.example",
                    "Authorization": "Bearer fixture",
                    "Cookie": "fixture=secret",
                },
            )[0]
            == 200
        )
        assert sni and set(sni) == {"platform.example.org"}
        assert all(
            row.get("Host") == "platform.example.org"
            and not row.get("Authorization")
            and not row.get("Cookie")
            for row in observed
        )
        for path in [
            "/api/private",
            "/api/kit/omnia-inspector.js?secret=1",
            "//api/kit/omnia-inspector.js",
            "/api/kit/../kit/omnia-inspector.js",
            "/api/kit/%6fmnia-inspector.js",
        ]:
            assert get(path)[0] == 404
        assert get("/api/kit/omnia-inspector.js", method="POST")[0] == 405
        upstream_status[0] = 302
        assert get("/api/kit/omnia-inspector.js")[0] == 502
        upstream_status[0] = 200
        process.terminate()
        process.wait(timeout=5)
        start(
            relay.replace(
                "proxy_ssl_name platform.example.org;", "proxy_ssl_name wrong.example.org;"
            )
        )
        assert get("/api/kit/omnia-inspector.js")[0] == 502
    finally:
        for process in processes:
            if process.poll() is None:
                process.terminate()
                process.wait(timeout=5)
        upstream.shutdown()
        upstream.server_close()
        thread.join(timeout=5)

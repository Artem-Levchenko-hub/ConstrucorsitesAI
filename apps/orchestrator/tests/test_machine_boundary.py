import base64
import hmac
import importlib
import importlib.util
import json
import re
import shutil
import subprocess
import time

import pytest


def module():
    name = "omnia_orchestrator.services.machine_boundary"
    assert importlib.util.find_spec(name) is not None, "framework-neutral MAX boundary is missing"
    return importlib.import_module(name)


def cookie(secret, payload):
    encoded = base64.urlsafe_b64encode(json.dumps(payload).encode()).decode().rstrip("=")
    signature = base64.urlsafe_b64encode(hmac.digest(secret.encode(), encoded.encode(), "sha256"))
    return encoded + "." + signature.decode().rstrip("=")


def test_boundary_accepts_only_its_valid_unexpired_max_cookie():
    boundary = module()
    secret = "project-boundary-secret"
    value = cookie(secret, {"id": "user-one", "expiresAt": int(time.time()) + 60})
    assert boundary.verified_user(value, secret)["id"] == "user-one"
    assert boundary.verified_user(value, "different-project-secret") is None
    assert (
        boundary.verified_user(cookie(secret, {"id": "user-one", "expiresAt": 1}), secret) is None
    )
    assert boundary.verified_user("forged", secret) is None


def test_caller_identity_and_credentials_are_not_forwarded_to_product_code():
    boundary = module()
    headers = boundary.product_headers(
        {
            "x-omnia-user-id": "forged",
            "X-Omnia-Project-ID": "other",
            "Cookie": "secret-session",
            "Authorization": "Bearer secret",
            "Accept": "text/html",
        },
        project_id="project-one",
        epoch=7,
        user={"id": "user-one"},
    )
    assert headers["X-Omnia-User-ID"] == "user-one"
    assert headers["X-Omnia-Project-ID"] == "project-one"
    assert headers["X-Omnia-Session-Epoch"] == "7"
    assert not any(key.casefold() in {"cookie", "authorization"} for key in headers)
    assert headers["Accept"] == "text/html"


def test_routing_preserves_managed_namespaces_and_uses_longest_product_prefix():
    boundary = module()
    routes = [
        {"path": "/", "service": "web", "port": 8080},
        {"path": "/v1", "service": "api", "port": 9090},
    ]
    assert boundary.route_port("/v1/items", routes) == 9090
    assert boundary.route_port("/v10", routes) == 8080
    for path in (
        "/api/omnia/actions",
        "/api/max/session",
        "//api/omnia/actions",
        "/%2fapi/omnia/actions",
        "/x/../api/omnia/actions",
    ):
        assert boundary.route_port(path, routes) is None


def test_actual_http_boundary_rejects_bad_auth_and_strips_product_credentials():
    import http.client
    import http.server
    import threading

    boundary = module()
    received = []

    class Product(http.server.BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            received.append(dict(self.headers))
            self.send_response(200)
            self.send_header("Set-Cookie", "__Host-max_session=forged; Secure; Path=/")
            self.end_headers()
            self.wfile.write(b"non-Next product")

    product = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Product)
    gateway = boundary.BoundaryServer(("127.0.0.1", 0), boundary.BoundaryHandler)
    gateway.config = {
        "secret": "private-signing-secret",
        "project_id": "project-A",
        "epoch": 7,
        "machine_host": "127.0.0.1",
        "core_host": "127.0.0.1",
        "routes": [{"path": "/", "port": product.server_port}],
    }
    threads = [
        threading.Thread(target=server.serve_forever, daemon=True) for server in (product, gateway)
    ]
    for thread in threads:
        thread.start()
    try:

        def request(path, headers=None):
            connection = http.client.HTTPConnection("127.0.0.1", gateway.server_port, timeout=3)
            try:
                connection.request("GET", path, headers=headers or {})
                response = connection.getresponse()
                return response.status, response.read(), dict(response.getheaders())
            finally:
                connection.close()

        assert request("/")[0] == 401
        assert request("/", {"Cookie": "__Host-max_session=forged"})[0] == 401
        assert not received
        signed = cookie(
            gateway.config["secret"], {"id": "user-A", "expiresAt": int(time.time()) + 60}
        )
        headers = {
            "Cookie": "__Host-max_session=" + signed,
            "Authorization": "Bearer secret",
            "X-Omnia-User-ID": "forged-user",
        }
        status, content, response_headers = request("/", headers)
        assert status == 200 and content == b"non-Next product"
        assert "Set-Cookie" not in response_headers
        assert received[0]["X-Omnia-User-ID"] == "user-A"
        assert "Cookie" not in received[0] and "Authorization" not in received[0]
        status, content, _ = request("/__omnia/identity", headers)
        assert status == 200 and json.loads(content)["project_id"] == "project-A"
    finally:
        for server in (gateway, product):
            server.shutdown()
            server.server_close()
        for thread in threads:
            thread.join(timeout=3)


@pytest.fixture
def public_boundary():
    import http.client
    import http.server
    import threading

    boundary = module()
    received = []

    class Product(http.server.BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            received.append(dict(self.headers))
            self.send_response(200)
            self.send_header("Set-Cookie", "__Host-max_session=forged; Secure; Path=/")
            self.end_headers()
            self.wfile.write(b"protected product")

    product = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Product)
    gateway = boundary.BoundaryServer(("127.0.0.1", 0), boundary.BoundaryHandler)
    gateway.config = {
        "secret": "public-test-secret", "public_mode": True,
        "project_id": "project-A", "epoch": 7,
        "machine_host": "127.0.0.1", "core_host": "127.0.0.1",
        "routes": [{"path": "/", "port": product.server_port}],
    }
    threads = [threading.Thread(target=s.serve_forever, daemon=True) for s in (product, gateway)]
    for thread in threads:
        thread.start()

    def request(path, headers=None, method="GET"):
        connection = http.client.HTTPConnection("127.0.0.1", gateway.server_port, timeout=3)
        try:
            connection.request(method, path, headers=headers or {})
            response = connection.getresponse()
            return response.status, response.read(), dict(response.getheaders())
        finally:
            connection.close()

    yield request, received
    for server in (gateway, product):
        server.shutdown()
        server.server_close()
    for thread in threads:
        thread.join(timeout=3)


def test_public_first_navigation_bootstraps_without_exposing_product(public_boundary):
    request, received = public_boundary
    status, body, headers = request("/?untrusted=%3Cscript%3E", {"Accept": "text/html"})
    assert status == 200
    assert headers["Content-Type"].startswith("text/html")
    assert headers["Cache-Control"] == "no-store"
    assert headers["Referrer-Policy"] == "no-referrer"
    assert "Content-Security-Policy" in headers
    assert b"<script>" not in body
    assert not received
    for path in ("/api/orders", "/__omnia/identity", "/_next/static/private.js"):
        assert request(path)[0] == 401
    assert request("/", {"Accept": "text/html"}, "POST")[0] == 401


@pytest.mark.parametrize("path", [
    "/api/omnia/preview-session", "/api/omnia/preview-session?signature=forged",
    "/api/omnia/%70review-session", "/api/omnia/preview-session/",
    "/api/omnia/preview-session/extra",
])
def test_public_preview_bootstrap_is_never_forwarded(public_boundary, path):
    request, received = public_boundary
    assert request(path, {"Accept": "text/html"})[0] == 404
    assert not received


@pytest.mark.parametrize("payload,secret", [
    ({"id": "preview", "expiresAt": 9_999_999_999}, "public-test-secret"),
    ({"id": "123", "expiresAt": 9_999_999_999}, "private-preview-secret"),
    ({"id": "123", "expiresAt": 1}, "public-test-secret"),
])
def test_public_rejects_preview_wrong_project_and_expired_sessions(
    public_boundary, payload, secret,
):
    request, received = public_boundary
    headers = {"Cookie": "__Host-max_session=" + cookie(secret, payload)}
    assert request("/__omnia/identity", headers)[0] == 401
    assert request("/api/omnia/actions", headers)[0] == 401
    assert not received


def test_public_trusted_numeric_user_reaches_product_without_credentials(public_boundary):
    request, received = public_boundary
    signed = cookie("public-test-secret", {"id": "123", "expiresAt": 9_999_999_999})
    headers = {
        "Cookie": "__Host-max_session=" + signed, "X-Omnia-User-ID": "forged",
        "X-Omnia-MAX-Init-Data": "launch-secret", "Authorization": "Bearer secret",
        "Forwarded": "host=evil.test", "X-Forwarded-Host": "evil.test",
    }
    status, body, response_headers = request("/", headers)
    assert status == 200 and body == b"protected product"
    assert "Set-Cookie" not in response_headers
    assert received[0]["X-Omnia-User-ID"] == "123"
    assert not ({"Cookie", "Authorization", "Forwarded", "X-Forwarded-Host",
                 "X-Omnia-MAX-Init-Data"} & received[0].keys())


@pytest.mark.parametrize("session_ok,identity_ok,expected_calls,reloads", [
    (True, True, 2, 1), (True, False, 2, 0), (False, False, 1, 0),
])
@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js required for bootstrap execution")
def test_public_bootstrap_verifies_cookie_before_navigation(
    public_boundary, session_ok, identity_ok, expected_calls, reloads,
):
    request, _ = public_boundary
    status, html, _ = request("/", {"Accept": "text/html"})
    assert status == 200
    scripts = re.findall(r"<script[^>]*>(.*?)</script>", html.decode(), re.S)
    assert scripts
    harness = r"""
const vm = require('node:vm');
const input = JSON.parse(require('node:fs').readFileSync(0, 'utf8'));
const calls = [], navigation = [], message = {textContent: ''};
const button = {disabled: false, addEventListener() {}};
const context = {
  window: {WebApp: {initData: 'signed-test-launch', ready() {}, expand() {}}},
  document: {getElementById: id => id === 'retry' ? button : message},
  location: {href: 'https://app.example/?safe=1#WebAppData=kept',
             replace: target => navigation.push(target)},
  fetch: async (url, options) => {
    calls.push({url, options});
    return {ok: calls.length === 1 ? input.session_ok : input.identity_ok,
            json: async () => ({user_id: '123', project_id: 'project-A'})};
  },
  AbortSignal, setTimeout, clearTimeout, URL,
};
vm.runInNewContext(input.script, context);
setTimeout(() => process.stdout.write(JSON.stringify({calls, navigation, message})), 30);
"""
    result = subprocess.run(
        ["node", "-e", harness], input=json.dumps({
            "script": scripts[-1], "session_ok": session_ok, "identity_ok": identity_ok,
        }), text=True, capture_output=True, timeout=10, check=True,
    )
    result = json.loads(result.stdout)
    assert len(result["calls"]) == expected_calls
    assert result["calls"][0]["url"] == "/api/max/session"
    assert json.loads(result["calls"][0]["options"]["body"]) == {
        "initData": "signed-test-launch",
    }
    assert result["calls"][0]["options"]["credentials"] == "include"
    if expected_calls == 2:
        assert result["calls"][1]["url"] == "/__omnia/identity"
    assert len(result["navigation"]) == reloads
    if not reloads:
        assert result["message"]["textContent"]

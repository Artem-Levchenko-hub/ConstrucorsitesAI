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
    name = "yleum_orchestrator.services.machine_boundary"
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
        "owner_origins": ["https://yleum.ru", "https://constructor.lead-generator.ru"],
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
        assert response_headers["Content-Security-Policy"] == (
            "frame-ancestors 'self' https://yleum.ru https://constructor.lead-generator.ru"
        )
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


@pytest.mark.parametrize("headers", [
    {},
    {"Accept": "*/*"},
    {"Accept": "text/html", "Sec-Fetch-Dest": "iframe"},
    {"Accept": "text/html", "Sec-Fetch-Dest": ""},
])
def test_public_root_launch_does_not_require_browser_navigation_headers(public_boundary, headers):
    request, received = public_boundary
    status, body, response_headers = request("/", headers)
    assert status == 200
    assert response_headers["Content-Type"].startswith("text/html")
    assert response_headers["Cache-Control"] == "no-store"
    assert b"/api/max/session" in body and b"max-web-app.js" in body
    assert not received


@pytest.mark.parametrize("destination", ["iframe", "frame", ""])
def test_public_embedded_page_navigation_can_start_max_login(public_boundary, destination):
    request, received = public_boundary
    status, body, _ = request(
        "/orders?source=menu", {"Accept": "text/html", "Sec-Fetch-Dest": destination},
    )
    assert status == 200 and b"/api/max/session" in body
    assert not received


@pytest.mark.parametrize("path", [
    "/api/orders", "/api/omnia/actions", "/__omnia/identity", "/_next/static/private.js", "/auth",
])
def test_public_embedded_navigation_headers_never_authorize_protected_routes(public_boundary, path):
    request, received = public_boundary
    assert request(path, {"Accept": "text/html", "Sec-Fetch-Dest": "iframe"})[0] == 401
    assert not received


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
             reload: () => navigation.push('reload')},
  fetch: async (url, options) => {
    calls.push({url, options});
    return {ok: calls.length === 1 ? input.session_ok : input.identity_ok,
            json: async () => ({user_id: '123', project_id: 'project-A'})};
  },
  AbortSignal, AbortController, setTimeout, clearTimeout, URL,
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


def test_owner_framing_follows_the_cabinet_address_and_keeps_the_spare_one(monkeypatch) -> None:
    """Браузер показывает превью только тем кабинетам, что перечислены здесь.

    После переименования кабинет переехал на yleum.ru, а политика осталась со
    старым адресом — и браузер перестал встраивать превью владельца (проверено
    живьём на проде 26.09). Адрес обязан приходить из настроек, а запасной
    кабинет — оставаться разрешённым, пока он жив.
    """
    from yleum_orchestrator.core.config import Settings
    from yleum_orchestrator.services import studio_origins as studio_origins_module
    from yleum_orchestrator.services.machine_boundary import owner_framing
    from yleum_orchestrator.services.studio_origins import studio_origins

    settings = Settings(
        _env_file=None,
        database_url="postgresql://test:test@127.0.0.1:5432/test",
        internal_token="test-internal-token-not-a-real-secret",
    )
    monkeypatch.setattr(studio_origins_module, "get_settings", lambda: settings)

    origins = studio_origins()
    assert origins[0] == "https://yleum.ru"
    assert "https://constructor.lead-generator.ru" in origins

    policy = owner_framing(origins)
    assert policy == (
        "frame-ancestors 'self' https://yleum.ru https://www.yleum.ru "
        "https://constructor.lead-generator.ru"
    )

    # Шлюз, созданный до этой правки, не знает ключа и остаётся на запасном
    # кабинете, а не отдаёт пустую политику, встраиваемую откуда угодно.
    assert owner_framing(None) == (
        "frame-ancestors 'self' https://constructor.lead-generator.ru"
    )
    # Адреса приходят из настроек платформы, но заголовок собирается из них
    # буквально: значение не по форме не должно попасть в политику.
    assert owner_framing(["http://evil.example", "https://ok.example", "junk"]) == (
        "frame-ancestors 'self' https://ok.example"
    )


@pytest.fixture
def private_boundary():
    import http.client
    import http.server
    import threading

    boundary = module()
    received = []

    class Product(http.server.BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            received.append(self.path)
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"private product HTML")

    product = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Product)
    gateway = boundary.BoundaryServer(("127.0.0.1", 0), boundary.BoundaryHandler)
    gateway.config = {
        "secret": "private-recovery-secret", "project_id": "project-A", "epoch": 7,
        "machine_host": "127.0.0.1", "core_host": "127.0.0.1",
        "routes": [{"path": "/", "port": product.server_port}],
        "owner_origins": [
            "http://insecure.example", "https://editor.example", "https://legacy.example",
            "https://injected.example\n", "https://bad.example/'><script>",
        ],
    }
    threads = [threading.Thread(target=s.serve_forever, daemon=True) for s in (product, gateway)]
    for thread in threads:
        thread.start()

    def request(path, headers=None, method="GET", body=None):
        connection = http.client.HTTPConnection("127.0.0.1", gateway.server_port, timeout=3)
        try:
            connection.request(method, path, headers=headers or {}, body=body)
            response = connection.getresponse()
            return response.status, response.read(), response.getheaders()
        finally:
            connection.close()

    yield request, received, gateway.config
    for server in (gateway, product):
        server.shutdown()
        server.server_close()
    for thread in threads:
        thread.join(timeout=3)


def test_private_document_returns_only_trusted_recovery_shell(private_boundary):
    request, received, _ = private_boundary
    status, body, raw_headers = request("/orders?filter=%3Cscript%3E", {
        "Accept": "text/html", "Sec-Fetch-Dest": "iframe", "Sec-Fetch-Mode": "navigate",
        "Host": "forged.example", "Origin": "https://forged.example",
        "Referer": "https://forged.example/editor", "X-Forwarded-Host": "forged.example",
    })
    assert status == 200
    headers = dict(raw_headers)
    assert headers["Content-Type"] == "text/html; charset=utf-8"
    assert headers["Cache-Control"] == "no-store"
    assert headers["Referrer-Policy"] == "no-referrer"
    assert headers["X-Content-Type-Options"] == "nosniff"
    policies = [value for key, value in raw_headers if key == "Content-Security-Policy"]
    assert any("default-src 'none'" in value and "connect-src 'self'" in value
               and "base-uri 'none'" in value and "form-action 'none'" in value
               for value in policies)
    assert any(value == "frame-ancestors 'self' https://editor.example https://legacy.example"
               for value in policies)
    text = body.decode()
    assert 'href="https://editor.example/max/project-A/preview"' in text
    assert not any(value in text for value in (
        "private product HTML", "private-recovery-secret", "forged.example", "injected.example",
        "insecure.example", "bad.example", "max-web-app.js", "<script>", "filter=",
    ))
    assert "Set-Cookie" not in headers
    assert not received


def test_private_recovery_never_opens_non_documents_or_unsafe_paths(private_boundary):
    request, received, _ = private_boundary
    document = {"Accept": "text/html", "Sec-Fetch-Dest": "document"}
    for path in ("/api/orders", "/api", "/_next/static/private.js", "/_next",
                 "/__omnia/identity", "/__omnia", "/auth/login", "/auth"):
        assert request(path, document)[0] == 401, path
    for headers in ({}, {"Accept": "application/json"},
                    {"Accept": "text/html", "Sec-Fetch-Dest": "script"},
                    {"Accept": "text/html", "Sec-Fetch-Mode": "cors"},
                    {"Accept": "text/html;q=0"},
                    {"Accept": "text/html", "RSC": "1"},
                    {"Accept": "text/html", "Next-Action": "business-action"}):
        assert request("/orders", headers)[0] == 401, headers
    for method in ("POST", "PUT", "PATCH", "DELETE", "HEAD"):
        assert request("/orders", document, method)[0] == 401, method
    assert request("/orders", document, body=b"business payload")[0] == 401
    for path in ("/%2fapi/orders", "/%5capi/orders", "/x/../orders", "/%252forders",
                 "https://evil.example/orders", "//evil.example/orders"):
        assert request(path, document)[0] == 400, path
    assert not received


def test_private_recovery_preserves_authenticated_forwarding_and_identity(private_boundary):
    request, received, _ = private_boundary
    headers = {"Accept": "text/html", "Cookie": "__Host-max_session=" + cookie(
        "private-recovery-secret", {"id": "preview", "expiresAt": 9_999_999_999},
    )}
    status, body, _ = request("/orders?filter=mine", headers)
    assert status == 200 and body == b"private product HTML"
    assert received == ["/orders?filter=mine"]
    status, body, _ = request("/__omnia/identity", headers)
    assert status == 200
    assert json.loads(body) == {"project_id": "project-A", "user_id": "preview", "epoch": 7}


def test_private_recovery_validates_origins_and_encodes_owner_link(private_boundary):
    request, received, config = private_boundary
    config["owner_origins"] = [
        "https://invalid-port.example:65536", "https://editor.example:443",
    ]
    config["project_id"] = "project/with\"<markup>"
    status, body, headers = request("/", {"Accept": "text/html"})
    assert status == 200
    assert (
        'href="https://editor.example/max/project%2Fwith%22%3Cmarkup%3E/preview"' in body.decode()
    )
    assert b"invalid-port.example" not in body
    assert ("Content-Security-Policy", "frame-ancestors 'self' https://editor.example") in headers
    assert b"<markup>" not in body
    assert not received


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js required for recovery execution")
def test_private_recovery_requires_parent_capability_and_server_session_proof(private_boundary):
    request, received, _ = private_boundary
    status, html, _ = request("/orders?filter=mine", {"Accept": "text/html"})
    assert status == 200
    script = re.findall(r"<script[^>]*>(.*?)</script>", html.decode(), re.S)[-1]
    harness = r"""
const vm = require('node:vm');
const input = JSON.parse(require('node:fs').readFileSync(0, 'utf8'));
const calls = [], navigation = [], messages = [], listeners = new Map(), timers = new Map();
const status = {textContent: ''}; let sequence = 0;
const parent = {postMessage: (data, origin) => messages.push({data, origin})};
const location = {origin: 'https://private.example', href: 'https://private.example/orders?filter=mine',
  reload: () => navigation.push('/orders?filter=mine')};
const window = {parent, location, crypto: {getRandomValues: bytes => bytes.fill(161)},
  addEventListener: (type, fn) => listeners.set(type, fn),
  removeEventListener: (type, fn) => {if (listeners.get(type) === fn) listeners.delete(type);}};
if (input.scenario === 'top-level') window.parent = window;
const context = {window, location, document: {getElementById: () => status},
  URL, Uint8Array, AbortController,
  setTimeout: (fn, delay) => {const key = ++sequence; timers.set(key, {fn, delay}); return key;},
  clearTimeout: key => timers.delete(key),
  fetch: async (url, options) => {
    calls.push({url, options}); const first = calls.length === 1;
    if (input.scenario === (first ? 'bootstrap-timeout' : 'identity-timeout') ||
        (first && input.scenario === 'unload-during-bootstrap')) {
      return new Promise((_, reject) => options.signal.addEventListener('abort',
        () => reject(new Error('timeout'))));
    }
    const identity = {project_id: 'project-A', user_id: 'preview', epoch: 7};
    if (input.scenario === 'bad-proof-user') identity.user_id = '123';
    if (input.scenario === 'bad-proof-project') identity.project_id = 'project-B';
    if (input.scenario === 'bad-proof-epoch') identity.epoch = 8;
    return {ok: input.scenario !== (first ? 'bootstrap-http-failure' : 'identity-http-failure'),
      json: async () => {
        if (!first && input.scenario === 'identity-json-failure') throw Error('not JSON');
        return first ? (input.scenario === 'bad-bootstrap'
          ? {mode: 'max', user: {id: '123'}} : {mode: 'preview', user: {id: 'preview'}}) : identity;
      }};
  }};
const settle = async () => {for (let i=0; i<8; i++) await new Promise(setImmediate);};
(async () => {
  vm.runInNewContext(input.script, context); await settle();
  if (messages.length && input.scenario !== 'timeout') {
    const data = {type: 'omnia:preview-session:result', nonce: messages[0].data.nonce,
      url: 'https://private.example/api/omnia/preview-session?expires=1&signature=opaque'};
    let source = parent, origin = 'https://editor.example';
    if (input.scenario === 'forged-source') source = {};
    if (input.scenario === 'forged-origin') origin = 'https://evil.example';
    if (input.scenario === 'forged-nonce') data.nonce = 'b'.repeat(32);
    if (input.scenario === 'wrong-capability-origin') data.url = 'https://evil.example/api/omnia/preview-session';
    if (input.scenario === 'capability-path') data.url = 'https://private.example/api/orders';
    if (input.scenario === 'capability-credentials') data.url = 'https://x:y@private.example/api/omnia/preview-session';
    if (input.scenario === 'capability-fragment') data.url += '#credential';
    if (input.scenario === 'parent-error') {delete data.url; data.error = 'session_unavailable';}
    if (input.scenario === 'unload-before-reply') listeners.get('pagehide')?.();
    const listener = listeners.get('message'); listener?.({source, origin, data});
    // A matching response must be consumed once even while bootstrap is pending.
    listener?.({source, origin, data});
    await settle();
    if (input.scenario === 'unload-during-bootstrap') listeners.get('pagehide')?.();
  }
  for (const timer of [...timers.values()]) timer.fn();
  await settle();
  process.stdout.write(JSON.stringify({calls, navigation, messages, status,
    listening: listeners.has('message'), timers: [...timers.values()].map(t => t.delay)}));
})().catch(error => {console.error(error); process.exit(1);});
"""
    cases = [
        ("valid", 2, 1), ("forged-source", 0, 0), ("forged-origin", 0, 0),
        ("forged-nonce", 0, 0), ("wrong-capability-origin", 0, 0),
        ("capability-path", 0, 0), ("capability-credentials", 0, 0),
        ("capability-fragment", 0, 0), ("parent-error", 0, 0),
        ("bad-bootstrap", 1, 0), ("bootstrap-http-failure", 1, 0),
        ("bad-proof-user", 2, 0), ("bad-proof-project", 2, 0), ("bad-proof-epoch", 2, 0),
        ("identity-http-failure", 2, 0), ("identity-json-failure", 2, 0),
        ("timeout", 0, 0), ("bootstrap-timeout", 1, 0), ("identity-timeout", 2, 0),
        ("top-level", 0, 0), ("unload-before-reply", 0, 0), ("unload-during-bootstrap", 1, 0),
    ]
    for scenario, count, reloads in cases:
        process = subprocess.run(["node", "-e", harness], input=json.dumps({
            "script": script, "scenario": scenario,
        }), text=True, capture_output=True, timeout=5, check=True)
        result = json.loads(process.stdout)
        assert len(result["calls"]) == count, scenario
        assert result["navigation"] == ["/orders?filter=mine"] * reloads, scenario
        assert not result["listening"] and not result["timers"], scenario
        if scenario != "top-level":
            assert [item["origin"] for item in result["messages"]] == [
                "https://editor.example", "https://legacy.example",
            ], scenario
            assert all(item["data"]["type"] == "omnia:preview-session:renew"
                       and re.fullmatch("[a-f0-9]{32}", item["data"]["nonce"])
                       for item in result["messages"]), scenario
        if count:
            first = result["calls"][0]
            assert first["url"] == (
                "https://private.example/api/omnia/preview-session?expires=1&signature=opaque"
            ), scenario
            assert first["options"]["headers"]["Accept"] == "application/json", scenario
            assert first["options"]["redirect"] == "error", scenario
            assert first["options"]["credentials"] == "include", scenario
            assert first["options"].get("method", "GET") == "GET", scenario
        if count == 2:
            assert result["calls"][1]["url"] == "/__omnia/identity", scenario
        if not reloads:
            assert result["status"]["textContent"], scenario
    assert not received

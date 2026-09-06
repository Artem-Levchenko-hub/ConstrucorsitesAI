"""Real HTTP coverage for MAX session cookies crossing an embedded browser boundary."""
import base64
import hmac
import http.client
import http.cookies
import http.server
import json
import threading
import time
from types import SimpleNamespace

import pytest

from omnia_orchestrator.services import machine_boundary as boundary

ORIGIN = "https://app.example.test"
CANONICAL = "__Host-max_session"
EMBEDDED = "__Host-max_session_embedded"
PARTITIONED = "__Host-max_session_partitioned"
COOKIE_NAMES = (CANONICAL, EMBEDDED, PARTITIONED)
SECRET = "disposable-cookie-test-key"


def signed_session(user_id="123", *, secret=SECRET, expires=None):
    payload = {"id": user_id, "expiresAt": expires or int(time.time()) + 3600}
    value = base64.urlsafe_b64encode(json.dumps(payload).encode()).decode().rstrip("=")
    signature = base64.urlsafe_b64encode(hmac.digest(secret.encode(), value.encode(), "sha256"))
    return value + "." + signature.decode().rstrip("=")


@pytest.fixture
def session_boundary(monkeypatch):
    token = signed_session()
    core_requests, product_requests = [], []
    core_state = SimpleNamespace(
        status=200,
        cookies=[f"{CANONICAL}={token}; Path=/; Secure; HttpOnly; SameSite=Lax; Max-Age=3600"],
    )

    class Core(http.server.BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def do_GET(self):
            core_requests.append((self.path, dict(self.headers)))
            self.send_response(core_state.status)
            if self.command == "POST" and self.path == "/api/max/session":
                for value in core_state.cookies:
                    self.send_header("Set-Cookie", value)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"user":{"id":"123"}}')

        do_POST = do_GET
        do_DELETE = do_GET

    class Product(http.server.BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def do_GET(self):
            product_requests.append((self.path, dict(self.headers)))
            self.send_response(200)
            # Generated code must never mint a trusted alias.
            self.send_header("Set-Cookie", f"{EMBEDDED}=forged; Secure; Path=/; SameSite=None")
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(b"<!doctype html><h1>Protected application</h1>")

        do_POST = do_GET
        do_PUT = do_GET
        do_PATCH = do_GET
        do_DELETE = do_GET

    product = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Product)
    core = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Core)
    gateway = boundary.BoundaryServer(("127.0.0.1", 0), boundary.BoundaryHandler)
    gateway.config = {
        "secret": SECRET, "public_mode": True, "public_origin": ORIGIN,
        "project_id": "project-cookie-test", "epoch": 7,
        "machine_host": "127.0.0.1", "core_host": "127.0.0.1",
        "routes": [{"path": "/", "port": product.server_port}],
    }
    # Keep real HTTP on isolated random ports, including the fixed-port core hop.
    connection_type = http.client.HTTPConnection
    monkeypatch.setattr(boundary.http.client, "HTTPConnection", lambda host, port, **kwargs:
                        connection_type(host, core.server_port if port == 3000 else port, **kwargs))
    servers = (gateway, core, product)
    threads = [threading.Thread(target=s.serve_forever, daemon=True) for s in servers]
    for thread in threads:
        thread.start()

    def request(path, *, headers=None, method="GET", body=None):
        connection = connection_type("127.0.0.1", gateway.server_port, timeout=3)
        try:
            connection.request(method, path, headers=headers or {}, body=body)
            response = connection.getresponse()
            return response.status, response.read(), response.getheaders()
        finally:
            connection.close()

    try:
        yield SimpleNamespace(
            request=request, gateway=gateway, core=core_state, token=token,
            core_requests=core_requests, product_requests=product_requests,
        )
    finally:
        for server in servers:
            server.shutdown()
            server.server_close()
        for thread in threads:
            thread.join(timeout=3)


def test_login_keeps_first_party_cookie_and_sets_secure_embedded_transports(session_boundary):
    session = session_boundary
    status, _, headers = session.request(
        "/api/max/session", method="POST", headers={"Origin": ORIGIN},
    )
    assert status == 200
    values = [value for key, value in headers if key.lower() == "set-cookie"]
    assert len(values) == 3
    assert values[0] == session.core.cookies[0]
    for value in values:
        parsed = http.cookies.SimpleCookie(value.replace("; Partitioned", ""))
        assert len(parsed) == 1
        name, morsel = next(iter(parsed.items()))
        assert name in COOKIE_NAMES and morsel.value == session.token
        assert morsel["secure"] and morsel["httponly"]
        assert morsel["path"] == "/" and not morsel["domain"]
        assert int(morsel["max-age"]) <= 3600
        assert morsel["samesite"].lower() == ("lax" if name == CANONICAL else "none")
        assert ("; Partitioned" in value) == (name == PARTITIONED)


@pytest.mark.parametrize("name", COOKIE_NAMES)
def test_each_transport_authenticates_but_credentials_never_reach_product(session_boundary, name):
    session = session_boundary
    headers = {"Cookie": f"{name}={session.token}", "Authorization": "Bearer untrusted"}
    status, body, _ = session.request("/__omnia/identity", headers=headers)
    assert status == 200 and json.loads(body)["user_id"] == "123"
    status, _, response_headers = session.request("/", headers=headers)
    assert status == 200
    assert not any(key.lower() == "set-cookie" for key, _ in response_headers)
    delivered = {key.lower(): value for key, value in session.product_requests[-1][1].items()}
    assert delivered["x-omnia-user-id"] == "123"
    assert "cookie" not in delivered and "authorization" not in delivered
    assert session.request("/api/omnia/actions", headers=headers)[0] == 200
    core_cookie = http.cookies.SimpleCookie(session.core_requests[-1][1].get("Cookie", ""))
    assert core_cookie[CANONICAL].value == session.token
    assert EMBEDDED not in core_cookie and PARTITIONED not in core_cookie


@pytest.mark.parametrize("name", (EMBEDDED, PARTITIONED))
@pytest.mark.parametrize("token", [
    "forged", signed_session(secret="other-project"), signed_session("preview"),
    signed_session(expires=1),
])
def test_embedded_transports_cannot_bypass_identity_checks(session_boundary, name, token):
    session = session_boundary
    assert session.request("/__omnia/identity", headers={"Cookie": f"{name}={token}"})[0] == 401
    assert not session.core_requests and not session.product_requests


def test_preview_does_not_accept_public_cookie_aliases(session_boundary):
    session = session_boundary
    session.gateway.config["public_mode"] = False
    assert session.request("/__omnia/identity", headers={
        "Cookie": f"{EMBEDDED}={session.token}; {PARTITIONED}={session.token}",
    })[0] == 401


@pytest.mark.parametrize("origin", [
    None, "null", "https://evil.example.test", ORIGIN + ".evil.test",
])
@pytest.mark.parametrize("path", ["/api/max/session", "/api/records", "/api/omnia/actions"])
def test_unsafe_cross_origin_requests_never_reach_core_or_product(session_boundary, origin, path):
    session = session_boundary
    headers = {
        "Cookie": f"{EMBEDDED}={session.token}", "Host": "evil.example.test",
        "X-Forwarded-Host": "evil.example.test", "Sec-Fetch-Site": "same-origin",
    }
    if origin is not None:
        headers["Origin"] = origin
    assert session.request(path, method="POST", headers=headers)[0] == 403
    assert not session.core_requests and not session.product_requests


@pytest.mark.parametrize("method", ["POST", "PUT", "PATCH", "DELETE"])
def test_same_origin_mutations_work_with_embedded_cookie(session_boundary, method):
    session = session_boundary
    assert session.request("/api/records", method=method, headers={
        "Cookie": f"{PARTITIONED}={session.token}", "Origin": ORIGIN,
    })[0] == 200
    assert session.product_requests[-1][1]["X-Omnia-User-ID"] == "123"


def test_webhook_still_reaches_its_own_signature_verifier_without_browser_origin(session_boundary):
    session = session_boundary
    assert session.request("/api/max/webhook", method="POST")[0] == 200
    assert session.core_requests[-1][0] == "/api/max/webhook"


def test_missing_configured_origin_fails_closed_even_when_host_matches(session_boundary):
    session = session_boundary
    session.gateway.config.pop("public_origin")
    assert session.request("/api/max/session", method="POST", headers={
        "Origin": ORIGIN, "Host": "app.example.test",
    })[0] == 403
    assert not session.core_requests


def test_duplicate_origin_is_rejected(session_boundary):
    session = session_boundary
    connection = http.client.HTTPConnection("127.0.0.1", session.gateway.server_port, timeout=3)
    try:
        connection.putrequest("POST", "/api/max/session")
        connection.putheader("Origin", ORIGIN)
        connection.putheader("Origin", "https://evil.example.test")
        connection.putheader("Content-Length", "0")
        connection.endheaders()
        response = connection.getresponse()
        assert response.status == 403
        response.read()
        assert not session.core_requests
    finally:
        connection.close()


def test_public_documents_limit_embedding_to_max_and_self(session_boundary):
    session = session_boundary
    for headers in ({}, {"Cookie": f"{EMBEDDED}={session.token}"}):
        status, _, response_headers = session.request("/", headers=headers)
        assert status == 200
        policies = [value for key, value in response_headers
                    if key.lower() == "content-security-policy"]
        expected = "frame-ancestors 'self' https://web.max.ru https://max.ru"
        assert any(expected in p for p in policies)


def test_conflicting_identities_fail_closed_but_can_log_in_again(session_boundary):
    session = session_boundary
    headers = {"Cookie": f"{CANONICAL}={session.token}; {EMBEDDED}={signed_session('456')}"}
    assert session.request("/__omnia/identity", headers=headers)[0] == 401
    assert session.request("/api/records", headers=headers)[0] == 401
    assert session.request("/api/max/session", method="POST", headers={
        **headers, "Origin": ORIGIN,
    })[0] == 200


@pytest.mark.parametrize("status,cookie_value", [
    (401, f"{CANONICAL}={signed_session()}; Secure; Path=/; Max-Age=3600"),
    (200, f"{CANONICAL}=forged; Secure; Path=/; Max-Age=3600"),
    (200, f"{CANONICAL}={signed_session('preview')}; Secure; Path=/; Max-Age=3600"),
    (200, "unrelated=value; Secure; Path=/; Max-Age=3600"),
])
def test_failed_or_unverified_core_session_never_mints_compatibility_cookies(
    session_boundary, status, cookie_value,
):
    session = session_boundary
    session.core.status, session.core.cookies = status, [cookie_value]
    _, _, headers = session.request("/api/max/session", method="POST", headers={"Origin": ORIGIN})
    values = [value for key, value in headers if key.lower() == "set-cookie"]
    assert values == [cookie_value]


@pytest.mark.parametrize("embedded,allowed_cookies", [
    (False, {CANONICAL}), (True, {EMBEDDED}), (True, {PARTITIONED}), (True, set(COOKIE_NAMES)),
])
@pytest.mark.parametrize("launch_fragment", [
    "", "#WebAppData=disposable-test-launch&WebAppPlatform=web",
])
def test_browser_login_roundtrip_uses_real_cookie_transport(
    session_boundary, embedded, allowed_cookies, launch_fragment,
):
    """Optional browser gate: route HTTPS to real gateway; never inject cookie storage.

    Run with a local Playwright Chromium installation. The core, identity and
    product are isolated HTTP fixtures; every browser request is intercepted.
    Filtering response cookies exercises each browser-compatibility fallback.
    """
    playwright = pytest.importorskip("playwright.sync_api")
    session = session_boundary
    parent = "https://web.max.ru"
    launch_url = ORIGIN + "/" + launch_fragment
    statuses = []
    with playwright.sync_playwright() as p:
        browser = p.chromium.launch()
        try:
            context = browser.new_context(viewport={"width": 390, "height": 844})
            context.add_init_script("window.WebApp = {initData:'disposable-test-launch'}")

            def handle(route):
                url = route.request.url
                if url == parent + "/":
                    return route.fulfill(content_type="text/html", body=(
                        f'<iframe style="width:100%;height:800px" src="{launch_url}"></iframe>'
                    ))
                if not url.startswith(ORIGIN + "/"):
                    return route.abort()
                path = url[len(ORIGIN):]
                status, body, headers = session.request(
                    path, method=route.request.method, headers=route.request.all_headers(),
                    body=route.request.post_data_buffer,
                )
                statuses.append((path, status))
                response_headers = {}
                for key, value in headers:
                    key = key.lower()
                    if key == "set-cookie" and value.split("=", 1)[0] not in allowed_cookies:
                        continue
                    separator = "\n" if key == "set-cookie" else ", "
                    response_headers[key] = (response_headers[key] + separator + value
                                             if key in response_headers else value)
                route.fulfill(status=status, headers=response_headers, body=body)

            context.route("**/*", handle)
            page = context.new_page()
            page.goto(parent + "/" if embedded else launch_url)
            target = page.frame_locator("iframe") if embedded else page
            target.get_by_role("heading", name="Protected application").wait_for(timeout=15000)
            assert ("/api/max/session", 200) in statuses
            assert ("/__omnia/identity", 200) in statuses
            assert ("/__omnia/identity", 401) not in statuses
            assert session.product_requests[-1][1]["X-Omnia-User-ID"] == "123"
            app_frame = next(frame for frame in page.frames if frame.url.startswith(ORIGIN + "/"))
            assert app_frame.url == launch_url
            cookies = context.cookies()
            assert cookies and all(c["secure"] and c["httpOnly"] for c in cookies)
            assert all(c["name"] in allowed_cookies for c in cookies)
            context.close()
        finally:
            browser.close()


@pytest.mark.parametrize("delay_ms,has_timeout_api,should_succeed", [
    (19000, True, True), (0, False, True), (90000, True, False),
])
def test_browser_login_handles_cold_response_with_a_bounded_portable_timeout(
    session_boundary, delay_ms, has_timeout_api, should_succeed,
):
    playwright = pytest.importorskip("playwright.sync_api")
    session = session_boundary
    with playwright.sync_playwright() as p:
        browser = p.chromium.launch()
        try:
            context = browser.new_context(viewport={"width": 390, "height": 844})
            context.add_init_script("""
                window.WebApp = {initData:'disposable-test-launch'};
                const originalFetch = window.fetch;
                window.fetch = async (url, options) => {
                  if (url === '/api/max/session') {
                    await new Promise((resolve, reject) => {
                      const timer = setTimeout(resolve, DELAY);
                      options.signal.addEventListener('abort', () => {
                        clearTimeout(timer); reject(new Error('timed out'));
                      }, {once:true});
                    });
                  }
                  return originalFetch(url, options);
                };
            """.replace("DELAY", str(delay_ms)) + (
                "AbortSignal.timeout = undefined;" if not has_timeout_api else ""
            ))

            def handle(route):
                if not route.request.url.startswith(ORIGIN + "/"):
                    return route.abort()
                status, body, headers = session.request(
                    route.request.url[len(ORIGIN):], method=route.request.method,
                    headers=route.request.all_headers(), body=route.request.post_data_buffer,
                )
                response_headers = {}
                for key, value in headers:
                    key = key.lower()
                    separator = "\n" if key == "set-cookie" else ", "
                    response_headers[key] = (response_headers[key] + separator + value
                                             if key in response_headers else value)
                route.fulfill(status=status, headers=response_headers, body=body)

            context.route("**/*", handle)
            page = context.new_page()
            page.clock.install()
            page.goto(ORIGIN + "/")
            page.clock.fast_forward(19001 if should_succeed else 61000)
            if should_succeed:
                page.get_by_role("heading", name="Protected application").wait_for(timeout=5000)
                assert session.product_requests[-1][1]["X-Omnia-User-ID"] == "123"
            else:
                assert "Не удалось войти" in page.locator("#status").inner_text()
                assert page.get_by_role("button", name="Повторить").is_enabled()
                assert not session.product_requests
            context.close()
        finally:
            browser.close()

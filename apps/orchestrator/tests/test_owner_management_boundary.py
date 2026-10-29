"""Only two exact platform paths reach the product, with separate authorities."""

import http.client
import http.server
import threading
import time

import pytest

from tests.test_machine_boundary import cookie
from yleum_orchestrator.services.machine_boundary import BoundaryHandler, BoundaryServer


@pytest.fixture
def gateway():
    seen = []

    class Product(http.server.BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def do_GET(self):
            body = self.rfile.read(int(self.headers.get("Content-Length", "0")))
            seen.append((self.path, dict(self.headers), body))
            self.send_response(200)
            self.send_header("Cache-Control", "public, max-age=3600")
            self.end_headers()
            self.wfile.write(b"{}")

        do_POST = do_GET

    app = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Product)
    boundary = BoundaryServer(("127.0.0.1", 0), BoundaryHandler)
    boundary.config = {
        "secret": "synthetic-boundary",
        "project_id": "project-A",
        "epoch": 7,
        "machine_host": "127.0.0.1",
        "core_host": "127.0.0.1",
        "public_mode": True,
        "public_origin": "https://app.example",
        "routes": [{"path": "/", "port": app.server_port}],
    }
    servers = (app, boundary)
    threads = [threading.Thread(target=s.serve_forever, daemon=True) for s in servers]
    for thread in threads:
        thread.start()

    def request(path, *, method="GET", headers=None, body=None):
        connection = http.client.HTTPConnection("127.0.0.1", boundary.server_port, timeout=3)
        try:
            connection.request(method, path, body=body, headers=headers or {})
            response = connection.getresponse()
            return response.status, response.read(), dict(response.getheaders())
        finally:
            connection.close()

    yield boundary, request, seen
    for server in servers:
        server.shutdown()
        server.server_close()
    for thread in threads:
        thread.join(timeout=3)


def test_owner_rpc_reaches_only_runtime_verifier_without_customer_identity(gateway):
    _, request, seen = gateway
    status, _, headers = request(
        "/api/omnia/owner",
        method="POST",
        body=b'{"op":"describe"}',
        headers={
            "X-Yleum-Owner-Assertion": "synthetic-signed-capability",
            "X-Omnia-User-ID": "forged",
            "Cookie": "__Host-max_session=private",
            "Authorization": "Bearer private",
            "Content-Type": "application/json",
        },
    )
    assert status == 200
    path, forwarded, body = seen[0]
    assert path == "/api/omnia/owner" and body == b'{"op":"describe"}'
    assert forwarded["X-Yleum-Owner-Assertion"] == "synthetic-signed-capability"
    assert not any(k.lower().startswith("x-omnia-") for k in forwarded)
    assert "Cookie" not in forwarded and "Authorization" not in forwarded
    assert headers["Cache-Control"] == "private, no-store"


@pytest.mark.parametrize("method,assertion,expected", [("GET", "one", 405), ("POST", None, 401)])
def test_owner_has_no_customer_or_unsigned_fallback(gateway, method, assertion, expected):
    _, request, seen = gateway
    headers = {"X-Yleum-Owner-Assertion": assertion} if assertion else {}
    assert request("/api/omnia/owner", method=method, headers=headers)[0] == expected
    assert seen == []


def test_owner_rpc_is_absent_from_preview(gateway):
    server, request, seen = gateway
    server.config["public_mode"] = False
    assert (
        request("/api/omnia/owner", method="POST", headers={"X-Yleum-Owner-Assertion": "one"})[0]
        == 404
    )
    assert seen == []


def test_managed_business_uses_verified_max_actor_and_never_accepts_anonymous(gateway):
    _, request, seen = gateway
    assert request("/api/omnia/business/orders")[0] == 401
    value = cookie("synthetic-boundary", {"id": "42", "expiresAt": int(time.time()) + 60})
    status, _, headers = request(
        "/api/omnia/business/orders?limit=1",
        headers={
            "Cookie": f"__Host-max_session={value}",
            "X-Omnia-User-ID": "forged",
        },
    )
    assert status == 200
    assert seen[0][1]["X-Omnia-User-ID"] == "42"
    assert "Cookie" not in seen[0][1]
    assert headers["Cache-Control"] == "private, no-store"


def test_business_exception_never_opens_arbitrary_managed_paths(gateway):
    _, request, seen = gateway
    assert request("/api/omnia/business/private_diaries")[0] == 401
    assert request("/api/omnia/actions")[0] == 401
    assert seen == []

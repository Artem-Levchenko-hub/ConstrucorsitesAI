import base64
import hmac
import importlib
import importlib.util
import json
import time


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

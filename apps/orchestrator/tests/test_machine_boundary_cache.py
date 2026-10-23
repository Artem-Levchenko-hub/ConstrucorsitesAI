"""Personalized responses must not inherit generated code's HTTP cache policy."""
import base64
import hmac
import http.client
import http.server
import json
import threading
import time
from types import SimpleNamespace

import pytest

from yleum_orchestrator.services import machine_boundary as boundary

SECRET = "disposable-private-response-key"
ORIGIN = "https://app.example.test"


def signed_cookie(actor):
    payload = {"id": actor, "expiresAt": int(time.time()) + 3600}
    encoded = base64.urlsafe_b64encode(json.dumps(payload).encode()).decode().rstrip("=")
    signature = base64.urlsafe_b64encode(hmac.digest(SECRET.encode(), encoded.encode(), "sha256"))
    return "__Host-max_session=" + encoded + "." + signature.decode().rstrip("=")


@pytest.fixture
def cache_boundary(monkeypatch):
    state = SimpleNamespace(cache_headers=[], status=200, content_type="application/json")

    class Upstream(http.server.BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def do_GET(self):
            body = json.dumps({
                "actor": self.headers.get("X-Omnia-User-ID"), "private": True,
            }).encode()
            self.send_response(state.status)
            self.send_header("Content-Type", state.content_type)
            self.send_header("Vary", "RSC, Next-Router-State-Tree")
            self.send_header("ETag", '"retained-revision"')
            for name, value in state.cache_headers:
                self.send_header(name, value)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)

        do_HEAD = do_GET
        do_POST = do_GET

    upstream = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Upstream)
    gateway = boundary.BoundaryServer(("127.0.0.1", 0), boundary.BoundaryHandler)
    gateway.config = {"secret": SECRET, "public_mode": True, "public_origin": ORIGIN,
        "project_id": "private-cache-project", "epoch": 9,
        "machine_host": "127.0.0.1", "core_host": "127.0.0.1",
        "routes": [{"path": "/", "port": upstream.server_port}]}
    connection_type = http.client.HTTPConnection
    monkeypatch.setattr(
        boundary.http.client, "HTTPConnection", lambda host, port, **kwargs:
        connection_type(host, upstream.server_port if port == 3000 else port, **kwargs),
    )
    servers = (upstream, gateway)
    threads = [threading.Thread(target=server.serve_forever, daemon=True) for server in servers]
    for thread in threads:
        thread.start()

    def request(path, *, actor="101", method="GET"):
        connection = connection_type("127.0.0.1", gateway.server_port, timeout=3)
        try:
            headers = {"Origin": ORIGIN, **({"Cookie": signed_cookie(actor)} if actor else {})}
            connection.request(method, path, headers=headers)
            response = connection.getresponse()
            return response.status, response.read(), response.getheaders()
        finally:
            connection.close()

    try:
        yield state, gateway, request
    finally:
        for server in servers:
            server.shutdown()
            server.server_close()
        for thread in threads:
            thread.join(timeout=3)


def header_values(headers, name):
    return [value for key, value in headers if key.lower() == name.lower()]


@pytest.mark.parametrize("upstream_headers", [
    [], [("Cache-Control", "public, max-age=3600")],
    [("Cache-Control", "private"), ("cache-control", "public, s-maxage=3600"),
     ("CDN-Cache-Control", "public, max-age=3600"), ("Surrogate-Control", "max-age=3600")],
])
def test_private_api_has_one_authoritative_policy_for_both_actors(cache_boundary, upstream_headers):
    state, _, request = cache_boundary
    state.cache_headers = upstream_headers
    for actor in ("101", "202"):
        status, body, headers = request("/api/notes", actor=actor)
        assert status == 200 and json.loads(body)["actor"] == actor
        assert header_values(headers, "Cache-Control") == ["private, no-store"]
        assert not header_values(headers, "CDN-Cache-Control")
        assert not header_values(headers, "Surrogate-Control")
        assert header_values(headers, "Vary") == ["RSC, Next-Router-State-Tree"]
        assert header_values(headers, "ETag") == ['"retained-revision"']


@pytest.mark.parametrize("path,method,status,content_type", [
    ("/", "GET", 200, "text/html"),
    ("/orders?_rsc=123", "GET", 200, "text/x-component"),
    ("/assets/private-report.csv", "GET", 200, "text/csv"),
    ("/_next/image?url=/private-avatar", "GET", 200, "image/png"),
    ("/api/notes/missing", "GET", 404, "application/json"),
    ("/api/notes", "HEAD", 200, "application/json"),
    ("/api/omnia/actions", "GET", 200, "application/json"),
])
def test_all_personal_dynamic_responses_are_uncacheable(
    cache_boundary, path, method, status, content_type,
):
    state, _, request = cache_boundary
    state.status, state.content_type = status, content_type
    actual, body, headers = request(path, method=method)
    assert actual == status
    assert not body if method == "HEAD" else body
    assert header_values(headers, "Cache-Control") == ["private, no-store"]


@pytest.mark.parametrize("path", ["/api/max/session", "/api/omnia/preview-session"])
def test_session_bootstrap_is_not_cached_without_an_existing_cookie(cache_boundary, path):
    _, gateway, request = cache_boundary
    gateway.config["public_mode"] = path != "/api/omnia/preview-session"
    status, _, headers = request(path, actor=None, method="POST")
    assert status == 200
    assert header_values(headers, "Cache-Control") == ["private, no-store"]


def test_trusted_core_static_cache_policy_is_preserved(cache_boundary):
    state, _, request = cache_boundary
    state.cache_headers = [("Cache-Control", "public, max-age=31536000, immutable")]
    status, _, headers = request("/api/omnia/core-assets/static/build/main.js")
    assert status == 200
    assert header_values(headers, "Cache-Control") == ["public, max-age=31536000, immutable"]


def test_unauthenticated_product_still_denied(cache_boundary):
    _, _, request = cache_boundary
    status, _, headers = request("/api/notes", actor=None)
    assert status == 401
    assert header_values(headers, "Cache-Control") == ["no-store"]


def test_generated_static_looking_path_cannot_cache_personal_rewrite(cache_boundary):
    state, _, request = cache_boundary
    state.cache_headers = [("Cache-Control", "public, max-age=3600")]
    for actor in ("101", "202"):
        status, body, headers = request("/_next/static/rewrite-target.json", actor=actor)
        assert status == 200 and json.loads(body)["actor"] == actor
        assert header_values(headers, "Cache-Control") == ["private, no-store"]

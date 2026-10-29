"""The product reaches a fixed receipt relay, never general platform/core access."""

import http.client
import json
import threading
from uuid import uuid4

import pytest

from yleum_orchestrator.services import machine_egress as egress


def test_real_allowed_relay_hop_and_negative_destinations(monkeypatch):
    delivered = []

    def deliver(origin, project, body, assertion):
        delivered.append((origin, project, json.loads(body), assertion))
        return 204

    monkeypatch.setattr(egress, "deliver_business_receipt", deliver)
    with egress._Proxy(("127.0.0.1", 0), egress._Handler) as server:
        server.denied_cidrs = []
        server.analytics_project = str(uuid4())
        server.analytics_origin = "https://platform.example.test"
        server.relay_only = True
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:

            def request(method, path, data, **headers):
                client = http.client.HTTPConnection(*server.server_address, timeout=3)
                client.request(
                    method,
                    path,
                    data,
                    {"X-Yleum-Business-Assertion": "v1.fixture.signature", **headers},
                )
                response = client.getresponse()
                status = response.status
                response.read()
                client.close()
                return status

            receipt = json.dumps(
                {"event_id": str(uuid4()), "kind": "action", "event_type": "order_created"}
            )
            assert request("POST", "/__yleum/business-analytics", receipt) == 204
            assert delivered == [
                (
                    "https://platform.example.test",
                    server.analytics_project,
                    json.loads(receipt),
                    "v1.fixture.signature",
                )
            ]
            for method, path, data in [
                ("CONNECT", "platform.example.test:443", ""),
                ("POST", "https://platform.example.test/api/admin", receipt),
                ("GET", "/__yleum/business-analytics", ""),
                ("POST", "/__yleum/business-analytics?url=https://evil.test", receipt),
                (
                    "POST",
                    "/__yleum/business-analytics",
                    receipt[:-1] + ',"url":"https://evil.test"}',
                ),
                (
                    "POST",
                    "/__yleum/business-analytics",
                    receipt.replace("order_created", "message_sent"),
                ),
                ("POST", "/__yleum/business-analytics", "x" * 4097),
            ]:
                assert request(method, path, data) == 403
            assert len(delivered) == 1
        finally:
            server.shutdown()
            thread.join(timeout=3)


def test_relay_origin_rejects_non_https_private_credentials_paths_and_queries():
    for origin in [
        "http://platform.test",
        "https://127.0.0.1",
        "https://user@platform.test",
        "https://platform.test/admin",
        "https://platform.test?target=x",
    ]:
        with pytest.raises(egress.DestinationDenied):
            egress.business_relay_origin(origin)
    assert egress.business_relay_origin("https://platform.test/") == ("platform.test", 443)


def test_fixed_upstream_pins_public_dns_verifies_tls_and_never_follows_redirects(monkeypatch):
    project = str(uuid4())
    body = json.dumps(
        {"event_id": str(uuid4()), "kind": "action", "event_type": "lead_created"}
    ).encode()
    calls = []

    class Socket:
        def close(self):
            calls.append(("close_socket",))

    raw = Socket()
    tls = Socket()

    class Context:
        def wrap_socket(self, value, *, server_hostname):
            assert value is raw
            calls.append(("tls", server_hostname))
            return tls

    class Connection:
        def __init__(self, host, port, *, timeout):
            calls.append(("https", host, port, timeout))
            self.sock = None

        def request(self, method, path, payload, headers):
            assert self.sock is tls
            calls.append(("request", method, path, payload, headers))

        def getresponse(self):
            return type("Redirect", (), {"status": 302})()

        def close(self):
            calls.append(("close_connection",))

    monkeypatch.setattr(
        egress.socket,
        "getaddrinfo",
        lambda *args, **kwargs: [(2, 1, 6, "", ("93.184.216.34", 443))],
    )

    def connect(address, *, timeout):
        calls.append(("connect", address, timeout))
        return raw

    monkeypatch.setattr(egress.socket, "create_connection", connect)
    monkeypatch.setattr(egress.ssl, "create_default_context", Context)
    monkeypatch.setattr(egress.http.client, "HTTPSConnection", Connection)
    assert (
        egress.deliver_business_receipt("https://platform.example.test", project, body, "signed")
        == 503
    )
    assert ("connect", ("93.184.216.34", 443), 2) in calls
    assert ("tls", "platform.example.test") in calls
    assert [entry for entry in calls if entry[0] == "request"] == [
        (
            "request",
            "POST",
            f"/api/runtime/projects/{project}/analytics",
            body,
            {
                "Content-Type": "application/json",
                "X-Yleum-Business-Assertion": "signed",
                "Connection": "close",
            },
        )
    ]
    calls.clear()
    monkeypatch.setattr(
        egress.socket, "getaddrinfo", lambda *args, **kwargs: [(2, 1, 6, "", ("127.0.0.1", 443))]
    )
    with pytest.raises(egress.DestinationDenied):
        egress.deliver_business_receipt("https://platform.example.test", project, body, "signed")
    assert calls == []

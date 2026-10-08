import http.client
import http.server
import threading
import time

import pytest

from tests.test_machine_boundary import cookie, module


@pytest.mark.parametrize("origin", [None, "", "http://app.example", "https://app.example/path",
                                    "https://app.example\nX-Injected: yes", ["https://app.example"]])
def test_invalid_controller_origin_cannot_supply_forwarding_headers(origin):
    headers = module().product_headers(
        {"Host": "forged.example", "X-Forwarded-Host": "forged.example"},
        project_id="coffee", epoch=7, user={"id": "123"}, origin=origin,
    )
    assert not ({"host", "x-forwarded-host", "x-forwarded-proto"}
                & {key.casefold() for key in headers})


@pytest.mark.parametrize("public", [False, True])
def test_product_actions_receive_controller_host_not_internal_or_forged_host(public):
    boundary = module()
    received = []

    class Product(http.server.BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            received.append(dict(self.headers))
            self.send_response(200)
            self.end_headers()

    product = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Product)
    gateway = boundary.BoundaryServer(("127.0.0.1", 0), boundary.BoundaryHandler)
    origin = "https://coffee.apps.example" if public else "https://coffee-dev.dev.example"
    gateway.config = {
        "secret": "test-only", "project_id": "coffee", "epoch": 7,
        "machine_host": "127.0.0.1", "core_host": "127.0.0.1",
        "routes": [{"path": "/", "port": product.server_port}],
        "public_mode": public,
        "public_origin" if public else "preview_origin": origin,
    }
    threads = [threading.Thread(target=s.serve_forever, daemon=True) for s in (product, gateway)]
    for thread in threads:
        thread.start()
    signed = cookie("test-only", {"id": "123", "expiresAt": int(time.time()) + 60})
    try:
        connection = http.client.HTTPConnection("127.0.0.1", gateway.server_port, timeout=3)
        connection.request("POST", "/", headers={
            "Cookie": "__Host-max_session=" + signed,
            "Host": "forged.example", "Origin": origin,
            "X-Forwarded-Host": "forged.example", "X-Forwarded-Proto": "http",
            "Forwarded": "host=forged.example",
        })
        response = connection.getresponse()
        assert response.status == 200
        response.read()
        connection.close()
        assert received[0]["Host"] == origin.removeprefix("https://")
        assert received[0]["X-Forwarded-Host"] == received[0]["Host"]
        assert received[0]["X-Forwarded-Proto"] == "https"
        assert received[0]["Origin"] == origin
        assert "Cookie" not in received[0] and "Forwarded" not in received[0]

        connection = http.client.HTTPConnection("127.0.0.1", gateway.server_port, timeout=3)
        connection.request("POST", "/", headers={
            "Cookie": "__Host-max_session=" + signed,
            "Origin": "https://attacker.example",
            "X-Forwarded-Host": "attacker.example",
        })
        response = connection.getresponse()
        assert response.status == (403 if public else 200)
        response.read()
        connection.close()
        if public:
            assert len(received) == 1
        else:
            assert received[1]["Origin"] == "https://attacker.example"
            assert received[1]["Host"] == origin.removeprefix("https://")
    finally:
        for server in (gateway, product):
            server.shutdown()
            server.server_close()
        for thread in threads:
            thread.join(timeout=3)

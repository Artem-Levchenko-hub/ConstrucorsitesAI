from types import SimpleNamespace

import pytest

from yleum_api.services import max_behavior_browser as browser


@pytest.mark.parametrize("text,secrets", [
    ("Authorization: Basic Zml4dHVyZTpmaXh0dXJl", ("Zml4dHVyZTpmaXh0dXJl",)),
    ('{"access_token":"access-fixture","refresh_token":"refresh-fixture"}',
     ("access-fixture", "refresh-fixture")),
    ("Cookie: session=fixture-session; auth=fixture-auth", ("fixture-session", "fixture-auth")),
    ("Set-Cookie: auth=fixture-auth; Path=/; HttpOnly", ("fixture-auth",)),
    ("HTTPS://fixture-user:fixture-pass@example.org/x?token=fixture-query",
     ("fixture-user", "fixture-pass", "fixture-query")),
])
def test_standard_credentials_never_survive_error_capture(text, secrets):
    collector = browser.BrowserFailureCollector()
    collector.record("pageerror", text=text, stack=text)
    assert all(secret not in str(collector.events) for secret in secrets)


class Page:
    def __init__(self):
        self.handlers = {}

    def on(self, event, handler):
        self.handlers[event] = handler


def test_independent_errors_survive_missing_inspector_and_redact_secrets():
    page = Page()
    collector = browser.BrowserFailureCollector()
    collector.attach(page)
    page.handlers["console"](
        SimpleNamespace(
            type="error",
            text="failed token=secret123",
            location={
                "url": "https://user:password@example.org/app.js?token=abc#secret",
                "lineNumber": 4,
            },
        )
    )
    page.handlers["pageerror"](
        SimpleNamespace(
            message="TypeError: missing item", stack="at https://example.org/app.js?key=secret:4"
        )
    )
    page.handlers["requestfailed"](
        SimpleNamespace(
            url="https://example.org/_omnia/inspector.js?token=abc",
            failure="net::ERR_CONNECTION_REFUSED",
        )
    )
    page.handlers["response"](
        SimpleNamespace(url="https://example.org/_omnia/inspector.js", status=502)
    )
    assert len(collector.events) == 4
    assert collector.events[1]["text"] == "TypeError: missing item"
    assert collector.events[2]["text"] == "net::ERR_CONNECTION_REFUSED"
    assert collector.events[3]["status"] == 502
    encoded = str(collector.events)
    for secret in ("secret123", "password", "token=abc", "key=secret", "#secret"):
        assert secret not in encoded
    collector.attach(Page())
    assert len(collector.events) == 4  # no implicit history clearing on reload/new page


def test_private_known_values_redacted_and_overflow_explicit():
    collector = browser.BrowserFailureCollector(secret_values=("opaque-user-supplied-value",))
    for _ in range(130):
        collector.record("pageerror", text="error opaque-user-supplied-value")
    assert len(collector.events) == 128
    assert collector.overflow is True
    assert "opaque-user-supplied-value" not in str(collector.events)


def test_authorization_and_quoted_credentials_are_redacted():
    collector = browser.BrowserFailureCollector(secret_values=("known-private-path",))
    collector.record(
        "console_error",
        text='Authorization: Bearer bearer-secret {"token":"json-secret"}',
        url="https://example.org/known-private-path",
    )
    encoded = str(collector.events)
    for secret in ("bearer-secret", "json-secret", "known-private-path"):
        assert secret not in encoded


def test_real_browser_capture_does_not_require_inspector():
    """Root runs this only in its approved isolated browser fixture runtime."""
    import os
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    import pytest

    if os.environ.get("OMNIA_TEST_INSPECTOR_BROWSER") != "1":
        pytest.skip("requires root-approved isolated browser runtime")
    from playwright.sync_api import sync_playwright

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path == "/":
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b"""<script src="/_omnia/inspector.js"></script>
                <script>console.error('independent console failure');
                setTimeout(()=>{throw new Error('independent exception')},0);
                Promise.reject(new Error('independent rejection'));
                fetch('/failed-fetch').catch(()=>{});</script>""")
            elif self.path == "/failed-fetch":
                self.connection.close()
            else:
                self.send_response(502)
                self.end_headers()

        def log_message(self, *_args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    collector = browser.BrowserFailureCollector()
    try:
        with sync_playwright() as playwright:
            chromium = playwright.chromium.launch(headless=True, args=["--no-sandbox"])
            try:
                page = chromium.new_page()
                collector.attach(page)
                page.goto(f"http://127.0.0.1:{server.server_port}/", wait_until="networkidle")
                first = len(collector.events)
                page.reload(wait_until="networkidle")
                assert len(collector.events) > first
                assert not collector.overflow
                assert any(
                    row["kind"] == "failed_response" and row["status"] == 502
                    for row in collector.events
                )
                assert any(row["kind"] == "requestfailed" for row in collector.events)
                assert any(row["text"] == "independent console failure" for row in collector.events)
                for text in ("independent exception", "independent rejection"):
                    assert any(
                        text in str(row["text"]) and row["stack"] for row in collector.events
                    )
            finally:
                chromium.close()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)

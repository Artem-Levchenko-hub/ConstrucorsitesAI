"""Auth.js seed-login helper tests (Area C, DARK).

Two layers:
  * pure derivation — :func:`derive_seed_password` is deterministic and matches
    the Node ``init-db.mjs`` reference byte-for-byte (a refactor on either side
    that breaks the seed login fails here).
  * fake-browser — a stub ``async_playwright`` drives the CSRF → credentials
    callback sequence so :func:`establish_session` is exercised with no real
    Chromium: a login that lands a ``*session-token*`` cookie returns the
    storage_state dict; no cookie → ``None``; a render error → ``None``
    (fail-soft, R-10).
"""

from __future__ import annotations

import pytest

from omnia_api.services.auth_session import derive_seed_password, establish_session


# ── pure derivation ─────────────────────────────────────────────────────────


def test_derive_seed_password_deterministic() -> None:
    """Same secret → same 24-char urlsafe password; different secret → different."""
    a = derive_seed_password("secret")
    assert a == derive_seed_password("secret")
    assert len(a) == 24
    # urlsafe-b64 alphabet, no padding
    assert "=" not in a
    assert all(c.isalnum() or c in "-_" for c in a)
    assert derive_seed_password("other-secret") != a


def test_derive_seed_password_matches_node_reference() -> None:
    """Byte-for-byte match with the template's init-db.mjs Node derivation:
    ``crypto.createHmac('sha256','secret').update('omnia-gate-seed-v1')
    .digest('base64url').slice(0,24)`` → this fixed string. Pins the cross-
    language contract so a Python or template change can't silently desync the
    seed login."""
    assert derive_seed_password("secret") == "Vmvw0PrVLJdPcy3OLs7WMlbk"


# ── fake-browser plumbing ───────────────────────────────────────────────────


class _FakeResponse:
    def __init__(self, payload: dict) -> None:
        self._payload = payload

    async def json(self) -> dict:
        return self._payload


class _FakeRequest:
    def __init__(self, csrf_payload: dict, *, post_raises: bool = False) -> None:
        self._csrf_payload = csrf_payload
        self._post_raises = post_raises
        self.posted: dict | None = None

    async def get(self, url: str, *, timeout: int) -> _FakeResponse:
        return _FakeResponse(self._csrf_payload)

    async def post(self, url: str, *, form: dict, timeout: int) -> _FakeResponse:
        if self._post_raises:
            raise RuntimeError("callback boom")
        self.posted = form
        return _FakeResponse({})


class _FakeContext:
    def __init__(self, request: _FakeRequest, state: dict) -> None:
        self.request = request
        self._state = state

    async def storage_state(self) -> dict:
        return self._state

    async def close(self) -> None:
        return None


class _FakeBrowser:
    def __init__(self, context: _FakeContext) -> None:
        self._context = context

    async def new_context(self) -> _FakeContext:
        return self._context

    async def close(self) -> None:
        return None


class _FakeChromium:
    def __init__(self, browser: _FakeBrowser) -> None:
        self._browser = browser

    async def launch(self, *, headless: bool) -> _FakeBrowser:
        return self._browser


class _FakePlaywright:
    def __init__(self, chromium: _FakeChromium) -> None:
        self.chromium = chromium


class _FakeAsyncPlaywrightCM:
    """Async-context-manager mirror of ``async_playwright()``."""

    def __init__(self, playwright: _FakePlaywright) -> None:
        self._playwright = playwright

    async def __aenter__(self) -> _FakePlaywright:
        return self._playwright

    async def __aexit__(self, *exc: object) -> bool:
        return False


def _install_fake_playwright(
    monkeypatch: pytest.MonkeyPatch,
    *,
    csrf_payload: dict,
    storage_state: dict,
    post_raises: bool = False,
) -> _FakeRequest:
    request = _FakeRequest(csrf_payload, post_raises=post_raises)
    context = _FakeContext(request, storage_state)
    cm = _FakeAsyncPlaywrightCM(_FakePlaywright(_FakeChromium(_FakeBrowser(context))))
    # establish_session imports `from playwright.async_api import async_playwright`
    # inside the function, so patch the symbol on that module.
    import playwright.async_api as pw_async

    monkeypatch.setattr(pw_async, "async_playwright", lambda: cm)
    return request


# ── establish_session ───────────────────────────────────────────────────────


async def test_establish_session_success_returns_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A login that lands an ``authjs.session-token`` cookie returns the
    storage_state dict, and the credentials callback was driven with the
    derived password + the seeded email."""
    state = {"cookies": [{"name": "authjs.session-token", "value": "jwe"}]}
    request = _install_fake_playwright(
        monkeypatch, csrf_payload={"csrfToken": "tok"}, storage_state=state
    )

    result = await establish_session(
        "http://app:3000/", "gate@omnia.local", "secret"
    )

    assert result == state
    assert request.posted is not None
    assert request.posted["csrfToken"] == "tok"
    assert request.posted["email"] == "gate@omnia.local"
    assert request.posted["password"] == derive_seed_password("secret")
    assert request.posted["callbackUrl"] == "http://app:3000/dashboard"


async def test_establish_session_secure_prefix_cookie_returns_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The ``__Secure-`` prefixed v5 cookie (https) is also accepted."""
    state = {"cookies": [{"name": "__Secure-authjs.session-token", "value": "x"}]}
    _install_fake_playwright(
        monkeypatch, csrf_payload={"csrfToken": "tok"}, storage_state=state
    )
    result = await establish_session("http://app:3000", "gate@omnia.local", "s")
    assert result == state


async def test_establish_session_no_cookie_returns_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No session cookie after the callback (bad creds / login rejected) → None."""
    state = {"cookies": [{"name": "authjs.csrf-token", "value": "x"}]}
    _install_fake_playwright(
        monkeypatch, csrf_payload={"csrfToken": "tok"}, storage_state=state
    )
    result = await establish_session("http://app:3000", "gate@omnia.local", "s")
    assert result is None


async def test_establish_session_no_csrf_returns_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A CSRF endpoint that yields no token → None (never posts the callback)."""
    request = _install_fake_playwright(
        monkeypatch, csrf_payload={}, storage_state={"cookies": []}
    )
    result = await establish_session("http://app:3000", "gate@omnia.local", "s")
    assert result is None
    assert request.posted is None


async def test_establish_session_render_error_returns_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Playwright raising anywhere in the flow → None (fail-soft, R-10)."""
    _install_fake_playwright(
        monkeypatch,
        csrf_payload={"csrfToken": "tok"},
        storage_state={"cookies": []},
        post_raises=True,
    )
    result = await establish_session("http://app:3000", "gate@omnia.local", "s")
    assert result is None


async def test_establish_session_playwright_import_failure_returns_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If async_playwright() itself blows up, the helper abstains rather than
    propagating (the worker must never crash on a login attempt)."""
    import playwright.async_api as pw_async

    def _boom() -> object:
        raise RuntimeError("no browser binary")

    monkeypatch.setattr(pw_async, "async_playwright", _boom)
    result = await establish_session("http://app:3000", "gate@omnia.local", "s")
    assert result is None

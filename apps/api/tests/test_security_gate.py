"""Unit tests for the security gate's pure surface assertions (G005)."""

from __future__ import annotations

import pytest

from yleum_api.services import security_gate
from yleum_api.services.security_gate import (
    PUBLIC_MAX_FRAMING_POLICY,
    SecCheck,
    assert_cors_safe,
    assert_payload_cap,
    assert_security_headers,
    owner_preview_framing_policy,
    summarize,
    surface_verdict_from_headers,
)

OWNER_PREVIEW_FRAMING_POLICY = owner_preview_framing_policy()


def test_headers_present_pass() -> None:
    checks = assert_security_headers(
        {
            "X-Content-Type-Options": "nosniff",
            "Content-Security-Policy": OWNER_PREVIEW_FRAMING_POLICY,
        },
        require_embedded_framing=True,
    )
    assert all(c.ok for c in checks)


def test_missing_nosniff_fails() -> None:
    checks = assert_security_headers(
        {"Content-Security-Policy": OWNER_PREVIEW_FRAMING_POLICY},
        require_embedded_framing=True,
    )
    assert any(not c.ok and "nosniff" in c.name for c in checks)


def test_header_lookup_is_case_insensitive() -> None:
    checks = assert_security_headers(
        {
            "x-content-type-options": "nosniff",
            "content-security-policy": OWNER_PREVIEW_FRAMING_POLICY,
        },
        require_embedded_framing=True,
    )
    assert all(c.ok for c in checks)


@pytest.mark.parametrize(
    "value",
    [
        f"{OWNER_PREVIEW_FRAMING_POLICY}, frame-ancestors 'none'",
        f"frame-ancestors 'none', {OWNER_PREVIEW_FRAMING_POLICY}",
    ],
)
def test_conflicting_effective_csp_policies_fail(value: str) -> None:
    checks = assert_security_headers(
        {
            "x-content-type-options": "nosniff",
            "content-security-policy": value,
        },
        require_embedded_framing=True,
    )

    assert any("frame-ancestors" in check.name and not check.ok for check in checks)


def test_first_same_named_directive_is_effective_within_one_policy() -> None:
    allowed_first = assert_security_headers(
        {
            "x-content-type-options": "nosniff",
            "content-security-policy": (
                f"{OWNER_PREVIEW_FRAMING_POLICY}; frame-ancestors 'none'"
            ),
        },
        require_embedded_framing=True,
    )
    blocked_first = assert_security_headers(
        {
            "x-content-type-options": "nosniff",
            "content-security-policy": (
                f"frame-ancestors 'none'; {OWNER_PREVIEW_FRAMING_POLICY}"
            ),
        },
        require_embedded_framing=True,
    )

    assert all(check.ok for check in allowed_first)
    assert any(
        "frame-ancestors" in check.name and not check.ok for check in blocked_first
    )


def test_wildcard_cors_with_credentials_is_a_leak() -> None:
    bad = assert_cors_safe(
        {"Access-Control-Allow-Origin": "*", "Access-Control-Allow-Credentials": "true"}
    )
    assert bad.ok is False


def test_same_origin_cors_is_safe() -> None:
    # No ACAO header (same-origin) — safe.
    assert assert_cors_safe({}).ok is True
    # Explicit origin + credentials — safe.
    ok = assert_cors_safe(
        {
            "Access-Control-Allow-Origin": "https://app.example.com",
            "Access-Control-Allow-Credentials": "true",
        }
    )
    assert ok.ok is True


def test_payload_cap_enforced() -> None:
    assert assert_payload_cap(413).ok is True
    assert assert_payload_cap(200).ok is False  # accepted an over-cap body = bug


def test_summary_combines_and_fails_on_any() -> None:
    leak = [SecCheck("outsider denied messages (403)", True)]
    surface = [SecCheck("X-Content-Type-Options: nosniff", False, "None")]
    verdict = summarize(leak, surface)
    assert verdict.passed is False
    assert "nosniff" in verdict.summary

    all_ok = summarize([SecCheck("outsider denied", True)], [SecCheck("headers", True)])
    assert all_ok.passed is True


def test_no_checks_is_not_a_pass() -> None:
    assert summarize([], []).passed is False


# ── surface_verdict_from_headers (the WIRED blocking verdict) ────────────────


def test_surface_passes_on_template_headers() -> None:
    # What the templates actually serve: nosniff + Referrer-Policy, NO X-Frame.
    v = surface_verdict_from_headers(
        {"x-content-type-options": "nosniff", "referrer-policy": "no-referrer"}
    )
    assert v.passed is True  # absence of X-Frame-Options must NOT block (preview iframe)


def test_surface_does_not_block_on_missing_xframe() -> None:
    # The crux: a good app embedded in the preview has no X-Frame-Options — and the
    # gate must still pass, or it would false-block every single build.
    v = surface_verdict_from_headers({"x-content-type-options": "nosniff"})
    assert v.passed is True
    assert all("X-Frame" not in c.name for c in v.checks)


def test_embedded_surface_requires_current_frame_ancestors_policy() -> None:
    good_headers = {
        "x-content-type-options": "nosniff",
        "content-security-policy": OWNER_PREVIEW_FRAMING_POLICY,
    }
    good = surface_verdict_from_headers(
        good_headers,
        require_embedded_framing=True,
        protected_status=200,
        document_headers=good_headers,
        document_status=200,
    )
    missing = surface_verdict_from_headers(
        {"x-content-type-options": "nosniff"},
        require_embedded_framing=True,
        protected_status=200,
        document_headers={"x-content-type-options": "nosniff"},
        document_status=200,
    )
    stale_headers = {
        "x-content-type-options": "nosniff",
        "content-security-policy": "frame-ancestors 'none'",
    }
    stale = surface_verdict_from_headers(
        stale_headers,
        require_embedded_framing=True,
        protected_status=200,
        document_headers=stale_headers,
        document_status=200,
    )

    assert good.passed is True
    assert missing.passed is False
    assert stale.passed is False
    assert any("frame-ancestors" in check.name for check in good.checks)


def test_embedded_surface_requires_authenticated_protected_response() -> None:
    headers = {
        "x-content-type-options": "nosniff",
        "content-security-policy": OWNER_PREVIEW_FRAMING_POLICY,
    }
    verdict = surface_verdict_from_headers(
        headers,
        require_embedded_framing=True,
        protected_status=401,
        document_headers=headers,
        document_status=200,
    )

    assert verdict.passed is False
    assert any("protected" in check.name and not check.ok for check in verdict.checks)


@pytest.mark.parametrize(
    ("document_csp", "passed"),
    [
        (OWNER_PREVIEW_FRAMING_POLICY, True),
        (f"{OWNER_PREVIEW_FRAMING_POLICY}, frame-ancestors 'none'", False),
    ],
)
async def test_signed_embedded_gate_checks_protected_api_and_final_document(
    monkeypatch,
    document_csp: str,
    passed: bool,
) -> None:
    import playwright.async_api

    from yleum_api.services import auth_session

    base_url = "https://cell-123-dev.preview.test"
    bootstrap_url = (
        f"{base_url}/api/omnia/preview-session?expires=4102444800&signature=" + "a" * 64
    )
    calls: list[tuple[str, str]] = []

    class Page:
        async def evaluate(self, _script, probe):
            assert probe["includeDocument"] is True
            calls.append(("fetch", probe["path"]))
            return {
                "protected": {
                    "status": 200,
                    "headers": {"x-content-type-options": "nosniff"},
                },
                "document": {
                    "status": 200,
                    "url": f"{base_url}/product",
                    "headers": {
                        "x-content-type-options": "nosniff",
                        "content-security-policy": document_csp,
                    },
                },
            }

    class Context:
        async def new_page(self):
            return Page()

    class Browser:
        async def new_context(self):
            return Context()

        async def close(self):
            return None

    class Chromium:
        async def launch(self, **_kwargs):
            return Browser()

    class Playwright:
        chromium = Chromium()

    class PlaywrightContext:
        async def __aenter__(self):
            return Playwright()

        async def __aexit__(self, *_args):
            return False

    async def settle(_page, url, *, timeout_ms):
        assert timeout_ms == 30_000
        calls.append(("navigate", url))

    monkeypatch.setattr(playwright.async_api, "async_playwright", PlaywrightContext)
    monkeypatch.setattr(security_gate, "goto_and_settle", settle)
    monkeypatch.setattr(auth_session, "preview_resolver_args", lambda: [])

    verdict = await security_gate.run_security_gate(
        base_url,
        bootstrap_url=bootstrap_url,
        require_embedded_framing=True,
        framing_policy=OWNER_PREVIEW_FRAMING_POLICY,
    )

    assert verdict.passed is passed
    assert calls == [
        ("navigate", bootstrap_url),
        ("fetch", "/api/omnia/actions?limit=1"),
    ]


def test_owner_and_public_framing_policies_are_distinct() -> None:
    # Превью владельца встраивает кабинет, публичное — клиенты MAX. Адрес
    # кабинета берётся из настройки, поэтому проверяем разделение, а не домен.
    assert OWNER_PREVIEW_FRAMING_POLICY.startswith("frame-ancestors 'self' ")
    assert "web.max.ru" not in OWNER_PREVIEW_FRAMING_POLICY
    assert "web.max.ru" in PUBLIC_MAX_FRAMING_POLICY
    assert OWNER_PREVIEW_FRAMING_POLICY != PUBLIC_MAX_FRAMING_POLICY


def test_surface_blocks_on_missing_nosniff() -> None:
    v = surface_verdict_from_headers({"referrer-policy": "no-referrer"})
    assert v.passed is False
    assert any("nosniff" in c.name and not c.ok for c in v.checks)


def test_surface_blocks_on_wildcard_cors_with_credentials() -> None:
    v = surface_verdict_from_headers(
        {
            "x-content-type-options": "nosniff",
            "access-control-allow-origin": "*",
            "access-control-allow-credentials": "true",
        }
    )
    assert v.passed is False
    assert any("CORS" in c.name and not c.ok for c in v.checks)


def test_expected_framing_follows_the_cabinet_address(monkeypatch) -> None:
    """Адрес кабинета сменился при переименовании — ожидание должно ехать за ним.

    Вшитый адрес однажды уже привёл к тому, что браузер перестал показывать
    превью владельцу, а проверка этого не заметила: обе стороны сверялись с
    одним и тем же устаревшим значением.
    """
    from yleum_api.core.config import get_settings

    settings = get_settings()
    monkeypatch.setattr(
        "yleum_api.services.security_gate.get_settings",
        lambda: settings.model_copy(update={"web_base_url": "https://yleum.ru/"}),
    )
    assert security_gate.owner_preview_framing_policy() == (
        "frame-ancestors 'self' https://yleum.ru"
    )


def test_extra_allowed_cabinet_does_not_fail_the_check(monkeypatch) -> None:
    """Превью разрешает и запасной кабинет: проверке достаточно нужного адреса."""
    from yleum_api.core.config import get_settings

    settings = get_settings()
    monkeypatch.setattr(
        "yleum_api.services.security_gate.get_settings",
        lambda: settings.model_copy(update={"web_base_url": "https://yleum.ru"}),
    )
    served = (
        "frame-ancestors 'self' https://yleum.ru https://constructor.lead-generator.ru"
    )
    checks = assert_security_headers(
        {"x-content-type-options": "nosniff", "content-security-policy": served},
        require_embedded_framing=True,
    )
    assert all(check.ok for check in checks)

    without_cabinet = assert_security_headers(
        {
            "x-content-type-options": "nosniff",
            "content-security-policy": (
                "frame-ancestors 'self' https://constructor.lead-generator.ru"
            ),
        },
        require_embedded_framing=True,
    )
    assert any(
        "frame-ancestors" in check.name and not check.ok for check in without_cabinet
    )

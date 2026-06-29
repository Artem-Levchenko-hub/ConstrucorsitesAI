"""Unit tests for the security gate's pure surface assertions (G005)."""

from __future__ import annotations

from omnia_api.services.security_gate import (
    SecCheck,
    assert_cors_safe,
    assert_payload_cap,
    assert_security_headers,
    summarize,
    surface_verdict_from_headers,
)


def test_headers_present_pass() -> None:
    checks = assert_security_headers(
        {"X-Content-Type-Options": "nosniff", "X-Frame-Options": "DENY"}
    )
    assert all(c.ok for c in checks)


def test_missing_nosniff_fails() -> None:
    checks = assert_security_headers({"X-Frame-Options": "DENY"})
    assert any(not c.ok and "nosniff" in c.name for c in checks)


def test_header_lookup_is_case_insensitive() -> None:
    checks = assert_security_headers(
        {"x-content-type-options": "nosniff", "x-frame-options": "DENY"}
    )
    assert all(c.ok for c in checks)


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

"""V1.6 16/5d — route-targeting: gate the WOW/content surface, not login `/`.

Covers the pure decision (``pick_route`` over the 5 route classes + adversarial
baseline) and the async orchestration (``resolve_target_route`` probes ``/`` and
only the candidate; fail-soft to ``/``). The browser is never launched — the
``_probe`` render is monkeypatched, exactly the JS-extracts / Python-scores split
the gates use.
"""

from __future__ import annotations

from omnia_api.services import route_target
from omnia_api.services.route_target import pick_route, resolve_target_route

# ── observation builders (the shape taste_gate._AUDIT_JS emits) ───────────────


def _obs(*, texts: int, has_password: bool, vh: float = 900.0) -> dict:
    """An observation with ``texts`` painted nodes all above the fold."""
    return {
        "viewportHeight": vh,
        "hasPassword": has_password,
        "texts": [{"size": 16.0, "top": 100.0 + i} for i in range(texts)],
        "sections": [],
    }


# A login wall: password field + sparse above-the-fold (~8 nodes).
_LOGIN = _obs(texts=8, has_password=True)
# A rich content landing / dashboard: many above-fold nodes, no password.
_CONTENT = _obs(texts=42, has_password=False)
# An auth-gated app whose /dashboard redirects back to the login wall.
_REDIRECT_LOGIN = _obs(texts=7, has_password=True)
# A hollow client shell (redirect mid-flight / skeleton): sparse, no password.
_EMPTY_SHELL = _obs(texts=3, has_password=False)
# Bootstrap baseline: a plain content page (no password) — must stay GATED.
_BASELINE = _obs(texts=30, has_password=False)


# ── pure pick_route: the 5 route classes + adversarial baseline ───────────────


def test_marketing_landing_root_is_content_keeps_root() -> None:
    """fitness / shop / fintech: `/` is a rich landing → score `/` (no divert)."""
    assert pick_route(_CONTENT, None) == "/"


def test_login_root_with_content_dashboard_targets_dashboard() -> None:
    """school / CRM: `/` is login, `/dashboard` renders content → target it."""
    assert pick_route(_LOGIN, _CONTENT) == "/dashboard"


def test_login_root_with_dashboard_redirecting_to_login_falls_back() -> None:
    """The teeth case: `/dashboard` bounces to the login wall (no public
    dashboard) → DON'T gate the redirect, fall back to `/` (waiver owns it)."""
    assert pick_route(_LOGIN, _REDIRECT_LOGIN) == "/"


def test_login_root_with_empty_shell_dashboard_falls_back() -> None:
    """A hollow / skeleton `/dashboard` is not content → fall back to `/`."""
    assert pick_route(_LOGIN, _EMPTY_SHELL) == "/"


def test_baseline_root_stays_gated_not_diverted() -> None:
    """Adversarial: a plain bootstrap page at `/` has no password → not a login
    surface → stays on `/` and is fully GATED (route-targeting can't launder a
    mediocre page into a pass)."""
    assert pick_route(_BASELINE, None) == "/"


def test_custom_candidate_route_is_honoured() -> None:
    assert pick_route(_LOGIN, _CONTENT, "/app/home") == "/app/home"


# ── _is_content_surface floor ─────────────────────────────────────────────────


def test_is_content_surface_true_for_rich_page() -> None:
    assert route_target._is_content_surface(_CONTENT) is True


def test_is_content_surface_false_for_login() -> None:
    assert route_target._is_content_surface(_REDIRECT_LOGIN) is False


def test_is_content_surface_false_for_empty_shell() -> None:
    assert route_target._is_content_surface(_EMPTY_SHELL) is False


# ── async resolve_target_route orchestration (browser stubbed) ────────────────


def _stub_probe(monkeypatch, by_url: dict[str, dict]) -> dict:
    """Replace the render-probe with a lookup; record the URLs actually probed."""
    probed: dict = {"urls": []}

    async def _probe(url, *, timeout_ms=15_000):
        probed["urls"].append(url)
        if url not in by_url:
            raise RuntimeError(f"unprobed url {url}")
        return by_url[url]

    monkeypatch.setattr(route_target, "_probe", _probe)
    return probed


async def test_resolve_skips_candidate_when_root_is_content(monkeypatch) -> None:
    """Marketing landing: only `/` is probed (the candidate render is skipped)."""
    base = "http://omnia-dev-fit:3000"
    probed = _stub_probe(monkeypatch, {base: _CONTENT})
    assert await resolve_target_route(base) == "/"
    assert probed["urls"] == [base]  # one render, not two


async def test_resolve_targets_dashboard_when_root_is_login(monkeypatch) -> None:
    """Login-first app with a real content dashboard → resolve `/dashboard`."""
    base = "http://omnia-dev-crm:3000"
    probed = _stub_probe(
        monkeypatch, {base: _LOGIN, base + "/dashboard": _CONTENT}
    )
    assert await resolve_target_route(base) == "/dashboard"
    assert probed["urls"] == [base, base + "/dashboard"]


async def test_resolve_falls_back_when_dashboard_is_login(monkeypatch) -> None:
    base = "http://omnia-dev-crm:3000"
    _stub_probe(monkeypatch, {base: _LOGIN, base + "/dashboard": _REDIRECT_LOGIN})
    assert await resolve_target_route(base) == "/"


async def test_resolve_failsoft_on_root_probe_error(monkeypatch) -> None:
    """A render hiccup on `/` → gate the root as before, never raise (R-10)."""
    async def _boom(_url, *, timeout_ms=15_000):
        raise RuntimeError("chromium crashed")

    monkeypatch.setattr(route_target, "_probe", _boom)
    assert await resolve_target_route("http://omnia-dev-x:3000") == "/"


async def test_resolve_failsoft_on_candidate_probe_error(monkeypatch) -> None:
    """`/` is login but the candidate render errors → fall back to `/`."""
    base = "http://omnia-dev-crm:3000"

    async def _probe(url, *, timeout_ms=15_000):
        if url == base:
            return _LOGIN
        raise RuntimeError("candidate render failed")

    monkeypatch.setattr(route_target, "_probe", _probe)
    assert await resolve_target_route(base) == "/"


async def test_resolve_handles_trailing_slash_base(monkeypatch) -> None:
    """A base URL with a trailing slash must not double-slash the candidate."""
    base = "http://omnia-dev-crm:3000/"
    probed = _stub_probe(
        monkeypatch,
        {base: _LOGIN, "http://omnia-dev-crm:3000/dashboard": _CONTENT},
    )
    assert await resolve_target_route(base) == "/dashboard"
    assert "http://omnia-dev-crm:3000/dashboard" in probed["urls"]

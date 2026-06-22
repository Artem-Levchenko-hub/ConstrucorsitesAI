"""Acceptance-lock for BS-7 / P-AUTHGATE — the protected `(app)` route group is
now gated SERVER-SIDE / at the EDGE, not client-side only.

Original gap (dogfood run #6, 2026-06-16, sandbox dogfood-crm-live-b4c0c7): an
anonymous GET of `/dashboard` and `/dashboard/clients` returned HTTP 200 with
the full app-shell HTML (sidebar «Дашборд / Клиенты / Заметки») instead of a
redirect to `/signin`. Data was never leaked (`/api/entities/Client` 401s for
anon; `owner` entities scope to `created_by`), but the operational chrome of a
"приватный кабинет" was served to anyone and to no-JS clients, and the SDK's
`safeCollection` swallowed the 401 into `[]` so an expired-session user saw a
silently-empty app instead of being bounced to re-login. Root cause was
prompt-level: the writer-generated `(app)/layout.tsx` gated via `auth.me()` in a
`useEffect` (renders the shell before the redirect; no JS = no gate), faithful to
a SYSTEM_PROMPT that only "permitted" the server `requireUser()`.

The P-AUTHGATE fix landed: the template now ships a fixed `src/middleware.ts`
(edge cookie-presence probe → redirect `/dashboard/*` and `/admin/*` to
`/signin` before any page renders — the deterministic security floor), and
SYSTEM_PROMPT now MANDATES a SERVER `(app)/layout.tsx` whose first line is
`await requireUser()`. The middleware is a presence-probe only (no JWT verify at
the edge — the Drizzle adapter isn't edge-compatible); the real identity/
ownership check stays in `requireUser()` + the owner-scoped entity API.

These are deterministic file-content asserts (money-free, no container).
"""

from __future__ import annotations

import re
from pathlib import Path

_ENTITIES = Path(__file__).resolve().parents[1] / "templates" / "nextjs-entities"
_SESSION = _ENTITIES / "src" / "lib" / "session.ts"
_PROMPT = _ENTITIES / "SYSTEM_PROMPT.md"


def test_server_gate_building_block_is_already_shipped() -> None:
    """EVIDENCE (green today): `requireUser()` — a SERVER-side redirect-to-signin
    — already exists in the template, so the gate is achievable with zero new
    runtime code. The writer simply isn't directed to put it on the protected
    route-group layout."""
    src = _SESSION.read_text(encoding="utf-8")
    assert "export async function requireUser" in src
    # It redirects at the server (works even for a no-JS client / curl), unlike
    # the client `auth.me()` + router.push the writer actually used.
    assert 'redirect(`/signin' in src


def test_entity_template_ships_edge_route_middleware_is_enforced() -> None:
    """ENFORCED (the P-AUTHGATE fix landed): a fixed `src/middleware.ts` now
    ships and gates the protected route groups at the EDGE — before any page
    renders — so the gate no longer depends on whatever the writer puts in the
    page/layout. This is the deterministic security floor.

    It is a cookie-PRESENCE probe (not a JWT verify at the edge: the Drizzle
    adapter isn't edge-compatible), so we assert it makes the logged-OUT case
    unreachable and redirects to `/signin` — the real identity/ownership check
    stays in `requireUser()` + the owner-scoped entity API."""
    mw = _ENTITIES / "src" / "middleware.ts"
    assert mw.exists(), "the edge auth-floor middleware.ts must ship in the template"
    src = mw.read_text(encoding="utf-8")

    # Gates only the protected cabinet groups — /dashboard/* and /admin/* — via
    # the matcher (public `/`, /signin, /api/* stay open).
    assert "matcher" in src
    assert "/dashboard/:path*" in src
    assert "/admin/:path*" in src

    # Cookie-presence probe over the Auth.js session-cookie names (v4 + v5,
    # bare and __Secure- prefixed) → anyone without it is bounced to /signin.
    assert "authjs.session-token" in src
    assert "__Secure-authjs.session-token" in src
    assert "cookies.has" in src
    assert "NextResponse.redirect" in src
    assert "/signin" in src

    # Presence-probe ONLY — the real identity check is requireUser() on the
    # page, not a JWT verify in the edge (documented in the file itself).
    assert "jwtVerify" not in src and "decode(" not in src


def test_protected_route_group_layout_must_be_server_gated() -> None:
    """The fix landed: the prompt now REQUIRES the protected `(app)` route-group
    layout to be server-gated with `requireUser()` (so the shell is never served
    to an unauthenticated request), not merely permits it.

    SYSTEM_PROMPT now mandates the `(app)/layout.tsx` be a SERVER component whose
    first line is `await requireUser()`, and a mandatory window (requireUser +
    `(app)`/route-group/layout + a MUST/Always/обяз directive) exists — so this
    asserts the closed gap (previously @pytest.mark.xfail before P-AUTHGATE)."""
    prompt = _PROMPT.read_text(encoding="utf-8")

    # A mandatory directive (MUST / обязан / всегда / Always / required) must
    # appear in the same window as BOTH `requireUser` and the route-group layout
    # it has to guard. Today requireUser co-occurs only with the permissive
    # "you may use" phrasing, so no such window exists.
    mandatory = r"(MUST|обяз|всегда|Always|required|обязательн)"
    windows = re.findall(
        r"requireUser[\s\S]{0,240}", prompt
    ) + re.findall(r"[\s\S]{0,240}requireUser", prompt)
    gated = any(
        re.search(mandatory, w)
        and re.search(r"\(app\)|route-group|layout", w)
        for w in windows
    )
    assert gated, (
        "SYSTEM_PROMPT does not mandate server-gating the (app) route-group "
        "layout with requireUser() — writers default to a client-only auth.me() "
        "gate, so the protected shell is served (HTTP 200) to anon/no-JS users."
    )

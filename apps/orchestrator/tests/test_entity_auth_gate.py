"""Acceptance-lock for BS-7 (dogfood run #6, 2026-06-16): generated entity apps
gate their protected `(app)` route group CLIENT-SIDE ONLY.

Live evidence (sandbox dogfood-crm-live-b4c0c7): an anonymous GET of `/dashboard`
and `/dashboard/clients` returns **HTTP 200 with the full app-shell HTML**
(sidebar «Дашборд / Клиенты / Заметки»), not a redirect to `/signin`. Data is
not leaked — `/api/entities/Client` 401s for anon and `owner` entities scope to
`created_by` — but the operational chrome of a "приватный кабинет" is served to
anyone (and to no-JS clients indefinitely), and because the SDK's
`safeCollection` swallows the 401 into `[]`, an expired-session user sees a
silently-empty app («Пока пусто») instead of being bounced to re-login.

Root cause is prompt-level: the writer-generated `(app)/layout.tsx` is a
`"use client"` component that gates via `auth.me()` in a `useEffect` →
`router.push("/signin")` (renders + hydrates the shell before the redirect; no
JS = no gate). It is faithful to SYSTEM_PROMPT.md, which makes the client
`auth.me()` gate the PRIMARY instruction and presents the server-side
`requireUser()` only as an optional "you may". The template ships no
`middleware.ts`. The fix (mandate a server gate on the route-group layout, or
ship an edge-safe gating middleware) is security-sensitive + cross-zone + needs
a base-image rebuild + regen-verify → PROPOSAL P-AUTHGATE, not a blind ship.

These are deterministic file-content asserts (money-free, no container).
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

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


def test_entity_template_ships_no_route_middleware_today() -> None:
    """EVIDENCE (green today): there is no `middleware.ts` gating the protected
    routes at the edge — the only auth gate is whatever the writer puts in the
    page/layout. Documents the gap the fix would close."""
    assert not (_ENTITIES / "middleware.ts").exists()
    assert not (_ENTITIES / "src" / "middleware.ts").exists()


@pytest.mark.xfail(
    strict=False,
    reason="BS-7 / P-AUTHGATE not yet landed: SYSTEM_PROMPT still presents the "
    "server gate as optional ('you may use requireUser()') and never requires "
    "the (app)/layout.tsx to be server-gated, so writers ship a client-only "
    "auth.me() gate. XPASSES once the protected route-group layout MUST be "
    "server-gated (or an edge-safe middleware ships).",
)
def test_protected_route_group_layout_must_be_server_gated() -> None:
    """The fix: the prompt must REQUIRE the protected `(app)` route-group layout
    to be server-gated with `requireUser()` (so the shell is never served to an
    unauthenticated request), not merely permit it.

    Today the only `requireUser` mention is the optional "you may use
    `requireUser()`" sentence, which is NOT tied to the `(app)/layout.tsx` and
    carries no mandatory force → this assertion fails (xfail). When the fix makes
    server-gating the route-group layout mandatory, it XPASSES."""
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

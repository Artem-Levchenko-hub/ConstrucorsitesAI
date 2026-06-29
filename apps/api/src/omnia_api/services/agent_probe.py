"""Agent PROBE tool — the engine behind the builder loop's `probe` action.

Gives the agent the EYE it was missing: it can make a REAL authenticated request
against the live app — as a logged-in user, through a browser, with cookies — and
read the exact `{status, body}` back. A typecheck-clean app with a 200 home page
can still 4xx every user POST (a client/server field mismatch, a broken action);
that failure is invisible to `build`/`runtime_check`/`see` but is exactly what
`probe` surfaces.

Reuses the PROVEN functional-gate machinery (Playwright + NextAuth csrf+callback
login + in-page fetch with the page's cookies), so the probe takes the exact
network path a real user's browser takes — a raw httpx client would not reproduce
preview-host resolution or the session cookie.

Stateful flows compose across calls: the probe user persists in the project DB, so
`probe POST /api/channels` then `probe POST /api/realtime/conversation:<id>` send
to a channel the probe user owns (and is therefore a member of).

Fail-soft everywhere (R-10): no preview, a login failure, or a render error all
degrade to a readable observation dict, never an exception that kills the loop.
"""

from __future__ import annotations

import json
from typing import Any
from uuid import UUID

# Fixed throwaway identity so multi-step probes (create → act) share DB state.
_PROBE_EMAIL = "agent-probe@omnia.local"
_PROBE_PASSWORD = "agent-probe-1234"
_MAX_BODY_CHARS = 1800  # cap the observation so one fat response can't blow context
_METHODS = {"GET", "POST", "PUT", "PATCH", "DELETE"}


async def run_probe(
    project_id: UUID | str,
    *,
    method: str,
    path: str,
    body: Any = None,
) -> dict[str, Any]:
    """Make one authenticated request against the live preview and return the
    executor observation ``{ok, detail|error}``.

    ``ok`` is True only for a 2xx — a 4xx/5xx is ``ok=False`` with the status +
    response body, because for an interactive feature THAT is the real failure the
    agent must fix (not a green build)."""
    m = (method or "GET").upper().strip()
    if m not in _METHODS:
        return {"ok": False, "error": f"probe: bad method {method!r} (use {sorted(_METHODS)})"}
    if not isinstance(path, str) or not path.startswith("/"):
        return {"ok": False, "error": "probe: path must be a same-origin path like /api/..."}

    try:
        pid = UUID(str(project_id))
    except (TypeError, ValueError):
        return {"ok": False, "error": "probe: bad project id"}

    from omnia_api.services import orchestrator_client

    st = await orchestrator_client.get_status(pid)
    base = st.get("dev_url") if isinstance(st, dict) else None
    if not base:
        return {"ok": False, "error": "probe: preview not running — build/start the app first"}
    base = base.rstrip("/")

    # Lazy imports keep the pure engine + its tests free of Playwright.
    from playwright.async_api import async_playwright

    from omnia_api.services import functional_gate as fg
    from omnia_api.services.auth_session import preview_resolver_args

    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True, args=preview_resolver_args())
            try:
                ctx = await browser.new_context()
                page = await ctx.new_page()
                await page.goto(f"{base}/signin", wait_until="domcontentloaded")
                # Register (idempotent: 409 = already exists) then log in.
                await fg._api(
                    page, "POST", "/api/auth/register",
                    {"email": _PROBE_EMAIL, "password": _PROBE_PASSWORD},
                )
                try:
                    await fg._login(page, base, _PROBE_EMAIL, _PROBE_PASSWORD)
                except Exception as exc:  # noqa: BLE001 — login is itself a finding
                    return {
                        "ok": False,
                        "error": f"probe: could not log in as the test user ({type(exc).__name__}: {exc})",
                    }
                res = await fg._api(page, m, path, body)
            finally:
                await browser.close()
    except Exception as exc:  # noqa: BLE001 — a probe error must not kill the loop
        return {"ok": False, "error": f"probe failed: {type(exc).__name__}: {exc}"}

    status = int(res.get("status", 0))
    payload = res.get("json")
    rendered = json.dumps(payload, ensure_ascii=False) if payload is not None else "(no JSON body)"
    if len(rendered) > _MAX_BODY_CHARS:
        rendered = rendered[:_MAX_BODY_CHARS] + " …(truncated)"
    ok = 200 <= status < 300
    verdict = "OK" if ok else "FAILED"
    return {
        "ok": ok,
        "detail": (
            f"probe {m} {path} -> HTTP {status} ({verdict})\n"
            f"response: {rendered}"
            + ("" if ok else "\nThis is the REAL user-facing result — fix until this request is 2xx.")
        ),
    }


__all__ = ["run_probe"]

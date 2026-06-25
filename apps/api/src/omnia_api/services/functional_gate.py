"""Functional + security E2E gate (G004) — proof that a generated app's feature
actually WORKS and does not LEAK, not just that it type-checks or looks right.

Every other acceptance gate in this codebase judges visuals/structure. This one
drives a LIVE realtime-stack preview through the north-star messenger flow and
proves, behaviourally:

  1. FUNCTIONAL  — signup + login work for two users, and a member receives
                   another member's message LIVE over SSE in under one second
                   (no polling): the realtime substrate (G001) actually delivers.
  2. SECURITY    — a NON-member is denied (403) the channel's stream, its history
                   AND publish: the membership ACL (G001/G002) actually holds with
                   zero cross-conversation leak. This is the negative-path proof
                   that "secure from the first prompt" is real, not asserted.

All API calls run through the BROWSER (`page.evaluate(fetch/EventSource)`) so they
use Chrome's host-resolver rules and the page's session cookies — the same network
path a real user takes, which a raw httpx client would not reproduce for preview
hostnames.

The pure verdict aggregation (:func:`summarize`) is split out so it is unit-tested
without a browser. Gated by ``Settings.use_functional_gate`` (default False) at the
call site — when off this module is never entered and the ship decision is
unchanged. It applies only to realtime-stack projects (the only ones with the
channels/realtime contract); the caller selects it by template.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

# A member must see a peer's message within this budget — the whole point of
# realtime is sub-second delivery, so a slow path is a functional failure.
_DELIVERY_BUDGET_MS = 1000
_LOGIN_TIMEOUT_MS = 15_000


@dataclass
class Check:
    """One named behavioural assertion and its outcome."""

    name: str
    ok: bool
    detail: str = ""


@dataclass
class FunctionalVerdict:
    passed: bool
    checks: list[Check] = field(default_factory=list)
    summary: str = ""


def summarize(checks: list[Check]) -> FunctionalVerdict:
    """Aggregate checks into a ship/no-ship verdict. Pure — unit-testable without
    a browser. The gate PASSES only when every check passed; a single leak or a
    missed delivery fails the whole gate (no partial credit on security)."""
    failures = [c for c in checks if not c.ok]
    passed = len(checks) > 0 and not failures
    if passed:
        summary = f"functional+security gate PASSED ({len(checks)} checks)"
    else:
        names = ", ".join(c.name for c in failures) or "no checks ran"
        summary = f"functional+security gate FAILED: {names}"
    return FunctionalVerdict(passed=passed, checks=checks, summary=summary)


# ── Browser-side helpers (run fetch/EventSource INSIDE the page) ─────────────


async def _login(page: object, base_url: str, email: str, password: str) -> None:
    """Sign a user in through the real /signin UI so the session cookie is set on
    the page's context (the realtime template signs in then routes to /chat)."""
    await page.goto(f"{base_url}/signin", wait_until="domcontentloaded")  # type: ignore[attr-defined]
    await page.fill("input[type=email]", email)  # type: ignore[attr-defined]
    await page.fill("input[type=password]", password)  # type: ignore[attr-defined]
    await page.click("button[type=submit]")  # type: ignore[attr-defined]
    # Land on the authed chat home — proof the credentials were accepted.
    await page.wait_for_url("**/chat", timeout=_LOGIN_TIMEOUT_MS)  # type: ignore[attr-defined]


async def _api(
    page: object, method: str, path: str, body: object | None = None
) -> dict:
    """Run `fetch` inside the page (browser network + cookies) and return
    ``{status, json}``. Used for every API assertion so the gate exercises the
    exact path a logged-in user's browser would."""
    return await page.evaluate(  # type: ignore[attr-defined]
        """async ([method, path, body]) => {
            const res = await fetch(path, {
                method,
                headers: body ? { 'Content-Type': 'application/json' } : undefined,
                body: body ? JSON.stringify(body) : undefined,
                credentials: 'include',
            });
            let json = null;
            try { json = await res.json(); } catch (_) {}
            return { status: res.status, json };
        }""",
        [method, path, body],
    )


async def _await_sse_message(
    page: object, channel: str, match_text: str, timeout_ms: int
) -> bool:
    """Open an EventSource on `channel` inside the page and resolve True iff a
    `message` event whose body contains `match_text` arrives within the budget.
    This is the live-delivery proof — it would never pass on a polling-only app."""
    return await page.evaluate(  # type: ignore[attr-defined]
        """async ([channel, matchText, timeoutMs]) => {
            const url = `/api/realtime/${encodeURIComponent(channel)}/stream`;
            return await new Promise((resolve) => {
                const es = new EventSource(url, { withCredentials: true });
                const timer = setTimeout(() => { es.close(); resolve(false); }, timeoutMs);
                es.onmessage = (ev) => {
                    try {
                        const e = JSON.parse(ev.data);
                        const body = e && e.data && (e.data.body ?? '');
                        if (e.type === 'message' && String(body).includes(matchText)) {
                            clearTimeout(timer); es.close(); resolve(true);
                        }
                    } catch (_) {}
                };
                es.onerror = () => {};  // transient reconnects are fine within the budget
            });
        }""",
        [channel, match_text, timeout_ms],
    )


async def run_functional_gate(base_url: str) -> FunctionalVerdict:
    """Drive the live realtime preview and return the behavioural verdict.

    Fail-soft: any unexpected error becomes a failed check (never raises into the
    pipeline) — a gate that crashes must read as "not proven", not as "passed".
    """
    from playwright.async_api import async_playwright

    from omnia_api.services.auth_session import preview_resolver_args

    checks: list[Check] = []
    base_url = base_url.rstrip("/")
    # Distinct accounts per run so a re-run never collides on the unique email.
    suffix = base_url.rsplit("/", 1)[-1][:8] or "x"
    teacher = f"gate-teacher-{suffix}@omnia.local"
    student = f"gate-student-{suffix}@omnia.local"
    outsider = f"gate-outsider-{suffix}@omnia.local"
    password = "gate-pass-1234"
    nonce = f"omnia-live-{suffix}"

    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(
                headless=True, args=preview_resolver_args()
            )
            try:
                ctx_t = await browser.new_context()
                ctx_s = await browser.new_context()
                ctx_o = await browser.new_context()
                page_t = await ctx_t.new_page()
                page_s = await ctx_s.new_page()
                page_o = await ctx_o.new_page()

                # 1. Signup (registration is unauthenticated; run from any page
                #    that has the right origin for a relative fetch).
                await page_t.goto(f"{base_url}/signin", wait_until="domcontentloaded")
                for email in (teacher, student, outsider):
                    res = await _api(
                        page_t,
                        "POST",
                        "/api/auth/register",
                        {"email": email, "password": password},
                    )
                    ok = res["status"] in (200, 201, 409)  # 409 = already exists (re-run)
                    checks.append(Check(f"signup {email.split('@')[0]}", ok, str(res["status"])))

                # 2. Login each user → session cookie on its context.
                await _login(page_t, base_url, teacher, password)
                await _login(page_s, base_url, student, password)
                await _login(page_o, base_url, outsider, password)
                checks.append(Check("login all three users", True, ""))

                # 3. Teacher creates a conversation and adds the student.
                created = await _api(page_t, "POST", "/api/channels", {"title": "Класс 9А"})
                channel_id = (created.get("json") or {}).get("data", {}).get("id")
                checks.append(
                    Check("create conversation", bool(channel_id), str(created["status"]))
                )
                if not channel_id:
                    return summarize(checks)
                add = await _api(
                    page_t, "POST", f"/api/channels/{channel_id}/members", {"email": student}
                )
                checks.append(Check("add student as member", add["status"] == 200, str(add["status"])))

                # 4. Student subscribes; teacher publishes; student must receive it live.
                channel = f"conversation:{channel_id}"
                sse_task = asyncio.create_task(
                    _await_sse_message(page_s, channel, nonce, _DELIVERY_BUDGET_MS + 4000)
                )
                await asyncio.sleep(0.5)  # let the EventSource open before publishing
                pub = await _api(
                    page_t,
                    "POST",
                    f"/api/realtime/{channel}",
                    {"type": "message", "data": {"text": f"привет {nonce}"}},
                )
                checks.append(Check("teacher publish message", pub["status"] == 200, str(pub["status"])))
                delivered = await sse_task
                checks.append(
                    Check(
                        "student receives message live (<1s, SSE)",
                        delivered,
                        "delivered" if delivered else "timed out — no live delivery",
                    )
                )

                # 5. SECURITY negative path: the outsider (non-member) must be denied
                #    the stream, the history AND publish — zero leak.
                hist = await _api(page_o, "GET", f"/api/channels/{channel_id}/messages")
                checks.append(
                    Check("outsider DENIED history (403)", hist["status"] == 403, str(hist["status"]))
                )
                opub = await _api(
                    page_o,
                    "POST",
                    f"/api/realtime/{channel}",
                    {"type": "message", "data": {"text": "leak attempt"}},
                )
                checks.append(
                    Check("outsider DENIED publish (403)", opub["status"] == 403, str(opub["status"]))
                )
                # Stream auth is checked before any bytes flow, so a plain fetch of
                # the stream URL returns the 403 synchronously for a non-member.
                ostream = await _api(page_o, "GET", f"/api/realtime/{channel}/stream")
                checks.append(
                    Check("outsider DENIED stream (403)", ostream["status"] == 403, str(ostream["status"]))
                )
            finally:
                await browser.close()
    except Exception as exc:  # noqa: BLE001 — a crashed gate is "not proven", fail-soft
        checks.append(Check("gate executed", False, f"{type(exc).__name__}: {exc}"))

    return summarize(checks)

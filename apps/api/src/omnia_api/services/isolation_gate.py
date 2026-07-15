"""Cross-tenant ISOLATION gate — behavioural proof a generated DATA app does not
LEAK one user's rows to another (or to the public).

The realtime functional gate (:mod:`functional_gate`) already proves the messenger
membership ACL holds; the static security_gate (G005) asserts transport hardening.
But the data-app stack (``fullstack`` → ``nextjs-postgres-drizzle``) had NO runtime
isolation gate at all: a typecheck-clean app routinely ships every owned row to an
UNAUTHENTICATED request (RLS-off / public-by-default route handlers — the single
most common real-world vibe-coded leak, e.g. CVE-2025-48757). A green build is
exactly what a model hallucinates "done" around.

Two browser-driven proofs, both reusing the PROVEN functional-gate machinery
(NextAuth csrf+callback login + in-page `fetch` + the preview host-resolver), so
each takes the exact network path a real user's browser takes:

  :func:`run_public_access_gate` — the UNCONDITIONAL negative gate the build
      dispatch runs for every drizzle/fullstack build. Discovers the app's OWN
      generated API routes and asserts none of the static collection routes serves
      a 2xx data body to an anonymous request (``/api/auth/*`` excepted). Low
      false-positive by construction: an authed data app must never answer an
      anonymous request WITH rows.

  :func:`run_isolation_probe` — the agent's ``verify_isolation`` tool. Given a
      create request + an optional read-back path, it logs in TWO distinct users,
      has user A create a resource, then asserts user B is DENIED reading it AND
      that A's row never appears in B's collection listing. The positive two-tenant
      proof the agent runs on its OWN endpoints during the build (it knows the
      payloads, so there is no fragile guessing and no false block).

Verdict aggregation reuses :func:`functional_gate.summarize` (pure, unit-tested).
The route-parsing and leak-judgement cores are split out as pure functions so the
risky logic is unit-tested without a browser. Everything is fail-soft (R-10): a
crash, a login failure or a missing preview degrades to a failed/short verdict,
never an exception into the build pipeline.
"""

from __future__ import annotations

import json
from typing import Any
from uuid import UUID

from omnia_api.services.functional_gate import Check, FunctionalVerdict, summarize

_PASSWORD = "iso-gate-1234"
# Cap how many discovered routes the anonymous sweep probes, so a huge app can't
# make the gate run unbounded. The riskiest leak (a "list everything" collection
# route) is near the top of any sane API surface; deep/rare routes are covered by
# the agent's positive verify_isolation on its own endpoints.
_MAX_ROUTES = 12


# ── Pure cores (unit-tested without a browser) ──────────────────────────────


def api_routes_from_grep(grep_output: str) -> list[str]:
    """Parse an `agent_grep` dump of route-handler exports into the list of STATIC
    collection URL paths worth probing anonymously.

    Maps ``src/app/api/<seg>/route.ts`` → ``/api/<seg>``. Drops:
      * dynamic routes (a ``[param]`` segment — can't fabricate a real id for an
        anon GET; covered instead by the agent's positive isolation probe),
      * auth routes (``/api/auth/...`` — login/register/session are MEANT to be
        reachable unauthenticated),
      * Next.js route-group ``(group)`` segments (not part of the URL).
    Deduped, sorted, capped at :data:`_MAX_ROUTES`.
    """
    paths: set[str] = set()
    for raw in (grep_output or "").splitlines():
        line = raw.strip()
        if not line:
            continue
        # grep lines look like "src/app/api/tasks/route.ts:3:export async function GET"
        file_path = line.split(":", 1)[0].strip()
        if not file_path.endswith("/route.ts"):
            continue
        marker = "app/api/"
        idx = file_path.find(marker)
        if idx == -1:
            continue
        rel = file_path[idx + len(marker) :]
        rel = rel[: -len("/route.ts")]  # strip trailing /route.ts
        if not rel or "[" in rel:  # empty or dynamic segment
            continue
        # strip Next.js route-group "(group)" segments from the URL
        segs = [s for s in rel.split("/") if not (s.startswith("(") and s.endswith(")"))]
        if not segs:
            continue
        url = "/api/" + "/".join(segs)
        if url.startswith("/api/auth"):
            continue
        paths.add(url)
    return sorted(paths)[:_MAX_ROUTES]


def body_leaks_data(status: int, json_body: Any) -> bool:
    """True iff an ANONYMOUS response actually carried owned rows — the leak.

    Conservative on purpose (a security gate that false-blocks good builds is the
    very frustration we are avoiding): a leak is only a 2xx whose body is a
    NON-EMPTY collection — a top-level non-empty list, or a dict whose ``data`` /
    ``items`` / ``results`` / ``rows`` / ``records`` value is a non-empty list.
    Anything else (401/403/redirect/404/5xx, an empty list, a bare object) reads as
    safe — single-object leaks live on dynamic item routes, covered by the agent's
    positive isolation probe instead.
    """
    if not (200 <= int(status) < 300):
        return False
    if isinstance(json_body, list):
        return len(json_body) > 0
    if isinstance(json_body, dict):
        for key in ("data", "items", "results", "rows", "records"):
            v = json_body.get(key)
            if isinstance(v, list) and len(v) > 0:
                return True
    return False


def extract_resource_id(json_body: Any) -> str | None:
    """Best-effort id of a freshly created resource: top-level ``id`` or ``data.id``."""
    if isinstance(json_body, dict):
        rid = json_body.get("id")
        if rid is not None:
            return str(rid)
        data = json_body.get("data")
        if isinstance(data, dict) and data.get("id") is not None:
            return str(data["id"])
    return None


def body_contains_id(json_body: Any, rid: str) -> bool:
    """True iff A's resource id appears anywhere in B's response body."""
    if not rid:
        return False
    try:
        return rid in json.dumps(json_body, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        return rid in str(json_body)


# ── Browser-driven gates (fail-soft) ────────────────────────────────────────


async def _resolve_base(project_id: UUID | str) -> str | None:
    from omnia_api.services import orchestrator_client

    try:
        pid = UUID(str(project_id))
    except (TypeError, ValueError):
        return None
    st = await orchestrator_client.get_status(pid)
    base = st.get("dev_url") if isinstance(st, dict) else None
    return base.rstrip("/") if isinstance(base, str) and base else None


async def run_public_access_gate(
    project_id: UUID | str, slug: str, base_url: str
) -> FunctionalVerdict:
    """Discover the app's own data routes and prove none serves rows to an
    anonymous request. Unconditional negative gate for the drizzle/fullstack stack."""
    from playwright.async_api import async_playwright

    from omnia_api.services import functional_gate as fg
    from omnia_api.services import orchestrator_client
    from omnia_api.services.auth_session import preview_resolver_args

    checks: list[Check] = []
    base_url = (base_url or "").rstrip("/")
    if not base_url:
        return summarize([Check("preview running", False, "no dev_url")])

    # Discover generated route-handler files under src/app/api. CAUTION: the
    # orchestrator's `agent_grep` is a FIXED-STRING / basic grep — it does NOT
    # support extended-regex. The old ERE pattern
    # `export (async )?(function|const) (GET|POST|…)` therefore silently returned
    # "(no matches)" for EVERY app, so this gate passed VACUOUSLY since it shipped
    # (verified live 2026-07-07: fixed strings match, `(GET|POST)` → no matches).
    # Grep the literal `export` instead — `api_routes_from_grep` keys off the
    # `/route.ts` FILE path (method-agnostic), so any exported line in a route
    # file is enough to surface that route's URL.
    try:
        dump = await orchestrator_client.agent_grep(
            UUID(str(project_id)),
            slug,
            pattern="export",
            path="src/app/api",
        )
    except Exception as exc:
        return summarize([Check("discover api routes", False, f"{type(exc).__name__}: {exc}")])

    routes = api_routes_from_grep(dump)
    if not routes:
        # No custom data routes (auth-only app) → nothing can leak. Pass explicitly.
        return summarize([Check("no public data routes to leak", True, "0 static data routes")])

    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True, args=preview_resolver_args())
            try:
                # A FRESH context with NO login — a true anonymous visitor.
                ctx = await browser.new_context()
                page = await ctx.new_page()
                await page.goto(f"{base_url}/", wait_until="domcontentloaded")
                for route in routes:
                    res = await fg._api(page, "GET", route)
                    status = int(res.get("status", 0))
                    leaks = body_leaks_data(status, res.get("json"))
                    checks.append(
                        Check(
                            f"anon DENIED {route}",
                            not leaks,
                            f"HTTP {status}"
                            + (" — LEAKS ROWS to anonymous" if leaks else " (no rows to anon)"),
                        )
                    )
            finally:
                await browser.close()
    except Exception as exc:
        checks.append(Check("public-access gate executed", False, f"{type(exc).__name__}: {exc}"))

    return summarize(checks)


async def run_isolation_probe(
    project_id: UUID | str,
    *,
    create: dict[str, Any] | None = None,
    read: dict[str, Any] | None = None,
) -> FunctionalVerdict:
    """Positive two-tenant proof (the agent's ``verify_isolation`` tool): user A
    creates a resource, user B must NOT be able to read it.

    ``create`` = ``{"method","path","body"}`` (the create request, run as A).
    ``read``   = ``{"path"}`` to read A's resource as B; a ``{id}`` placeholder is
    filled with A's created id. If ``read`` is omitted, B re-reads the create
    collection path and we assert A's row is absent from B's listing.
    """
    create = create or {}
    create_path = create.get("path")
    if not isinstance(create_path, str) or not create_path.startswith("/"):
        return summarize([Check("verify_isolation args", False, "create.path must be /api/…")])
    create_method = str(create.get("method") or "POST").upper()
    create_body = create.get("body")
    read_path_tmpl = (read or {}).get("path") if isinstance(read, dict) else None

    base = await _resolve_base(project_id)
    if not base:
        return summarize(
            [Check("preview running", False, "no dev_url — build/start the app first")]
        )

    from playwright.async_api import async_playwright

    from omnia_api.services import functional_gate as fg
    from omnia_api.services.auth_session import preview_resolver_args

    suffix = base.rsplit("/", 1)[-1][:8] or "x"
    user_a = f"iso-a-{suffix}@omnia.local"
    user_b = f"iso-b-{suffix}@omnia.local"
    checks: list[Check] = []

    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True, args=preview_resolver_args())
            try:
                ctx_a = await browser.new_context()
                ctx_b = await browser.new_context()
                page_a = await ctx_a.new_page()
                page_b = await ctx_b.new_page()
                await page_a.goto(f"{base}/signin", wait_until="domcontentloaded")
                await page_b.goto(f"{base}/signin", wait_until="domcontentloaded")
                for email, page in ((user_a, page_a), (user_b, page_b)):
                    await fg._api(
                        page, "POST", "/api/auth/register",
                        {"email": email, "password": _PASSWORD},
                    )
                try:
                    await fg._login(page_a, base, user_a, _PASSWORD)
                    await fg._login(page_b, base, user_b, _PASSWORD)
                except Exception as exc:
                    return summarize(
                        [Check("login two test users", False, f"{type(exc).__name__}: {exc}")]
                    )

                # A creates the resource.
                created = await fg._api(page_a, create_method, create_path, create_body)
                c_status = int(created.get("status", 0))
                rid = extract_resource_id(created.get("json"))
                checks.append(
                    Check("user A creates resource", 200 <= c_status < 300, f"HTTP {c_status}")
                )
                if not (200 <= c_status < 300):
                    return summarize(checks)

                # B tries to read A's specific resource (if a read path is known).
                if isinstance(read_path_tmpl, str) and read_path_tmpl.startswith("/"):
                    read_path = read_path_tmpl.replace("{id}", rid) if rid else read_path_tmpl
                    rb = await fg._api(page_b, "GET", read_path)
                    rb_status = int(rb.get("status", 0))
                    denied = rb_status in (401, 403, 404) or (
                        200 <= rb_status < 300
                        and not (rid and body_contains_id(rb.get("json"), rid))
                    )
                    checks.append(
                        Check(
                            f"user B DENIED A's resource ({read_path})",
                            denied,
                            f"HTTP {rb_status}"
                            + ("" if denied else " — B can READ A's data (LEAK)"),
                        )
                    )

                # B reads the collection — A's row must NOT appear in B's listing.
                lb = await fg._api(page_b, "GET", create_path)
                lb_status = int(lb.get("status", 0))
                if rid is not None and 200 <= lb_status < 300:
                    absent = not body_contains_id(lb.get("json"), rid)
                    checks.append(
                        Check(
                            f"A's row absent from B's list ({create_path})",
                            absent,
                            "isolated" if absent else "B's list CONTAINS A's row (LEAK)",
                        )
                    )
            finally:
                await browser.close()
    except Exception as exc:
        checks.append(Check("isolation probe executed", False, f"{type(exc).__name__}: {exc}"))

    return summarize(checks)


__all__ = [
    "api_routes_from_grep",
    "body_contains_id",
    "body_leaks_data",
    "extract_resource_id",
    "run_isolation_probe",
    "run_public_access_gate",
]

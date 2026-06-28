"""Context7 client — up-to-date library docs for the agent loop.

Wires Context7 (https://context7.com) INTO the agentic builder: the model can
call the `docs` action to pull CURRENT official docs/signatures for an external
library (Next.js, Drizzle, NextAuth, aiogram, …) instead of guessing from a
stale training cut-off. Hallucinated APIs are the #1 cause of build-loop /
edit-fail churn, so real docs on demand make the environment actually reliable.

Works keyless (Context7's low-rate-limit tier). Set ``CONTEXT7_API_KEY`` (a
``ctx7sk-…`` key from context7.com/dashboard) in the env for higher limits — it
is sent as a Bearer token when present. Fully fail-soft: any error/timeout
returns "" so a docs miss never breaks a build.
"""

from __future__ import annotations

import os

import httpx
import structlog

log = structlog.get_logger("omnia_api.context7")

_BASE = "https://context7.com/api/v2"
_TIMEOUT = 12.0
_MAX_CHARS = 6000


def _headers() -> dict[str, str]:
    key = os.getenv("CONTEXT7_API_KEY", "").strip()
    return {"Authorization": f"Bearer {key}"} if key else {}


async def _resolve_library_id(client: httpx.AsyncClient, library: str) -> str | None:
    """Top Context7 library id for a package name, e.g. 'drizzle' → '/drizzle-team/drizzle-orm'."""
    try:
        r = await client.get(
            f"{_BASE}/libs/search",
            params={"libraryName": library, "query": library},
            headers=_headers(),
        )
    except Exception:
        return None
    if r.status_code != 200:
        return None
    try:
        data = r.json()
    except Exception:
        return None
    items = data.get("results") if isinstance(data, dict) else data
    if not isinstance(items, list) or not items:
        return None
    first = items[0]
    if not isinstance(first, dict):
        return None
    return first.get("id") or first.get("libraryId") or first.get("settings", {}).get("project")


async def fetch_docs(library: str, query: str) -> str:
    """Current docs snippet for ``library`` about ``query``, or "" on any miss.

    Fail-soft by contract — the agent loop must never break because docs were
    unreachable; an empty string just means "no docs, continue from what you know".
    """
    library = (library or "").strip()
    query = (query or "").strip()
    if not library:
        return ""
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            lib_id = await _resolve_library_id(client, library)
            if not lib_id:
                return ""
            r = await client.get(
                f"{_BASE}/context",
                params={"libraryId": lib_id, "query": query or library, "type": "txt"},
                headers=_headers(),
            )
            if r.status_code != 200:
                return ""
            text = (r.text or "").strip()
            return text[:_MAX_CHARS]
    except Exception as exc:  # noqa: BLE001 — docs are best-effort, never fatal
        log.warning("context7.fetch_failed", library=library, err=str(exc))
        return ""

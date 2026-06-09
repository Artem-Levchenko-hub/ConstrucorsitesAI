"""Active HTTP probe for server-side runtime (5xx) errors in a dev app.

The compile-status probe (`services.compile_status`) only *reads logs* — it
catches a broken **build**. But a Next.js app can compile cleanly and still
throw a 500 when a route is actually rendered: server components, `generateMetadata`,
and data fetching run lazily, per-route, only when something requests the page.
Nothing requests it until a human opens the preview, so the chat stays silent
while the user lands on a broken page.

This probe closes that gap: it GETs the running dev app over its host-bound port
to *force* the server render, then classifies the result.

  * 2xx / 3xx / 4xx  → app responded; nothing to report (a 404 route is the
    app's own concern, not a crash).
  * 5xx              → the rendered route errored. We read fresh dev logs and
    parse the Next.js error block (`parse_next_compile_error` already matches
    the render-error glyph) so the card carries the real cause + file.
  * transport error  → connection refused / timeout. The dev server may simply
    still be booting, so we stay conservative and report *ok* (a false negative
    beats a red card on a healthy app that was mid-restart).

Pure orchestration glue around docker + httpx; the log grammar lives in
`compile_status`, kept DRY.
"""

from __future__ import annotations

import httpx

from omnia_orchestrator.core.docker_client import (
    container_logs,
    container_status,
)
from omnia_orchestrator.services.compile_status import parse_next_compile_error

# A dev render that legitimately takes longer than this is itself a problem, but
# we don't want the probe to hang the background task — keep it short and bounded.
_PROBE_TIMEOUT = 8.0


class RuntimeProbeResult:
    """Outcome of one runtime probe. Plain holder — the router maps it to a schema."""

    __slots__ = ("error", "file", "ok", "status_code")

    def __init__(
        self,
        ok: bool,
        status_code: int | None = None,
        error: str | None = None,
        file: str | None = None,
    ) -> None:
        self.ok = ok
        self.status_code = status_code
        self.error = error
        self.file = file


async def probe_runtime_error(name: str, *, path: str = "/") -> RuntimeProbeResult:
    """GET the dev app's ``path`` and classify a server-side 5xx as an app error.

    ``name`` is the dev container name. Returns ``ok=True`` for any non-5xx
    response (including 4xx) and for a transport error against a possibly-booting
    server; ``ok=False`` only when the running app returns 5xx, in which case the
    fresh dev logs are parsed for the real error text + implicated file.
    """
    status = await container_status(name)
    if status.get("state") != "running":
        # Paused / hibernated / missing — it'll recompile on wake; nothing to report.
        return RuntimeProbeResult(ok=True)

    port = status.get("port")
    if not port:
        return RuntimeProbeResult(ok=True)

    code = await _http_status(int(port), path)
    if code is None or code < 500:
        # No response (still booting) or a non-server-error — healthy enough.
        return RuntimeProbeResult(ok=True, status_code=code)

    # 5xx: the route errored on render. Pull the cause out of the logs the request
    # just produced (Next logs the error synchronously while handling the request).
    logs = await container_logs(name, tail=250, kind="dev")
    _, error, file = parse_next_compile_error(logs["logs"])
    return RuntimeProbeResult(ok=False, status_code=code, error=error, file=file)


async def _http_status(port: int, path: str) -> int | None:
    """Return the HTTP status of ``GET http://127.0.0.1:<port><path>``, or None.

    None means the request never produced a response (connection refused / reset /
    timeout) — treated as "can't tell" by the caller, never as a failure.
    """
    url = f"http://127.0.0.1:{port}{path}"
    try:
        async with httpx.AsyncClient(timeout=_PROBE_TIMEOUT) as client:
            resp = await client.get(url)
            return resp.status_code
    except httpx.HTTPError:
        return None

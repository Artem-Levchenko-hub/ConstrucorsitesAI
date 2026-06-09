"""Unit tests for the active server-side 5xx runtime probe.

`container_status`, `_http_status` and `container_logs` are monkey-patched so the
classification logic is exercised without Docker or a live server. The Next.js
log grammar is covered separately in test_compile_status.py.
"""

from __future__ import annotations

import pytest

from omnia_orchestrator.services import runtime_probe

_X = chr(0x2A2F)  # Turbopack error glyph

_RENDER_500_LOGS = f"""\
 ✓ Compiled /(app) in 1820ms
 {_X} ./src/app/(app)/page.tsx (12:3)
Error: Cannot read properties of undefined (reading 'map')
    at Page (src/app/(app)/page.tsx:12:18)
 GET /(app) 500 in 240ms
"""


def _patch(
    monkeypatch: pytest.MonkeyPatch,
    *,
    state: str,
    port: str = "3210",
    code: int | None = 200,
    logs: str = "",
) -> None:
    async def _status(name: str) -> dict[str, str]:
        return {"state": state, "id": "x", "port": port}

    async def _http(p: int, path: str) -> int | None:
        return code

    async def _logs(name: str, *, tail: int = 250, kind: str = "dev") -> dict[str, str]:
        return {"logs": logs}

    monkeypatch.setattr(runtime_probe, "container_status", _status)
    monkeypatch.setattr(runtime_probe, "_http_status", _http)
    monkeypatch.setattr(runtime_probe, "container_logs", _logs)


@pytest.mark.asyncio
async def test_running_app_200_is_ok(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch(monkeypatch, state="running", code=200)
    r = await runtime_probe.probe_runtime_error("omnia-dev-x")
    assert r.ok is True
    assert r.status_code == 200
    assert r.error is None


@pytest.mark.asyncio
async def test_404_is_not_a_crash(monkeypatch: pytest.MonkeyPatch) -> None:
    # A 404 route is the app's own concern — never a red card.
    _patch(monkeypatch, state="running", code=404)
    r = await runtime_probe.probe_runtime_error("omnia-dev-x")
    assert r.ok is True
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_500_reports_error_with_file(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch(monkeypatch, state="running", code=500, logs=_RENDER_500_LOGS)
    r = await runtime_probe.probe_runtime_error("omnia-dev-x")
    assert r.ok is False
    assert r.status_code == 500
    assert r.error is not None
    assert "Cannot read properties of undefined" in r.error
    assert r.file == "src/app/(app)/page.tsx"


@pytest.mark.asyncio
async def test_503_with_unparseable_logs_still_flags(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The HTTP status is authoritative: a 5xx is a failure even if the logs don't
    # yield a parseable Next.js block (error=None, but ok stays False).
    _patch(monkeypatch, state="running", code=503, logs="something opaque")
    r = await runtime_probe.probe_runtime_error("omnia-dev-x")
    assert r.ok is False
    assert r.status_code == 503
    assert r.error is None
    assert r.file is None


@pytest.mark.asyncio
async def test_paused_container_is_ok(monkeypatch: pytest.MonkeyPatch) -> None:
    # Hibernated/paused app recompiles on wake — nothing to report.
    _patch(monkeypatch, state="paused", code=500, logs=_RENDER_500_LOGS)
    r = await runtime_probe.probe_runtime_error("omnia-dev-x")
    assert r.ok is True
    assert r.status_code is None


@pytest.mark.asyncio
async def test_no_port_is_ok(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch(monkeypatch, state="running", port="", code=500)
    r = await runtime_probe.probe_runtime_error("omnia-dev-x")
    assert r.ok is True


@pytest.mark.asyncio
async def test_transport_error_is_conservative(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # No response (booting / connection refused) — false negative beats a red
    # card on a server that was merely mid-restart.
    _patch(monkeypatch, state="running", code=None)
    r = await runtime_probe.probe_runtime_error("omnia-dev-x")
    assert r.ok is True
    assert r.status_code is None

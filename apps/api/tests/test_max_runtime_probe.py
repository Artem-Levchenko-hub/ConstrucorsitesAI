from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock
from uuid import UUID

import httpx
import pytest

from yleum_api.services import max_runtime_probe

PROJECT_ID = UUID("00000000-0000-0000-0000-000000000001")
SLUG = "fitness-demo"
ORIGIN = "https://fitness-demo-dev.preview.example.test"
BOOTSTRAP = (
    f"{ORIGIN}/api/omnia/preview-session?expires=1893456000&signature=" + "a" * 43
)


@pytest.mark.asyncio
@pytest.mark.parametrize("unauthenticated_status", [401, 200, 502])
async def test_portable_runtime_requires_negative_auth_and_trusted_identity(
    monkeypatch, unauthenticated_status
):
    from yleum_api.services.orchestrator_client import ProjectCellPreviewSession

    observed = []

    def handler(request):
        observed.append(request.url.path)
        if request.url.path == "/api/omnia/preview-session":
            return httpx.Response(
                307,
                headers={"Location": "/", "Set-Cookie": "__Host-max_session=valid; Path=/; Secure"},
            )
        if request.url.path == "/__omnia/identity":
            if request.headers.get("cookie") != "__Host-max_session=valid":
                return httpx.Response(unauthenticated_status)
            return httpx.Response(
                200, json={"project_id": str(PROJECT_ID), "user_id": "preview", "epoch": 7}
            )
        if request.url.path == "/api/omnia/actions":
            return httpx.Response(200, json={"actions": []})
        return httpx.Response(200, text="Flask product")

    _install_transport(monkeypatch, handler)
    origin = f"https://cell-{PROJECT_ID.hex[:12]}-dev.preview.lead-generator.ru"
    result = await max_runtime_probe.probe_max_cell_runtime(
        ProjectCellPreviewSession(
            PROJECT_ID, origin, BOOTSTRAP.replace(ORIGIN, origin), "2030-01-01T00:00:00Z"
        ),
        portable_project_id=PROJECT_ID,
        expected_epoch=7,
    )
    assert result.ok is (unauthenticated_status == 401)
    if result.ok:
        assert observed.count("/__omnia/identity") == 3
    elif unauthenticated_status == 200:
        assert "unauthenticated" in result.detail
    else:
        assert "HTTP 502" in result.detail
        assert "accepts unauthenticated" not in result.detail


@pytest.mark.asyncio
@pytest.mark.parametrize("support_status,epoch", [(200, 7), (500, 7), (200, 6)])
async def test_portable_proof_can_fall_back_when_home_page_is_missing(
    monkeypatch,
    support_status,
    epoch,
):
    from yleum_api.services.orchestrator_client import ProjectCellPreviewSession

    visited = []

    def handler(request):
        visited.append(request.url.path)
        if request.url.path == "/api/omnia/preview-session":
            return httpx.Response(
                307,
                headers={"Location": "/", "Set-Cookie": "__Host-max_session=valid; Path=/; Secure"},
            )
        if request.url.path == "/__omnia/identity":
            if request.headers.get("cookie") != "__Host-max_session=valid":
                return httpx.Response(401)
            return httpx.Response(
                200, json={"project_id": str(PROJECT_ID), "user_id": "preview", "epoch": epoch}
            )
        if request.url.path == "/api/omnia/actions":
            return httpx.Response(200, json={"actions": []})
        if request.url.path == "/":
            return httpx.Response(404)
        if request.url.path == "/support":
            return httpx.Response(support_status)
        return httpx.Response(200)

    _install_transport(monkeypatch, handler)
    origin = f"https://cell-{PROJECT_ID.hex[:12]}-dev.preview.lead-generator.ru"
    result = await max_runtime_probe.probe_max_cell_runtime(
        ProjectCellPreviewSession(
            PROJECT_ID, origin, BOOTSTRAP.replace(ORIGIN, origin), "2030-01-01T00:00:00Z"
        ),
        path="/",
        fallback_paths=("/support",),
        portable_project_id=PROJECT_ID,
        expected_epoch=7,
    )
    assert "/" in visited
    assert "/support" in visited
    assert result.ok is (support_status == 200 and epoch == 7)


@pytest.mark.asyncio
async def test_portable_proof_keeps_root_strict_without_an_explicit_fallback(
    monkeypatch,
):
    from yleum_api.services.orchestrator_client import ProjectCellPreviewSession

    visited = []

    def handler(request):
        visited.append(request.url.path)
        if request.url.path == "/api/omnia/preview-session":
            return httpx.Response(
                307,
                headers={"Location": "/", "Set-Cookie": "__Host-max_session=valid; Path=/; Secure"},
            )
        if request.url.path == "/__omnia/identity":
            if request.headers.get("cookie") != "__Host-max_session=valid":
                return httpx.Response(401)
            return httpx.Response(
                200, json={"project_id": str(PROJECT_ID), "user_id": "preview", "epoch": 7}
            )
        if request.url.path == "/api/omnia/actions":
            return httpx.Response(200, json={"actions": []})
        if request.url.path == "/":
            return httpx.Response(404)
        if request.url.path == "/support":
            return httpx.Response(200)
        return httpx.Response(200)

    _install_transport(monkeypatch, handler)
    origin = f"https://cell-{PROJECT_ID.hex[:12]}-dev.preview.lead-generator.ru"
    result = await max_runtime_probe.probe_max_cell_runtime(
        ProjectCellPreviewSession(
            PROJECT_ID, origin, BOOTSTRAP.replace(ORIGIN, origin), "2030-01-01T00:00:00Z"
        ),
        path="/",
        portable_project_id=PROJECT_ID,
        expected_epoch=7,
    )
    assert result.ok is False
    assert visited.count("/") == 1
    assert "/support" not in visited


def _install_transport(
    monkeypatch: pytest.MonkeyPatch,
    handler: Any,
) -> None:
    real_client = httpx.AsyncClient
    transport = httpx.MockTransport(handler)

    def client(**kwargs: Any) -> httpx.AsyncClient:
        return real_client(
            transport=transport,
            timeout=kwargs.get("timeout"),
            follow_redirects=kwargs.get("follow_redirects", False),
        )

    monkeypatch.setattr(max_runtime_probe.httpx, "AsyncClient", client)


@pytest.mark.asyncio
async def test_portable_probe_retries_one_connection_failure_without_skipping_identity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from yleum_api.services.orchestrator_client import ProjectCellPreviewSession

    observed: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        observed.append(request.url.path)
        if len(observed) == 1:
            raise httpx.ConnectError("private-transport-detail", request=request)
        if request.url.path == "/__omnia/identity":
            if request.headers.get("cookie") != "__Host-max_session=valid":
                return httpx.Response(401)
            return httpx.Response(
                200, json={"project_id": str(PROJECT_ID), "user_id": "preview", "epoch": 7}
            )
        if request.url.path == "/api/omnia/preview-session":
            return httpx.Response(
                307,
                headers={"Location": "/", "Set-Cookie": "__Host-max_session=valid; Path=/; Secure"},
            )
        if request.url.path == "/api/omnia/actions":
            return httpx.Response(200, json={"actions": []})
        return httpx.Response(200)

    _install_transport(monkeypatch, handler)
    origin = f"https://cell-{PROJECT_ID.hex[:12]}-dev.preview.lead-generator.ru"
    result = await max_runtime_probe.probe_max_cell_runtime(
        ProjectCellPreviewSession(
            PROJECT_ID, origin, BOOTSTRAP.replace(ORIGIN, origin), "2030-01-01T00:00:00Z"
        ),
        portable_project_id=PROJECT_ID,
        expected_epoch=7,
        proof_key="a" * 64,
    )

    assert result.ok is True
    assert result.artifact_digest is not None
    assert observed.count("/__omnia/identity") == 4
    assert observed.count("/api/omnia/actions") == 1


@pytest.mark.asyncio
async def test_portable_probe_bounds_connection_retries_and_redacts_transport(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from yleum_api.services.orchestrator_client import ProjectCellPreviewSession

    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        raise httpx.ConnectError("private-transport-detail", request=request)

    _install_transport(monkeypatch, handler)
    origin = f"https://cell-{PROJECT_ID.hex[:12]}-dev.preview.lead-generator.ru"
    result = await max_runtime_probe.probe_max_cell_runtime(
        ProjectCellPreviewSession(
            PROJECT_ID, origin, BOOTSTRAP.replace(ORIGIN, origin), "2030-01-01T00:00:00Z"
        ),
        portable_project_id=PROJECT_ID,
        expected_epoch=7,
    )

    assert result.ok is False
    assert calls == 3
    assert "ConnectError (stage=identity_anonymous; attempts=3)" in result.detail
    assert "private-transport-detail" not in result.detail
    assert origin not in result.detail


@pytest.mark.asyncio
@pytest.mark.parametrize("cookie", [None, "__Host-max_session=invalid.invalid"])
@pytest.mark.parametrize(
    ("disconnects", "status", "identity_change"),
    [(1, 401, {}), (3, 401, {}), (1, 200, {}), (1, 502, {}),
     (1, 401, {"project_id": str(UUID(int=2))}), (1, 401, {"epoch": 6})],
)
async def test_anonymous_identity_disconnect_is_bounded_without_weakening_proof(
    monkeypatch: pytest.MonkeyPatch,
    cookie: str | None,
    disconnects: int,
    status: int,
    identity_change: dict[str, object],
) -> None:
    calls = 0
    visited: list[str] = []
    sleep = AsyncMock()
    monkeypatch.setattr(max_runtime_probe.asyncio, "sleep", sleep)

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        visited.append(request.url.path)
        if request.url.path == "/__omnia/identity":
            if request.headers.get("cookie") == cookie:
                calls += 1
                if calls <= disconnects:
                    raise httpx.RemoteProtocolError("private-disconnect-detail", request=request)
                return httpx.Response(status)
            if request.headers.get("cookie") != "__Host-max_session=valid":
                return httpx.Response(401)
            return httpx.Response(200, json={
                "project_id": str(PROJECT_ID), "user_id": "preview", "epoch": 7,
                **identity_change,
            })
        if request.url.path == "/api/omnia/preview-session":
            return httpx.Response(307, headers={
                "Location": "/", "Set-Cookie": "__Host-max_session=valid; Path=/; Secure",
            })
        if request.url.path == "/api/omnia/actions":
            return httpx.Response(200, json={"actions": []})
        return httpx.Response(200)

    _install_transport(monkeypatch, handler)
    result = await max_runtime_probe._probe_signed_runtime(
        BOOTSTRAP, portable_project_id=PROJECT_ID, expected_epoch=7,
    )

    assert calls == min(disconnects + 1, 3)
    assert [call.args[0] for call in sleep.await_args_list] == (
        [0.2, 0.4] if disconnects == 3 else [0.2]
    )
    assert result.ok is (disconnects == 1 and status == 401 and not identity_change)
    assert "private-disconnect-detail" not in result.detail
    assert ORIGIN not in result.detail
    if disconnects == 3:
        assert "RemoteProtocolError (stage=identity_anonymous; attempts=3)" in result.detail
        assert "/api/omnia/preview-session" not in visited
    elif status != 401:
        assert "/api/omnia/preview-session" not in visited
        assert ("accepts unauthenticated" if status == 200 else "HTTP 502") in result.detail
    else:
        assert visited.count("/__omnia/identity") == 4
        assert visited.count("/api/omnia/preview-session") == 1
        assert visited.count("/api/omnia/actions") == 1
        if identity_change:
            assert result.detail == "portable boundary identity proof failed"


@pytest.mark.asyncio
@pytest.mark.parametrize("stage", ["bootstrap", "product", "actions", "identity_signed"])
async def test_non_anonymous_protocol_disconnect_remains_terminal(
    monkeypatch: pytest.MonkeyPatch, stage: str,
) -> None:
    calls = 0
    sleep = AsyncMock()
    monkeypatch.setattr(max_runtime_probe.asyncio, "sleep", sleep)

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        path = request.url.path
        if path == "/__omnia/identity":
            if request.headers.get("cookie") != "__Host-max_session=valid":
                return httpx.Response(401)
            current_stage = "identity_signed"
        else:
            current_stage = {
                "/api/omnia/preview-session": "bootstrap",
                "/api/omnia/actions": "actions", "/": "product",
            }[path]
        if current_stage == stage:
            calls += 1
            raise httpx.RemoteProtocolError("private-disconnect-detail", request=request)
        if current_stage == "bootstrap":
            return httpx.Response(307, headers={
                "Location": "/", "Set-Cookie": "__Host-max_session=valid; Path=/; Secure",
            })
        return httpx.Response(200, json={"actions": []})

    _install_transport(monkeypatch, handler)
    result = await max_runtime_probe._probe_signed_runtime(
        BOOTSTRAP, portable_project_id=PROJECT_ID, expected_epoch=7,
    )

    assert result.ok is False
    assert calls == 1
    sleep.assert_not_awaited()
    assert f"RemoteProtocolError (stage={stage}; attempts=1)" in result.detail
    assert "private-disconnect-detail" not in result.detail
    assert ORIGIN not in result.detail


@pytest.mark.asyncio
async def test_portable_probe_retries_late_connect_timeout_but_keeps_signed_checks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from yleum_api.services.orchestrator_client import ProjectCellPreviewSession

    actions_calls = 0
    identity_calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal actions_calls, identity_calls
        if request.url.path == "/__omnia/identity":
            identity_calls += 1
            if request.headers.get("cookie") != "__Host-max_session=valid":
                return httpx.Response(401)
            return httpx.Response(
                200, json={"project_id": str(PROJECT_ID), "user_id": "preview", "epoch": 7}
            )
        if request.url.path == "/api/omnia/preview-session":
            return httpx.Response(
                307,
                headers={"Location": "/", "Set-Cookie": "__Host-max_session=valid; Path=/; Secure"},
            )
        if request.url.path == "/api/omnia/actions":
            actions_calls += 1
            if actions_calls == 1:
                raise httpx.ConnectTimeout("private-transport-detail", request=request)
            return httpx.Response(200, json={"actions": []})
        return httpx.Response(200)

    _install_transport(monkeypatch, handler)
    origin = f"https://cell-{PROJECT_ID.hex[:12]}-dev.preview.lead-generator.ru"
    result = await max_runtime_probe.probe_max_cell_runtime(
        ProjectCellPreviewSession(
            PROJECT_ID, origin, BOOTSTRAP.replace(ORIGIN, origin), "2030-01-01T00:00:00Z"
        ),
        portable_project_id=PROJECT_ID,
        expected_epoch=7,
    )

    assert result.ok is True
    assert actions_calls == 2
    assert identity_calls == 3


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("route_status", "location"),
    [(200, "/"), (500, "/"), (200, "https://attacker.example/")],
)
async def test_cell_runtime_uses_cell_cookie_and_route_without_legacy(
    monkeypatch, route_status, location,
):
    from yleum_api.services.orchestrator_client import ProjectCellPreviewSession

    observed: list[str] = []

    def handler(request):
        assert request.extensions["timeout"]["read"] == 120.0
        assert request.extensions["timeout"]["connect"] == 5.0
        observed.append(request.url.path)
        if request.url.path == "/api/omnia/preview-session":
            return httpx.Response(307, headers={
                "Location": location,
                "Set-Cookie": "__Host-max_session=cell; Path=/; Secure",
            })
        assert request.headers.get("cookie") == "__Host-max_session=cell"
        if request.url.path == "/product":
            return httpx.Response(route_status)
        assert request.url.path == "/api/omnia/actions"
        return httpx.Response(200, json={"actions": []})

    _install_transport(monkeypatch, handler)
    workspace_id = UUID("00000000-0000-0000-0000-000000000002")
    cell_origin = f"https://cell-{workspace_id.hex[:12]}-dev.preview.lead-generator.ru"
    cell_bootstrap = (
        f"{cell_origin}/api/omnia/preview-session?expires=1893456000&signature=" + "a" * 43
    )
    result = await max_runtime_probe.probe_max_cell_runtime(
        ProjectCellPreviewSession(
            workspace_id, cell_origin, cell_bootstrap, "2030-01-01T00:00:00Z",
        ),
        path="/product",
    )
    escaped = location.startswith("https://attacker")
    assert result.ok is (route_status == 200 and not escaped)
    assert observed == (
        ["/api/omnia/preview-session"]
        if escaped
        else (
            ["/api/omnia/preview-session", "/product", "/api/omnia/actions"]
            if route_status == 200
            else ["/api/omnia/preview-session", "/product"]
        )
    )
    assert cell_bootstrap not in result.detail

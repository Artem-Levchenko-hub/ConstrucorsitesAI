"""Cookie request-origin boundaries through the real app, without external services."""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest
from sqlalchemy.sql import Select
from sqlalchemy.sql.elements import TextClause

from tests.test_auth_transport_security import Socket
from tests.test_auth_transport_security import site as site
from yleum_api.core.config import get_settings
from yleum_api.core.db import get_session
from yleum_api.main import create_app
from yleum_api.models.account import AuthSession
from yleum_api.models.generation_run import GenerationRun
from yleum_api.models.project import Project
from yleum_api.models.user import User
from yleum_api.routers import messages, runtime, ws
from yleum_api.schemas.message import GenerationRunPublic
from yleum_api.schemas.runtime import RuntimeStatus

TRUSTED_ORIGIN = "https://studio.example.test"
CORS_ORIGIN = "https://alternate.example.test"
UNTRUSTED_BROWSER_ORIGINS = [
    pytest.param("null", id="null"),
    pytest.param("https://attacker.dev.example.test", id="same-site-sibling"),
    pytest.param("https://studio.example.test.attacker.invalid", id="hostname-lookalike"),
    pytest.param("https://studio.example.test:444", id="wrong-port"),
    pytest.param("https://untrusted.invalid", id="unrelated-site"),
]
UNTRUSTED_ORIGINS = [pytest.param(None, id="missing"), *UNTRUSTED_BROWSER_ORIGINS]
ACTIONS = [
    pytest.param("logout", 204, id="logout"),
    pytest.param("runtime/start", 200, id="runtime-start"),
    pytest.param("generation/cancel", 202, id="generation-cancel"),
]


@pytest.fixture
def origin_site(origin_app, site, monkeypatch):
    """Keep authentication/ownership/router code real and isolate durable effects."""
    _base_app, user, login, project, token = site
    run = GenerationRun(
        id=uuid4(), project_id=project.id, user_id=user.id,
        status="running", created_at=datetime.now(UTC),
    )
    rows = {User: user, AuthSession: login, Project: project, GenerationRun: run}
    effects = SimpleNamespace(runtime_state="paused", signals=[], events=[], commits=0, queries=0)

    class Session:
        async def get(self, model, identifier):
            row = rows.get(model)
            return row if row is not None and row.id == identifier else None

        async def execute(self, statement, _parameters=None):
            effects.queries += 1
            if isinstance(statement, TextClause):
                assert str(statement).startswith("SELECT pg_advisory_xact_lock")
                return SimpleNamespace()
            assert isinstance(statement, Select)
            assert statement.column_descriptions[0]["entity"] is GenerationRun
            return SimpleNamespace(scalar_one_or_none=lambda: run)

        async def commit(self):
            effects.commits += 1

        async def refresh(self, _row):
            pass

    session = Session()

    async def sessions():
        yield session

    async def start_runtime(actual_session, actual_project, *, owner):
        assert actual_session is session and actual_project is project and owner is user
        effects.runtime_state = "running"
        return RuntimeStatus(state="running", dev_url="https://qa.dev.example.test")

    async def signal_cancel(run_id):
        assert run_id == run.id
        effects.signals.append(run_id)

    async def publish(project_id, event, _payload):
        assert project_id == project.id
        effects.events.append(event)

    async def generation_public(actual_session, *, project_id, user_id, run_id):
        assert actual_session is session
        assert (project_id, user_id, run_id) == (project.id, user.id, run.id)
        return GenerationRunPublic(
            id=run.id, project_id=project.id, status=run.status, created_at=run.created_at,
        )

    monkeypatch.setattr(runtime.project_cell_runtime, "start_project_cell_runtime", start_runtime)
    monkeypatch.setattr(messages, "request_generation_cancel", signal_cancel)
    monkeypatch.setattr(messages, "publish_event", publish)
    monkeypatch.setattr(messages, "project_generation_public", generation_public)
    origin_app.dependency_overrides[get_session] = sessions
    yield SimpleNamespace(
        app=origin_app, user=user, login=login, project=project,
        run=run, token=token, effects=effects,
    )
    origin_app.dependency_overrides.clear()


@pytest.fixture(scope="module")
def origin_app():
    """Build real middleware/routes once; session rows stay isolated per test."""
    with pytest.MonkeyPatch.context() as patch:
        settings = get_settings()
        patch.setattr(settings, "web_base_url", TRUSTED_ORIGIN)
        patch.setattr(settings, "cors_origins", CORS_ORIGIN)
        # ASGITransport does not start lifespan workers or external listeners.
        yield create_app()


def action_path(state, action):
    if action == "logout":
        return "/api/auth/logout"
    return f"/api/projects/{state.project.id}/{action}"


def observed_effects(state):
    return {
        "revoked": state.login.revoked_at is not None,
        "run_status": state.run.status,
        "runtime_state": state.effects.runtime_state,
        "signals": list(state.effects.signals),
        "events": list(state.effects.events),
        "commits": state.effects.commits,
        "queries": state.effects.queries,
    }


UNCHANGED = {
    "revoked": False,
    "run_status": "running",
    "runtime_state": "paused",
    "signals": [],
    "events": [],
    "commits": 0,
    "queries": 0,
}


@pytest.mark.parametrize("action,_success_status", ACTIONS)
@pytest.mark.parametrize("origin", UNTRUSTED_ORIGINS)
@pytest.mark.parametrize("bearer_also_present", [False, True], ids=["cookie", "cookie-and-bearer"])
async def test_cookie_unsafe_http_rejects_untrusted_origin_before_effects(
    origin_site, action, _success_status, origin, bearer_also_present,
):
    state = origin_site
    headers = {"Cookie": f"omnia_session={state.token}"}
    if origin is not None:
        headers["Origin"] = origin
    if bearer_also_present:
        headers["Authorization"] = f"Bearer {state.token}"
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=state.app), base_url=TRUSTED_ORIGIN,
    ) as client:
        response = await client.post(action_path(state, action), headers=headers)
    assert {"status": response.status_code, **observed_effects(state)} == {
        "status": 403, **UNCHANGED,
    }
    assert response.json()["error"]["code"] == "forbidden"
    assert "set-cookie" not in response.headers


@pytest.mark.parametrize("action,success_status", ACTIONS)
@pytest.mark.parametrize("origin", [TRUSTED_ORIGIN, CORS_ORIGIN], ids=["web-origin", "cors-origin"])
async def test_trusted_origin_allows_cookie_http_action(
    origin_site, action, success_status, origin,
):
    state = origin_site
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=state.app), base_url=TRUSTED_ORIGIN,
    ) as client:
        response = await client.post(
            action_path(state, action),
            headers={"Cookie": f"omnia_session={state.token}", "Origin": origin},
        )
    assert response.status_code == success_status, response.text
    assert_action_applied(state, action, response)


@pytest.mark.parametrize("action,success_status", ACTIONS)
@pytest.mark.parametrize("origin", [None, "https://untrusted.invalid"], ids=["missing", "foreign"])
async def test_cookie_free_bearer_http_keeps_explicit_token_compatibility(
    origin_site, action, success_status, origin,
):
    state = origin_site
    headers = {"Authorization": f"Bearer {state.token}"}
    if origin is not None:
        headers["Origin"] = origin
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=state.app), base_url=TRUSTED_ORIGIN,
    ) as client:
        response = await client.post(action_path(state, action), headers=headers)
    assert response.status_code == success_status, response.text
    assert_action_applied(state, action, response)


def assert_action_applied(state, action, response):
    if action == "logout":
        assert state.login.revoked_at is not None
        assert state.effects.commits == 1
    elif action == "runtime/start":
        assert state.effects.runtime_state == "running"
        assert response.json()["state"] == "running"
    else:
        assert state.run.status == "cancel_requested"
        assert response.json()["status"] == "cancel_requested"
        assert state.effects.signals == [state.run.id]
        assert state.effects.commits == 1


@pytest.mark.parametrize("action", ["runtime/start", "generation/cancel"])
async def test_trusted_origin_does_not_bypass_http_project_ownership(origin_site, action):
    state = origin_site
    state.project.owner_id = uuid4()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=state.app), base_url=TRUSTED_ORIGIN,
    ) as client:
        response = await client.post(
            action_path(state, action),
            headers={"Cookie": f"omnia_session={state.token}", "Origin": TRUSTED_ORIGIN},
        )
    assert {"status": response.status_code, **observed_effects(state)} == {
        "status": 404, **UNCHANGED,
    }


async def test_cookie_safe_http_read_does_not_require_origin(origin_site):
    state = origin_site
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=state.app), base_url=TRUSTED_ORIGIN,
    ) as client:
        response = await client.get(
            "/api/account/credentials", headers={"Cookie": f"omnia_session={state.token}"},
        )
    assert response.status_code == 200
    assert response.json() == {"has_password": False}
    assert observed_effects(state) == UNCHANGED


@pytest.mark.parametrize("origin", UNTRUSTED_ORIGINS)
@pytest.mark.parametrize("transport", ["cookie", "cookie+query"])
async def test_cookie_project_websocket_rejects_untrusted_origin_before_accept(
    origin_site, origin, transport,
):
    state = origin_site
    async with Socket(
        state.app, state.project, state.token, transport=transport, origin=origin,
    ) as socket:
        assert socket.messages == [{"type": "websocket.close", "code": 1008, "reason": ""}]
    assert not ws.hub._connections.get(state.project.id)


@pytest.mark.parametrize("origin", [TRUSTED_ORIGIN, CORS_ORIGIN])
async def test_trusted_origin_allows_cookie_project_websocket(origin_site, origin):
    state = origin_site
    async with Socket(state.app, state.project, state.token, origin=origin) as socket:
        assert socket.messages[0]["type"] == "websocket.accept"


async def test_cookie_free_query_token_websocket_allows_nonbrowser_without_origin(origin_site):
    state = origin_site
    async with Socket(
        state.app, state.project, state.token, transport="query", origin=None,
    ) as socket:
        assert socket.messages[0]["type"] == "websocket.accept"


@pytest.mark.parametrize("origin", UNTRUSTED_BROWSER_ORIGINS)
async def test_explicit_token_websocket_rejects_declared_untrusted_origin(origin_site, origin):
    state = origin_site
    async with Socket(
        state.app, state.project, state.token, transport="query", origin=origin,
    ) as socket:
        assert socket.messages == [{"type": "websocket.close", "code": 1008, "reason": ""}]
    assert not ws.hub._connections.get(state.project.id)


@pytest.mark.parametrize("extra_headers", [
    pytest.param(
        [("Origin", TRUSTED_ORIGIN), ("Origin", TRUSTED_ORIGIN)], id="duplicate-trusted",
    ),
    pytest.param(
        [("Origin", TRUSTED_ORIGIN), ("Origin", "https://untrusted.invalid")],
        id="duplicate-mixed",
    ),
    pytest.param([("Origin", f"{TRUSTED_ORIGIN},https://untrusted.invalid")], id="comma-list"),
    pytest.param([("Origin", f"{TRUSTED_ORIGIN}/")], id="path"),
    pytest.param([("Origin", f"{TRUSTED_ORIGIN}?")], id="empty-query"),
    pytest.param([("Origin", f"{TRUSTED_ORIGIN}#")], id="empty-fragment"),
    pytest.param([("Origin", f"{TRUSTED_ORIGIN}:00443")], id="nonserialized-port"),
    pytest.param([("Origin", f"{TRUSTED_ORIGIN}:65536")], id="port-out-of-range"),
    pytest.param([("Origin", f"{TRUSTED_ORIGIN}:")], id="empty-port"),
    pytest.param([("Origin", "https://studio.example.test@attacker.invalid")], id="userinfo"),
    pytest.param(
        [("Origin", "https://attacker.invalid"), ("Host", "attacker.invalid")], id="host-spoof",
    ),
    pytest.param(
        [("Origin", "https://attacker.invalid"), ("X-Forwarded-Host", "attacker.invalid")],
        id="forwarded-host-spoof",
    ),
])
async def test_http_origin_rejects_ambiguous_serialization_and_host_spoof(
    origin_site, extra_headers,
):
    state = origin_site
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=state.app), base_url=TRUSTED_ORIGIN,
    ) as client:
        response = await client.post(
            "/api/auth/logout",
            headers=[("Cookie", f"omnia_session={state.token}"), *extra_headers],
        )
    assert {"status": response.status_code, **observed_effects(state)} == {
        "status": 403, **UNCHANGED,
    }


@pytest.mark.parametrize("cookie_headers", [
    pytest.param(["omnia_session={token}; omnia_session="], id="one-header-last-empty"),
    pytest.param(["omnia_session=", "omnia_session={token}"], id="two-headers-first-empty"),
    pytest.param(["omnia_session={token}", 'omnia_session=""'], id="two-headers-last-empty"),
])
async def test_duplicate_session_cookies_cannot_hide_cookie_from_origin_guard(
    origin_site, cookie_headers,
):
    state = origin_site
    headers = [
        ("Cookie", value.format(token=state.token)) for value in cookie_headers
    ]
    headers.extend([
        ("Origin", "https://untrusted.invalid"), ("Authorization", f"Bearer {state.token}"),
    ])
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=state.app), base_url=TRUSTED_ORIGIN,
    ) as client:
        response = await client.post("/api/auth/logout", headers=headers)
    assert {"status": response.status_code, **observed_effects(state)} == {
        "status": 403, **UNCHANGED,
    }


@pytest.mark.parametrize("second_origin", [TRUSTED_ORIGIN, "https://untrusted.invalid"])
async def test_duplicate_websocket_origin_is_rejected_before_database_access(
    origin_site, monkeypatch, second_origin,
):
    state = origin_site
    engine_calls = []

    def engine():
        engine_calls.append("requested")
        return None

    monkeypatch.setattr(ws, "get_engine", engine)
    socket = Socket(state.app, state.project, state.token, origin=TRUSTED_ORIGIN)
    socket.scope["headers"].append((b"origin", second_origin.encode()))
    async with socket:
        assert socket.messages == [{"type": "websocket.close", "code": 1008, "reason": ""}]
    assert engine_calls == []

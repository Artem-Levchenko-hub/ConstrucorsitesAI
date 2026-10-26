import httpx
import pytest


@pytest.mark.asyncio
async def test_support_context_is_available_without_session():
    from yleum_api.main import app

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        response = await client.get("/api/support/context")
    assert response.status_code == 200
    assert response.json() == {"user_id": None, "email": None, "is_admin": False}


def test_support_email_escapes_plain_text():
    from yleum_api.services import email_templates

    render = getattr(email_templates, "support_email", None)
    assert callable(render), "Support email renderer is missing"
    result = render(
        reference="SUP-42",
        subject="<script>x</script>",
        body="<img src=x onerror=alert(1)>",
        url="https://yleum.ru/admin/max?tab=support",
        staff=True,
    )
    assert "<script>" not in result.html
    assert "<img src=x" not in result.html
    assert "&lt;script&gt;" in result.html
    assert "<img src=x onerror=alert(1)>" in result.text


@pytest.mark.parametrize(
    "field,value",
    [
        ("subject", "   "),
        ("message", "   "),
        ("subject", "x" * 161),
        ("message", "x" * 8001),
        ("source_path", "https://evil.example/path"),
        ("source_path", "//evil.example/path"),
        ("source_path", "/\\evil.example"),
        ("guest_token", "short"),
    ],
)
def test_schema_rejects_unsafe_or_empty_input(field, value):
    from uuid import uuid4

    from pydantic import ValidationError

    from yleum_api.schemas.support import CreateTicket

    with pytest.raises(ValidationError):
        CreateTicket(
            **{"request_id": uuid4(), "subject": "Subject", "message": "Message", field: value}
        )


def test_schema_trims_and_removes_private_query():
    from uuid import uuid4

    from yleum_api.schemas.support import CreateTicket

    parsed = CreateTicket(
        request_id=uuid4(),
        subject=" Subject ",
        message=" Message ",
        source_path="/max?secret=private#token",
    )
    assert parsed.subject == "Subject"
    assert parsed.message == "Message"
    assert parsed.source_path == "/max"


def test_archive_exact_boundary_without_worker():
    from datetime import UTC, datetime, timedelta

    from yleum_api.models.support import SupportTicket
    from yleum_api.services.support_tickets import archived

    now = datetime(2026, 10, 26, 12, tzinfo=UTC)
    ticket = SupportTicket(status="done", resolved_at=now - timedelta(hours=24))
    assert archived(ticket, now)
    ticket.resolved_at += timedelta(microseconds=1)
    assert not archived(ticket, now)
    ticket.status = "new"
    ticket.resolved_at = None
    assert not archived(ticket, now)


def test_origin_allowlist_is_exact(monkeypatch):
    from yleum_api.core.config import get_settings
    from yleum_api.routers.support import allowed_origin

    monkeypatch.setattr(get_settings(), "web_base_url", "https://yleum.ru/")
    monkeypatch.setattr(get_settings(), "cors_origins", "http://localhost:3000")
    assert allowed_origin("https://yleum.ru")
    assert allowed_origin("http://localhost:3000")
    for origin in (
        None,
        "null",
        "https://yleum.ru.evil.example",
        "https://yleum.ru/",
        "https://evil.example",
    ):
        assert not allowed_origin(origin)


def test_support_socket_ready_and_ping_without_url_capability(monkeypatch):
    from uuid import uuid4

    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from yleum_api.core.config import get_settings
    from yleum_api.routers import support

    user_id = uuid4()

    async def identity(*args):
        return f"user:{user_id}", user_id

    monkeypatch.setattr(support, "_socket_identity", identity)
    app = FastAPI()
    app.include_router(support.router)
    with (
        TestClient(app) as client,
        client.websocket_connect(
            "/api/ws/support", headers={"origin": get_settings().web_base_url.rstrip("/")}
        ) as socket,
    ):
        socket.send_json({"type": "subscribe"})
        assert socket.receive_json() == {"type": "support.ready"}
        socket.send_json({"type": "ping"})
        assert socket.receive_json() == {"type": "support.pong"}


@pytest.mark.parametrize(
    "frame",
    [
        "not-json",
        '{"type":"wrong"}',
        "x" * 1025,
        '{"type":"subscribe","ticket_id":"00000000-0000-0000-0000-000000000001"}',
    ],
)
def test_support_socket_rejects_invalid_first_frame(frame):
    from fastapi import FastAPI, WebSocketDisconnect
    from fastapi.testclient import TestClient

    from yleum_api.core.config import get_settings
    from yleum_api.routers.support import router

    app = FastAPI()
    app.include_router(router)
    with (
        TestClient(app) as client,
        client.websocket_connect(
            "/api/ws/support", headers={"origin": get_settings().web_base_url.rstrip("/")}
        ) as socket,
    ):
        socket.send_text(frame)
        with pytest.raises(WebSocketDisconnect) as error:
            socket.receive_json()
        assert error.value.code == 1008


def test_socket_closes_when_periodic_authority_changes(monkeypatch):
    from uuid import uuid4

    from fastapi import FastAPI, WebSocketDisconnect
    from fastapi.testclient import TestClient

    from yleum_api.core.config import get_settings
    from yleum_api.routers import support

    actor_id = uuid4()
    checks = 0

    async def identity(*args):
        nonlocal checks
        checks += 1
        return ("admins", actor_id) if checks == 1 else (f"user:{actor_id}", actor_id)

    monkeypatch.setattr(support, "_socket_identity", identity)
    monkeypatch.setattr(support, "_AUTH_RECHECK_SECONDS", 0.01)
    app = FastAPI()
    app.include_router(support.router)
    with (
        TestClient(app) as client,
        client.websocket_connect(
            "/api/ws/support", headers={"origin": get_settings().web_base_url.rstrip("/")}
        ) as socket,
    ):
        socket.send_json({"type": "subscribe"})
        assert socket.receive_json() == {"type": "support.ready"}
        with pytest.raises(WebSocketDisconnect) as error:
            socket.receive_json()
        assert error.value.code == 1008

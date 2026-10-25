import asyncio
import threading
from types import SimpleNamespace as NS

import httpx
import pytest
from fastapi import FastAPI

from yleum_orchestrator.core.errors import OrchestratorError, orchestrator_error_handler
from yleum_orchestrator.routers import font_validation as route
from yleum_orchestrator.services import font_validation as sandbox


@pytest.fixture
def app(monkeypatch):
    def auth(token):
        if token != "fixture-token":
            raise OrchestratorError("unauthorized", "Invalid internal token", 401)

    monkeypatch.setattr(route, "verify_internal_token", auth)
    monkeypatch.setattr(route, "_slot", asyncio.Semaphore(1))
    monkeypatch.setattr(route, "_admission_lock", asyncio.Lock())
    monkeypatch.setattr(route, "_quarantine", None)
    application = FastAPI()
    application.add_exception_handler(OrchestratorError, orchestrator_error_handler)
    application.include_router(route.router)
    return application


async def test_auth_is_checked_before_reading_untrusted_body_or_running_job(app, monkeypatch):
    async def unread():
        raise AssertionError("unauthenticated body was consumed")
        yield b""

    monkeypatch.setattr(route, "run_font_validation", lambda _: pytest.fail("unauthenticated job"))
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app), base_url="http://test"
    ) as client:
        response = await client.post(
            "/internal/font-validation",
            content=unread(),
            headers={"Content-Type": "application/octet-stream"},
        )
    assert response.status_code == 401


@pytest.mark.parametrize(
    "headers,body",
    [
        ({"Content-Type": "text/plain"}, b"font"),
        ({"Content-Type": "application/octet-stream"}, b""),
        ({"Content-Type": "application/octet-stream", "Content-Length": "8388609"}, b"font"),
    ],
    ids=["wrong_type", "empty", "oversized_declared"],
)
async def test_invalid_input_never_runs_parser(app, monkeypatch, headers, body):
    monkeypatch.setattr(route, "run_font_validation", lambda _: pytest.fail("invalid input ran"))
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app), base_url="http://test"
    ) as client:
        response = await client.post(
            "/internal/font-validation",
            content=body,
            headers={**headers, "X-Internal-Token": "fixture-token"},
        )
    assert response.status_code == 422


async def test_chunked_input_is_bounded_without_content_length(app, monkeypatch):
    async def oversized():
        yield b"a" * (8 * 1024**2)
        yield b"b"

    monkeypatch.setattr(route, "run_font_validation", lambda _: pytest.fail("oversized parser"))
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app), base_url="http://test"
    ) as client:
        response = await client.post(
            "/internal/font-validation",
            content=oversized(),
            headers={
                "Content-Type": "application/octet-stream",
                "X-Internal-Token": "fixture-token",
            },
        )
    assert response.status_code == 422


async def test_cancelled_request_keeps_single_slot_until_job_cleanup_finishes(app, monkeypatch):
    entered, finish = asyncio.Event(), threading.Event()
    loop = asyncio.get_running_loop()

    def validate(raw):
        assert raw == b"font"
        loop.call_soon_threadsafe(entered.set)
        assert finish.wait(3)
        return {"family": "Fixture"}

    monkeypatch.setattr(route, "run_font_validation", validate)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app), base_url="http://test"
    ) as client:
        headers = {"Content-Type": "application/octet-stream", "X-Internal-Token": "fixture-token"}
        pending = asyncio.create_task(
            client.post("/internal/font-validation", content=b"font", headers=headers)
        )
        await asyncio.wait_for(entered.wait(), timeout=3)
        pending.cancel()
        await asyncio.sleep(0)
        busy = await client.post("/internal/font-validation", content=b"font", headers=headers)
        assert busy.status_code == 503
        pending.cancel()
        await asyncio.sleep(0)
        still_busy = await client.post(
            "/internal/font-validation", content=b"font", headers=headers
        )
        assert still_busy.status_code == 503
        finish.set()
        with pytest.raises(asyncio.CancelledError):
            await pending
        accepted = await client.post("/internal/font-validation", content=b"font", headers=headers)
        assert accepted.status_code == 200 and accepted.json()["family"] == "Fixture"


async def test_unknown_cleanup_quarantines_admission_until_exact_job_reconciles(app, monkeypatch):
    calls = []
    failure = route.FontValidationCleanupUnconfirmed("owned", "nonce", "sha256:" + "a" * 64)

    def unknown(_raw):
        calls.append(1)
        raise failure

    monkeypatch.setattr(route, "run_font_validation", unknown)
    monkeypatch.setattr(route, "reconcile_font_validation", lambda prior: False)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app), base_url="http://test"
    ) as client:
        headers = {"Content-Type": "application/octet-stream", "X-Internal-Token": "fixture-token"}
        failed = await client.post("/internal/font-validation", content=b"font", headers=headers)
        denied = await client.post("/internal/font-validation", content=b"font", headers=headers)
        assert failed.status_code == denied.status_code == 503 and calls == [1]
        monkeypatch.setattr(route, "reconcile_font_validation", lambda prior: prior is failure)
        monkeypatch.setattr(route, "run_font_validation", lambda raw: {"family": "Recovered"})
        recovered = await client.post("/internal/font-validation", content=b"font", headers=headers)
        assert recovered.status_code == 200 and recovered.json()["family"] == "Recovered"


async def test_lost_start_ack_and_failed_remove_never_creates_second_job(app, monkeypatch):
    created = []

    def unavailable(*args, **kwargs):
        raise TimeoutError("daemon outcome unknown")

    connection = NS(close=lambda: None)
    helper = NS(start=unavailable, remove=unavailable, attach_socket=lambda **kw: connection)

    def create(*args, **kwargs):
        created.append(1)
        helper.attrs = {
            "Name": "/" + kwargs["name"],
            "Image": args[0],
            "Config": {"Labels": kwargs["labels"]},
        }
        return helper

    client = NS(
        images=NS(
            get=lambda tag: NS(id="sha256:" + "a" * 64, attrs={"Config": {"Env": ["PATH=/bin"]}})
        ),
        containers=NS(create=create, list=lambda **kw: [], get=lambda name: helper),
        close=lambda: None,
    )
    monkeypatch.setattr(sandbox.docker, "DockerClient", lambda **kw: client)
    monkeypatch.setattr(
        sandbox,
        "get_settings",
        lambda: NS(
            omnia_release_sha="b" * 40,
            docker_host="unix:///var/run/docker.sock",
        ),
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app), base_url="http://test"
    ) as http:
        headers = {"Content-Type": "application/octet-stream", "X-Internal-Token": "fixture-token"}
        first = await http.post("/internal/font-validation", content=b"font", headers=headers)
        second = await http.post("/internal/font-validation", content=b"font", headers=headers)
    assert first.status_code == second.status_code == 503
    assert created == [1] and route._slot.locked()

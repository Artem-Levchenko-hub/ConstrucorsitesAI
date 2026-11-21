"""Unverified run views are private, optional and independent of build acceptance."""

import asyncio
import hashlib
import io
import threading
from dataclasses import replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from urllib.parse import urlsplit
from uuid import uuid4

import pytest
from PIL import Image
from playwright.async_api import APIRequestContext
from playwright.async_api import Error as PlaywrightError

from tests.test_agent_retry_context import history  # noqa: F401
from yleum_api.core.errors import ApiError
from yleum_api.routers import max_studio
from yleum_api.services.generation import draft_preview_image as draft
from yleum_api.workers import preview


@pytest.fixture
def view(history, monkeypatch):  # noqa: F811
    history.ensure_project()
    run = history.add("Create my app", status="running")
    digest = "a" * 64
    run.agent_state = {"source_checkpoint": {"digest": digest}}
    history.session.commit()
    stream = io.BytesIO()
    Image.new("RGB", (390, 844), color="navy").save(stream, format="PNG")
    png = stream.getvalue()
    workspace = uuid4()
    session = SimpleNamespace(workspace_id=workspace, preview_url="https://preview.test/",
                              bootstrap_url="https://preview.test/api/omnia/preview-session")
    handle = SimpleNamespace(workspace_id=workspace,
                             existing_preview_session=AsyncMock(return_value=session))
    browser = AsyncMock(return_value=preview.PrivateDraftCapture(png, server_action_blocked=True))
    upload = Mock()
    monkeypatch.setattr(draft, "capture_private_draft_png", browser)
    monkeypatch.setattr(draft, "_upload", upload)
    ids = history.ids(run)

    async def capture(files=None):
        await draft.capture_before_rollback(
            history.factory, ids, digest,
            files if files is not None else {"src/app/feed/page.tsx": "route"}, handle,
        )
    return SimpleNamespace(run=run, ids=ids, capture=capture, browser=browser, handle=handle,
                           upload=upload, digest=digest, png=png, db=history)


@pytest.mark.parametrize("limited", [True, False])
async def test_failed_run_with_running_app_captures_once_and_only_owner_can_view(
    view, monkeypatch, limited,
):
    h = view  # no full-build receipt, application snapshot or model invocation
    h.browser.return_value = preview.PrivateDraftCapture(h.png, server_action_blocked=limited)
    await h.capture()
    await h.capture()
    assert h.handle.existing_preview_session.await_count == 2
    h.browser.assert_awaited_once_with(
        "https://preview.test/api/omnia/preview-session", "https://preview.test/feed", 390, 844,
    )
    sha = hashlib.sha256(h.png).hexdigest()
    assert draft.preview_digest(h.run) == sha
    assert h.run.agent_state[draft.KEY]["origin"] == "working_environment"
    h.upload.assert_called_once_with(
        f"generation-checkpoints/{h.ids.project_id}/{h.ids.run_id}/previews/{sha}.png", h.png,
    )
    read = Mock(return_value=h.png)
    monkeypatch.setattr(max_studio, "read_draft_png", read)
    async def get(project, run, user):
        return await max_studio.get_draft_preview_image(
            project, run, h.db.factory(), SimpleNamespace(id=user),
        )
    with pytest.raises(ApiError):
        await get(h.ids.project_id, h.run.id, h.ids.user_id)
    h.run.status = "failed"
    h.db.session.commit()
    for project, run_id, user in [(uuid4(), h.run.id, h.ids.user_id),
                                  (h.ids.project_id, uuid4(), h.ids.user_id),
                                  (h.ids.project_id, h.run.id, uuid4())]:
        with pytest.raises(ApiError) as failure:
            await get(project, run_id, user)
        assert failure.value.status_code == 404
    read.assert_not_called()
    response = await get(h.ids.project_id, h.run.id, h.ids.user_id)
    assert response.body == h.png and response.headers["content-type"] == "image/png"
    assert response.headers["cache-control"] == "private, no-store"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers.get("x-yleum-preview-partial") == ("1" if limited else None)
    assert response.headers.get("access-control-expose-headers") == (
        "X-Yleum-Preview-Partial" if limited else None
    )


@pytest.mark.parametrize("fault", ["stopped", "gone", "changed_lease", "storage", "browser"])
async def test_rollback_is_unaffected_when_preview_cannot_be_saved(view, fault):
    h = view
    session = h.handle.existing_preview_session
    if fault == "stopped":
        session.side_effect = RuntimeError("not running")
    elif fault == "gone":
        session.side_effect = [session.return_value, RuntimeError("expired")]
    elif fault == "changed_lease":
        session.side_effect = [session.return_value, SimpleNamespace(workspace_id=uuid4())]
    elif fault == "storage":
        h.upload.side_effect = OSError("unavailable")
    else:
        h.browser.side_effect = TimeoutError()
    await h.capture({})
    if h.browser.await_count:
        assert h.browser.call_args.args[1] == "https://preview.test/"
    assert draft.preview_digest(h.run) is None and h.run.status == "running"


async def test_checkpoint_or_owner_change_cannot_publish_a_late_capture(view):
    h = view
    h.run.status = "failed"
    h.db.session.commit()
    assert not await draft.save_draft_image(
        h.db.factory, replace(h.ids, user_id=uuid4()), h.digest, h.png,
        origin="reconstructed_checkpoint",
    )
    h.run.agent_state = {"source_checkpoint": {"digest": "b" * 64}}
    h.db.session.commit()
    assert not await draft.save_draft_image(
        h.db.factory, h.ids, h.digest, h.png, origin="reconstructed_checkpoint",
    )
    h.upload.assert_not_called()


async def test_cancelled_capture_propagates(view):
    view.browser.side_effect = asyncio.CancelledError()
    with pytest.raises(asyncio.CancelledError):
        await view.capture()
    view.upload.assert_not_called()


async def test_changed_checkpoint_during_upload_does_not_gain_a_reference(view):
    h = view
    uploading, release = threading.Event(), threading.Event()

    def slow_upload(*_):
        uploading.set()
        assert release.wait(5)

    h.upload.side_effect = slow_upload
    task = asyncio.create_task(draft.save_draft_image(
        h.db.factory, h.ids, h.digest, h.png, origin="reconstructed_checkpoint",
    ))
    try:
        assert await asyncio.to_thread(uploading.wait, 5)
        h.run.agent_state = {"source_checkpoint": {"digest": "b" * 64}}
        h.db.session.commit()
    finally:
        release.set()
    assert not await task and draft.preview_digest(h.run) is None


@pytest.mark.parametrize("corrupt", [True, False])
def test_private_read_verifies_the_content_digest_and_closes_object(view, monkeypatch, corrupt):
    h = view
    digest = hashlib.sha256(h.png).hexdigest()
    storage = Mock()
    stream = io.BytesIO(b"other object" if corrupt else h.png)
    stream.release_conn = Mock()
    storage.get_object.return_value = stream
    monkeypatch.setattr(draft, "get_minio_client", lambda: storage)
    data = draft.read_draft_png(h.ids.project_id, h.run.id, digest)
    assert data == (None if corrupt else h.png)
    assert stream.closed
    stream.release_conn.assert_called_once()


@pytest.mark.parametrize("new_location", ["/today", "https://draft.test/today",
                                          "https://other.test/", "/api/omnia/preview-session", "/"])
async def test_redirects_preserve_document_path_without_leaving_the_draft(new_location):
    origin = ("https", "draft.test")
    redirect = SimpleNamespace(status=302, headers={"location": new_location}, dispose=AsyncMock())
    page = SimpleNamespace(status=200)
    client = SimpleNamespace(get=AsyncMock(
        side_effect=lambda url, **_: redirect if url.endswith("/") else page,
    ))
    if new_location.endswith("/today"):
        assert await preview._draft_get(client, "https://draft.test/", origin, "text/html") == (
            "https://draft.test/today", page,
        )
    else:
        with pytest.raises(ValueError):
            await preview._draft_get(client, "https://draft.test/", origin, "text/html")
    for call in client.get.await_args_list:
        assert call.args[0].startswith("https://draft.test/")
        assert call.kwargs["max_redirects"] == 0
    assert client.get.await_count <= 6


@pytest.mark.parametrize("needs_server_action", [False, True])
async def test_mobile_png_renders_redirected_page_with_real_chromium(
    monkeypatch, needs_server_action,
):
    calls = []

    class LocalDraft(BaseHTTPRequestHandler):
        def do_GET(self):
            calls.append((self.path, self.headers.get("Cookie")))
            if self.path == "/api/omnia/preview-session":
                self.send_response(200)
                self.send_header("Set-Cookie", "preview=test; Path=/")
                self.end_headers()
                self.wfile.write(b'{"mode":"preview","user":{"id":"preview"}}')
            elif self.path == "/":
                self.send_response(307)
                self.send_header("Location", "/today")
                self.end_headers()
            elif self.path == "/today" and self.headers.get("Cookie") == "preview=test":
                self.send_response(200)
                self.send_header("Content-Type", "text/html")
                self.end_headers()
                self.wfile.write(
                    b'<body><main>Daily menu</main><script>'
                    b"if (location.pathname==='/today') document.body.style.background='#008000';"
                    b"fetch('/ignored',{method:'POST'}).catch(()=>{});"
                    b"fetch('https://external.invalid/foreign').catch(()=>{});"
                    + (b"fetch('/today',{method:'POST',headers:{'Next-Action':'get-profile'}})"
                       b".catch(()=>{});"
                       if needs_server_action else b"") + b"</script>"
                )
            else:
                self.send_response(404)
                self.end_headers()

        def log_message(self, *_):
            pass

    origin = "https://draft.test"
    real_get = APIRequestContext.get
    with ThreadingHTTPServer(("127.0.0.1", 0), LocalDraft) as server:
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        local = f"http://127.0.0.1:{server.server_port}"

        async def local_get(self, url, **options):
            assert url.startswith(origin + "/")  # remap only the test's own origin
            return await real_get(self, local + urlsplit(url).path, **options)

        monkeypatch.setattr(APIRequestContext, "get", local_get)
        try:
            capture = await preview.capture_private_draft_png(
                origin + "/api/omnia/preview-session", origin + "/", 390, 844,
            )
        except PlaywrightError as exc:
            if exc.message.startswith("BrowserType.launch: Executable doesn't exist at "):
                pytest.skip("Locked Playwright Chromium is not installed")
            raise
        finally:
            server.shutdown()
            thread.join()
    assert capture.server_action_blocked is needs_server_action
    with Image.open(io.BytesIO(capture.png)) as image:
        assert (image.format, image.size) == ("PNG", (390, 844))
        assert image.getpixel((0, 0)) == (0, 128, 0)
    assert [path for path, _ in calls][:3] == ["/api/omnia/preview-session", "/", "/today"]
    assert not any(path in {"/ignored", "/foreign"} for path, _ in calls)

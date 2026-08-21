from __future__ import annotations

import asyncio
import uuid
from pathlib import Path
from typing import ClassVar

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from omnia_api.models.generation_run import GenerationRun
from omnia_api.models.generation_telegram_report import GenerationTelegramReport
from omnia_api.models.message import Message
from omnia_api.models.project import Project
from omnia_api.models.snapshot import Snapshot
from omnia_api.models.user import User
from omnia_api.workers import preview

pytestmark = pytest.mark.asyncio

PNG = b"\x89PNG\r\n\x1a\nobserver-preview"


async def _seed_report(
    session: AsyncSession,
    *,
    template: str = "blank",
) -> tuple[Snapshot, GenerationTelegramReport]:
    suffix = uuid.uuid4().hex[:8]
    owner = User(email=f"preview-{suffix}@example.com", password_hash="x")
    session.add(owner)
    await session.flush()
    project = Project(
        owner_id=owner.id,
        name=f"Preview {suffix}",
        slug=f"preview-{suffix}",
        template=template,
    )
    session.add(project)
    await session.flush()
    snapshot = Snapshot(
        project_id=project.id,
        commit_sha="a" * 40,
        prompt_text="собери страницу",
        model_id="test",
    )
    session.add(snapshot)
    await session.flush()
    user_message = Message(project_id=project.id, role="user", content="собери страницу")
    assistant_message = Message(
        project_id=project.id,
        role="assistant",
        content="готово",
        snapshot_id=snapshot.id,
        tokens_in=1,
        tokens_out=1,
    )
    session.add_all([user_message, assistant_message])
    await session.flush()
    run = GenerationRun(
        project_id=project.id,
        user_id=owner.id,
        user_message_id=user_message.id,
        assistant_message_id=assistant_message.id,
        idempotency_key=f"preview-{suffix}",
        prompt_hash="hash",
        status="completed",
        response_mode="build",
    )
    session.add(run)
    await session.flush()
    report = GenerationTelegramReport(
        run_id=run.id,
        start_state="sent",
        start_message_id=123,
        finish_state="waiting_preview",
        terminal_status="completed",
        last_stage="snapshot",
    )
    session.add(report)
    await session.commit()
    return snapshot, report


class _FakePage:
    def __init__(self, *, render_error: bool = False) -> None:
        self.render_error = render_error

    async def route(self, *_args: object, **_kwargs: object) -> None:
        return None

    async def goto(self, *_args: object, **_kwargs: object) -> None:
        if self.render_error:
            raise RuntimeError("browser failed")

    async def evaluate(self, *_args: object, **_kwargs: object) -> int:
        return 1280

    async def wait_for_timeout(self, *_args: object, **_kwargs: object) -> None:
        return None

    async def screenshot(self, *, path: str, **_kwargs: object) -> None:
        await asyncio.to_thread(Path(path).write_bytes, PNG)

    async def close(self) -> None:
        return None


class _FakeBrowser:
    def __init__(self, *, render_error: bool = False) -> None:
        self.render_error = render_error

    async def new_page(self, **_kwargs: object) -> _FakePage:
        return _FakePage(render_error=self.render_error)

    async def close(self) -> None:
        return None


class _FakeChromium:
    def __init__(self, *, render_error: bool = False) -> None:
        self.render_error = render_error

    async def launch(self, **_kwargs: object) -> _FakeBrowser:
        return _FakeBrowser(render_error=self.render_error)


class _FakePlaywright:
    def __init__(self, *, render_error: bool = False) -> None:
        self.chromium = _FakeChromium(render_error=render_error)


class _FakePlaywrightContext:
    def __init__(self, *, render_error: bool = False) -> None:
        self.value = _FakePlaywright(render_error=render_error)

    async def __aenter__(self) -> _FakePlaywright:
        return self.value

    async def __aexit__(self, *_args: object) -> None:
        return None


class _FakeRedis:
    async def publish(self, *_args: object, **_kwargs: object) -> int:
        return 1

    async def aclose(self) -> None:
        return None


class _FakeMinio:
    upload_error = False
    uploads: ClassVar[list[tuple[str, str, bytes]]] = []

    def __init__(self, *_args: object, **_kwargs: object) -> None:
        return None

    def fput_object(self, bucket: str, key: str, path: str, **_kwargs: object) -> None:
        if self.upload_error:
            raise RuntimeError("upload failed")
        self.uploads.append((bucket, key, Path(path).read_bytes()))


def _patch_runtime(
    monkeypatch: pytest.MonkeyPatch,
    *,
    database_url: str,
    files: dict[str, str] | None = None,
    render_error: bool = False,
    upload_error: bool = False,
) -> None:
    settings = preview.get_settings().model_copy(update={"database_url": database_url})
    monkeypatch.setattr(preview, "get_settings", lambda: settings)
    _FakeMinio.upload_error = upload_error
    _FakeMinio.uploads = []
    monkeypatch.setattr(preview.repo_svc, "read_files", lambda *_args: files or {})
    monkeypatch.setattr(
        preview,
        "async_playwright",
        lambda: _FakePlaywrightContext(render_error=render_error),
    )
    monkeypatch.setattr(preview, "Minio", _FakeMinio)
    monkeypatch.setattr(preview.minio_core, "ensure_public_bucket", lambda *_args: None)
    monkeypatch.setattr(
        preview.aioredis,
        "from_url",
        lambda *_args, **_kwargs: _FakeRedis(),
    )


def _test_database_url(session: AsyncSession) -> str:
    bind = session.bind
    assert isinstance(bind, AsyncEngine)
    return bind.url.render_as_string(hide_password=False)


async def test_success_marks_preview_ready_in_snapshot_transaction(
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    snapshot, report = await _seed_report(db_session)
    snapshot_id = snapshot.id
    report_id = report.run_id
    _patch_runtime(
        monkeypatch,
        database_url=_test_database_url(db_session),
        files={"index.html": "<main>готово</main>"},
    )

    await preview._render_async(str(snapshot_id))
    db_session.expire_all()

    stored_snapshot = await db_session.get(Snapshot, snapshot_id)
    stored_report = await db_session.get(GenerationTelegramReport, report_id)
    assert stored_snapshot is not None
    assert stored_snapshot.preview_key is not None
    assert stored_report is not None
    assert stored_report.last_stage == "preview"
    assert stored_report.preview_error_code is None
    assert _FakeMinio.uploads[0][2] == PNG


@pytest.mark.parametrize(
    ("template", "files", "live_url", "expected_code"),
    [
        ("blank", {}, "unused", "source_missing"),
        ("spa", {}, None, "container_unreachable"),
    ],
)
async def test_early_exit_records_fixed_preview_code(
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
    template: str,
    files: dict[str, str],
    live_url: str | None,
    expected_code: str,
) -> None:
    snapshot, report = await _seed_report(db_session, template=template)
    snapshot_id = snapshot.id
    report_id = report.run_id
    _patch_runtime(
        monkeypatch,
        database_url=_test_database_url(db_session),
        files=files,
    )

    async def _live_url(*_args: object) -> str | None:
        return live_url

    monkeypatch.setattr(preview, "_resolve_live_url", _live_url)
    await preview._render_async(str(snapshot_id))
    db_session.expire_all()

    stored = await db_session.get(GenerationTelegramReport, report_id)
    assert stored is not None
    assert stored.preview_error_code == expected_code
    assert stored.last_stage == "snapshot"


@pytest.mark.parametrize(
    ("render_error", "upload_error", "expected_code"),
    [
        (True, False, "render_failed"),
        (False, True, "upload_failed"),
    ],
)
async def test_render_and_upload_errors_record_fixed_code(
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
    render_error: bool,
    upload_error: bool,
    expected_code: str,
) -> None:
    snapshot, report = await _seed_report(db_session)
    snapshot_id = snapshot.id
    report_id = report.run_id
    _patch_runtime(
        monkeypatch,
        database_url=_test_database_url(db_session),
        files={"index.html": "<main>готово</main>"},
        render_error=render_error,
        upload_error=upload_error,
    )

    with pytest.raises(RuntimeError):
        await preview._render_async(str(snapshot_id))
    db_session.expire_all()

    stored = await db_session.get(GenerationTelegramReport, report_id)
    assert stored is not None
    assert stored.preview_error_code == expected_code
    assert stored.last_stage == "snapshot"


async def test_observer_ready_failure_does_not_fail_successful_preview(
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    snapshot, _report = await _seed_report(db_session)
    snapshot_id = snapshot.id
    _patch_runtime(
        monkeypatch,
        database_url=_test_database_url(db_session),
        files={"index.html": "<main>готово</main>"},
    )

    async def _observer_failure(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("observer unavailable")

    monkeypatch.setattr(preview, "mark_snapshot_preview_ready", _observer_failure, raising=False)
    await preview._render_async(str(snapshot_id))
    db_session.expire_all()

    stored_snapshot = await db_session.get(Snapshot, snapshot_id)
    assert stored_snapshot is not None
    assert stored_snapshot.preview_key is not None

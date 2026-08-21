from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta
from importlib import import_module
from types import ModuleType, SimpleNamespace

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from omnia_api.models.generation_run import GenerationRun
from omnia_api.models.generation_telegram_report import GenerationTelegramReport
from omnia_api.models.message import Message
from omnia_api.models.project import Project
from omnia_api.models.snapshot import Snapshot
from omnia_api.models.user import User
from omnia_api.services.generation_telegram_delivery import TelegramFailure

pytestmark = pytest.mark.asyncio


def _worker() -> ModuleType:
    try:
        return import_module("omnia_api.workers.generation_reports")
    except ModuleNotFoundError:
        pytest.fail("generation report delivery worker is missing", pytrace=False)


def _factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False)


async def _seed(
    session: AsyncSession,
    *,
    prompt: str = "собери страницу",
    mode: str = "build",
    status: str = "running",
    start_state: str = "pending",
    finish_state: str = "waiting_terminal",
    start_message_id: int | None = None,
    with_snapshot: bool = False,
    preview_key: str | None = None,
    terminal_status: str | None = None,
    preview_deadline_at: datetime | None = None,
) -> tuple[GenerationRun, GenerationTelegramReport, Snapshot | None]:
    suffix = uuid.uuid4().hex[:8]
    owner = User(email=f"worker-{suffix}@example.com", password_hash="x")
    session.add(owner)
    await session.flush()
    project = Project(
        owner_id=owner.id,
        name=f"Worker {suffix}",
        slug=f"worker-{suffix}",
        template="blank",
    )
    session.add(project)
    await session.flush()
    snapshot: Snapshot | None = None
    if with_snapshot:
        snapshot = Snapshot(
            project_id=project.id,
            commit_sha="b" * 40,
            prompt_text=prompt,
            model_id="test",
            preview_key=preview_key,
        )
        session.add(snapshot)
        await session.flush()
    user_message = Message(project_id=project.id, role="user", content=prompt)
    assistant = Message(
        project_id=project.id,
        role="assistant",
        content="готово" if status == "completed" else "ошибка",
        snapshot_id=snapshot.id if snapshot is not None else None,
        tokens_in=1,
        tokens_out=1,
    )
    session.add_all([user_message, assistant])
    await session.flush()
    now = datetime.now(UTC)
    run = GenerationRun(
        project_id=project.id,
        user_id=owner.id,
        user_message_id=user_message.id,
        assistant_message_id=assistant.id,
        idempotency_key=f"worker-{suffix}",
        prompt_hash="hash",
        status=status,
        response_mode=mode,
        started_at=now - timedelta(seconds=90),
        finished_at=now if status in {"completed", "failed", "cancelled"} else None,
        error=("provider failed for owner@example.com" if status == "failed" else None),
    )
    session.add(run)
    await session.flush()
    report = GenerationTelegramReport(
        run_id=run.id,
        start_state=start_state,
        start_message_id=start_message_id,
        finish_state=finish_state,
        terminal_status=terminal_status,
        last_stage="snapshot" if with_snapshot else "accepted",
        preview_deadline_at=preview_deadline_at,
    )
    session.add(report)
    await session.commit()
    return run, report, snapshot


class _Telegram:
    def __init__(self, failures: list[TelegramFailure | None] | None = None) -> None:
        self.failures = list(failures or [])
        self.messages: list[tuple[str, int | None]] = []
        self.documents: list[tuple[bytes, str, str, int]] = []
        self.photos: list[tuple[bytes, str, int]] = []
        self.next_id = 700

    def _maybe_fail(self) -> None:
        if self.failures:
            failure = self.failures.pop(0)
            if failure is not None:
                raise failure

    async def send_message(self, text: str, *, reply_to: int | None = None) -> int:
        self._maybe_fail()
        self.messages.append((text, reply_to))
        self.next_id += 1
        return self.next_id

    async def send_document(
        self,
        data: bytes,
        filename: str,
        *,
        caption: str,
        reply_to: int,
    ) -> int:
        self._maybe_fail()
        self.documents.append((data, filename, caption, reply_to))
        self.next_id += 1
        return self.next_id

    async def send_photo(self, data: bytes, *, caption: str, reply_to: int) -> int:
        self._maybe_fail()
        self.photos.append((data, caption, reply_to))
        self.next_id += 1
        return self.next_id


async def _png_loader(_key: str) -> bytes:
    return b"\x89PNG\r\nworker-preview"


async def test_claim_prioritizes_starts_and_suppresses_duplicate_claims(
    db_session: AsyncSession,
    test_engine: AsyncEngine,
) -> None:
    worker = _worker()
    first, _report, _snapshot = await _seed(db_session)
    second, _report2, _snapshot2 = await _seed(db_session)
    factory = _factory(test_engine)
    now = datetime.now(UTC)

    claim_one = await worker.claim_due_report(factory, now)
    claim_two = await worker.claim_due_report(factory, now)
    claim_three = await worker.claim_due_report(factory, now)

    assert claim_one is not None and claim_one.event == "start"
    assert claim_two is not None and claim_two.event == "start"
    assert {claim_one.run_id, claim_two.run_id} == {first.id, second.id}
    assert claim_three is None
    db_session.expire_all()
    stored = await db_session.get(GenerationTelegramReport, claim_one.run_id)
    assert stored is not None
    assert stored.start_state == "sending"
    assert stored.start_attempts == 1
    assert stored.lease_until is not None


async def test_expired_start_lease_is_reclaimed(
    db_session: AsyncSession,
    test_engine: AsyncEngine,
) -> None:
    worker = _worker()
    run, report, _snapshot = await _seed(db_session, start_state="sending")
    report.lease_until = datetime.now(UTC) - timedelta(seconds=1)
    report.start_attempts = 1
    await db_session.commit()

    claim = await worker.claim_due_report(_factory(test_engine), datetime.now(UTC))

    assert claim is not None
    assert claim.run_id == run.id
    assert claim.event == "start"
    assert claim.attempt == 2


async def test_expired_lease_at_attempt_limit_is_failed_instead_of_stuck(
    db_session: AsyncSession,
    test_engine: AsyncEngine,
) -> None:
    worker = _worker()
    run, report, _snapshot = await _seed(db_session, start_state="sending")
    run_id = run.id
    report.lease_until = datetime.now(UTC) - timedelta(seconds=1)
    report.start_attempts = worker.MAX_ATTEMPTS
    await db_session.commit()

    claim = await worker.claim_due_report(_factory(test_engine), datetime.now(UTC))
    db_session.expire_all()

    stored = await db_session.get(GenerationTelegramReport, run_id)
    assert claim is None
    assert stored is not None
    assert stored.start_state == "failed"
    assert stored.lease_until is None
    assert stored.last_delivery_error_code == "lease_attempts_exhausted"


async def test_start_checkpoint_refreshes_only_the_active_attempt_lease(
    db_session: AsyncSession,
    test_engine: AsyncEngine,
) -> None:
    worker = _worker()
    run, _report, _snapshot = await _seed(db_session)
    run_id = run.id
    factory = _factory(test_engine)
    now = datetime.now(UTC) - timedelta(seconds=30)

    active_claim = await worker.claim_due_report(factory, now)
    assert active_claim is not None
    db_session.expire_all()
    before = await db_session.get(GenerationTelegramReport, run_id)
    assert before is not None and before.lease_until is not None
    original_lease = before.lease_until

    await worker._persist_start_message_id(factory, active_claim, 701)
    db_session.expire_all()
    refreshed = await db_session.get(GenerationTelegramReport, run_id)
    assert refreshed is not None
    assert refreshed.start_message_id == 701
    assert refreshed.lease_until is not None
    assert refreshed.lease_until > original_lease


async def test_stale_attempt_cannot_overwrite_the_reclaimed_delivery_state(
    db_session: AsyncSession,
    test_engine: AsyncEngine,
) -> None:
    worker = _worker()
    run, _report, _snapshot = await _seed(db_session)
    run_id = run.id
    factory = _factory(test_engine)
    now = datetime.now(UTC)
    stale_claim = await worker.claim_due_report(factory, now)
    assert stale_claim is not None
    active_claim = await worker.claim_due_report(
        factory,
        now + timedelta(seconds=worker.LEASE_SECONDS + 1),
    )
    assert active_claim is not None and active_claim.attempt == stale_claim.attempt + 1
    db_session.expire_all()
    reclaimed = await db_session.get(GenerationTelegramReport, run_id)
    assert reclaimed is not None and reclaimed.lease_until is not None
    reclaimed_lease = reclaimed.lease_until

    await worker._persist_start_message_id(factory, stale_claim, 701)
    await worker._mark_success(factory, stale_claim)
    await worker._mark_failure(
        factory,
        stale_claim,
        code="telegram_timeout",
        retryable=True,
        now=now,
    )
    db_session.expire_all()
    stored = await db_session.get(GenerationTelegramReport, run_id)
    assert stored is not None
    assert stored.start_message_id == 701
    assert stored.start_state == "sending"
    assert stored.start_attempts == active_claim.attempt
    assert stored.lease_until == reclaimed_lease


async def test_long_prompt_resume_keeps_start_message_id_and_does_not_duplicate_text(
    db_session: AsyncSession,
    test_engine: AsyncEngine,
) -> None:
    worker = _worker()
    prompt = "точный пользовательский промпт\n" * 220
    run, _report, _snapshot = await _seed(db_session, prompt=prompt)
    run_id = run.id
    factory = _factory(test_engine)
    now = datetime.now(UTC)
    telegram = _Telegram(
        [None, TelegramFailure("telegram_network_error", retryable=True)]
    )

    claim = await worker.claim_due_report(factory, now)
    assert claim is not None
    await worker.deliver_claim(
        factory,
        claim,
        telegram,
        now=now,
        load_preview=_png_loader,
        enabled=True,
    )
    db_session.expire_all()
    stored = await db_session.get(GenerationTelegramReport, run_id)
    assert stored is not None
    assert stored.start_message_id == 701
    assert stored.start_state == "pending"
    assert len(telegram.messages) == 1
    assert telegram.documents == []

    retry_at = stored.start_next_attempt_at
    assert retry_at is not None
    second_claim = await worker.claim_due_report(factory, retry_at + timedelta(milliseconds=1))
    assert second_claim is not None
    await worker.deliver_claim(
        factory,
        second_claim,
        telegram,
        now=retry_at + timedelta(milliseconds=1),
        load_preview=_png_loader,
        enabled=True,
    )
    db_session.expire_all()
    stored = await db_session.get(GenerationTelegramReport, run_id)
    assert stored is not None and stored.start_state == "sent"
    assert len(telegram.messages) == 1
    assert telegram.documents[0][0] == prompt.encode("utf-8")
    assert telegram.documents[0][3] == 701


async def test_completed_snapshot_finish_sends_exact_preview_bytes_as_thread_reply(
    db_session: AsyncSession,
    test_engine: AsyncEngine,
) -> None:
    worker = _worker()
    run, _report, _snapshot = await _seed(
        db_session,
        status="completed",
        start_state="sent",
        start_message_id=812,
        finish_state="pending",
        terminal_status="completed",
        with_snapshot=True,
        preview_key="private/preview.png",
    )
    run_id = run.id
    factory = _factory(test_engine)
    telegram = _Telegram()
    claim = await worker.claim_due_report(factory, datetime.now(UTC))
    assert claim is not None and claim.event == "finish"

    await worker.deliver_claim(
        factory,
        claim,
        telegram,
        now=datetime.now(UTC),
        load_preview=_png_loader,
        enabled=True,
    )
    db_session.expire_all()

    stored = await db_session.get(GenerationTelegramReport, run_id)
    assert stored is not None and stored.finish_state == "sent"
    assert telegram.photos == [
        (b"\x89PNG\r\nworker-preview", telegram.photos[0][1], 812)
    ]
    assert "✅ BUILD завершён" in telegram.photos[0][1]
    assert telegram.messages == []


async def test_never_resolving_preview_load_is_bounded_and_retried(
    db_session: AsyncSession,
    test_engine: AsyncEngine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    worker = _worker()
    run, _report, _snapshot = await _seed(
        db_session,
        status="completed",
        start_state="sent",
        start_message_id=812,
        finish_state="pending",
        terminal_status="completed",
        with_snapshot=True,
        preview_key="private/preview.png",
    )
    run_id = run.id
    factory = _factory(test_engine)
    claim = await worker.claim_due_report(factory, datetime.now(UTC))
    assert claim is not None and claim.event == "finish"

    async def _never(_key: str) -> bytes:
        await asyncio.Event().wait()
        return b"unreachable"

    monkeypatch.setattr(worker, "PREVIEW_LOAD_TIMEOUT_SECONDS", 0.01, raising=False)
    await asyncio.wait_for(
        worker.deliver_claim(
            factory,
            claim,
            _Telegram(),
            now=datetime.now(UTC),
            load_preview=_never,
            enabled=True,
        ),
        timeout=1,
    )
    db_session.expire_all()
    stored = await db_session.get(GenerationTelegramReport, run_id)
    assert stored is not None
    assert stored.finish_state == "pending"
    assert stored.last_delivery_error_code == "preview_load_failed"


async def test_preview_wait_consumes_no_attempt_then_warns_and_sends_one_late_photo(
    db_session: AsyncSession,
    test_engine: AsyncEngine,
) -> None:
    worker = _worker()
    now = datetime.now(UTC)
    run, _report, snapshot = await _seed(
        db_session,
        status="completed",
        start_state="sent",
        start_message_id=913,
        finish_state="waiting_preview",
        terminal_status="completed",
        with_snapshot=True,
        preview_deadline_at=now + timedelta(seconds=30),
    )
    assert snapshot is not None
    run_id = run.id
    snapshot_id = snapshot.id
    factory = _factory(test_engine)

    assert await worker.reconcile_waiting_previews(factory, now) == 0
    assert await worker.claim_due_report(factory, now) is None
    db_session.expire_all()
    waiting = await db_session.get(GenerationTelegramReport, run_id)
    assert waiting is not None and waiting.finish_attempts == 0

    assert await worker.reconcile_waiting_previews(factory, now + timedelta(seconds=31)) == 1
    warning_claim = await worker.claim_due_report(factory, now + timedelta(seconds=31))
    assert warning_claim is not None
    telegram = _Telegram()
    await worker.deliver_claim(
        factory,
        warning_claim,
        telegram,
        now=now + timedelta(seconds=31),
        load_preview=_png_loader,
        enabled=True,
    )
    assert len(telegram.messages) == 1
    assert "preview не получен" in telegram.messages[0][0]
    assert telegram.messages[0][1] == 913

    async with factory() as session:
        stored_snapshot = await session.get(Snapshot, snapshot_id)
        assert stored_snapshot is not None
        stored_snapshot.preview_key = "late.png"
        await session.commit()
    assert await worker.reconcile_waiting_previews(factory, now + timedelta(seconds=32)) == 0
    late_claim = await worker.claim_due_report(factory, now + timedelta(seconds=32))
    assert late_claim is not None
    await worker.deliver_claim(
        factory,
        late_claim,
        telegram,
        now=now + timedelta(seconds=32),
        load_preview=_png_loader,
        enabled=True,
    )
    assert len(telegram.photos) == 1
    assert "Preview появился позже" in telegram.photos[0][1]
    assert await worker.claim_due_report(factory, now + timedelta(seconds=33)) is None


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        ("failed", "❌ BUILD упал"),
        ("cancelled", "⚪ BUILD отменён пользователем"),
    ],
)
async def test_terminal_text_is_threaded_and_failure_is_sanitized(
    db_session: AsyncSession,
    test_engine: AsyncEngine,
    status: str,
    expected: str,
) -> None:
    worker = _worker()
    run, _report, _snapshot = await _seed(
        db_session,
        status=status,
        start_state="sent",
        start_message_id=1014,
        finish_state="pending",
        terminal_status=status,
    )
    run_id = run.id
    telegram = _Telegram()
    factory = _factory(test_engine)
    claim = await worker.claim_due_report(factory, datetime.now(UTC))
    assert claim is not None
    await worker.deliver_claim(
        factory,
        claim,
        telegram,
        now=datetime.now(UTC),
        load_preview=_png_loader,
        enabled=True,
    )

    assert expected in telegram.messages[0][0]
    assert telegram.messages[0][1] == 1014
    assert "owner@example.com" not in telegram.messages[0][0]
    db_session.expire_all()
    stored = await db_session.get(GenerationTelegramReport, run_id)
    assert stored is not None and stored.finish_state == "sent"


async def test_retry_after_and_exhaustion_use_fixed_state_only(
    db_session: AsyncSession,
    test_engine: AsyncEngine,
) -> None:
    worker = _worker()
    run, report, _snapshot = await _seed(db_session)
    run_id = run.id
    report.start_attempts = worker.MAX_ATTEMPTS - 1
    await db_session.commit()
    factory = _factory(test_engine)
    now = datetime.now(UTC)
    claim = await worker.claim_due_report(factory, now)
    assert claim is not None and claim.attempt == worker.MAX_ATTEMPTS
    telegram = _Telegram(
        [
            TelegramFailure(
                "telegram_rate_limited",
                retryable=True,
                retry_after_seconds=900,
            )
        ]
    )
    await worker.deliver_claim(
        factory,
        claim,
        telegram,
        now=now,
        load_preview=_png_loader,
        enabled=True,
    )
    db_session.expire_all()

    stored = await db_session.get(GenerationTelegramReport, run_id)
    assert stored is not None
    assert stored.start_state == "failed"
    assert stored.last_delivery_error_code == "telegram_rate_limited"
    assert stored.lease_until is None
    assert stored.start_next_attempt_at is None


async def test_permanent_telegram_failure_and_missing_build_snapshot_do_not_retry(
    db_session: AsyncSession,
    test_engine: AsyncEngine,
) -> None:
    worker = _worker()
    permanent_run, _report, _snapshot = await _seed(db_session)
    permanent_id = permanent_run.id
    missing_run, _report2, _snapshot2 = await _seed(
        db_session,
        status="completed",
        start_state="sent",
        start_message_id=991,
        finish_state="pending",
        terminal_status="completed",
    )
    missing_id = missing_run.id
    factory = _factory(test_engine)
    now = datetime.now(UTC)

    start_claim = await worker.claim_due_report(factory, now)
    assert start_claim is not None and start_claim.run_id == permanent_id
    await worker.deliver_claim(
        factory,
        start_claim,
        _Telegram([TelegramFailure("telegram_forbidden", retryable=False)]),
        now=now,
        load_preview=_png_loader,
        enabled=True,
    )
    finish_claim = await worker.claim_due_report(factory, now)
    assert finish_claim is not None and finish_claim.run_id == missing_id
    await worker.deliver_claim(
        factory,
        finish_claim,
        _Telegram(),
        now=now,
        load_preview=_png_loader,
        enabled=True,
    )
    db_session.expire_all()

    permanent = await db_session.get(GenerationTelegramReport, permanent_id)
    missing = await db_session.get(GenerationTelegramReport, missing_id)
    assert permanent is not None and permanent.start_state == "failed"
    assert permanent.last_delivery_error_code == "telegram_forbidden"
    assert missing is not None and missing.finish_state == "failed"
    assert missing.last_delivery_error_code == "source_snapshot_missing"


async def test_preview_loader_uses_internal_bytes_and_always_releases_response(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    worker = _worker()

    class _Response:
        closed = False
        released = False

        def read(self) -> bytes:
            return b"exact internal png"

        def close(self) -> None:
            self.closed = True

        def release_conn(self) -> None:
            self.released = True

    response = _Response()
    requested: list[tuple[str, str]] = []

    class _Minio:
        def get_object(self, bucket: str, key: str) -> _Response:
            requested.append((bucket, key))
            return response

    monkeypatch.setattr(worker, "get_minio_client", lambda: _Minio())
    monkeypatch.setattr(
        worker,
        "get_settings",
        lambda: SimpleNamespace(minio_bucket_previews="previews"),
    )

    data = await worker.load_preview_bytes("opaque-preview-key")

    assert data == b"exact internal png"
    assert requested == [("previews", "opaque-preview-key")]
    assert response.closed is True
    assert response.released is True


async def test_disabled_cycle_suppresses_without_external_calls(
    db_session: AsyncSession,
    test_engine: AsyncEngine,
) -> None:
    worker = _worker()
    run, _report, _snapshot = await _seed(db_session)
    run_id = run.id
    telegram = _Telegram()

    worked = await worker.run_cycle(
        _factory(test_engine),
        telegram,
        now=datetime.now(UTC),
        load_preview=_png_loader,
        enabled=False,
    )
    db_session.expire_all()

    stored = await db_session.get(GenerationTelegramReport, run_id)
    assert worked is False
    assert stored is not None
    assert stored.start_state == "suppressed"
    assert stored.finish_state == "suppressed"
    assert telegram.messages == []
    assert telegram.documents == []
    assert telegram.photos == []


async def test_logs_and_delivery_codes_never_contain_sensitive_values(
    db_session: AsyncSession,
    test_engine: AsyncEngine,
    caplog: pytest.LogCaptureFixture,
) -> None:
    worker = _worker()
    secret = "12345678:" + "Z" * 30
    prompt = f"owner@example.com {secret} https://minio/x.png?signature=secret"
    run, _report, _snapshot = await _seed(db_session, prompt=prompt)
    run_id = run.id
    telegram = _Telegram(
        [TelegramFailure("telegram_server_error", retryable=True)]
    )
    factory = _factory(test_engine)
    now = datetime.now(UTC)
    claim = await worker.claim_due_report(factory, now)
    assert claim is not None

    with caplog.at_level("INFO"):
        await worker.deliver_claim(
            factory,
            claim,
            telegram,
            now=now,
            load_preview=_png_loader,
            enabled=True,
        )
    db_session.expire_all()

    stored = await db_session.get(GenerationTelegramReport, run_id)
    evidence = caplog.text + str(stored.last_delivery_error_code if stored else "")
    assert secret not in evidence
    assert "owner@example.com" not in evidence
    assert "minio" not in evidence.lower()
    assert "signature=secret" not in evidence
    assert "postgresql" not in evidence.lower()

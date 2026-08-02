from __future__ import annotations

from uuid import uuid4

from rq import Retry
from rq.job import JobStatus

from omnia_api.services import queue


class _Connection:
    def __init__(self, acquired: bool = True) -> None:
        self.acquired = acquired
        self.set_calls: list[tuple[object, ...]] = []
        self.deleted: list[str] = []

    def set(self, *args: object, **kwargs: object) -> bool:
        self.set_calls.append((*args, kwargs))
        return self.acquired

    def delete(self, key: str) -> None:
        self.deleted.append(key)


class _Job:
    def __init__(self, status: JobStatus) -> None:
        self.status = status
        self.deleted = False

    def get_status(self, *, refresh: bool) -> JobStatus:
        assert refresh is True
        return self.status

    def delete(self) -> None:
        self.deleted = True


class _Queue:
    def __init__(self, existing: _Job | None = None) -> None:
        self.calls: list[tuple[tuple[object, ...], dict[str, object]]] = []
        self.existing = existing

    def fetch_job(self, _job_id: str) -> _Job | None:
        return self.existing

    def enqueue(self, *args: object, **kwargs: object) -> None:
        self.calls.append((args, kwargs))


def test_enqueue_preview_is_bounded_retryable_and_deduplicated(monkeypatch) -> None:
    connection = _Connection()
    fake_queue = _Queue()
    monkeypatch.setattr(queue, "_connection", lambda: connection)
    monkeypatch.setattr(queue, "Queue", lambda *args, **kwargs: fake_queue)
    snapshot_id = uuid4()

    assert queue.enqueue_preview(snapshot_id) is True
    assert len(fake_queue.calls) == 1
    args, kwargs = fake_queue.calls[0]
    assert args == (queue.PREVIEW_JOB, str(snapshot_id))
    assert kwargs["job_id"] == f"snapshot-preview-{snapshot_id}"
    assert kwargs["job_timeout"] == 240
    retry = kwargs["retry"]
    assert isinstance(retry, Retry)
    assert retry.max == 2
    assert retry.intervals == [10, 30]
    assert connection.deleted == [f"omnia:preview:enqueue:{snapshot_id}"]


def test_enqueue_preview_skips_duplicate_lock(monkeypatch) -> None:
    connection = _Connection(acquired=False)
    fake_queue = _Queue()
    monkeypatch.setattr(queue, "_connection", lambda: connection)
    monkeypatch.setattr(queue, "Queue", lambda *args, **kwargs: fake_queue)

    assert queue.enqueue_preview(uuid4()) is False
    assert fake_queue.calls == []


def test_enqueue_preview_uses_active_rq_job_as_source_of_truth(monkeypatch) -> None:
    connection = _Connection()
    existing = _Job(JobStatus.SCHEDULED)
    fake_queue = _Queue(existing)
    monkeypatch.setattr(queue, "_connection", lambda: connection)
    monkeypatch.setattr(queue, "Queue", lambda *args, **kwargs: fake_queue)

    assert queue.enqueue_preview(uuid4()) is False
    assert fake_queue.calls == []
    assert existing.deleted is False


def test_enqueue_preview_replaces_terminal_failed_job(monkeypatch) -> None:
    connection = _Connection()
    existing = _Job(JobStatus.FAILED)
    fake_queue = _Queue(existing)
    monkeypatch.setattr(queue, "_connection", lambda: connection)
    monkeypatch.setattr(queue, "Queue", lambda *args, **kwargs: fake_queue)

    assert queue.enqueue_preview(uuid4()) is True
    assert existing.deleted is True
    assert len(fake_queue.calls) == 1

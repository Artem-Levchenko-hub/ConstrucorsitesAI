"""AV23.1: the canary retries its own project's DELETE after 409/503 with backoff
and a deadline, cancels only its own unfinished work, refuses foreign IDs and
keeps the primary error separate from a cleanup failure. FV017/FV021/FV022."""

from __future__ import annotations

from dataclasses import dataclass, field

import httpx
import pytest

from tests.production_canary_fakes import PROJECT_ID, FakeProduction
from tests.test_production_canary import _test_config
from yleum_api.ops import production_canary as canary_module


@dataclass
class BusyProduction(FakeProduction):
    """DELETE answers from a script: the last status repeats forever."""

    delete_statuses: list[int] = field(default_factory=lambda: [204])
    restoration_cancellable: bool = False
    restoration_cancel_status: int = 200
    deletes: int = 0
    restoration_cancels: int = 0

    def handle(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == f"/api/projects/{PROJECT_ID}" and request.method == "DELETE":
            self.deletes += 1
            status = self.delete_statuses[min(self.deletes, len(self.delete_statuses)) - 1]
            return httpx.Response(status)
        if path == f"/api/projects/{PROJECT_ID}/restorations" and request.method == "GET":
            items = [{"id": "op-qa", "state": "ready", "can_cancel": True}] if (
                self.restoration_cancellable
            ) else []
            return httpx.Response(200, json={"items": items, "enabled": True})
        if path == f"/api/projects/{PROJECT_ID}/restorations/op-qa/cancel":
            self.restoration_cancels += 1
            if self.restoration_cancel_status == 200:
                self.restoration_cancellable = False
            return httpx.Response(
                self.restoration_cancel_status, json={"id": "op-qa", "state": "reconciling"}
            )
        return super().handle(request)


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0
        self.sleeps: list[float] = []

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


def _run(scenario: BusyProduction, clock: Clock, events: list[dict[str, object]]):
    return canary_module.ProductionCanary(
        _test_config(),
        transport=httpx.MockTransport(scenario.handle),
        clock=clock,
        sleep=clock.sleep,
        emit=events.append,
    ).run()


def _delete_events(events: list[dict[str, object]]) -> list[dict[str, object]]:
    return [event for event in events if event["step"] == "project_delete"]


def test_busy_delete_is_retried_until_it_succeeds():  # FV017
    scenario = BusyProduction(delete_statuses=[409, 503, 204], restoration_cancellable=True)
    clock, events = Clock(), []

    result = _run(scenario, clock, events)

    assert result.cleanup_complete is True
    assert scenario.deletes == 3
    statuses = [
        (e["status"], e.get("attempts"), e.get("http_status")) for e in _delete_events(events)
    ]
    assert statuses == [("retry", 1, 409), ("retry", 2, 503), ("ok", 3, None)]
    assert len(clock.sleeps) == 2
    assert 1.5 <= clock.sleeps[0] <= 2.5 and 3.0 <= clock.sleeps[1] <= 5.0
    # On 409 the canary cancelled its own QA restoration before retrying.
    assert scenario.restoration_cancels == 1
    assert any(e["step"] == "restoration_cancel" for e in events)


def test_endless_busy_stops_at_the_cleanup_deadline_with_the_primary_outcome_intact():  # FV022
    scenario = BusyProduction(delete_statuses=[409])
    clock, events = Clock(), []

    with pytest.raises(canary_module.CanaryCleanupFailure) as failure:
        _run(scenario, clock, events)

    assert failure.value.cleanup == "failed"
    last = _delete_events(events)[-1]
    assert last["status"] == "failed" and last["error_code"] == "cleanup_deadline"
    assert scenario.deletes >= 5  # bounded backoff: several attempts within 180 s
    assert sum(clock.sleeps) <= canary_module._CLEANUP_RETRY_DEADLINE_SECONDS + 20
    assert max(clock.sleeps) <= canary_module._CLEANUP_RETRY_MAX_SECONDS * 1.25
    # The generation itself succeeded; only cleanup is reported as failed.
    assert any(e["step"] == "generation" and e["status"] == "ok" for e in events)


def test_definitive_refusal_is_not_retried():
    scenario = BusyProduction(delete_statuses=[403])
    clock, events = Clock(), []

    with pytest.raises(canary_module.CanaryCleanupFailure):
        _run(scenario, clock, events)

    assert scenario.deletes == 1 and clock.sleeps == []
    assert _delete_events(events)[-1]["error_code"] == "project_delete_rejected"


def test_only_the_project_this_run_created_can_be_deleted():  # FV021
    scenario = BusyProduction()
    events: list[dict[str, object]] = []
    canary = canary_module.ProductionCanary(
        _test_config(), transport=httpx.MockTransport(scenario.handle), emit=events.append
    )
    canary._created_project_id = PROJECT_ID

    assert canary._delete_project_with_retry("11111111-2222-4333-8444-555555555555") is False

    assert scenario.deletes == 0
    assert _delete_events(events) == [{
        "step": "project_delete", "status": "refused",
        "elapsed_seconds": events[0]["elapsed_seconds"],
        "project_id": "11111111-2222-4333-8444-555555555555", "error_code": "foreign_project",
    }]


def test_a_rejected_restoration_cancel_is_reported_honestly():
    scenario = BusyProduction(
        delete_statuses=[409, 204], restoration_cancellable=True, restoration_cancel_status=409
    )
    clock, events = Clock(), []

    result = _run(scenario, clock, events)

    assert result.cleanup_complete is True and scenario.restoration_cancels == 1
    cancel = [e for e in events if e["step"] == "restoration_cancel"]
    assert [(e["status"], e.get("error_code")) for e in cancel] == [
        ("failed", "restoration_cancel_rejected")
    ]

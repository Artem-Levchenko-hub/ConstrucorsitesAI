from datetime import UTC, datetime
from uuid import uuid4

import pytest

from yleum_api.core.errors import ApiError
from yleum_api.schemas.max_staff import StaffAction, StaffRecordUpdate, StaffResource, StaffStatus
from yleum_api.services.max_staff import order_times, transition


@pytest.mark.parametrize(
    ("delay", "payment_minute", "expected_pickup", "expected_start"),
    [(30, 10, 30, 23), (30, 26, 30, 26), (0, 26, 33, 26)],
)
def test_order_enters_queue_at_planned_or_late_payment_time(
    delay: int,
    payment_minute: int,
    expected_pickup: int,
    expected_start: int,
) -> None:
    scheduled = datetime(2026, 11, 6, 10, delay, tzinfo=UTC)
    paid_at = datetime(2026, 11, 6, 10, payment_minute, tzinfo=UTC)
    pickup, start = order_times(scheduled, delay, paid_at, prep_minutes=7)
    assert (pickup.minute, start.minute) == (expected_pickup, expected_start)


def test_barista_can_only_finish_their_own_order_in_sequence() -> None:
    barista, coworker = uuid4(), uuid4()

    def update(action: StaffAction) -> StaffRecordUpdate:
        return StaffRecordUpdate(
            resource=StaffResource.orders,
            action=action,
            expected_revision="a" * 64,
            operation_key=uuid4(),
        )

    assert transition(StaffStatus.new, None, barista, False, update(StaffAction.claim)) == (
        StaffStatus.in_progress,
        barista,
    )
    with pytest.raises(ApiError):
        transition(StaffStatus.in_progress, barista, coworker, False, update(StaffAction.claim))
    with pytest.raises(ApiError):
        transition(StaffStatus.in_progress, barista, coworker, False, update(StaffAction.ready))
    with pytest.raises(ApiError):
        transition(StaffStatus.in_progress, barista, barista, False, update(StaffAction.hand_over))
    assert transition(
        StaffStatus.in_progress,
        barista,
        barista,
        False,
        update(StaffAction.ready),
    ) == (StaffStatus.ready, barista)
    assert transition(
        StaffStatus.ready,
        barista,
        barista,
        False,
        update(StaffAction.hand_over),
    ) == (StaffStatus.completed, barista)

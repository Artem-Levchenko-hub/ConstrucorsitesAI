from datetime import UTC, datetime, timedelta

import pytest


@pytest.mark.parametrize(
    "hours,expected",
    [(73, None), (72, 3), (49, 3), (48, None), (25, None), (24, 1), (1, 1), (0, None)],
)
def test_windows_match_actual_time_remaining(hours, expected):
    from yleum_api.services.subscription_email import reminder_days

    now = datetime(2026, 10, 26, tzinfo=UTC)
    assert reminder_days(now + timedelta(hours=hours), now) == expected

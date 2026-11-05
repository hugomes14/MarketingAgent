from datetime import UTC, datetime, timedelta

import pytest

from marketing_agent.clock import FixedClock, SystemClock


def test_fixed_clock_advance() -> None:
    clock = FixedClock(datetime(2026, 11, 1, tzinfo=UTC))
    clock.advance(timedelta(hours=2))
    assert clock.now() == datetime(2026, 11, 1, 2, tzinfo=UTC)


def test_naive_datetime_rejected() -> None:
    with pytest.raises(ValueError):
        FixedClock(datetime(2026, 11, 1))


def test_system_clock_returns_utc_aware() -> None:
    now = SystemClock().now()
    assert now.tzinfo is not None
    assert now.utcoffset() == timedelta(0)

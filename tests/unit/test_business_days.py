"""営業日・在籍営業日の試験（CLC-003、CLC-004）。"""

from datetime import date

import pytest

from crm_g.domain.business_days import BusinessCalendar, is_present, present_business_days


def test_business_calendar_day_count() -> None:
    calendar = BusinessCalendar((date(2026, 11, 2), date(2026, 11, 4), date(2026, 11, 5)))

    assert calendar.day_count == 3


def test_business_calendar_rejects_duplicate_days() -> None:
    with pytest.raises(ValueError, match="重複"):
        BusinessCalendar((date(2026, 11, 2), date(2026, 11, 2)))


def test_business_calendar_rejects_unsorted_days() -> None:
    with pytest.raises(ValueError, match="昇順"):
        BusinessCalendar((date(2026, 11, 5), date(2026, 11, 2)))


def test_is_present_without_join_or_retire_date_is_true() -> None:
    assert is_present(date(2026, 11, 10), None, None) is True


def test_is_present_before_join_date_is_false() -> None:
    assert is_present(date(2026, 11, 10), date(2026, 11, 16), None) is False


def test_is_present_on_join_date_is_true() -> None:
    assert is_present(date(2026, 11, 16), date(2026, 11, 16), None) is True


def test_is_present_after_retire_date_is_false() -> None:
    assert is_present(date(2026, 11, 10), None, date(2026, 11, 9)) is False


def test_is_present_on_retire_date_is_true() -> None:
    assert is_present(date(2026, 11, 9), None, date(2026, 11, 9)) is True


def test_present_business_days_filters_calendar() -> None:
    calendar = BusinessCalendar((date(2026, 11, 2), date(2026, 11, 16), date(2026, 11, 20)))

    result = present_business_days(calendar, date(2026, 11, 16), None)

    assert result == (date(2026, 11, 16), date(2026, 11, 20))

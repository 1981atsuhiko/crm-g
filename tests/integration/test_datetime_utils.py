"""日時の保存形式の試験（データ設計書 DBD-005）。"""

from datetime import UTC, datetime, timedelta, timezone

import pytest

from crm_g.infrastructure.datetime_utils import from_iso8601, to_iso8601, utc_now


def test_utc_now_has_utc_timezone() -> None:
    now = utc_now()

    assert now.tzinfo is not None
    assert now.utcoffset() == timedelta(0)


def test_to_iso8601_formats_utc_datetime_with_trailing_z() -> None:
    value = datetime(2026, 10, 23, 9, 30, 0, tzinfo=UTC)

    assert to_iso8601(value) == "2026-10-23T09:30:00.000000Z"


def test_to_iso8601_converts_non_utc_timezone_to_utc() -> None:
    jst = timezone(timedelta(hours=9))
    value = datetime(2026, 10, 23, 18, 30, 0, tzinfo=jst)

    assert to_iso8601(value) == "2026-10-23T09:30:00.000000Z"


def test_to_iso8601_always_includes_six_digit_microseconds() -> None:
    """桁数が変わると、TEXT列の辞書式比較が時系列と一致しなくなるため、常に6桁にする。"""
    without_microseconds = datetime(2026, 10, 23, 9, 30, 0, tzinfo=UTC)
    with_microseconds = datetime(2026, 10, 23, 9, 30, 0, 500_000, tzinfo=UTC)

    earlier = to_iso8601(without_microseconds)
    later = to_iso8601(with_microseconds)

    assert len(earlier) == len(later)
    assert earlier < later


def test_to_iso8601_rejects_naive_datetime() -> None:
    with pytest.raises(ValueError, match="タイムゾーン"):
        to_iso8601(datetime(2026, 10, 23, 9, 30, 0))


def test_from_iso8601_parses_trailing_z_as_utc() -> None:
    result = from_iso8601("2026-10-23T09:30:00Z")

    assert result == datetime(2026, 10, 23, 9, 30, 0, tzinfo=UTC)


def test_from_iso8601_rejects_value_without_timezone() -> None:
    with pytest.raises(ValueError, match="タイムゾーン"):
        from_iso8601("2026-10-23T09:30:00")


def test_from_iso8601_parses_offset_timezone_as_utc() -> None:
    result = from_iso8601("2026-10-23T18:30:00+09:00")

    assert result == datetime(2026, 10, 23, 9, 30, 0, tzinfo=UTC)


def test_round_trip_through_iso8601() -> None:
    original = utc_now()

    restored = from_iso8601(to_iso8601(original))

    assert restored == original.astimezone(UTC)

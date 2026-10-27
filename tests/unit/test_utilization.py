"""稼働人日率・待機者の試験（`CLC-025`）。"""

from datetime import date
from fractions import Fraction

from crm_g.domain.utilization import (
    StatusSpan,
    WaitingSummary,
    count_billable_man_days,
    man_day_ratio,
    summarize_waiting,
)

_FIRST = date(2026, 10, 1)
_LAST = date(2026, 10, 31)
_DAYS = [date(2026, 10, 1), date(2026, 10, 2), date(2026, 10, 5), date(2026, 10, 6)]


def _span(
    start: date, end: date | None, code: str = "DEV", name: str = "開発稼働", billable: bool = True
) -> StatusSpan:
    return StatusSpan(start, end, code, name, billable)


def _wait_full(start: date, end: date | None) -> StatusSpan:
    return _span(start, end, "WAIT_FULL", "待機（減額無し）", billable=False)


def _wait_cut(start: date, end: date | None) -> StatusSpan:
    return _span(start, end, "WAIT_CUT", "待機（減額あり）", billable=False)


def test_count_billable_man_days_counts_only_billable_spans() -> None:
    spans = [_span(date(2026, 10, 1), date(2026, 10, 2)), _wait_full(date(2026, 10, 5), None)]

    assert count_billable_man_days(_DAYS, spans) == 2


def test_count_billable_man_days_without_spans_is_zero() -> None:
    assert count_billable_man_days(_DAYS, []) == 0


def test_man_day_ratio() -> None:
    assert man_day_ratio(1, 2) == Fraction(1, 2)


def test_man_day_ratio_without_present_days_is_none() -> None:
    assert man_day_ratio(0, 0) is None


def test_summarize_waiting_without_waiting_is_none() -> None:
    assert summarize_waiting(_FIRST, _LAST, _DAYS, [_span(_FIRST, None)]) is None


def test_summarize_waiting_ignores_waiting_without_present_days() -> None:
    # 在籍営業日がない日（週末など）だけの待機は、待機者に含めない。
    spans = [_wait_full(date(2026, 10, 3), date(2026, 10, 4))]

    assert summarize_waiting(_FIRST, _LAST, _DAYS, spans) is None


def test_summarize_waiting_joins_names_and_clips_periods() -> None:
    spans = [
        _wait_cut(date(2026, 10, 5), None),
        _wait_full(date(2026, 9, 1), date(2026, 10, 1)),
        _wait_full(date(2026, 10, 2), date(2026, 10, 2)),
    ]

    assert summarize_waiting(_FIRST, _LAST, _DAYS, spans) == WaitingSummary(
        "待機（減額無し）→待機（減額あり）",
        4,
        (
            (date(2026, 10, 1), date(2026, 10, 1)),
            (date(2026, 10, 2), date(2026, 10, 2)),
            (date(2026, 10, 5), date(2026, 10, 31)),
        ),
    )

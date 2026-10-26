"""社員×月の表のセルの試験（`SCN-050`、`CLC-011`）。"""

from datetime import date

from crm_g.domain.work_period_summary import StatusPeriod, summarize_month

_START = date(2026, 10, 1)
_END = date(2026, 10, 31)
_BUSINESS_DAYS = (date(2026, 10, 1), date(2026, 10, 15), date(2026, 10, 30))


def test_joins_status_names_in_date_order() -> None:
    periods = (
        StatusPeriod(date(2026, 10, 16), None, "開発稼働"),
        StatusPeriod(date(2026, 9, 1), date(2026, 10, 15), "待機（減額あり）"),
    )

    assert summarize_month(_START, _END, periods, _BUSINESS_DAYS) == "待機（減額あり）→開発稼働"


def test_merges_consecutive_same_status() -> None:
    periods = (
        StatusPeriod(date(2026, 10, 1), date(2026, 10, 15), "開発稼働"),
        StatusPeriod(date(2026, 10, 16), None, "開発稼働"),
    )

    assert summarize_month(_START, _END, periods, _BUSINESS_DAYS) == "開発稼働"


def test_marks_unset_when_present_business_day_has_no_period() -> None:
    periods = (StatusPeriod(date(2026, 10, 10), None, "開発稼働"),)

    assert summarize_month(_START, _END, periods, _BUSINESS_DAYS) == "開発稼働（未設定あり）"


def test_only_unset_mark_when_no_period() -> None:
    assert summarize_month(_START, _END, (), _BUSINESS_DAYS) == "（未設定あり）"


def test_empty_when_no_period_and_no_present_business_day() -> None:
    assert summarize_month(_START, _END, (), ()) == ""


def test_ignores_periods_outside_the_month() -> None:
    periods = (
        StatusPeriod(date(2026, 9, 1), date(2026, 9, 30), "待機（減額なし）"),
        StatusPeriod(date(2026, 11, 1), None, "開発稼働"),
        StatusPeriod(date(2026, 10, 1), date(2026, 10, 31), "教育"),
    )

    assert summarize_month(_START, _END, periods, _BUSINESS_DAYS) == "教育"

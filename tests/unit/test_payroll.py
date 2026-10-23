"""給与支払額・減額・正誤判定の試験（TC-CLC-001〜004、010、TC-CAL-001、002、008）。

入力は、計算仕様書9章の数値例（9.1〜9.4）による。
"""

from datetime import date
from fractions import Fraction

import pytest

from crm_g.domain.business_days import BusinessCalendar
from crm_g.domain.pay_item import PayItem
from crm_g.domain.payroll import (
    PayrollItemResult,
    WorkPeriod,
    WorkStatusRate,
    calculate_payroll,
    verify_payroll_totals,
)
from crm_g.domain.salary_history import (
    SalaryHistory,
    SalaryHistoryItem,
    select_effective_salary_history,
)

_BASE = PayItem("BASE", "基本給", "基本給", True, False, None, 1)
_DUTY = PayItem("DUTY", "勤務手当（職務給）", "固定支給", True, False, None, 2)
_OVERTIME = PayItem("OVERTIME", "固定残業手当", "固定支給", True, False, 7, 3)
_HOUSING = PayItem("HOUSING", "住宅手当", "固定支給", True, False, 6, 4)
_LEADER = PayItem("LEADER", "リーダー手当", "固定支給", True, False, 1, 7)

_EX000_ITEMS = (
    SalaryHistoryItem(_BASE, 200_000),
    SalaryHistoryItem(_DUTY, 50_000),
    SalaryHistoryItem(_OVERTIME, 30_000),
    SalaryHistoryItem(_HOUSING, 10_000),
    SalaryHistoryItem(_LEADER, 10_000),
)
_MONTHLY_SALARY = 300_000

_NOVEMBER_2026_BUSINESS_DAYS = BusinessCalendar(
    (
        date(2026, 11, 2),
        date(2026, 11, 4),
        date(2026, 11, 5),
        date(2026, 11, 6),
        date(2026, 11, 9),
        date(2026, 11, 10),
        date(2026, 11, 11),
        date(2026, 11, 12),
        date(2026, 11, 13),
        date(2026, 11, 16),
        date(2026, 11, 17),
        date(2026, 11, 18),
        date(2026, 11, 19),
        date(2026, 11, 20),
        date(2026, 11, 24),
        date(2026, 11, 25),
        date(2026, 11, 26),
        date(2026, 11, 27),
        date(2026, 11, 30),
    )
)
_ALWAYS_VALID_FROM = date(2000, 1, 1)


def _item_paid(items: tuple[PayrollItemResult, ...], code: str) -> int:
    return next(result.paid_amount for result in items if result.pay_item.code == code)


def test_tc_clc_001_ex001_waiting_then_development() -> None:
    work_periods = (
        WorkPeriod(date(2026, 11, 2), date(2026, 11, 9), "WAIT"),
        WorkPeriod(date(2026, 11, 10), date(2026, 11, 30), "DEV"),
    )
    work_status_rates = (
        WorkStatusRate("WAIT", _ALWAYS_VALID_FROM, None, Fraction(3, 5)),
        WorkStatusRate("DEV", _ALWAYS_VALID_FROM, None, Fraction(1)),
    )

    result = calculate_payroll(
        monthly_salary=_MONTHLY_SALARY,
        items=_EX000_ITEMS,
        business_calendar=_NOVEMBER_2026_BUSINESS_DAYS,
        join_date=None,
        retire_date=None,
        work_periods=work_periods,
        work_status_rates=work_status_rates,
    )

    assert result.paid_amount == 268_422
    assert result.deduction == 31_578
    assert _item_paid(result.items, "OVERTIME") == 18_422
    assert result.check_result == "OK"


def test_tc_clc_002_ex002_join_mid_month() -> None:
    work_periods = (WorkPeriod(date(2026, 11, 2), date(2026, 11, 30), "EDU"),)
    work_status_rates = (WorkStatusRate("EDU", _ALWAYS_VALID_FROM, None, Fraction(1)),)

    result = calculate_payroll(
        monthly_salary=_MONTHLY_SALARY,
        items=_EX000_ITEMS,
        business_calendar=_NOVEMBER_2026_BUSINESS_DAYS,
        join_date=date(2026, 11, 16),
        retire_date=None,
        work_periods=work_periods,
        work_status_rates=work_status_rates,
    )

    assert result.paid_amount == 157_895
    assert result.deduction == 142_105
    assert _item_paid(result.items, "BASE") == 107_895


def test_tc_clc_003_ex003_leave_whole_month() -> None:
    work_periods = (WorkPeriod(date(2026, 11, 2), date(2026, 11, 30), "LEAVE"),)
    work_status_rates = (WorkStatusRate("LEAVE", _ALWAYS_VALID_FROM, None, Fraction(0)),)

    result = calculate_payroll(
        monthly_salary=_MONTHLY_SALARY,
        items=_EX000_ITEMS,
        business_calendar=_NOVEMBER_2026_BUSINESS_DAYS,
        join_date=None,
        retire_date=None,
        work_periods=work_periods,
        work_status_rates=work_status_rates,
    )

    assert result.paid_amount == 0
    assert result.deduction == 300_000
    assert _item_paid(result.items, "BASE") == 0
    assert _item_paid(result.items, "DUTY") == 0


def test_tc_clc_004_ex004_full_development() -> None:
    work_periods = (WorkPeriod(date(2026, 11, 2), date(2026, 11, 30), "DEV"),)
    work_status_rates = (WorkStatusRate("DEV", _ALWAYS_VALID_FROM, None, Fraction(1)),)

    result = calculate_payroll(
        monthly_salary=_MONTHLY_SALARY,
        items=_EX000_ITEMS,
        business_calendar=_NOVEMBER_2026_BUSINESS_DAYS,
        join_date=None,
        retire_date=None,
        work_periods=work_periods,
        work_status_rates=work_status_rates,
    )

    assert result.paid_amount == 300_000
    assert result.deduction == 0


def test_tc_clc_010_item_totals_match_ceil_of_p_raw() -> None:
    work_periods = (
        WorkPeriod(date(2026, 11, 2), date(2026, 11, 9), "WAIT"),
        WorkPeriod(date(2026, 11, 10), date(2026, 11, 30), "DEV"),
    )
    work_status_rates = (
        WorkStatusRate("WAIT", _ALWAYS_VALID_FROM, None, Fraction(3, 5)),
        WorkStatusRate("DEV", _ALWAYS_VALID_FROM, None, Fraction(1)),
    )

    result = calculate_payroll(
        monthly_salary=_MONTHLY_SALARY,
        items=_EX000_ITEMS,
        business_calendar=_NOVEMBER_2026_BUSINESS_DAYS,
        join_date=None,
        retire_date=None,
        work_periods=work_periods,
        work_status_rates=work_status_rates,
    )

    assert result.paid_amount == 268_422
    assert sum(item.paid_amount for item in result.items) == result.paid_amount


def test_tc_cal_001_verify_payroll_totals() -> None:
    assert verify_payroll_totals(300_000, 268_422, 31_000) == "要確認"
    assert verify_payroll_totals(300_000, 268_422, 31_578) == "OK"


def test_tc_cal_002_undetermined_work_status_days() -> None:
    base = PayItem("BASE", "基本給", "基本給", True, False, None, 1)
    duty = PayItem("DUTY", "勤務手当", "固定支給", True, False, None, 2)
    items = (SalaryHistoryItem(base, 200_000), SalaryHistoryItem(duty, 100_000))
    business_calendar = BusinessCalendar(tuple(date(2026, 10, day) for day in range(1, 21)))
    work_periods = (WorkPeriod(date(2026, 10, 1), date(2026, 10, 15), "DEV"),)
    work_status_rates = (WorkStatusRate("DEV", _ALWAYS_VALID_FROM, None, Fraction(1)),)

    result = calculate_payroll(
        monthly_salary=300_000,
        items=items,
        business_calendar=business_calendar,
        join_date=None,
        retire_date=None,
        work_periods=work_periods,
        work_status_rates=work_status_rates,
    )

    assert result.paid_amount == 225_000
    assert result.deduction == 75_000
    assert result.check_result == "要確認"


def test_tc_cal_008_mid_month_salary_history_change() -> None:
    base_before = PayItem("BASE", "基本給", "基本給", True, False, None, 1)
    duty_before = PayItem("DUTY", "勤務手当", "固定支給", True, False, None, 2)
    history_before = SalaryHistory(
        date(2026, 10, 1),
        date(2026, 10, 15),
        (SalaryHistoryItem(base_before, 200_000), SalaryHistoryItem(duty_before, 100_000)),
    )
    history_after = SalaryHistory(
        date(2026, 10, 16),
        None,
        (SalaryHistoryItem(base_before, 220_000), SalaryHistoryItem(duty_before, 110_000)),
    )
    business_calendar = BusinessCalendar(tuple(date(2026, 10, day) for day in range(1, 21)))
    basis_date = business_calendar.business_days[-1]

    effective_history = select_effective_salary_history((history_before, history_after), basis_date)
    assert effective_history is not None

    work_periods = (WorkPeriod(date(2026, 10, 1), date(2026, 10, 20), "DEV"),)
    work_status_rates = (WorkStatusRate("DEV", _ALWAYS_VALID_FROM, None, Fraction(1)),)

    result = calculate_payroll(
        monthly_salary=effective_history.monthly_salary,
        items=effective_history.items,
        business_calendar=business_calendar,
        join_date=None,
        retire_date=None,
        work_periods=work_periods,
        work_status_rates=work_status_rates,
    )

    assert result.paid_amount == 330_000
    assert result.deduction == 0


def test_work_period_rejects_end_date_before_start_date() -> None:
    with pytest.raises(ValueError, match="end_date"):
        WorkPeriod(date(2026, 11, 20), date(2026, 11, 10), "DEV")


def test_work_status_rate_rejects_valid_to_before_valid_from() -> None:
    with pytest.raises(ValueError, match="valid_to"):
        WorkStatusRate("DEV", date(2026, 11, 20), date(2026, 11, 10), Fraction(1))


def test_work_status_rate_rejects_rate_above_one() -> None:
    with pytest.raises(ValueError, match="rate"):
        WorkStatusRate("DEV", _ALWAYS_VALID_FROM, None, Fraction(11, 10))


def test_work_period_covers_rejects_day_before_start() -> None:
    period = WorkPeriod(date(2026, 11, 10), date(2026, 11, 20), "DEV")

    assert period.covers(date(2026, 11, 9)) is False


def test_work_period_covers_accepts_open_ended_period() -> None:
    period = WorkPeriod(date(2026, 11, 10), None, "DEV")

    assert period.covers(date(2027, 1, 1)) is True


def test_work_status_rate_covers_rejects_different_status() -> None:
    rate = WorkStatusRate("DEV", _ALWAYS_VALID_FROM, None, Fraction(1))

    assert rate.covers("EDU", date(2026, 11, 10)) is False


def test_work_status_rate_covers_rejects_day_before_valid_from() -> None:
    rate = WorkStatusRate("DEV", date(2026, 11, 10), None, Fraction(1))

    assert rate.covers("DEV", date(2026, 11, 9)) is False


def test_work_status_rate_covers_accepts_open_ended_period() -> None:
    rate = WorkStatusRate("DEV", _ALWAYS_VALID_FROM, None, Fraction(1))

    assert rate.covers("DEV", date(2027, 1, 1)) is True


def test_undetermined_when_period_exists_but_rate_is_missing() -> None:
    """稼働期間はあるが、対応する支給率が無い日も、要確認とする（CLC-011の延長の扱い）。"""
    items = (SalaryHistoryItem(_BASE, 200_000), SalaryHistoryItem(_DUTY, 100_000))
    business_calendar = BusinessCalendar((date(2026, 11, 2),))
    work_periods = (WorkPeriod(date(2026, 11, 2), date(2026, 11, 2), "UNKNOWN"),)
    work_status_rates = (WorkStatusRate("DEV", _ALWAYS_VALID_FROM, None, Fraction(1)),)

    result = calculate_payroll(
        monthly_salary=300_000,
        items=items,
        business_calendar=business_calendar,
        join_date=None,
        retire_date=None,
        work_periods=work_periods,
        work_status_rates=work_status_rates,
    )

    assert result.check_result == "要確認"


def test_calculate_payroll_rejects_item_total_mismatch() -> None:
    with pytest.raises(ValueError, match="monthly_salary"):
        calculate_payroll(
            monthly_salary=999,
            items=_EX000_ITEMS,
            business_calendar=_NOVEMBER_2026_BUSINESS_DAYS,
            join_date=None,
            retire_date=None,
            work_periods=(),
            work_status_rates=(),
        )


def test_calculate_payroll_rejects_empty_business_calendar() -> None:
    items = (SalaryHistoryItem(_BASE, 0),)
    with pytest.raises(ValueError, match="営業日"):
        calculate_payroll(
            monthly_salary=0,
            items=items,
            business_calendar=BusinessCalendar(()),
            join_date=None,
            retire_date=None,
            work_periods=(),
            work_status_rates=(),
        )

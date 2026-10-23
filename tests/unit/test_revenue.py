"""売上の計算の試験（TC-CLC-005〜007）。入力は計算仕様書9.6（EX-005）による。"""

from datetime import date
from fractions import Fraction

import pytest

from crm_g.domain.business_days import BusinessCalendar
from crm_g.domain.rate_history import RateHistoryEntry
from crm_g.domain.revenue import BillingPeriod, calculate_revenue

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


def test_tc_clc_005_ex005_full_rate() -> None:
    billing_periods = (BillingPeriod(date(2026, 11, 10), date(2026, 11, 30), True, Fraction(1), 1),)
    rate_histories = (RateHistoryEntry(1, _ALWAYS_VALID_FROM, 800_000),)

    result = calculate_revenue(
        business_calendar=_NOVEMBER_2026_BUSINESS_DAYS,
        join_date=None,
        retire_date=None,
        billing_periods=billing_periods,
        rate_histories=rate_histories,
    )

    assert result.sales == 589_474
    assert result.has_missing_unit_price is False


def test_tc_clc_006_ex005_partial_rate() -> None:
    billing_periods = (
        BillingPeriod(date(2026, 11, 10), date(2026, 11, 30), True, Fraction(7, 10), 1),
    )
    rate_histories = (RateHistoryEntry(1, _ALWAYS_VALID_FROM, 800_000),)

    result = calculate_revenue(
        business_calendar=_NOVEMBER_2026_BUSINESS_DAYS,
        join_date=None,
        retire_date=None,
        billing_periods=billing_periods,
        rate_histories=rate_histories,
    )

    assert result.sales == 412_632


def test_tc_clc_007_ex005_unit_price_revision_mid_period() -> None:
    billing_periods = (
        BillingPeriod(date(2026, 11, 10), date(2026, 11, 24), True, Fraction(1), 1),
        BillingPeriod(date(2026, 11, 25), date(2026, 11, 30), True, Fraction(1, 2), 1),
    )
    rate_histories = (
        RateHistoryEntry(1, _ALWAYS_VALID_FROM, 800_000),
        RateHistoryEntry(1, date(2026, 11, 25), 850_000),
    )

    result = calculate_revenue(
        business_calendar=_NOVEMBER_2026_BUSINESS_DAYS,
        join_date=None,
        retire_date=None,
        billing_periods=billing_periods,
        rate_histories=rate_histories,
    )

    assert result.sales == 510_527


def test_revenue_excludes_non_billable_periods() -> None:
    billing_periods = (BillingPeriod(date(2026, 11, 2), date(2026, 11, 30), False, Fraction(1), 1),)
    rate_histories = (RateHistoryEntry(1, _ALWAYS_VALID_FROM, 800_000),)

    result = calculate_revenue(
        business_calendar=_NOVEMBER_2026_BUSINESS_DAYS,
        join_date=None,
        retire_date=None,
        billing_periods=billing_periods,
        rate_histories=rate_histories,
    )

    assert result.sales == 0


def test_revenue_flags_missing_unit_price() -> None:
    billing_periods = (BillingPeriod(date(2026, 11, 2), date(2026, 11, 30), True, Fraction(1), 1),)

    result = calculate_revenue(
        business_calendar=_NOVEMBER_2026_BUSINESS_DAYS,
        join_date=None,
        retire_date=None,
        billing_periods=billing_periods,
        rate_histories=(),
    )

    assert result.sales == 0
    assert result.has_missing_unit_price is True


def test_revenue_excludes_days_not_present() -> None:
    billing_periods = (BillingPeriod(date(2026, 11, 2), date(2026, 11, 30), True, Fraction(1), 1),)
    rate_histories = (RateHistoryEntry(1, _ALWAYS_VALID_FROM, 800_000),)

    result = calculate_revenue(
        business_calendar=_NOVEMBER_2026_BUSINESS_DAYS,
        join_date=date(2026, 11, 16),
        retire_date=None,
        billing_periods=billing_periods,
        rate_histories=rate_histories,
    )

    assert result.sales == 421_053


def test_revenue_excludes_days_without_billing_period() -> None:
    billing_periods = (BillingPeriod(date(2026, 11, 10), date(2026, 11, 30), True, Fraction(1), 1),)
    rate_histories = (RateHistoryEntry(1, _ALWAYS_VALID_FROM, 800_000),)

    result = calculate_revenue(
        business_calendar=_NOVEMBER_2026_BUSINESS_DAYS,
        join_date=None,
        retire_date=None,
        billing_periods=billing_periods,
        rate_histories=rate_histories,
    )

    assert result.sales == 589_474


def test_billing_period_rejects_end_date_before_start_date() -> None:
    with pytest.raises(ValueError, match="end_date"):
        BillingPeriod(date(2026, 11, 20), date(2026, 11, 10), True, Fraction(1), 1)


def test_billing_period_rejects_ratio_above_one() -> None:
    with pytest.raises(ValueError, match="billing_ratio"):
        BillingPeriod(date(2026, 11, 2), None, True, Fraction(11, 10), 1)


def test_calculate_revenue_rejects_empty_business_calendar() -> None:
    with pytest.raises(ValueError, match="営業日"):
        calculate_revenue(
            business_calendar=BusinessCalendar(()),
            join_date=None,
            retire_date=None,
            billing_periods=(),
            rate_histories=(),
        )

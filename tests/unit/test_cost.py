"""原価・粗利の試験（TC-CLC-008、017、020、TC-CAL-009）。"""

from datetime import date
from fractions import Fraction

import pytest

from crm_g.domain.cost import (
    CostCoefficientPeriod,
    calculate_cost,
    calculate_gross_profit,
    gross_profit_ratio,
    select_effective_cost_coefficient,
)
from crm_g.domain.pay_item import PayItem
from crm_g.domain.payroll import PayrollItemResult

_BASE = PayItem("BASE", "基本給", "基本給", True, False, None, 1)
_DUTY = PayItem("DUTY", "勤務手当", "固定支給", True, False, None, 2)
_OVERTIME = PayItem("OVERTIME", "固定残業手当", "固定支給", True, False, 7, 3)
_HOUSING = PayItem("HOUSING", "住宅手当", "固定支給", True, False, 6, 4)
_LEADER = PayItem("LEADER", "リーダー手当", "固定支給", True, False, 1, 7)


def test_tc_clc_008_ex006_cost_and_gross_profit() -> None:
    """計算仕様書9.7（EX-006）。EX-001の支払額と、EX-005 1件目の売上を使う。"""
    payroll_items = (
        PayrollItemResult(_BASE, 200_000, 0),
        PayrollItemResult(_DUTY, 50_000, 0),
        PayrollItemResult(_OVERTIME, 18_422, 11_578),
        PayrollItemResult(_HOUSING, 0, 10_000),
        PayrollItemResult(_LEADER, 0, 10_000),
    )

    cost = calculate_cost(
        payroll_items=payroll_items, cost_coefficient=Fraction(6, 5), cost_bonus=0
    )

    assert cost.cost_salary == 322_107
    assert cost.cost_allowance == 0
    assert cost.cost_total == 322_107

    sales = 589_474
    gross_profit = calculate_gross_profit(sales, cost.cost_total)
    assert gross_profit == 267_367


def test_tc_clc_017_zero_sales_gross_profit_ratio_not_calculated() -> None:
    gross_profit = calculate_gross_profit(sales=0, cost_total=100_000)

    assert gross_profit == -100_000
    assert gross_profit_ratio(sales=0, gross_profit=gross_profit) is None


def test_gross_profit_ratio_divides_profit_by_sales() -> None:
    assert gross_profit_ratio(sales=589_474, gross_profit=267_367) == Fraction(267_367, 589_474)


def test_tc_clc_020_bonus_cost_has_no_coefficient_applied() -> None:
    """賞与は支給月の原価だけに計上し、原価係数を掛けない（計算仕様書5.1）。"""
    payroll_items = (PayrollItemResult(_BASE, 300_000, 0),)

    cost_with_bonus = calculate_cost(
        payroll_items=payroll_items, cost_coefficient=Fraction(6, 5), cost_bonus=375_000
    )
    cost_without_bonus = calculate_cost(
        payroll_items=payroll_items, cost_coefficient=Fraction(6, 5), cost_bonus=0
    )

    assert cost_with_bonus.cost_bonus == 375_000
    assert cost_with_bonus.cost_total == cost_without_bonus.cost_total + 375_000


def test_cost_allowance_sums_cost_direct_items_without_coefficient() -> None:
    cost_direct_item = PayItem("ALLOW", "特別手当", "一時手当", False, True, None, 10)
    payroll_items = (
        PayrollItemResult(_BASE, 300_000, 0),
        PayrollItemResult(cost_direct_item, 20_000, 0),
    )

    cost = calculate_cost(payroll_items=payroll_items, cost_coefficient=Fraction(1), cost_bonus=0)

    assert cost.cost_allowance == 20_000
    assert cost.cost_total == 300_000 + 20_000


def test_tc_cal_009_mid_month_coefficient_change_uses_first_day_value() -> None:
    """計算仕様書12章-8の暫定：月の初日に有効な値を、その月全体に使う。"""
    periods = (
        CostCoefficientPeriod(date(2026, 1, 1), date(2026, 10, 15), Fraction(6, 5)),
        CostCoefficientPeriod(date(2026, 10, 16), None, Fraction(13, 10)),
    )

    coefficient = select_effective_cost_coefficient(periods, as_of=date(2026, 10, 1))
    assert coefficient == Fraction(6, 5)

    payroll_items = (PayrollItemResult(_BASE, 300_000, 0),)
    cost = calculate_cost(payroll_items=payroll_items, cost_coefficient=coefficient, cost_bonus=0)

    assert cost.cost_salary == 360_000


def test_select_effective_cost_coefficient_returns_none_when_no_match() -> None:
    periods = (CostCoefficientPeriod(date(2026, 10, 1), None, Fraction(6, 5)),)

    assert select_effective_cost_coefficient(periods, as_of=date(2026, 9, 30)) is None


def test_select_effective_cost_coefficient_raises_on_overlap() -> None:
    periods = (
        CostCoefficientPeriod(date(2026, 1, 1), None, Fraction(6, 5)),
        CostCoefficientPeriod(date(2026, 6, 1), None, Fraction(13, 10)),
    )

    with pytest.raises(ValueError, match="重複"):
        select_effective_cost_coefficient(periods, as_of=date(2026, 10, 1))


def test_cost_coefficient_period_rejects_valid_to_before_valid_from() -> None:
    with pytest.raises(ValueError, match="valid_to"):
        CostCoefficientPeriod(date(2026, 10, 16), date(2026, 10, 1), Fraction(1))


def test_cost_coefficient_period_rejects_negative_coefficient() -> None:
    with pytest.raises(ValueError, match="coefficient"):
        CostCoefficientPeriod(date(2026, 10, 1), None, Fraction(-1))

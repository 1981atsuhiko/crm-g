"""給与履歴・月給合計の試験（CLC-005、TC-CAL-008、TC-CAL-010）。"""

from datetime import date

import pytest

from crm_g.domain.pay_item import PayItem
from crm_g.domain.salary_history import (
    SalaryHistory,
    SalaryHistoryItem,
    select_effective_salary_history,
)

_BASE_SALARY = PayItem(
    code="BASE",
    name="基本給",
    category="基本給",
    in_monthly_salary=True,
    cost_direct=False,
    deduction_order=None,
    sort_order=1,
)
_ANNUAL_SALARY_ITEM = PayItem(
    code="ANNUAL",
    name="年俸",
    category="基本給",
    in_monthly_salary=True,
    cost_direct=False,
    deduction_order=None,
    sort_order=1,
)


def test_tc_cal_010_annual_salary_uses_item_total() -> None:
    """年俸制でも、月給合計は内訳の合計とする（計算仕様書12章-3の暫定）。"""
    history = SalaryHistory(
        effective_from=date(2026, 4, 1),
        effective_to=None,
        items=(SalaryHistoryItem(pay_item=_ANNUAL_SALARY_ITEM, amount=400_000),),
    )

    assert history.monthly_salary == 400_000


def test_select_effective_salary_history_returns_match() -> None:
    older = SalaryHistory(
        date(2026, 4, 1), date(2026, 9, 30), (SalaryHistoryItem(_BASE_SALARY, 300_000),)
    )
    newer = SalaryHistory(date(2026, 10, 1), None, (SalaryHistoryItem(_BASE_SALARY, 330_000),))

    result = select_effective_salary_history((older, newer), date(2026, 10, 15))

    assert result is newer


def test_select_effective_salary_history_returns_none_when_no_match() -> None:
    history = SalaryHistory(date(2026, 10, 1), None, (SalaryHistoryItem(_BASE_SALARY, 300_000),))

    result = select_effective_salary_history((history,), date(2026, 9, 30))

    assert result is None


def test_select_effective_salary_history_raises_on_overlap() -> None:
    first = SalaryHistory(date(2026, 4, 1), None, (SalaryHistoryItem(_BASE_SALARY, 300_000),))
    second = SalaryHistory(date(2026, 9, 1), None, (SalaryHistoryItem(_BASE_SALARY, 330_000),))

    with pytest.raises(ValueError, match="重複"):
        select_effective_salary_history((first, second), date(2026, 10, 1))


def test_salary_history_rejects_effective_to_before_effective_from() -> None:
    with pytest.raises(ValueError, match="effective_from"):
        SalaryHistory(date(2026, 10, 1), date(2026, 9, 30), ())


def test_pay_item_rejects_deduction_order_below_one() -> None:
    with pytest.raises(ValueError, match="deduction_order"):
        PayItem(
            code="X",
            name="X",
            category="固定支給",
            in_monthly_salary=True,
            cost_direct=False,
            deduction_order=0,
            sort_order=1,
        )


def test_salary_history_item_rejects_negative_amount() -> None:
    with pytest.raises(ValueError, match="amount"):
        SalaryHistoryItem(pay_item=_BASE_SALARY, amount=-1)

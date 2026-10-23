"""賞与の支給月・基準月・基準額・賞与額の試験（TC-CLC-009、012、TC-CAL-006）。"""

from datetime import date
from fractions import Fraction

import pytest

from crm_g.domain.bonus import (
    BonusRulePeriod,
    basic_salary_total,
    bonus_schedule,
    calculate_bonus_amount,
    determine_bonus_base_amount,
    select_effective_bonus_rule,
)
from crm_g.domain.pay_item import PayItem
from crm_g.domain.salary_history import SalaryHistory, SalaryHistoryItem

_BASE = PayItem("BASE", "基本給", "基本給", True, False, None, 1)
_DUTY = PayItem("DUTY", "勤務手当", "固定支給", True, False, None, 2)


@pytest.mark.parametrize(
    ("half_code", "pay_year", "pay_month", "base_year", "base_month"),
    [
        ("2025-H1", 2025, 10, 2025, 9),
        ("2026-H1", 2026, 10, 2026, 9),
        ("2026-H2", 2027, 4, 2027, 3),
    ],
)
def test_tc_clc_012_bonus_schedule_examples(
    half_code: str, pay_year: int, pay_month: int, base_year: int, base_month: int
) -> None:
    """計算仕様書9.10（EX-009）。"""
    schedule = bonus_schedule(half_code)

    assert schedule.pay_year == pay_year
    assert schedule.pay_month == pay_month
    assert schedule.base_year == base_year
    assert schedule.base_month == base_month


def test_bonus_schedule_rejects_invalid_half_code() -> None:
    with pytest.raises(ValueError, match="half_code"):
        bonus_schedule("2026-H3")


def test_tc_cal_006_bonus_base_amount_by_rule() -> None:
    """2025-10支給は基本給、2026-10支給は月給合計を基準額にする。"""
    base_month_history = SalaryHistory(
        date(2026, 4, 1),
        None,
        (SalaryHistoryItem(_BASE, 200_000), SalaryHistoryItem(_DUTY, 100_000)),
    )
    rules = (
        BonusRulePeriod(date(2000, 1, 1), date(2026, 3, 31), "基本給", "通常評価制度"),
        BonusRulePeriod(date(2026, 4, 1), None, "月給合計", "通常評価制度"),
    )

    rule_2025 = select_effective_bonus_rule(
        rules, as_of=date(2025, 10, 1), target_pay_system="通常評価制度"
    )
    assert rule_2025 is not None
    assert determine_bonus_base_amount(rule_2025.base_type, base_month_history) == 200_000

    rule_2026 = select_effective_bonus_rule(
        rules, as_of=date(2026, 10, 1), target_pay_system="通常評価制度"
    )
    assert rule_2026 is not None
    assert determine_bonus_base_amount(rule_2026.base_type, base_month_history) == 300_000


def test_basic_salary_total_excludes_non_basic_items() -> None:
    history = SalaryHistory(
        date(2026, 4, 1),
        None,
        (SalaryHistoryItem(_BASE, 200_000), SalaryHistoryItem(_DUTY, 100_000)),
    )

    assert basic_salary_total(history) == 200_000


def test_select_effective_bonus_rule_returns_none_when_no_match() -> None:
    rules = (BonusRulePeriod(date(2026, 4, 1), None, "月給合計", "通常評価制度"),)

    result = select_effective_bonus_rule(
        rules, as_of=date(2026, 3, 31), target_pay_system="通常評価制度"
    )
    assert result is None


def test_select_effective_bonus_rule_ignores_other_pay_system() -> None:
    """target_pay_system が異なるルールは、重複検査の対象にならない（データ設計書4.7節）。"""
    rules = (
        BonusRulePeriod(date(2026, 1, 1), None, "基本給", "通常評価制度"),
        BonusRulePeriod(date(2026, 1, 1), None, "月給合計", "年俸制"),
    )

    result = select_effective_bonus_rule(
        rules, as_of=date(2026, 10, 1), target_pay_system="通常評価制度"
    )
    assert result is not None
    assert result.base_type == "基本給"


def test_select_effective_bonus_rule_raises_on_overlap() -> None:
    rules = (
        BonusRulePeriod(date(2026, 1, 1), None, "基本給", "通常評価制度"),
        BonusRulePeriod(date(2026, 6, 1), None, "月給合計", "通常評価制度"),
    )

    with pytest.raises(ValueError, match="重複"):
        select_effective_bonus_rule(
            rules, as_of=date(2026, 10, 1), target_pay_system="通常評価制度"
        )


def test_bonus_rule_period_rejects_invalid_base_type() -> None:
    with pytest.raises(ValueError, match="base_type"):
        BonusRulePeriod(date(2026, 4, 1), None, "年俸", "通常評価制度")


def test_bonus_rule_period_rejects_invalid_target_pay_system() -> None:
    with pytest.raises(ValueError, match="target_pay_system"):
        BonusRulePeriod(date(2026, 4, 1), None, "基本給", "正社員")


def test_bonus_rule_period_rejects_valid_to_before_valid_from() -> None:
    with pytest.raises(ValueError, match="valid_to"):
        BonusRulePeriod(date(2026, 4, 1), date(2026, 3, 31), "基本給", "通常評価制度")


def test_determine_bonus_base_amount_rejects_invalid_base_type() -> None:
    history = SalaryHistory(date(2026, 4, 1), None, (SalaryHistoryItem(_BASE, 200_000),))

    with pytest.raises(ValueError, match="base_type"):
        determine_bonus_base_amount("年俸", history)


def test_tc_clc_009_bonus_amount_with_fraction() -> None:
    """計算仕様書9.8（EX-007）の1行目。"""
    assert calculate_bonus_amount(333_333, Fraction(5, 4)) == 416_667


def test_bonus_amount_without_fraction() -> None:
    assert calculate_bonus_amount(300_000, Fraction(5, 4)) == 375_000


def test_calculate_bonus_amount_rejects_negative_base_amount() -> None:
    with pytest.raises(ValueError, match="base_amount"):
        calculate_bonus_amount(-1, Fraction(1))


def test_calculate_bonus_amount_rejects_negative_rate() -> None:
    with pytest.raises(ValueError, match="rate"):
        calculate_bonus_amount(300_000, Fraction(-1))

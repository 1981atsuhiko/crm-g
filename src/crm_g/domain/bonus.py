"""賞与の支給月・基準月・基準額・賞与額（CLC-019〜CLC-021）。"""

import re
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from fractions import Fraction

from crm_g.domain.business_days import is_within_period
from crm_g.domain.rounding import ceil_yen
from crm_g.domain.salary_history import SalaryHistory

_HALF_CODE_PATTERN = re.compile(r"^(\d{4})-H([12])$")
_BASE_TYPES = ("基本給", "月給合計")
_TARGET_PAY_SYSTEMS = ("通常評価制度", "年俸制", "単価連動")


@dataclass(frozen=True)
class BonusSchedule:
    """賞与の支給月・基準月（CLC-019）。"""

    pay_year: int
    pay_month: int
    base_year: int
    base_month: int


def bonus_schedule(half_code: str) -> BonusSchedule:
    """査定対象の半期コードから、支給月・基準月を求める（CLC-019）。"""
    matched = _HALF_CODE_PATTERN.match(half_code)
    if matched is None:
        raise ValueError("half_code は YYYY-H1 または YYYY-H2 の形式にしてください")

    fiscal_year = int(matched.group(1))
    half = matched.group(2)
    if half == "1":
        return BonusSchedule(
            pay_year=fiscal_year, pay_month=10, base_year=fiscal_year, base_month=9
        )
    return BonusSchedule(
        pay_year=fiscal_year + 1, pay_month=4, base_year=fiscal_year + 1, base_month=3
    )


@dataclass(frozen=True)
class BonusRulePeriod:
    """賞与ルールの有効期間の1件（CLC-020）。"""

    valid_from: date
    valid_to: date | None
    base_type: str
    target_pay_system: str

    def __post_init__(self) -> None:
        if self.valid_to is not None and self.valid_to < self.valid_from:
            raise ValueError("valid_to は valid_from 以後にしてください")
        if self.base_type not in _BASE_TYPES:
            raise ValueError("base_type は 基本給 または 月給合計 にしてください")
        if self.target_pay_system not in _TARGET_PAY_SYSTEMS:
            raise ValueError(
                "target_pay_system は 通常評価制度・年俸制・単価連動 のいずれかにしてください"
            )


def select_effective_bonus_rule(
    rules: Sequence[BonusRulePeriod], as_of: date, target_pay_system: str
) -> BonusRulePeriod | None:
    """支給月の初日に有効な、対象の給与体系（target_pay_system）の賞与ルールを選ぶ（計算仕様書7.2）。

    データ設計書4.7節のとおり、有効期間の重複検査は target_pay_system ごとに行う。
    """
    candidates = [
        rule
        for rule in rules
        if rule.target_pay_system == target_pay_system
        and is_within_period(as_of, rule.valid_from, rule.valid_to)
    ]
    if not candidates:
        return None
    if len(candidates) > 1:
        raise ValueError("as_of に有効な賞与ルールが複数あります（期間が重複しています）")
    return candidates[0]


def basic_salary_total(history: SalaryHistory) -> int:
    """給与履歴の内訳のうち、区分（category）が基本給の項目の金額の合計。"""
    return sum(item.amount for item in history.items if item.pay_item.category == "基本給")


def determine_bonus_base_amount(base_type: str, base_month_history: SalaryHistory) -> int:
    """賞与の基準額を求める（CLC-020）。

    基準額は、減額・日割り前の金額（給与履歴の金額）を使う（計算仕様書12章-13の暫定）。
    """
    if base_type == "基本給":
        return basic_salary_total(base_month_history)
    if base_type == "月給合計":
        return base_month_history.monthly_salary
    raise ValueError("base_type は 基本給 または 月給合計 にしてください")


def calculate_bonus_amount(base_amount: int, rate: Fraction) -> int:
    """賞与額を求める（CLC-021）。"""
    if base_amount < 0:
        raise ValueError("base_amount は0以上にしてください")
    if rate < 0:
        raise ValueError("rate は0以上にしてください")
    return ceil_yen(Fraction(base_amount) * rate)

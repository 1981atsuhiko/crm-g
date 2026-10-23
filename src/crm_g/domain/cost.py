"""原価・粗利（CLC-014、CLC-015）。"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from fractions import Fraction

from crm_g.domain.business_days import is_within_period
from crm_g.domain.payroll import PayrollItemResult
from crm_g.domain.rounding import ceil_yen


@dataclass(frozen=True)
class CostCoefficientPeriod:
    """原価係数の有効期間（計算設定 system_setting の1行）。"""

    valid_from: date
    valid_to: date | None
    coefficient: Fraction

    def __post_init__(self) -> None:
        if self.valid_to is not None and self.valid_to < self.valid_from:
            raise ValueError("valid_to は valid_from 以後にしてください")
        if self.coefficient < 0:
            raise ValueError("coefficient は0以上にしてください")


@dataclass(frozen=True)
class CostResult:
    """1社員・1か月の原価の計算結果（CLC-014）。"""

    cost_salary: int
    cost_allowance: int
    cost_bonus: int
    cost_total: int


def select_effective_cost_coefficient(
    periods: Sequence[CostCoefficientPeriod], as_of: date
) -> Fraction | None:
    """as_of に有効な原価係数を選ぶ。

    「月の初日の値を、その月全体に使う」（計算仕様書12章-8の暫定）という判断は、
    呼び出し側が as_of に月の初日を渡すことで実現する。この関数自体は、任意の日に
    有効な係数を選ぶ汎用の処理である。
    """
    candidates = [
        period for period in periods if is_within_period(as_of, period.valid_from, period.valid_to)
    ]
    if not candidates:
        return None
    if len(candidates) > 1:
        raise ValueError("as_of に有効な原価係数が複数あります（期間が重複しています）")
    return candidates[0].coefficient


def calculate_cost(
    *,
    payroll_items: Sequence[PayrollItemResult],
    cost_coefficient: Fraction,
    cost_bonus: int,
) -> CostResult:
    """月給部分の原価・月給の外の手当・賞与から、原価合計を求める（CLC-014）。

    支払額だけから原価を求める（稼働状態の「原価計上」は見ない）。初期データでは、
    原価計上が×の状態は支給率0であり、影響しない前提（計算仕様書12章-9の暫定）。

    in_monthly_salary と cost_direct が同時に1の項目があると、両方に二重計上される。
    データ設計書4.5節は、両方が1にならないことを検査にする想定だが、確認が必要な
    未決事項である（データ設計書13章-5）。ここでは、両方が1にならない前提を置く。
    """
    salary_items_total = Fraction(
        sum(item.paid_amount for item in payroll_items if item.pay_item.in_monthly_salary)
    )
    cost_salary = ceil_yen(salary_items_total * cost_coefficient)
    cost_allowance = sum(item.paid_amount for item in payroll_items if item.pay_item.cost_direct)
    cost_total = cost_salary + cost_allowance + cost_bonus
    return CostResult(
        cost_salary=cost_salary,
        cost_allowance=cost_allowance,
        cost_bonus=cost_bonus,
        cost_total=cost_total,
    )


def calculate_gross_profit(sales: int, cost_total: int) -> int:
    """粗利を求める（CLC-015）。負の値を取り得る。"""
    return sales - cost_total


def gross_profit_ratio(sales: int, gross_profit: int) -> Fraction | None:
    """粗利率を求める（CLC-015）。売上が0のときは算出しない（None）。"""
    if sales == 0:
        return None
    return Fraction(gross_profit, sales)

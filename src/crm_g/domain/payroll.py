"""給与支払額・減額・正誤判定（CLC-006〜CLC-011）。"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from enum import StrEnum
from fractions import Fraction
from typing import cast

from crm_g.domain.business_days import BusinessCalendar, is_present
from crm_g.domain.pay_item import PayItem
from crm_g.domain.rounding import ceil_yen
from crm_g.domain.salary_history import SalaryHistoryItem


class CheckResult(StrEnum):
    """正誤判定の結果（CLC-010。データ設計書 monthly_payroll.check_result）。"""

    OK = "OK"
    NEEDS_REVIEW = "要確認"


@dataclass(frozen=True)
class WorkPeriod:
    """当月に重なる稼働期間の1件。社員の稼働期間は重ならない前提とする（計算仕様書3.6）。"""

    start_date: date
    end_date: date | None
    work_status_code: str

    def __post_init__(self) -> None:
        if self.end_date is not None and self.end_date < self.start_date:
            raise ValueError("end_date は start_date 以後にしてください")

    def covers(self, day: date) -> bool:
        if day < self.start_date:
            return False
        return self.end_date is None or day <= self.end_date


@dataclass(frozen=True)
class WorkStatusRate:
    """稼働状態ごとの給与支給率（有効期間付き）。率は Fraction で持つ（DBD-004）。"""

    work_status_code: str
    valid_from: date
    valid_to: date | None
    rate: Fraction

    def __post_init__(self) -> None:
        if self.valid_to is not None and self.valid_to < self.valid_from:
            raise ValueError("valid_to は valid_from 以後にしてください")
        if not 0 <= self.rate <= 1:
            raise ValueError("rate は0以上1以下にしてください（計算仕様書3.3）")

    def covers(self, work_status_code: str, day: date) -> bool:
        if work_status_code != self.work_status_code:
            return False
        if day < self.valid_from:
            return False
        return self.valid_to is None or day <= self.valid_to


@dataclass(frozen=True)
class PayrollItemResult:
    """給与項目ごとの計算結果（CLC-009）。"""

    pay_item: PayItem
    paid_amount: int
    deduction_amount: int


@dataclass(frozen=True)
class PayrollResult:
    """1社員・1か月の給与計算結果。"""

    monthly_salary: int
    paid_amount: int
    deduction: int
    check_result: CheckResult
    items: tuple[PayrollItemResult, ...]


def verify_payroll_totals(monthly_salary: int, paid_amount: int, deduction: int) -> CheckResult:
    """月給合計＝支払額＋減額が成り立つかを判定する（CLC-010、REQ-CAL-007）。"""
    if monthly_salary == paid_amount + deduction:
        return CheckResult.OK
    return CheckResult.NEEDS_REVIEW


def _find_work_period(day: date, work_periods: Sequence[WorkPeriod]) -> WorkPeriod | None:
    for period in work_periods:
        if period.covers(day):
            return period
    return None


def _find_rate(
    work_status_code: str, day: date, rates: Sequence[WorkStatusRate]
) -> Fraction | None:
    for rate in rates:
        if rate.covers(work_status_code, day):
            return rate.rate
    return None


def _daily_rate(
    day: date,
    join_date: date | None,
    retire_date: date | None,
    work_periods: Sequence[WorkPeriod],
    work_status_rates: Sequence[WorkStatusRate],
) -> tuple[Fraction, bool]:
    """営業日1日の支給率と、「要確認」フラグ（CLC-011）を求める。

    稼働期間が無い日は、支給率0としフラグをTrueにする（計算仕様書12章-5の暫定の扱い）。
    稼働期間はあるが有効な支給率が決まらない日も、同様に支給率0・フラグTrueとする
    （12章-5の素直な適用ではなく、同じ考え方を広げた拡張解釈。マスタ不整合の防御でもある）。
    """
    if not is_present(day, join_date, retire_date):
        return Fraction(0), False

    period = _find_work_period(day, work_periods)
    if period is None:
        return Fraction(0), True

    rate = _find_rate(period.work_status_code, day, work_status_rates)
    if rate is None:
        return Fraction(0), True

    return rate, False


def _deduction_order_key(item: SalaryHistoryItem) -> int:
    return cast(int, item.pay_item.deduction_order)


def _ordered_items(items: Sequence[SalaryHistoryItem]) -> tuple[SalaryHistoryItem, ...]:
    """減額を差し引く順（CLC-008）に並べ替える。

    減額順のある項目で足りない分は、表示順に従い次の減額順のない項目から差し引く
    （計算仕様書12章-4の暫定の扱い。この並び順が、その振る舞いを実現する）。
    """
    with_order = sorted(
        (item for item in items if item.pay_item.deduction_order is not None),
        key=_deduction_order_key,
    )
    without_order = sorted(
        (item for item in items if item.pay_item.deduction_order is None),
        key=lambda item: item.pay_item.sort_order,
    )
    return (*with_order, *without_order)


def calculate_payroll(  # noqa: PLR0913 計算仕様書CLC-006の入力をすべて受け取るため
    *,
    monthly_salary: int,
    items: Sequence[SalaryHistoryItem],
    business_calendar: BusinessCalendar,
    join_date: date | None,
    retire_date: date | None,
    work_periods: Sequence[WorkPeriod],
    work_status_rates: Sequence[WorkStatusRate],
) -> PayrollResult:
    """1社員・1か月の給与支払額・減額を計算する（CLC-006〜CLC-011）。"""
    if monthly_salary != sum(item.amount for item in items):
        raise ValueError("items の金額の合計が monthly_salary と一致しません")
    if business_calendar.day_count == 0:
        raise ValueError("当月の営業日がありません")

    has_undetermined_day = False
    rate_sum = Fraction(0)
    for day in business_calendar.business_days:
        rate, undetermined = _daily_rate(
            day, join_date, retire_date, work_periods, work_status_rates
        )
        rate_sum += rate
        if undetermined:
            has_undetermined_day = True

    p_raw = Fraction(monthly_salary) * rate_sum / business_calendar.day_count
    x_raw = Fraction(monthly_salary) - p_raw

    remaining = x_raw
    item_results: list[PayrollItemResult] = []
    for item in _ordered_items(items):
        amount = Fraction(item.amount)
        deduction_before = min(amount, remaining)
        remaining -= deduction_before
        paid_before = amount - deduction_before
        paid_amount = ceil_yen(paid_before)
        deduction_amount = item.amount - paid_amount
        item_results.append(PayrollItemResult(item.pay_item, paid_amount, deduction_amount))

    paid_total = sum(result.paid_amount for result in item_results)
    deduction_total = monthly_salary - paid_total

    if has_undetermined_day:
        check_result: CheckResult = CheckResult.NEEDS_REVIEW
    else:
        check_result = verify_payroll_totals(monthly_salary, paid_total, deduction_total)

    return PayrollResult(
        monthly_salary=monthly_salary,
        paid_amount=paid_total,
        deduction=deduction_total,
        check_result=check_result,
        items=tuple(item_results),
    )

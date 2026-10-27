"""再計算（`ARC-009`、`CLC-022`）。

1社員・1か月の入力をDBから組み立て、ドメイン層で給与・売上・原価を計算し、
`monthly_payroll`・`monthly_payroll_item`・`monthly_pl` に保存する。

- 締め済みの月は、再計算しない（`ARC-010`、データ設計書 10章 No.5）
- 計算エラー（計算仕様書 8章）が出た社員・月は、保存しない。前回の結果があっても
  消さずに残す（人事連携の入力 `hr_target`・`hr_note` を失わないため）。エラーは
  一覧に出す。他の社員の計算は続ける
- 再計算しても、`hr_target`・`hr_note` は上書きしない（`UID-075`）
- `monthly_pl` は `source = 再計算` で保存する。`取込` の行と、締め済みの行は上書きしない
- この関数群はトランザクションを開始しない。呼び出し側（更新ユースケース、または
  手動の一括再計算）のトランザクションの中で呼ぶ（`ARC-009`。同じトランザクション）

計算結果の保存は、利用者の操作ではなく、きっかけとなった更新の操作ログで追える
ため、操作ログには記録しない。
"""

import calendar
import sqlite3  # noqa: TID251 呼び出し側が開いた接続の型として使うだけで、接続はインフラ層に閉じ込める
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from datetime import date
from enum import StrEnum
from fractions import Fraction

from crm_g.domain.business_days import BusinessCalendar, is_present, is_within_period
from crm_g.domain.cost import (
    CostCoefficientPeriod,
    calculate_cost,
    calculate_gross_profit,
    select_effective_cost_coefficient,
)
from crm_g.domain.pay_item import PayItem
from crm_g.domain.payroll import (
    PayrollResult,
    WorkPeriod,
    WorkStatusRate,
    calculate_payroll,
)
from crm_g.domain.period_overlap import periods_overlap
from crm_g.domain.rate_history import RateHistoryEntry, determine_unit_price
from crm_g.domain.revenue import BillingPeriod, calculate_revenue
from crm_g.domain.salary_history import (
    SalaryHistory,
    SalaryHistoryItem,
    select_effective_salary_history,
)
from crm_g.domain.work_period_summary import StatusPeriod, summarize_month
from crm_g.infrastructure.datetime_utils import to_iso8601, utc_now
from crm_g.infrastructure.db import transaction

_COST_COEFFICIENT_KEY = "原価係数"
_BP = 10000
_SOURCE_RECALCULATED = "再計算"
_SOURCE_IMPORTED = "取込"


class CalculationErrorReason(StrEnum):
    """計算エラーの原因（計算仕様書 8章、画面設計書 `SCN-032` の計算エラー一覧）。"""

    NO_BUSINESS_DAY = "当月の営業日がない（営業日カレンダーが未作成）"
    NOT_PRESENT = "在籍している営業日がない"
    NO_SALARY_HISTORY = "有効な給与履歴がない"
    OVERLAPPING_PERIODS = "稼働期間が重なっている"
    MISSING_UNIT_PRICE = "売上計上の営業日に単価がない"
    NO_COST_COEFFICIENT = "原価係数が設定されていない"


@dataclass(frozen=True)
class CalculationError:
    """計算エラーの1件（保存しない。画面で開くたびに判定し直す）。"""

    employee_id: int
    employee_name: str
    year_month: str
    reason: CalculationErrorReason


@dataclass(frozen=True)
class RecalculationResult:
    """再計算の結果。

    `year_months` は、再計算した月（締め済みの月を除く）。`closed_months` は、変更が
    影響しうるが締め済みのため再計算しなかった月（`UID-054` の警告に使う）。
    """

    year_months: tuple[str, ...]
    saved_count: int
    errors: tuple[CalculationError, ...]
    closed_months: tuple[str, ...] = ()


RecalculationListener = Callable[[RecalculationResult], None]


@dataclass(frozen=True)
class _Employee:
    id: int
    name: str
    join_date: date | None
    retire_date: date | None


@dataclass(frozen=True)
class _MonthResult:
    payroll: PayrollResult
    work_status_summary: str
    sales: int
    cost_salary: int
    cost_allowance: int
    cost_bonus: int
    cost_total: int
    gross_profit: int
    category: str | None


def year_month_text(year: int, month: int) -> str:
    """年月を `YYYY-MM` の形にする（データ設計書の年月形式）。"""
    return f"{year:04d}-{month:02d}"


def _month_range(year_month: str) -> tuple[date, date]:
    year, month = (int(part) for part in year_month.split("-"))
    return date(year, month, 1), date(year, month, calendar.monthrange(year, month)[1])


def _parse_date(value: str | None) -> date | None:
    return date.fromisoformat(value) if value else None


def is_month_closed(connection: sqlite3.Connection, year_month: str) -> bool:
    """その月が締め済みか（締めた社員・月の行が1件でもあるか）を返す。

    `source` が `取込` の `monthly_pl` は、移行元の確定値として常に締め済みで取り込まれる。
    これは月次締めではないため、判定に含めない（取込済みの月も再計算の対象。移行設計書
    `MIG-054`）。取込の行そのものは、保存のときに上書きしない。
    """
    row = connection.execute(
        "SELECT 1 FROM monthly_payroll WHERE year_month = ? AND is_closed = 1 "
        "UNION ALL SELECT 1 FROM monthly_pl WHERE year_month = ? AND is_closed = 1 "
        "AND source != ? LIMIT 1",
        (year_month, year_month, _SOURCE_IMPORTED),
    ).fetchone()
    return row is not None


def calculated_open_months(connection: sqlite3.Connection, since: date) -> tuple[str, ...]:
    """`since` の月以降で、計算済み（結果が1件以上ある）かつ締め前の月を返す。

    更新のあとに再計算する「影響する月」の範囲である（計算仕様書 8章。ユーザー回答
    2026-10-26）。まだ一度も計算していない月は、画面の再計算で計算する。
    """
    rows = connection.execute(
        "SELECT DISTINCT year_month FROM monthly_payroll WHERE year_month >= ? ORDER BY year_month",
        (year_month_text(since.year, since.month),),
    ).fetchall()
    return tuple(row[0] for row in rows if not is_month_closed(connection, row[0]))


def _employees_in_month(
    connection: sqlite3.Connection,
    first: date,
    last: date,
    employee_ids: Sequence[int] | None,
) -> list[_Employee]:
    rows = connection.execute(
        "SELECT id, name, join_date, retire_date FROM employee "
        "WHERE (join_date IS NULL OR join_date <= ?) AND (retire_date IS NULL OR retire_date >= ?) "
        "ORDER BY id",
        (last.isoformat(), first.isoformat()),
    ).fetchall()
    wanted = set(employee_ids) if employee_ids is not None else None
    return [
        _Employee(row[0], row[1], _parse_date(row[2]), _parse_date(row[3]))
        for row in rows
        if wanted is None or row[0] in wanted
    ]


def load_business_calendar(
    connection: sqlite3.Connection, first: date, last: date
) -> BusinessCalendar:
    rows = connection.execute(
        "SELECT calendar_date FROM business_calendar "
        "WHERE calendar_date BETWEEN ? AND ? AND is_business_day = 1 ORDER BY calendar_date",
        (first.isoformat(), last.isoformat()),
    ).fetchall()
    return BusinessCalendar(tuple(date.fromisoformat(row[0]) for row in rows))


def load_pay_items(connection: sqlite3.Connection) -> dict[str, PayItem]:
    rows = connection.execute(
        "SELECT code, name, category, in_monthly_salary, cost_direct, deduction_order, "
        "sort_order FROM pay_item"
    ).fetchall()
    return {
        row[0]: PayItem(
            code=row[0],
            name=row[1],
            category=row[2],
            in_monthly_salary=bool(row[3]),
            cost_direct=bool(row[4]),
            deduction_order=row[5],
            sort_order=row[6],
        )
        for row in rows
    }


def _salary_histories(
    connection: sqlite3.Connection, employee_id: int, pay_items: dict[str, PayItem]
) -> list[SalaryHistory]:
    histories = []
    for history_id, effective_from, effective_to in connection.execute(
        "SELECT id, effective_from, effective_to FROM salary_history WHERE employee_id = ?",
        (employee_id,),
    ).fetchall():
        items = tuple(
            SalaryHistoryItem(pay_item=pay_items[code], amount=amount)
            for code, amount in connection.execute(
                "SELECT pay_item_code, amount FROM salary_history_item "
                "WHERE salary_history_id = ? ORDER BY pay_item_code",
                (history_id,),
            ).fetchall()
        )
        histories.append(
            SalaryHistory(date.fromisoformat(effective_from), _parse_date(effective_to), items)
        )
    return histories


@dataclass(frozen=True)
class _PeriodRow:
    start_date: date
    end_date: date | None
    work_status_code: str
    status_name: str
    is_billable: bool
    billing_ratio_bp: int
    assignment_id: int | None
    category: str | None


def _work_periods(
    connection: sqlite3.Connection, employee_id: int, first: date, last: date
) -> list[_PeriodRow]:
    rows = connection.execute(
        "SELECT p.start_date, p.end_date, p.work_status_code, s.name, s.is_billable, "
        "p.billing_ratio_bp, p.assignment_id, a.category "
        "FROM work_period p JOIN work_status s ON s.code = p.work_status_code "
        "LEFT JOIN assignment a ON a.id = p.assignment_id "
        "WHERE p.employee_id = ? AND p.start_date <= ? "
        "AND (p.end_date IS NULL OR p.end_date >= ?) ORDER BY p.start_date",
        (employee_id, last.isoformat(), first.isoformat()),
    ).fetchall()
    return [
        _PeriodRow(
            date.fromisoformat(row[0]),
            _parse_date(row[1]),
            row[2],
            row[3],
            bool(row[4]),
            row[5],
            row[6],
            row[7],
        )
        for row in rows
    ]


def _has_overlap(periods: Sequence[_PeriodRow]) -> bool:
    return any(
        periods_overlap(a.start_date, a.end_date, b.start_date, b.end_date)
        for index, a in enumerate(periods)
        for b in periods[index + 1 :]
    )


def _work_status_rates(connection: sqlite3.Connection) -> list[WorkStatusRate]:
    return [
        WorkStatusRate(
            work_status_code=row[0],
            valid_from=date.fromisoformat(row[1]),
            valid_to=_parse_date(row[2]),
            rate=Fraction(row[3], _BP),
        )
        for row in connection.execute(
            "SELECT work_status_code, valid_from, valid_to, payroll_rate_bp FROM work_status_rate"
        ).fetchall()
    ]


def _cost_coefficients(connection: sqlite3.Connection) -> list[CostCoefficientPeriod]:
    return [
        CostCoefficientPeriod(
            valid_from=date.fromisoformat(row[0]),
            valid_to=_parse_date(row[1]),
            coefficient=Fraction(row[2], _BP),
        )
        for row in connection.execute(
            "SELECT valid_from, valid_to, value_integer FROM system_setting WHERE setting_key = ?",
            (_COST_COEFFICIENT_KEY,),
        ).fetchall()
    ]


def _rate_histories(connection: sqlite3.Connection, employee_id: int) -> list[RateHistoryEntry]:
    return [
        RateHistoryEntry(
            assignment_id=row[0], effective_from=date.fromisoformat(row[1]), unit_price=row[2]
        )
        for row in connection.execute(
            "SELECT assignment_id, effective_from, unit_price FROM rate_history "
            "WHERE employee_id = ?",
            (employee_id,),
        ).fetchall()
    ]


def _cost_bonus(connection: sqlite3.Connection, employee_id: int, year_month: str) -> int:
    row = connection.execute(
        "SELECT COALESCE(SUM(bonus_amount), 0) FROM bonus "
        "WHERE employee_id = ? AND pay_month = ? AND is_closed = 1",
        (employee_id, year_month),
    ).fetchone()
    return int(row[0])


def _category(present_days: Sequence[date], periods: Sequence[_PeriodRow]) -> str | None:
    """`monthly_pl.category` を決める。

    売上計上の最後の在籍営業日の、参画の区分とする（暫定。データ設計書 13章-10）。
    """
    for day in reversed(present_days):
        for period in periods:
            if period.is_billable and is_within_period(day, period.start_date, period.end_date):
                return period.category
    return None


class _CalculationContext:
    """1か月の再計算で、社員をまたいで共通に使う入力（マスタ）。"""

    def __init__(self, connection: sqlite3.Connection, year_month: str) -> None:
        self.year_month = year_month
        self.first, self.last = _month_range(year_month)
        self.calendar = load_business_calendar(connection, self.first, self.last)
        self.pay_items = load_pay_items(connection)
        self.work_status_rates = _work_status_rates(connection)
        self.cost_coefficient = select_effective_cost_coefficient(
            _cost_coefficients(connection), self.first
        )


@dataclass(frozen=True)
class _Inputs:
    present_days: tuple[date, ...]
    history: SalaryHistory
    periods: list[_PeriodRow]
    cost_coefficient: Fraction


def _gather_inputs(
    connection: sqlite3.Connection, context: _CalculationContext, employee: _Employee
) -> _Inputs | CalculationErrorReason:
    """計算の前提を確かめ、入力を集める（計算仕様書 2.3・2.5・3.6）。エラーなら原因を返す。"""
    if context.calendar.day_count == 0:
        return CalculationErrorReason.NO_BUSINESS_DAY
    present_days = tuple(
        day
        for day in context.calendar.business_days
        if is_present(day, employee.join_date, employee.retire_date)
    )
    if not present_days:
        return CalculationErrorReason.NOT_PRESENT

    history = select_effective_salary_history(
        _salary_histories(connection, employee.id, context.pay_items), present_days[-1]
    )
    if history is None:
        return CalculationErrorReason.NO_SALARY_HISTORY

    periods = _work_periods(connection, employee.id, context.first, context.last)
    if _has_overlap(periods):
        return CalculationErrorReason.OVERLAPPING_PERIODS
    if context.cost_coefficient is None:
        return CalculationErrorReason.NO_COST_COEFFICIENT
    return _Inputs(present_days, history, periods, context.cost_coefficient)


def _calculate(
    connection: sqlite3.Connection, context: _CalculationContext, employee: _Employee
) -> _MonthResult | CalculationErrorReason:
    """1社員・1か月を計算する（計算仕様書 8章の計算順）。エラーなら原因を返す。"""
    inputs = _gather_inputs(connection, context, employee)
    if isinstance(inputs, CalculationErrorReason):
        return inputs
    present_days, history, periods = inputs.present_days, inputs.history, inputs.periods

    payroll = calculate_payroll(
        monthly_salary=history.monthly_salary,
        items=history.items,
        business_calendar=context.calendar,
        join_date=employee.join_date,
        retire_date=employee.retire_date,
        work_periods=[WorkPeriod(p.start_date, p.end_date, p.work_status_code) for p in periods],
        work_status_rates=context.work_status_rates,
    )
    revenue = calculate_revenue(
        business_calendar=context.calendar,
        join_date=employee.join_date,
        retire_date=employee.retire_date,
        billing_periods=[
            BillingPeriod(
                start_date=p.start_date,
                end_date=p.end_date,
                is_billable=p.is_billable,
                billing_ratio=Fraction(p.billing_ratio_bp, _BP),
                assignment_id=p.assignment_id,
            )
            for p in periods
        ],
        rate_histories=_rate_histories(connection, employee.id),
    )
    if revenue.has_missing_unit_price:
        return CalculationErrorReason.MISSING_UNIT_PRICE

    cost = calculate_cost(
        payroll_items=payroll.items,
        cost_coefficient=inputs.cost_coefficient,
        cost_bonus=_cost_bonus(connection, employee.id, context.year_month),
    )
    return _MonthResult(
        payroll=payroll,
        work_status_summary=summarize_month(
            context.first,
            context.last,
            [StatusPeriod(p.start_date, p.end_date, p.status_name) for p in periods],
            present_days,
        ),
        sales=revenue.sales,
        cost_salary=cost.cost_salary,
        cost_allowance=cost.cost_allowance,
        cost_bonus=cost.cost_bonus,
        cost_total=cost.cost_total,
        gross_profit=calculate_gross_profit(revenue.sales, cost.cost_total),
        category=_category(present_days, periods),
    )


def _save(
    connection: sqlite3.Connection, employee_id: int, year_month: str, result: _MonthResult
) -> None:
    """3テーブルを保存する。締め済みの行と、取込の `monthly_pl` は上書きしない。"""
    now = to_iso8601(utc_now())
    payroll = result.payroll
    connection.execute(
        "INSERT INTO monthly_payroll (employee_id, year_month, monthly_salary, paid_amount, "
        "deduction, work_status_summary, check_result, calculated_at, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
        "ON CONFLICT (employee_id, year_month) DO UPDATE SET "
        "monthly_salary = excluded.monthly_salary, paid_amount = excluded.paid_amount, "
        "deduction = excluded.deduction, work_status_summary = excluded.work_status_summary, "
        "check_result = excluded.check_result, calculated_at = excluded.calculated_at, "
        "updated_at = excluded.updated_at WHERE monthly_payroll.is_closed = 0",
        (
            employee_id,
            year_month,
            payroll.monthly_salary,
            payroll.paid_amount,
            payroll.deduction,
            result.work_status_summary,
            payroll.check_result.value,
            now,
            now,
            now,
        ),
    )
    # 内訳は、給与履歴の項目が変わりうるため、作り直す。
    # 計算結果の明細であり、利用者の入力ではない。
    connection.execute(
        "DELETE FROM monthly_payroll_item WHERE employee_id = ? AND year_month = ?",
        (employee_id, year_month),
    )
    connection.executemany(
        "INSERT INTO monthly_payroll_item (employee_id, year_month, pay_item_code, paid_amount, "
        "deduction_amount, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
        [
            (
                employee_id,
                year_month,
                item.pay_item.code,
                item.paid_amount,
                item.deduction_amount,
                now,
                now,
            )
            for item in payroll.items
        ],
    )
    connection.execute(
        "INSERT INTO monthly_pl (employee_id, year_month, category, sales, cost_salary, "
        "cost_allowance, cost_bonus, cost_total, gross_profit, source, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
        "ON CONFLICT (employee_id, year_month) DO UPDATE SET "
        "category = excluded.category, sales = excluded.sales, "
        "cost_salary = excluded.cost_salary, cost_allowance = excluded.cost_allowance, "
        "cost_bonus = excluded.cost_bonus, cost_total = excluded.cost_total, "
        "gross_profit = excluded.gross_profit, source = excluded.source, "
        "updated_at = excluded.updated_at "
        "WHERE monthly_pl.is_closed = 0 AND monthly_pl.source != ?",
        (
            employee_id,
            year_month,
            result.category,
            result.sales,
            result.cost_salary,
            result.cost_allowance,
            result.cost_bonus,
            result.cost_total,
            result.gross_profit,
            _SOURCE_RECALCULATED,
            now,
            now,
            _SOURCE_IMPORTED,
        ),
    )


def recalculate_month(
    connection: sqlite3.Connection,
    year_month: str,
    employee_ids: Sequence[int] | None = None,
) -> RecalculationResult:
    """1か月を再計算して保存する。締め済みの月は何もしない。トランザクションは開始しない。

    `employee_ids` が `None` なら、その月に在籍期間がかかる全社員を対象にする。
    """
    if is_month_closed(connection, year_month):
        return RecalculationResult((), 0, ())

    context = _CalculationContext(connection, year_month)
    saved = 0
    errors: list[CalculationError] = []
    for employee in _employees_in_month(connection, context.first, context.last, employee_ids):
        outcome = _calculate(connection, context, employee)
        if isinstance(outcome, CalculationErrorReason):
            errors.append(CalculationError(employee.id, employee.name, year_month, outcome))
            continue
        _save(connection, employee.id, year_month, outcome)
        saved += 1
    return RecalculationResult((year_month,), saved, tuple(errors))


def run_manual_recalculation(
    connection: sqlite3.Connection, year_month: str
) -> RecalculationResult:
    """手動の一括再計算（画面設計書 `SCN-032` の再計算ボタン）。1つのトランザクションで行う。"""
    with transaction(connection):
        return recalculate_month(connection, year_month)


def recalculate_since(
    connection: sqlite3.Connection,
    since: date,
    employee_ids: Sequence[int] | None = None,
) -> RecalculationResult:
    """更新ユースケースから呼ぶ再計算（`ARC-009`）。トランザクションは開始しない。

    `since` の月以降の、計算済みかつ締め前の月を再計算する。社員ごとの変更は、
    その社員だけ（`employee_ids`）、マスタの変更は全社員（`None`）を対象にする。
    """
    months: list[str] = []
    saved = 0
    errors: list[CalculationError] = []
    for year_month in calculated_open_months(connection, since):
        result = recalculate_month(connection, year_month, employee_ids)
        months.extend(result.year_months)
        saved += result.saved_count
        errors.extend(result.errors)
    return RecalculationResult(
        tuple(months), saved, tuple(errors), _closed_months(connection, since, employee_ids)
    )


def _closed_months(
    connection: sqlite3.Connection, since: date, employee_ids: Sequence[int] | None
) -> tuple[str, ...]:
    """`since` の月以降で、締め済みの月を返す。社員ごとの変更では、その社員の行がある月に限る。"""
    rows = connection.execute(
        "SELECT DISTINCT year_month, employee_id FROM monthly_payroll WHERE year_month >= ?",
        (year_month_text(since.year, since.month),),
    ).fetchall()
    wanted = set(employee_ids) if employee_ids is not None else None
    months = sorted({row[0] for row in rows if wanted is None or row[1] in wanted})
    return tuple(month for month in months if is_month_closed(connection, month))


EARLIEST = date(2000, 1, 1)
"""マスタの属性の変更など、すべての計算済みの月に影響する変更で使う起点。"""


def earliest_changed_date(pairs: Iterable[tuple[date | None, date | None]]) -> date | None:
    """変更前後の日付の組のうち、値が変わった組の日付で、最も古いものを返す。

    どの組も変わっていなければ `None`（再計算しない）。
    """
    candidates = [
        value
        for before, after in pairs
        if before != after
        for value in (before, after)
        if value is not None
    ]
    return min(candidates) if candidates else None


def recalculate_after_change(
    connection: sqlite3.Connection,
    since: date | None,
    employee_ids: Sequence[int] | None,
) -> RecalculationResult | None:
    """更新ユースケースの最後に、同じトランザクションで再計算する（`ARC-009`）。

    `since` が `None` なら、計算に影響する変更がないものとして何もせず `None` を返す。
    結果は、コミットのあとで `notify_recalculated` に渡す（コミットが失敗したときに、
    再計算したと通知しないため）。
    """
    if since is None:
        return None
    return recalculate_since(connection, since, employee_ids)


def notify_recalculated(
    on_recalculated: RecalculationListener | None, result: RecalculationResult | None
) -> None:
    """コミットのあとで、再計算の結果を画面に渡す（通知 `UID-055`・警告 `UID-054`）。"""
    if on_recalculated is not None and result is not None:
        on_recalculated(result)


def list_calculation_errors(
    connection: sqlite3.Connection, year_month: str
) -> tuple[CalculationError, ...]:
    """その月の計算エラーを、保存せずに判定し直して返す（画面設計書 `SCN-032`）。"""
    context = _CalculationContext(connection, year_month)
    errors = []
    for employee in _employees_in_month(connection, context.first, context.last, None):
        outcome = _calculate(connection, context, employee)
        if isinstance(outcome, CalculationErrorReason):
            errors.append(CalculationError(employee.id, employee.name, year_month, outcome))
    return tuple(errors)


def last_billable_unit_price(
    connection: sqlite3.Connection, employee_id: int, year_month: str
) -> int | None:
    """売上計上の最後の在籍営業日の単価を返す（画面設計書 `SCN-032` の単価の列）。

    単価は保存しないため、表示のたびに単価履歴から求める。売上計上の営業日がない月は
    `None`（画面では `－`）。
    """
    first, last = _month_range(year_month)
    row = connection.execute(
        "SELECT join_date, retire_date FROM employee WHERE id = ?", (employee_id,)
    ).fetchone()
    if row is None:
        return None
    join_date, retire_date = _parse_date(row[0]), _parse_date(row[1])
    periods = _work_periods(connection, employee_id, first, last)
    for day in reversed(load_business_calendar(connection, first, last).business_days):
        if not is_present(day, join_date, retire_date):
            continue
        for period in periods:
            if period.is_billable and is_within_period(day, period.start_date, period.end_date):
                return determine_unit_price(
                    day, period.assignment_id, _rate_histories(connection, employee_id)
                )
    return None

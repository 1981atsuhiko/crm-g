"""ダッシュボードの集計（画面設計書 `SCN-010`、計算仕様書 `CLC-015`・`CLC-024`・`CLC-025`）。

- 売上・原価・粗利は、`monthly_pl` の保存値の合計。締め前の月も含める
- 当半期・当期は、半期・期の開始月から対象月までの累計（`CLC-024`）
- 稼働人日率・待機者は、営業日カレンダーと稼働期間から求める（`CLC-025`）
"""

import sqlite3  # noqa: TID251 呼び出し側が開いた接続の型として使うだけで、接続はインフラ層に閉じ込める
from calendar import monthrange
from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from fractions import Fraction

from crm_g.application.recalculation import load_business_calendar, year_month_text
from crm_g.domain.business_days import present_business_days
from crm_g.domain.cost import gross_profit_ratio
from crm_g.domain.period import current_fiscal_half, fiscal_year_date_range, half_date_range
from crm_g.domain.utilization import (
    StatusSpan,
    count_billable_man_days,
    man_day_ratio,
    summarize_waiting,
)


@dataclass(frozen=True)
class PlTotal:
    """期間の売上・原価・粗利の合計。"""

    sales: int
    cost_total: int
    gross_profit: int

    @property
    def gross_profit_ratio(self) -> Fraction | None:
        """粗利率（`CLC-015`）。売上の合計が 0 のときは None。"""
        return gross_profit_ratio(self.sales, self.gross_profit)


@dataclass(frozen=True)
class WaitingRow:
    """待機者一覧の1行。"""

    employee_id: int
    employee_name: str
    status_names: str
    day_count: int
    periods: tuple[tuple[date, date], ...]


@dataclass(frozen=True)
class Dashboard:
    """対象月のダッシュボード。計算結果がない期間の合計は None（「未計算」）。"""

    year_month: str
    month_total: PlTotal | None
    half_total: PlTotal | None
    fiscal_year_total: PlTotal | None
    has_calendar: bool
    man_day_ratio: Fraction | None
    waiting: tuple[WaitingRow, ...]


def _pl_total(connection: sqlite3.Connection, first_month: str, last_month: str) -> PlTotal | None:
    count, sales, cost_total, gross_profit = connection.execute(
        "SELECT COUNT(*), SUM(sales), SUM(cost_total), SUM(gross_profit) FROM monthly_pl "
        "WHERE year_month BETWEEN ? AND ?",
        (first_month, last_month),
    ).fetchone()
    if count == 0:
        return None
    return PlTotal(sales, cost_total, gross_profit)


def _status_spans(
    connection: sqlite3.Connection, first: date, last: date
) -> dict[int, list[StatusSpan]]:
    spans: dict[int, list[StatusSpan]] = defaultdict(list)
    for employee_id, start, end, code, name, billable in connection.execute(
        "SELECT p.employee_id, p.start_date, p.end_date, p.work_status_code, s.name, s.is_billable "
        "FROM work_period p JOIN work_status s ON s.code = p.work_status_code "
        "WHERE p.start_date <= ? AND (p.end_date IS NULL OR p.end_date >= ?)",
        (last.isoformat(), first.isoformat()),
    ).fetchall():
        spans[employee_id].append(
            StatusSpan(
                date.fromisoformat(start),
                date.fromisoformat(end) if end else None,
                code,
                name,
                bool(billable),
            )
        )
    return spans


def load_dashboard(connection: sqlite3.Connection, year_month: str) -> Dashboard:
    """対象月（`YYYY-MM`）のダッシュボードの値を返す。"""
    year, month = (int(part) for part in year_month.split("-"))
    first = date(year, month, 1)
    last = date(year, month, monthrange(year, month)[1])
    fiscal_year, half = current_fiscal_half(first)
    half_start = half_date_range(fiscal_year, half)[0]
    fiscal_start = fiscal_year_date_range(fiscal_year)[0]

    business_calendar = load_business_calendar(connection, first, last)
    spans = _status_spans(connection, first, last)
    present_man_days = 0
    billable_man_days = 0
    waiting: list[WaitingRow] = []
    for employee_id, name, join_date, retire_date in connection.execute(
        "SELECT id, name, join_date, retire_date FROM employee "
        "WHERE (join_date IS NULL OR join_date <= ?) AND (retire_date IS NULL OR retire_date >= ?) "
        "ORDER BY COALESCE(name_kana, name), id",
        (last.isoformat(), first.isoformat()),
    ).fetchall():
        joined = date.fromisoformat(join_date) if join_date else None
        retired = date.fromisoformat(retire_date) if retire_date else None
        present_days = present_business_days(business_calendar, joined, retired)
        employee_spans = spans.get(employee_id, [])
        present_man_days += len(present_days)
        billable_man_days += count_billable_man_days(present_days, employee_spans)
        summary = summarize_waiting(first, last, present_days, employee_spans)
        if summary is not None:
            waiting.append(
                WaitingRow(
                    employee_id, name, summary.status_names, summary.day_count, summary.periods
                )
            )

    return Dashboard(
        year_month=year_month,
        month_total=_pl_total(connection, year_month, year_month),
        half_total=_pl_total(
            connection, year_month_text(half_start.year, half_start.month), year_month
        ),
        fiscal_year_total=_pl_total(
            connection, year_month_text(fiscal_start.year, fiscal_start.month), year_month
        ),
        has_calendar=business_calendar.day_count > 0,
        man_day_ratio=man_day_ratio(billable_man_days, present_man_days),
        waiting=tuple(waiting),
    )

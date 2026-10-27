"""売上の実績の集計（画面設計書 `SCN-051` の「実績の集計」、計算仕様書 `CLC-015`・`CLC-024`）。

- 売上・原価・粗利は、`monthly_pl` の保存値の合計。締め前の月も含める
- 月別・半期別は、選んだ期の12か月を対象にする。期別は、全期を対象にする
- カテゴリがない行は `未分類` として集計する
- 計算結果がない月は、集計に含めない。今日の月までの、計算結果がない月を数えて返す
"""

import sqlite3  # noqa: TID251 呼び出し側が開いた接続の型として使うだけで、接続はインフラ層に閉じ込める
from collections import Counter, defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date

from crm_g.application.assignment import ASSIGNMENT_CATEGORIES
from crm_g.application.dashboard import PlTotal
from crm_g.application.recalculation import year_month_text
from crm_g.domain.period import (
    FIRST_SELECTABLE_FISCAL_YEAR,
    current_fiscal_half,
    fiscal_year_label,
    half_label,
    half_months,
    selectable_halves,
)

PERIOD_UNITS = ("月別", "半期別", "期別")
AXES = ("合計", "カテゴリ別", "社員別")
UNCATEGORIZED = "未分類"
# 集計の軸ごとの、行の名称の列名（合計は列を持たない）。画面と CSV で共通に使う。
LABEL_COLUMNS = {"カテゴリ別": "カテゴリ", "社員別": "社員"}


class InvalidSalesConditionError(ValueError):
    """集計の期間・軸・期の指定が正しくないときのエラー。"""


@dataclass(frozen=True)
class SalesRow:
    """集計表の1行。`label` は、カテゴリ別ならカテゴリ、社員別なら氏名（合計なら None）。

    `employee_id` は、社員別のときだけ持つ（同じ氏名の社員を区別するため）。
    """

    period: str
    label: str | None
    total: PlTotal
    employee_id: int | None = None


@dataclass(frozen=True)
class SalesSummary:
    """実績の集計。`missing_months` は、集計に含めていない（計算結果がない）月。"""

    rows: tuple[SalesRow, ...]
    missing_months: tuple[str, ...]
    includes_open_months: bool


@dataclass(frozen=True)
class _PlRow:
    year_month: str
    category: str | None
    employee_id: int
    sort_key: str
    employee_name: str
    sales: int
    cost_total: int
    gross_profit: int
    is_closed: bool


def last_fiscal_year(today: date) -> int:
    """対象にできる最後の期。半期の選択（`UID-063`）と同じく、今日が属する半期の次の半期の期。"""
    return selectable_halves(today)[-1][0]


def _months(first_fiscal_year: int, last: int) -> list[str]:
    return [
        year_month_text(year, month)
        for fiscal_year in range(first_fiscal_year, last + 1)
        for half in (1, 2)
        for year, month in half_months(fiscal_year, half)
    ]


def _fiscal_half_of(year_month: str) -> tuple[int, int]:
    year, month = (int(part) for part in year_month.split("-"))
    return current_fiscal_half(date(year, month, 1))


def _period_of(unit: str, year_month: str) -> tuple[tuple[int, int], str]:
    """集計の期間の並び順のキーと、表示名（`UID-041`）を返す。"""
    year, month = (int(part) for part in year_month.split("-"))
    fiscal_year, half = _fiscal_half_of(year_month)
    if unit == "月別":
        return (year, month), f"{year:04d}/{month:02d}"
    if unit == "半期別":
        return (fiscal_year, half), half_label(fiscal_year, half)
    return (fiscal_year, 0), fiscal_year_label(fiscal_year)


def _sum(rows: Iterable[_PlRow]) -> PlTotal:
    items = list(rows)
    return PlTotal(
        sum(row.sales for row in items),
        sum(row.cost_total for row in items),
        sum(row.gross_profit for row in items),
    )


def _load(connection: sqlite3.Connection, first_month: str, last_month: str) -> list[_PlRow]:
    return [
        _PlRow(row[0], row[1], row[2], row[3], row[4], row[5], row[6], row[7], bool(row[8]))
        for row in connection.execute(
            "SELECT p.year_month, p.category, p.employee_id, COALESCE(e.name_kana, e.name), "
            "e.name, p.sales, p.cost_total, p.gross_profit, p.is_closed "
            "FROM monthly_pl p JOIN employee e ON e.id = p.employee_id "
            "WHERE p.year_month BETWEEN ? AND ?",
            (first_month, last_month),
        ).fetchall()
    ]


def summarize_sales(
    connection: sqlite3.Connection,
    unit: str,
    axis: str,
    fiscal_year: int | None,
    today: date,
) -> SalesSummary:
    """実績を集計する。`fiscal_year` は、月別・半期別で対象にする期（期別では使わない）。"""
    if unit not in PERIOD_UNITS or axis not in AXES:
        raise InvalidSalesConditionError("集計の期間・軸が正しくありません")
    if unit == "期別":
        oldest = connection.execute("SELECT MIN(year_month) FROM monthly_pl").fetchone()[0]
        first_year = FIRST_SELECTABLE_FISCAL_YEAR
        if oldest is not None:
            first_year = min(first_year, _fiscal_half_of(oldest)[0])
        months = _months(first_year, last_fiscal_year(today))
    elif fiscal_year is None:
        raise InvalidSalesConditionError("対象の期を選んでください")
    else:
        months = _months(fiscal_year, fiscal_year)

    rows = _load(connection, months[0], months[-1])
    calculated = {row.year_month for row in rows}
    current_month = year_month_text(today.year, today.month)
    missing = tuple(month for month in months if month <= current_month and month not in calculated)

    # 行の並び順：期間の順。カテゴリは区分の定義の順（未分類は最後）、社員は氏名（カナ）の順。
    category_order = (*ASSIGNMENT_CATEGORIES, UNCATEGORIZED)
    groups: dict[tuple[tuple[int, int], str, tuple[int, str, int], str | None], list[_PlRow]]
    groups = defaultdict(list)
    for row in rows:
        period_key, period_name = _period_of(unit, row.year_month)
        if axis == "カテゴリ別":
            category = row.category or UNCATEGORIZED
            label_key: tuple[int, str, int] = (category_order.index(category), "", 0)
            label: str | None = category
        elif axis == "社員別":
            label_key, label = (0, row.sort_key, row.employee_id), row.employee_name
        else:
            label_key, label = (0, "", 0), None
        groups[(period_key, period_name, label_key, label)].append(row)

    ordered = sorted(groups.items(), key=lambda item: (item[0][0], item[0][2]))
    return SalesSummary(
        rows=tuple(
            SalesRow(
                period_name,
                label,
                _sum(items),
                employee_id=label_key[2] if axis == "社員別" else None,
            )
            for (_, period_name, label_key, label), items in ordered
        ),
        missing_months=missing,
        includes_open_months=any(not row.is_closed for row in rows),
    )


def display_labels(rows: tuple[SalesRow, ...]) -> list[str | None]:
    """行の名称（カテゴリ・氏名）。同じ期間に同じ氏名が複数あるときは、社員IDを添える。"""
    counts = Counter((row.period, row.label) for row in rows if row.employee_id is not None)
    return [
        f"{row.label}（{row.employee_id}）"
        if row.employee_id is not None and counts[(row.period, row.label)] > 1
        else row.label
        for row in rows
    ]

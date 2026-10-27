"""売上画面（`SCN-051`）。

実績の集計：月別・半期別・期別に、合計・カテゴリ別・社員別で、売上・原価・粗利・粗利率を
集計して表とグラフで出す。締め前の月も含み、計算結果がない月は集計に含めない（月数を表示する）。
給与・賞与のテーブルを読まないため、`閲覧` は記録しない（画面設計書 `UID-060`）。
"""

from collections import Counter
from datetime import date

import streamlit as st

from crm_g.application.sales import (
    AXES,
    PERIOD_UNITS,
    SalesRow,
    SalesSummary,
    last_fiscal_year,
    summarize_sales,
)
from crm_g.domain.period import FIRST_SELECTABLE_FISCAL_YEAR, current_fiscal_half, fiscal_year_label
from crm_g.infrastructure.db import open_connection
from crm_g.infrastructure.paths import get_app_paths
from crm_g.ui.components.formatting import format_ratio_percent, format_yen
from crm_g.ui.components.messages import show_status, show_unexpected_error

_LABEL_COLUMNS = {"カテゴリ別": "カテゴリ", "社員別": "社員"}


def _select_fiscal_year(*, disabled: bool) -> int:
    # 期別のときも出し続けて、選んだ期を保つ（出さないと、選択の状態が破棄される）。
    today = date.today()
    options = list(range(FIRST_SELECTABLE_FISCAL_YEAR, last_fiscal_year(today) + 1))
    selected: int = st.selectbox(
        "対象の期",
        options,
        index=options.index(current_fiscal_half(today)[0]),
        format_func=fiscal_year_label,
        key="sales_fiscal_year",
        disabled=disabled,
    )
    return selected


def _labels(rows: tuple[SalesRow, ...]) -> list[str | None]:
    """行の名称（カテゴリ・氏名）。同じ期間に同じ氏名が複数あるときは、社員IDを添える。"""
    counts = Counter((row.period, row.label) for row in rows if row.employee_id is not None)
    return [
        f"{row.label}（{row.employee_id}）"
        if row.employee_id is not None and counts[(row.period, row.label)] > 1
        else row.label
        for row in rows
    ]


def _render_summary(summary: SalesSummary, axis: str) -> None:
    if summary.includes_open_months:
        st.caption("締め前の月を含む")
    if summary.missing_months:
        months = "、".join(month.replace("-", "/") for month in summary.missing_months)
        count = len(summary.missing_months)
        show_status(f"計算結果がない {count}か月（{months}）は、集計に含めていません。")
    if not summary.rows:
        show_status("集計する計算結果がありません。")
        return

    label_column = _LABEL_COLUMNS.get(axis)
    table = []
    chart = []
    for row, label in zip(summary.rows, _labels(summary.rows), strict=True):
        record: dict[str, str] = {"期間": row.period}
        if label_column is not None:
            record[label_column] = label or ""
        record |= {
            "売上（円）": format_yen(row.total.sales),
            "原価（円）": format_yen(row.total.cost_total),
            "粗利（円）": format_yen(row.total.gross_profit),
            "粗利率": format_ratio_percent(row.total.gross_profit_ratio),
        }
        table.append(record)
        name = row.period if label is None else f"{row.period} {label}"
        chart.append({"行": name, "売上": row.total.sales, "粗利": row.total.gross_profit})
    st.dataframe(table, hide_index=True)
    # 集計表と同じ順で並べる（既定では、横軸を文字列の順に並べ替える）。
    st.bar_chart(chart, x="行", y=["売上", "粗利"], stack=False, sort=False)


def render() -> None:
    st.title("売上")
    st.subheader("実績の集計")
    unit = (
        st.segmented_control("集計の期間", PERIOD_UNITS, default="月別", key="sales_unit") or "月別"
    )
    selected_year = _select_fiscal_year(disabled=unit == "期別")
    fiscal_year = None if unit == "期別" else selected_year
    axis = st.segmented_control("集計の軸", AXES, default="合計", key="sales_axis") or "合計"

    try:
        with open_connection(get_app_paths().db_path) as connection:
            summary = summarize_sales(connection, unit, axis, fiscal_year, date.today())
    except Exception:
        show_unexpected_error()
        return
    _render_summary(summary, axis)


render()

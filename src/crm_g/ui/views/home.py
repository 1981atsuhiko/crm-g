"""ダッシュボード画面（`SCN-010`）。

対象月・当半期・当期の売上・原価・粗利・粗利率と、対象月の稼働人日率・待機者を表示する。
当半期・当期は、開始月から対象月までの累計（計算仕様書 `CLC-024`）。給与・賞与のテーブルを
読まないため、`閲覧` は記録しない（画面設計書 `UID-060`）。
"""

import streamlit as st

from crm_g.application.dashboard import Dashboard, PlTotal, load_dashboard
from crm_g.infrastructure.db import open_connection
from crm_g.infrastructure.paths import get_app_paths
from crm_g.ui.components.formatting import format_ratio_percent, format_yen
from crm_g.ui.components.messages import show_status, show_unexpected_error, show_warning
from crm_g.ui.components.month_selector import select_year_month

_NOT_CALCULATED = "未計算"


def _render_total(title: str, total: PlTotal | None) -> None:
    st.markdown(f"**{title}**")
    if total is None:
        for label in ("売上（円）", "原価（円）", "粗利（円）", "粗利率"):
            st.metric(label, _NOT_CALCULATED)
        return
    st.metric("売上（円）", format_yen(total.sales))
    st.metric("原価（円）", format_yen(total.cost_total))
    st.metric("粗利（円）", format_yen(total.gross_profit))
    st.metric("粗利率", format_ratio_percent(total.gross_profit_ratio))


def _render_waiting(dashboard: Dashboard) -> None:
    st.subheader("待機者一覧")
    if not dashboard.waiting:
        show_status("対象月に待機の社員はいません。")
        return
    st.dataframe(
        [
            {
                "氏名": row.employee_name,
                "待機の稼働状態": row.status_names,
                "待機の営業日数": row.day_count,
                "期間": "、".join(
                    f"{start:%Y/%m/%d}〜{end:%Y/%m/%d}" for start, end in row.periods
                ),
            }
            for row in dashboard.waiting
        ],
        hide_index=True,
    )


def render() -> None:
    st.title("ダッシュボード")
    year_month = select_year_month(key="dashboard_month")
    try:
        with open_connection(get_app_paths().db_path) as connection:
            dashboard = load_dashboard(connection, year_month)
    except Exception:
        show_unexpected_error()
        return

    month_column, half_column, year_column = st.columns(3)
    with month_column:
        _render_total("当月", dashboard.month_total)
    with half_column:
        _render_total("当半期（累計）", dashboard.half_total)
    with year_column:
        _render_total("当期（累計）", dashboard.fiscal_year_total)
    st.caption("締め前の月を含みます。当半期・当期は、開始月から対象月までの累計です。")

    st.metric("稼働人日率", format_ratio_percent(dashboard.man_day_ratio))
    if not dashboard.has_calendar:
        # 在籍営業日が求められないため、待機者一覧は出さない（「いない」とは言えない）。
        show_warning(
            f"{year_month.replace('-', '/')} の営業日カレンダーがありません。"
            "稼働人日率と待機者は求められません。"
            "「設定」→「営業日カレンダー」で、カレンダーを作成してください。"
        )
        return
    _render_waiting(dashboard)


render()

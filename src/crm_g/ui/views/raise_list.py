"""昇給一覧画面（`SCN-031`）。

期間（半期、または通期）と氏名の部分一致で、昇給（給与改定）を一覧にする。
昇給額は、新しい月給合計から直前の履歴の月給合計を引いた値（`CLC-016`）で、
直前の履歴が無い行は「－」と表示する。期間の選択肢は、2025年度から、今日が
属する期・半期の次までとする（`UID-063`の半期選択に準じる）。
"""

from datetime import date

import streamlit as st

from crm_g.application.salary_history import RaiseRow, list_raises
from crm_g.domain.period import fiscal_year_date_range, half_date_range
from crm_g.infrastructure.db import open_connection
from crm_g.infrastructure.paths import get_app_paths
from crm_g.ui.components.messages import UNEXPECTED_ERROR_MESSAGE, show_errors
from crm_g.ui.components.view_log import record_view_if_newly_displayed

_SCREEN_NAME = "SCN-031"
_START_FISCAL_YEAR = 2025


def _current_fiscal_year_and_half(today: date) -> tuple[int, int]:
    fiscal_year = today.year if today.month >= 4 else today.year - 1
    half = 1 if 4 <= today.month <= 9 else 2
    return fiscal_year, half


def _half_options(today: date) -> list[tuple[int, int]]:
    end_year, end_half = _current_fiscal_year_and_half(today)
    if end_half == 2:
        end_year, end_half = end_year + 1, 1
    else:
        end_half = 2

    options: list[tuple[int, int]] = []
    year, half = _START_FISCAL_YEAR, 1
    while (year, half) <= (end_year, end_half):
        options.append((year, half))
        if half == 1:
            half = 2
        else:
            year += 1
            half = 1
    return options


def _year_options(today: date) -> list[int]:
    current_fiscal_year, _ = _current_fiscal_year_and_half(today)
    return list(range(_START_FISCAL_YEAR, current_fiscal_year + 2))


def _half_label(year: int, half: int) -> str:
    return f"{year}年度 {'上期' if half == 1 else '下期'}"


def _render_period_selector() -> tuple[date, date, str]:
    today = date.today()
    unit = st.radio("期間の単位", ["半期", "通期"], key="raise_list_period_unit")

    if unit == "半期":
        options = _half_options(today)
        current = _current_fiscal_year_and_half(today)
        labels = [_half_label(year, half) for year, half in options]
        default_index = options.index(current) if current in options else len(options) - 1
        selected_label = st.selectbox(
            "対象期間", labels, index=default_index, key="raise_list_half"
        )
        year, half = options[labels.index(selected_label)]
        period_start, period_end = half_date_range(year, half)
        return period_start, period_end, selected_label

    year_options = _year_options(today)
    current_fiscal_year, _ = _current_fiscal_year_and_half(today)
    default_index = (
        year_options.index(current_fiscal_year) if current_fiscal_year in year_options else 0
    )
    selected_year = st.selectbox(
        "対象期間",
        year_options,
        index=default_index,
        format_func=lambda year: f"{year}年度（通期）",
        key="raise_list_year",
    )
    period_start, period_end = fiscal_year_date_range(selected_year)
    return period_start, period_end, f"{selected_year}年度（通期）"


def _amount_or_dash(amount: int | None) -> str:
    if amount is None:
        return "－"
    return f"{amount:,}円"


def _render_table(rows: tuple[RaiseRow, ...]) -> None:
    if not rows:
        st.info("対象期間に、昇給の履歴がありません。")
        return

    st.dataframe(
        [
            {
                "氏名": row.employee_name,
                "適用開始日": row.effective_from.isoformat(),
                "改定区分": row.revision_type,
                "給与体系": row.pay_system,
                "直前の月給合計": _amount_or_dash(row.previous_monthly_salary),
                "新しい月給合計": _amount_or_dash(row.monthly_salary),
                "昇給額": _amount_or_dash(row.raise_amount),
            }
            for row in rows
        ],
        hide_index=True,
    )


def render() -> None:
    st.title("昇給一覧")

    period_start, period_end, period_label = _render_period_selector()
    search = st.text_input("検索（氏名）", key="raise_list_search")

    db_path = get_app_paths().db_path
    target_key = f"{period_start.isoformat()}~{period_end.isoformat()}"
    with open_connection(db_path) as connection:
        try:
            record_view_if_newly_displayed(connection, _SCREEN_NAME, "salary_history", target_key)
            record_view_if_newly_displayed(
                connection, _SCREEN_NAME, "salary_history_item", target_key
            )
        except Exception:
            show_errors([UNEXPECTED_ERROR_MESSAGE])
            return

        rows = list_raises(connection, period_start, period_end, search)

    st.caption(f"対象期間：{period_label}（{period_start.isoformat()}〜{period_end.isoformat()}）")
    _render_table(rows)


render()

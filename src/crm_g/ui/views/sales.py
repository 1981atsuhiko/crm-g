"""売上画面（`SCN-051`）。

2つのタブで構成する。

- 実績の集計：月別・半期別・期別に、合計・カテゴリ別・社員別で、売上・原価・粗利・粗利率を
  集計して表とグラフで出す。締め前の月も含み、計算結果がない月は集計に含めない（月数を表示する）
- 見込み：対象月の範囲の見込みを、確度別の表と一覧で出し、フォームで登録・更新する。見込み売上は、
  見込み単価 × 見込み稼働率の切り上げ（計算仕様書 `CLC-023`）

給与・賞与のテーブルを読まないため、`閲覧` は記録しない（画面設計書 `UID-060`）。
"""

from datetime import date
from pathlib import Path

import streamlit as st

from crm_g.application.employee import list_employees
from crm_g.application.forecast import (
    CONFIDENCES,
    ForecastInput,
    ForecastRow,
    InvalidForecastError,
    list_forecasts,
    save_forecast,
    total_by_confidence,
)
from crm_g.application.operation_log import Operation
from crm_g.application.sales import (
    AXES,
    LABEL_COLUMNS,
    PERIOD_UNITS,
    SalesSummary,
    display_labels,
    last_fiscal_year,
    summarize_sales,
)
from crm_g.domain.period import FIRST_SELECTABLE_FISCAL_YEAR, current_fiscal_half, fiscal_year_label
from crm_g.infrastructure.db import open_connection
from crm_g.infrastructure.paths import get_app_paths
from crm_g.ui.components.formatting import format_ratio_percent, format_yen
from crm_g.ui.components.messages import (
    show_errors,
    show_status,
    show_success,
    show_unexpected_error,
)
from crm_g.ui.components.month_selector import select_year_month

_LAST_RESULT_KEY = "sales_forecast_last_result"
_NEW = "（新規）"
_FORECAST_MONTHS = 6
_FORM_KEY_PREFIXES = (
    "sales_forecast_choice",
    "sales_forecast_employee_",
    "sales_forecast_month_",
    "sales_forecast_confidence_",
    "sales_forecast_unit_price_",
    "sales_forecast_ratio_",
)


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

    label_column = LABEL_COLUMNS.get(axis)
    table = []
    chart = []
    for row, label in zip(summary.rows, display_labels(summary.rows), strict=True):
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


def _render_actuals(db_path: Path) -> None:
    unit = (
        st.segmented_control("集計の期間", PERIOD_UNITS, default="月別", key="sales_unit") or "月別"
    )
    selected_year = _select_fiscal_year(disabled=unit == "期別")
    fiscal_year = None if unit == "期別" else selected_year
    axis = st.segmented_control("集計の軸", AXES, default="合計", key="sales_axis") or "合計"

    try:
        with open_connection(db_path) as connection:
            summary = summarize_sales(connection, unit, axis, fiscal_year, date.today())
    except Exception:
        show_unexpected_error()
        return
    _render_summary(summary, axis)


def _ratio(rate_bp: int | None) -> str:
    return "－" if rate_bp is None else f"{rate_bp / 100:.2f}%"


def _month_after(months: int) -> tuple[int, int]:
    today = date.today()
    index = today.year * 12 + today.month - 1 + months
    return index // 12, index % 12 + 1


def _split_month(year_month: str) -> tuple[int, int]:
    year, month = year_month.split("-")
    return int(year), int(month)


def _render_forecast_tables(rows: tuple[ForecastRow, ...]) -> None:
    st.markdown("**確度別**")
    st.dataframe(
        [
            {
                "確度": total.confidence,
                "人数": total.employee_count,
                "見込み売上（円）": format_yen(total.forecast_sales),
            }
            for total in total_by_confidence(rows)
        ],
        hide_index=True,
    )
    st.markdown("**見込みの一覧**")
    if not rows:
        show_status("対象月の範囲に、見込みはありません。")
        return
    st.dataframe(
        [
            {
                "氏名": row.employee_name,
                "年月": row.year_month.replace("-", "/"),
                "確度": row.confidence,
                "見込み単価（円）": format_yen(row.unit_price),
                "見込み稼働率": _ratio(row.billing_ratio_bp),
                "見込み売上（円）": format_yen(row.forecast_sales),
            }
            for row in rows
        ],
        hide_index=True,
    )


def _choice_label(key: str, existing: dict[str, ForecastRow]) -> str:
    # 同じ氏名の社員を区別するため、社員IDを添える（selectbox は表示の文字列で値を区別する）。
    if key == _NEW:
        return key
    row = existing[key]
    return f"{row.employee_id}：{row.employee_name}　{row.year_month.replace('-', '/')}"


def _render_forecast_form(db_path: Path, rows: tuple[ForecastRow, ...]) -> None:
    st.markdown("**見込みの入力**")
    with open_connection(db_path) as connection:
        employees = {employee.id: employee.name for employee in list_employees(connection)}
    # 一覧の見込みを選ぶと、その見込みを編集する（`st.dataframe` の行選択の代替）。
    existing = {f"{row.employee_id}:{row.year_month}": row for row in rows}
    # 入力できる社員がいないときは新規の入力を出さず、既存の見込みの編集だけにする。
    choices = [*([_NEW] if employees else []), *existing]
    if not choices:
        show_status("見込みを入力できる社員（在籍・退職予定）がいません。")
        return
    choice: str = st.selectbox(
        "編集する見込み",
        choices,
        format_func=lambda key: _choice_label(key, existing),
        key="sales_forecast_choice",
    )
    selected = existing.get(choice)
    suffix = choice.replace(":", "_") if selected else "new"
    with st.form(f"sales_forecast_form_{suffix}"):
        employee_id = 0
        year_month = ""
        if selected is not None:
            # 社員・年月は主キーのため、編集では変えられない（表示だけにする）。
            st.markdown(
                f"社員：{selected.employee_id}：{selected.employee_name}　"
                f"年月：{selected.year_month.replace('-', '/')}"
            )
        else:
            employee_id = st.selectbox(
                "社員",
                list(employees),
                format_func=lambda option: f"{option}：{employees[option]}",
                key=f"sales_forecast_employee_{suffix}",
            )
            year_month = select_year_month(
                key=f"sales_forecast_month_{suffix}", default=_month_after(1)
            )
        confidence: str = st.selectbox(
            "確度",
            CONFIDENCES,
            index=CONFIDENCES.index(selected.confidence) if selected else 0,
            key=f"sales_forecast_confidence_{suffix}",
        )
        unit_price = st.text_input(
            "見込み単価（円）",
            value=str(selected.unit_price) if selected and selected.unit_price is not None else "",
            key=f"sales_forecast_unit_price_{suffix}",
        )
        ratio = st.text_input(
            "見込み稼働率（%）",
            value=f"{selected.billing_ratio_bp / 100:g}"
            if selected and selected.billing_ratio_bp is not None
            else "",
            key=f"sales_forecast_ratio_{suffix}",
        )
        submitted = st.form_submit_button("見込みを保存", key=f"sales_forecast_submit_{suffix}")
    if not submitted:
        return
    data = ForecastInput(
        employee_id=selected.employee_id if selected else employee_id,
        year_month=selected.year_month if selected else year_month,
        confidence=confidence,
        unit_price=unit_price,
        billing_ratio_percent=ratio,
    )
    try:
        with open_connection(db_path) as connection:
            operation = save_forecast(connection, data)
    except InvalidForecastError as error:
        show_errors([str(error)])
        return
    except Exception:
        show_unexpected_error()
        return
    # 保存した値で入力欄を作り直すため、入力欄の値を捨てる（対象月の範囲は残す）。
    for key in list(st.session_state):
        if str(key).startswith(_FORM_KEY_PREFIXES):
            del st.session_state[key]
    st.session_state[_LAST_RESULT_KEY] = {
        Operation.CREATE: "見込みを登録しました",
        Operation.UPDATE: "見込みを更新しました",
        None: "見込みに変更はありません",
    }[operation]
    st.rerun()


def _render_forecast(db_path: Path) -> None:
    start_column, end_column = st.columns(2)
    with start_column:
        first_month = select_year_month(key="sales_forecast_from", label="開始月")
    with end_column:
        last_month = select_year_month(
            key="sales_forecast_to", label="終了月", default=_month_after(_FORECAST_MONTHS - 1)
        )
    if first_month > last_month:
        show_errors(["終了月は、開始月と同じか、それより後にしてください"])
        return
    try:
        with open_connection(db_path) as connection:
            rows = list_forecasts(connection, first_month, last_month)
    except Exception:
        show_unexpected_error()
        return
    _render_forecast_tables(rows)
    _render_forecast_form(db_path, rows)


def render() -> None:
    st.title("売上")
    last_result = st.session_state.pop(_LAST_RESULT_KEY, None)
    if last_result is not None:
        show_success(last_result)
    db_path = get_app_paths().db_path
    actuals_tab, forecast_tab = st.tabs(["実績の集計", "見込み"])
    with actuals_tab:
        _render_actuals(db_path)
    with forecast_tab:
        _render_forecast(db_path)


render()

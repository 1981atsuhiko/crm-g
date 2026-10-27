"""出力画面（`SCN-080`）。

出力の種類と条件を選び、「出力の準備」で CSV を作成する。作成に成功したときだけ、
ダウンロードのボタンを出す（`st.download_button` には作成済みの `bytes` を渡す。画面設計書 7.2）。
給与・賞与を含む出力は、準備の時点で `出力` を記録し、記録に失敗したときはダウンロードの
ボタンを出さない（`SCD-021`）。作成済みの CSV は、出力の種類・条件を変えたとき、または画面を
離れたときに破棄する（`app.py` で破棄する）。
"""

import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from typing import TypedDict

import streamlit as st

from crm_g.application.bonus import NoBaseMonthCalendarError, NoBonusRuleError
from crm_g.application.csv_export import ExportFile
from crm_g.application.employee import DEFAULT_ENROLLMENT_STATUSES, ENROLLMENT_STATUSES
from crm_g.application.hr_export import (
    HR_EXPORT_KIND,
    NoPayrollResultError,
    build_hr_export,
    is_unclosed_month_with_results,
)
from crm_g.application.list_exports import (
    BONUS_LIST,
    EMPLOYEE_LIST,
    PAYROLL_LIST,
    RAISE_LIST,
    SALES_SUMMARY,
    WORK_LIST,
    export_bonus_list,
    export_employee_list,
    export_payroll_list,
    export_raise_list,
    export_sales_summary,
    export_work_list,
)
from crm_g.application.sales import AXES, PERIOD_UNITS, last_fiscal_year
from crm_g.domain.period import (
    FIRST_SELECTABLE_FISCAL_YEAR,
    current_fiscal_half,
    fiscal_year_date_range,
    fiscal_year_label,
    half_code,
    half_date_range,
    half_label,
    previous_half,
)
from crm_g.infrastructure.db import open_connection
from crm_g.infrastructure.paths import get_app_paths
from crm_g.ui.components.half_selector import select_half
from crm_g.ui.components.messages import (
    show_errors,
    show_status,
    show_unexpected_error,
    show_warning,
)
from crm_g.ui.components.month_selector import select_year_month
from crm_g.ui.session import EXPORT_STATE_KEY

_KINDS = (
    HR_EXPORT_KIND,
    EMPLOYEE_LIST,
    RAISE_LIST,
    PAYROLL_LIST,
    BONUS_LIST,
    WORK_LIST,
    SALES_SUMMARY,
)
_SENSITIVE_NOTICE = (
    "出力するファイルには、給与・賞与などの機微情報を含む場合があります。"
    "保管場所と取り扱いに注意してください。"
)
# 利用者に原因を示してよい、想定内のエラー。
_EXPECTED_ERRORS = (NoPayrollResultError, NoBaseMonthCalendarError, NoBonusRuleError)

_Builder = Callable[[sqlite3.Connection], ExportFile]


class _Prepared(TypedDict):
    """作成済みの CSV（再実行をまたいで保持するため、組み込み型で持つ）。"""

    kind: str
    condition: str
    file_name: str
    content: bytes


@dataclass(frozen=True)
class _Condition:
    """選んだ条件。`key` は、条件を変えたかの判定に使う。`builder` は None なら準備できない。"""

    key: str
    builder: _Builder | None


def _month_condition(kind: str) -> _Condition:
    year_month = select_year_month(key="export_month")
    if kind == HR_EXPORT_KIND:
        with open_connection(get_app_paths().db_path) as connection:
            # 計算結果がない月は、準備でエラーを出すため、締め前の案内は出さない。
            if is_unclosed_month_with_results(connection, year_month):
                show_status("締め前の月（未確定の値）")
        return _Condition(year_month, lambda connection: build_hr_export(connection, year_month))
    return _Condition(year_month, lambda connection: export_payroll_list(connection, year_month))


def _employee_condition() -> _Condition:
    statuses = st.multiselect(
        "在籍ステータス",
        ENROLLMENT_STATUSES,
        default=list(DEFAULT_ENROLLMENT_STATUSES),
        key="export_enrollment_statuses",
    )
    if not statuses:
        show_status("在籍ステータスを1つ以上選んでください。")
        return _Condition("", None)
    # 選んだ順に依らず、選択肢の定義の順に並べる（条件とファイル名を揺らさないため）。
    ordered = [status for status in ENROLLMENT_STATUSES if status in statuses]
    return _Condition(
        "・".join(ordered), lambda connection: export_employee_list(connection, ordered)
    )


def _raise_condition() -> _Condition:
    unit = st.radio("期間の単位", ["半期", "通期"], key="export_raise_unit", horizontal=True)
    if unit == "半期":
        fiscal_year, half = select_half("対象期間", key="export_raise_half")
        start, end = half_date_range(fiscal_year, half)
        label = half_label(fiscal_year, half).replace(" ", "")
    else:
        current = current_fiscal_half(date.today())[0]
        years = list(range(FIRST_SELECTABLE_FISCAL_YEAR, current + 2))
        selected_year: int = st.selectbox(
            "対象期間",
            years,
            index=years.index(current),
            format_func=lambda year: f"{fiscal_year_label(year)}（通期）",
            key="export_raise_year",
        )
        start, end = fiscal_year_date_range(selected_year)
        label = f"{fiscal_year_label(selected_year)}通期"
    return _Condition(label, lambda connection: export_raise_list(connection, start, end, label))


def _bonus_condition() -> _Condition:
    fiscal_year, half = select_half(
        "半期", key="export_bonus_half", default=previous_half(*current_fiscal_half(date.today()))
    )
    eval_half = half_code(fiscal_year, half)
    return _Condition(eval_half, lambda connection: export_bonus_list(connection, eval_half))


def _work_condition() -> _Condition:
    fiscal_year, half = select_half("半期", key="export_work_half")
    return _Condition(
        half_code(fiscal_year, half),
        lambda connection: export_work_list(
            connection, fiscal_year, half, DEFAULT_ENROLLMENT_STATUSES
        ),
    )


def _sales_condition() -> _Condition:
    unit = (
        st.segmented_control("集計の期間", PERIOD_UNITS, default="月別", key="export_sales_unit")
        or "月別"
    )
    today = date.today()
    years = list(range(FIRST_SELECTABLE_FISCAL_YEAR, last_fiscal_year(today) + 1))
    selected_year: int = st.selectbox(
        "対象の期",
        years,
        index=years.index(current_fiscal_half(today)[0]),
        format_func=fiscal_year_label,
        key="export_sales_fiscal_year",
        disabled=unit == "期別",
    )
    fiscal_year = None if unit == "期別" else selected_year
    axis = st.segmented_control("集計の軸", AXES, default="合計", key="export_sales_axis") or "合計"
    return _Condition(
        f"{unit}_{fiscal_year}_{axis}",
        lambda connection: export_sales_summary(connection, unit, axis, fiscal_year, today),
    )


def _select_condition(kind: str) -> _Condition:
    if kind in (HR_EXPORT_KIND, PAYROLL_LIST):
        return _month_condition(kind)
    if kind == EMPLOYEE_LIST:
        return _employee_condition()
    if kind == RAISE_LIST:
        return _raise_condition()
    if kind == BONUS_LIST:
        return _bonus_condition()
    if kind == WORK_LIST:
        return _work_condition()
    return _sales_condition()


def _prepare(kind: str, condition_key: str, builder: _Builder) -> None:
    try:
        with open_connection(get_app_paths().db_path) as connection:
            export_file = builder(connection)
    except _EXPECTED_ERRORS as error:
        show_errors([str(error)])
        return
    except Exception:
        # `出力` の記録の失敗を含む。CSV は渡さない（`SCD-021`）。
        show_unexpected_error()
        return
    st.session_state[EXPORT_STATE_KEY] = _Prepared(
        kind=kind,
        condition=condition_key,
        file_name=export_file.file_name,
        content=export_file.content,
    )


def render() -> None:
    st.title("出力")
    kind: str = st.selectbox("出力の種類", _KINDS, key="export_kind")
    condition = _select_condition(kind)

    prepared: _Prepared | None = st.session_state.get(EXPORT_STATE_KEY)
    if prepared is not None and (prepared["kind"], prepared["condition"]) != (kind, condition.key):
        # 出力の種類・条件を変えたときは、作成済みの CSV を破棄する。
        st.session_state.pop(EXPORT_STATE_KEY)
        prepared = None

    show_warning(_SENSITIVE_NOTICE)
    if st.button("出力の準備", key="export_prepare", disabled=condition.builder is None):
        st.session_state.pop(EXPORT_STATE_KEY, None)
        if condition.builder is not None:
            _prepare(kind, condition.key, condition.builder)
        prepared = st.session_state.get(EXPORT_STATE_KEY)

    if prepared is not None:
        st.download_button(
            "ダウンロード",
            data=prepared["content"],
            file_name=prepared["file_name"],
            mime="text/csv",
            key="export_download",
        )


render()

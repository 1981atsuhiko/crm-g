"""社員画面（`SCN-020` 社員一覧、`SCN-021` 社員詳細）。

`SCN-021`・`SCN-022` は、`SCN-020` から開く画面で、メニューには出さない
（画面設計書 2.2）。このファイル1つで、一覧・詳細の表示モードを
`st.session_state` で切り替える。登録・編集（`SCN-022`）は、PH-07-2で追加する。
"""

from pathlib import Path

import streamlit as st

from crm_g.application.employee import (
    DEFAULT_ENROLLMENT_STATUSES,
    ENROLLMENT_STATUSES,
    Employee,
    get_employee,
    list_bonuses,
    list_employees,
    list_rate_history,
    list_salary_history,
    list_salary_history_items,
    list_work_periods,
)
from crm_g.infrastructure.db import open_connection
from crm_g.infrastructure.paths import get_app_paths
from crm_g.ui.components.messages import show_errors, show_unexpected_error
from crm_g.ui.components.view_log import record_view_if_newly_displayed

_MODE_KEY = "employee_mode"
_SELECTED_ID_KEY = "employee_selected_id"
_SCREEN_NAME = "SCN-021"
_VIEW_LOG_TABLES = ("salary_history", "salary_history_item", "bonus")


def _yen(amount: int) -> str:
    return f"{amount:,}円"


def _percent(rate_bp: int) -> str:
    return f"{rate_bp / 100:.2f}%"


def _go_to_detail(employee_id: int) -> None:
    st.session_state[_SELECTED_ID_KEY] = employee_id
    st.session_state[_MODE_KEY] = "detail"
    st.rerun()


def _go_to_list() -> None:
    st.session_state[_MODE_KEY] = "list"
    st.session_state.pop(_SELECTED_ID_KEY, None)
    st.rerun()


def _render_list(db_path: Path) -> None:
    st.title("社員一覧")

    statuses = st.multiselect(
        "在籍ステータス",
        ENROLLMENT_STATUSES,
        default=list(DEFAULT_ENROLLMENT_STATUSES),
        key="employee_filter_statuses",
    )
    search = st.text_input("検索（氏名・メールアドレス）", key="employee_search")

    with open_connection(db_path) as connection:
        employees = list_employees(connection, tuple(statuses), search)

    if not employees:
        st.info("該当する社員がいません。")
        return

    options = [f"{employee.id}：{employee.name}" for employee in employees]
    st.dataframe(
        [
            {
                "氏名": employee.name,
                "メールアドレス": employee.email,
                "社員番号": employee.employee_no or "",
                "雇用形態": employee.employment_type or "",
                "職種": employee.job_type or "",
                "在籍ステータス": employee.enrollment_status,
                "入社日": employee.join_date.isoformat() if employee.join_date else "",
                "退職日": employee.retire_date.isoformat() if employee.retire_date else "",
            }
            for employee in employees
        ],
        hide_index=True,
    )

    selected_option = st.selectbox("社員を選ぶ", options, key="employee_list_selected")
    if st.button("詳細を開く", key="employee_open_detail"):
        employee_id = int(selected_option.split("：", 1)[0])
        _go_to_detail(employee_id)


def _render_basic_info(employee: Employee) -> None:
    st.subheader(employee.name)
    st.write(f"メールアドレス：{employee.email}")
    st.write(f"氏名（カナ）：{employee.name_kana or '-'}")
    st.write(f"社員番号：{employee.employee_no or '-'}")
    st.write(f"雇用形態：{employee.employment_type or '-'}")
    st.write(f"生年月日：{employee.birth_date.isoformat() if employee.birth_date else '-'}")
    st.write(f"性別：{employee.gender or '-'}")
    st.write(f"入社日：{employee.join_date.isoformat() if employee.join_date else '-'}")
    st.write(f"最寄駅：{employee.nearest_station or '-'}")
    st.write(f"居住エリア：{employee.residence_area or '-'}")
    st.write(f"職種：{employee.job_type or '-'}")
    st.write(f"在籍ステータス：{employee.enrollment_status}")
    st.write(f"退職日：{employee.retire_date.isoformat() if employee.retire_date else '-'}")


def _render_salary_history_tab(db_path: Path, employee_id: int) -> None:
    with open_connection(db_path) as connection:
        history = list_salary_history(connection, employee_id)

    if not history:
        st.info("給与履歴がありません。")
        return

    st.dataframe(
        [
            {
                "適用開始日": row.effective_from.isoformat(),
                "適用終了日": row.effective_to.isoformat() if row.effective_to else "",
                "給与体系": row.pay_system,
                "雇用形態": row.employment_type,
                "等級": row.grade or "",
                "改定区分": row.revision_type,
                "月給合計": _yen(row.monthly_salary),
                "備考": row.note or "",
            }
            for row in history
        ],
        hide_index=True,
    )

    options = {f"{row.effective_from.isoformat()}（id={row.id}）": row.id for row in history}
    selected_label = st.selectbox(
        "内訳を見る給与履歴", list(options), key="employee_salary_history_selected"
    )
    with open_connection(db_path) as connection:
        items = list_salary_history_items(connection, options[selected_label])

    st.write("内訳")
    st.dataframe(
        [{"給与項目": item.pay_item_name, "金額": _yen(item.amount)} for item in items],
        hide_index=True,
    )


def _render_rate_history_tab(db_path: Path, employee_id: int) -> None:
    with open_connection(db_path) as connection:
        rates = list_rate_history(connection, employee_id)

    if not rates:
        st.info("単価履歴がありません。")
        return

    st.dataframe(
        [
            {
                "適用開始日": row.effective_from.isoformat(),
                "案件": f"{row.customer_name or ''}／{row.project_name or ''}",
                "単価": _yen(row.unit_price),
                "改定の根拠": row.revision_basis or "",
                "備考": row.note or "",
            }
            for row in rates
        ],
        hide_index=True,
    )


def _render_work_period_tab(db_path: Path, employee_id: int) -> None:
    with open_connection(db_path) as connection:
        periods = list_work_periods(connection, employee_id)

    if not periods:
        st.info("稼働がありません。")
        return

    st.dataframe(
        [
            {
                "開始日": row.start_date.isoformat(),
                "終了日": row.end_date.isoformat() if row.end_date else "",
                "稼働状態": row.work_status_name,
                "案件": f"{row.customer_name or ''}／{row.project_name or ''}",
                "稼働率": _percent(row.billing_ratio_bp),
                "備考": row.note or "",
            }
            for row in periods
        ],
        hide_index=True,
    )


def _render_bonus_tab(db_path: Path, employee_id: int) -> None:
    with open_connection(db_path) as connection:
        bonuses = list_bonuses(connection, employee_id)

    if not bonuses:
        st.info("賞与がありません。")
        return

    st.dataframe(
        [
            {
                "半期": row.eval_half,
                "支給月": row.pay_month,
                "基準額の種類": row.base_type,
                "基準額": _yen(row.base_amount),
                "評価ランク": row.eval_rank or "",
                "掛け率": _percent(row.rate_bp) if row.rate_bp is not None else "",
                "賞与額": _yen(row.bonus_amount),
                "確定済みか": "○" if row.is_closed else "×",
            }
            for row in bonuses
        ],
        hide_index=True,
    )


def _render_detail(db_path: Path, employee_id: int) -> None:
    with open_connection(db_path) as connection:
        employee = get_employee(connection, employee_id)
        if employee is None:
            show_errors(["社員が見つかりません。"])
            _go_to_list()
            return

        try:
            for table in _VIEW_LOG_TABLES:
                record_view_if_newly_displayed(connection, _SCREEN_NAME, table, str(employee_id))
        except Exception:
            show_unexpected_error()
            return

    st.title("社員詳細")
    if st.button("一覧へ戻る", key="employee_back_to_list"):
        _go_to_list()
        return

    _render_basic_info(employee)

    tab_salary, tab_rate, tab_work, tab_bonus = st.tabs(["給与履歴", "単価履歴", "稼働", "賞与"])
    with tab_salary:
        _render_salary_history_tab(db_path, employee_id)
    with tab_rate:
        _render_rate_history_tab(db_path, employee_id)
    with tab_work:
        _render_work_period_tab(db_path, employee_id)
    with tab_bonus:
        _render_bonus_tab(db_path, employee_id)


def render() -> None:
    db_path = get_app_paths().db_path
    mode = st.session_state.get(_MODE_KEY, "list")
    selected_id = st.session_state.get(_SELECTED_ID_KEY)

    if mode == "detail" and selected_id is not None:
        _render_detail(db_path, selected_id)
    else:
        _render_list(db_path)


render()

"""給与履歴の登録画面（`SCN-030`）。

`UID-052` の2段階の登録を、初めて実際に使う画面（`two_step_form.py`はPH-05で
実装済みだが、これまで利用する画面がなかった）。月給合計・前の履歴の終了日は、
送信後にだけ表示する（`UID-004`。`st.form`は送信まで再実行されないため）。
同じ理由により、年俸年収欄は常に表示し（年俸制のときだけ必須）、内訳の
給与項目は、今日時点で有効なものを表示する（適用開始日が未確定なため）。
"""

from datetime import date
from pathlib import Path
from typing import TypedDict

import streamlit as st

from crm_g.application.employee import EMPLOYMENT_TYPES, ENROLLMENT_STATUSES, list_employees
from crm_g.application.salary_history import (
    PAY_SYSTEMS,
    REVISION_TYPES,
    EmployeeNotFoundError,
    InvalidEmploymentTypeError,
    InvalidItemAmountError,
    InvalidPaySystemError,
    InvalidRevisionTypeError,
    InvalidValidFromError,
    MissingAnnualSalaryError,
    SalaryHistoryInput,
    SalaryItemInput,
    UnexpectedAnnualSalaryError,
    check_needs_warning,
    get_current_salary_history,
    list_available_pay_items,
    register_salary_history,
)
from crm_g.infrastructure.db import open_connection
from crm_g.infrastructure.paths import get_app_paths
from crm_g.ui.components.messages import (
    UNEXPECTED_ERROR_MESSAGE,
    show_errors,
    show_status,
    show_success,
)
from crm_g.ui.components.two_step_form import discard, is_awaiting_confirmation, start_confirmation
from crm_g.ui.components.view_log import record_view_if_newly_displayed

_FORM_KEY = "salary_history"
_PENDING_KEY = "salary_history_pending"
_LAST_RESULT_KEY = "salary_history_last_result"
_LAST_ERROR_KEY = "salary_history_last_error"
_SCREEN_NAME = "SCN-030"


class _PendingItem(TypedDict):
    pay_item_code: str
    amount: int


class _PendingSalaryHistory(TypedDict):
    """確認待ちの「給与履歴の登録」入力（複数回の再実行をまたいで保持する）。"""

    employee_id: int
    effective_from: str
    pay_system: str
    employment_type: str
    grade: str | None
    annual_salary: int | None
    revision_type: str
    note: str | None
    items: list[_PendingItem]


def _yen(amount: int) -> str:
    return f"{amount:,}円"


def _render_employee_selector(db_path: Path, *, disabled: bool) -> int | None:
    with open_connection(db_path) as connection:
        employees = list_employees(connection, ENROLLMENT_STATUSES)

    if not employees:
        st.info("社員が登録されていません。")
        return None

    options = {f"{employee.id}：{employee.name}": employee.id for employee in employees}
    selected_label = st.selectbox(
        "社員", list(options), key="salary_history_employee", disabled=disabled
    )
    return options[selected_label]


def _render_current_history(db_path: Path, employee_id: int) -> None:
    with open_connection(db_path) as connection:
        current = get_current_salary_history(connection, employee_id)

    if current is None:
        show_status("現在の給与履歴はありません。")
        return
    st.write(
        f"現在の履歴：適用開始日 {current.effective_from.isoformat()}、"
        f"月給合計 {_yen(current.monthly_salary)}"
    )


def _render_form(db_path: Path, employee_id: int) -> None:
    key_suffix = str(employee_id)
    item_date = date.today()
    with open_connection(db_path) as connection:
        available_items = list_available_pay_items(connection, item_date)

    with st.form(f"salary_history_form_{key_suffix}"):
        effective_from = st.date_input(
            "適用開始日", value=None, key=f"salary_history_effective_from_{key_suffix}"
        )
        pay_system = st.selectbox(
            "給与体系", PAY_SYSTEMS, key=f"salary_history_pay_system_{key_suffix}"
        )
        employment_type = st.selectbox(
            "雇用形態", EMPLOYMENT_TYPES, key=f"salary_history_employment_type_{key_suffix}"
        )
        grade = st.text_input(
            "等級（例：P5-15。新制度のみ）", key=f"salary_history_grade_{key_suffix}"
        )
        annual_salary_text = st.text_input(
            "年俸年収（円。給与体系が年俸制のときだけ入力）",
            key=f"salary_history_annual_salary_{key_suffix}",
        )
        revision_type = st.selectbox(
            "改定区分", REVISION_TYPES, key=f"salary_history_revision_type_{key_suffix}"
        )
        note = st.text_input("備考", key=f"salary_history_note_{key_suffix}")

        st.write("内訳（未入力は0円として扱う）")
        item_amounts: dict[str, str] = {}
        for item in available_items:
            item_amounts[item.code] = st.text_input(
                item.name, key=f"salary_history_item_{item.code}_{key_suffix}"
            )

        submitted = st.form_submit_button("登録", key=f"salary_history_submit_{key_suffix}")

    if not submitted:
        return

    if not isinstance(effective_from, date):
        show_errors(["適用開始日を入力してください"])
        return

    annual_salary: int | None = None
    if annual_salary_text:
        try:
            annual_salary = int(annual_salary_text)
        except ValueError:
            show_errors(["年俸年収は、整数で入力してください"])
            return

    items: list[_PendingItem] = []
    for code, amount_text in item_amounts.items():
        amount = 0
        if amount_text:
            try:
                amount = int(amount_text)
            except ValueError:
                show_errors(["内訳の金額は、整数で入力してください"])
                return
        items.append(_PendingItem(pay_item_code=code, amount=amount))

    pending = _PendingSalaryHistory(
        employee_id=employee_id,
        effective_from=effective_from.isoformat(),
        pay_system=pay_system,
        employment_type=employment_type,
        grade=grade or None,
        annual_salary=annual_salary,
        revision_type=revision_type,
        note=note or None,
        items=items,
    )
    st.session_state[_PENDING_KEY] = pending
    start_confirmation(_FORM_KEY)
    st.rerun()


def _pending_to_data(pending: _PendingSalaryHistory) -> SalaryHistoryInput:
    return SalaryHistoryInput(
        effective_from=date.fromisoformat(pending["effective_from"]),
        pay_system=pending["pay_system"],
        employment_type=pending["employment_type"],
        grade=pending["grade"],
        annual_salary=pending["annual_salary"],
        revision_type=pending["revision_type"],
        note=pending["note"],
        items=tuple(
            SalaryItemInput(pay_item_code=item["pay_item_code"], amount=item["amount"])
            for item in pending["items"]
        ),
    )


def _render_pending_confirmation(db_path: Path, pending: _PendingSalaryHistory) -> None:
    employee_id = pending["employee_id"]
    data = _pending_to_data(pending)

    with open_connection(db_path) as connection:
        needs_warning = check_needs_warning(connection, employee_id, data)

    if needs_warning:
        st.warning(
            "改定区分に対して、通常の改定月ではない適用開始日です。"
            "内容を確認し、問題なければ登録してください。"
        )

    button_label = "警告を確認して登録する" if needs_warning else "登録する"
    confirm_column, cancel_column = st.columns(2)

    if confirm_column.button(button_label, key="salary_history_confirm"):
        with open_connection(db_path) as connection:
            try:
                result = register_salary_history(connection, employee_id, data)
            except (
                EmployeeNotFoundError,
                InvalidPaySystemError,
                InvalidEmploymentTypeError,
                InvalidRevisionTypeError,
                MissingAnnualSalaryError,
                UnexpectedAnnualSalaryError,
                InvalidItemAmountError,
                InvalidValidFromError,
            ) as error:
                st.session_state[_LAST_ERROR_KEY] = str(error)
            except Exception:
                st.session_state[_LAST_ERROR_KEY] = UNEXPECTED_ERROR_MESSAGE
            else:
                previous_text = "（直前の履歴はありません）"
                if result.previous_effective_to is not None:
                    previous_text = (
                        f"直前の履歴の適用終了日：{result.previous_effective_to.isoformat()}"
                    )
                st.session_state[_LAST_RESULT_KEY] = (
                    f"登録しました。月給合計：{_yen(result.monthly_salary)}。{previous_text}"
                )
        st.session_state.pop(_PENDING_KEY, None)
        discard(_FORM_KEY)
        st.rerun()

    if cancel_column.button("キャンセル", key="salary_history_cancel"):
        st.session_state.pop(_PENDING_KEY, None)
        discard(_FORM_KEY)
        st.rerun()


def _render_history_list(db_path: Path, employee_id: int) -> None:
    with open_connection(db_path) as connection:
        try:
            record_view_if_newly_displayed(
                connection, _SCREEN_NAME, "salary_history", str(employee_id)
            )
            record_view_if_newly_displayed(
                connection, _SCREEN_NAME, "salary_history_item", str(employee_id)
            )
        except Exception:
            show_errors([UNEXPECTED_ERROR_MESSAGE])
            return

        rows = connection.execute(
            "SELECT effective_from, effective_to, pay_system, revision_type "
            "FROM salary_history WHERE employee_id = ? ORDER BY effective_from",
            (employee_id,),
        ).fetchall()

    st.subheader("現在の履歴（時系列）")
    st.dataframe(
        [
            {
                "適用開始日": row[0],
                "適用終了日": row[1] or "",
                "給与体系": row[2],
                "改定区分": row[3],
            }
            for row in rows
        ],
        hide_index=True,
    )


def render() -> None:
    st.title("給与履歴の登録")

    last_error = st.session_state.pop(_LAST_ERROR_KEY, None)
    if last_error is not None:
        show_errors([last_error])
    last_result = st.session_state.pop(_LAST_RESULT_KEY, None)
    if last_result is not None:
        show_success(last_result)

    db_path = get_app_paths().db_path
    pending: _PendingSalaryHistory | None = st.session_state.get(_PENDING_KEY)
    awaiting_confirmation = is_awaiting_confirmation(_FORM_KEY) and pending is not None

    selected_employee_id = _render_employee_selector(db_path, disabled=awaiting_confirmation)
    if selected_employee_id is None:
        return
    # 確認待ち中は、選択中の社員ではなく確認対象（送信時点の社員）を常に使う
    # （selectboxはdisabled=Trueでも表示上の値は保持されるため、両者は一致する）。
    employee_id = pending["employee_id"] if pending is not None else selected_employee_id

    _render_current_history(db_path, employee_id)

    if awaiting_confirmation and pending is not None:
        _render_pending_confirmation(db_path, pending)
    else:
        _render_form(db_path, employee_id)

    _render_history_list(db_path, employee_id)


render()

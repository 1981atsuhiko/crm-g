"""稼働画面（`SCN-050`）。

3つのタブ（稼働期間・参画・単価）で構成する。PH-09-1では、参画タブと単価タブを
作る。稼働期間タブはPH-09-2で作る。`UID-053` の確認ダイアログの対象外のため、
保存は確認なしで行う。締め済みの月への警告（`UID-054`）と再計算の接続はPH-10で
行う。操作ログへの記録は、アプリケーション層で行う（`SCD-020`）。
"""

from datetime import date
from pathlib import Path

import streamlit as st

from crm_g.application.assignment import (
    ASSIGNMENT_CATEGORIES,
    Assignment,
    AssignmentInput,
    AssignmentNotFoundError,
    InvalidCategoryError,
    InvalidCustomerNameError,
    InvalidPeriodError,
    create_assignment,
    list_assignments,
    update_assignment,
)
from crm_g.application.assignment import EmployeeNotFoundError as AssignmentEmployeeNotFoundError
from crm_g.application.employee import ENROLLMENT_STATUSES, list_employees
from crm_g.application.unit_price import (
    AssignmentNotOwnedError,
    DuplicateRateHistoryError,
    InvalidUnitPriceError,
    RateHistoryInput,
    list_rate_history,
    register_rate_history,
)
from crm_g.application.unit_price import EmployeeNotFoundError as RateEmployeeNotFoundError
from crm_g.infrastructure.db import open_connection
from crm_g.infrastructure.paths import get_app_paths
from crm_g.ui.components.messages import (
    show_errors,
    show_status,
    show_success,
    show_unexpected_error,
)

_LAST_RESULT_KEY = "work_last_result"
_NEW_ASSIGNMENT = "（新しく登録する）"
_EMPLOYEE_LEVEL = "（社員単位の単価）"
_ASSIGNMENT_FIELDS = ("customer", "project", "category", "start", "end")
# `value=None` の日付欄は、既定では10年前より古い日を選べないため、下限を明示する。
_MIN_DATE = date(2000, 1, 1)


def _yen(amount: int) -> str:
    return f"{amount:,}円"


def _select_employee(db_path: Path, key: str) -> int | None:
    with open_connection(db_path) as connection:
        employees = list_employees(connection, ENROLLMENT_STATUSES)
    if not employees:
        st.info("社員が登録されていません。")
        return None
    options = {f"{employee.id}：{employee.name}": employee.id for employee in employees}
    return options[st.selectbox("社員", list(options), key=key)]


def _save_and_rerun(message: str) -> None:
    st.session_state[_LAST_RESULT_KEY] = message
    st.rerun()


def _render_assignment_form(db_path: Path, employee_id: int, existing: Assignment | None) -> None:
    # 選んだ参画ごとにウィジェットのキーを変え、選び直したときに入力欄を作り直す。
    suffix = f"{employee_id}_{existing.id if existing else 'new'}"
    with st.form(f"work_assignment_form_{suffix}"):
        customer_name = st.text_input(
            "顧客名",
            value=existing.customer_name if existing else "",
            key=f"work_assignment_customer_{suffix}",
        )
        project_name = st.text_input(
            "案件名",
            value=(existing.project_name or "") if existing else "",
            key=f"work_assignment_project_{suffix}",
        )
        category = st.selectbox(
            "区分",
            ASSIGNMENT_CATEGORIES,
            index=ASSIGNMENT_CATEGORIES.index(existing.category) if existing else 0,
            key=f"work_assignment_category_{suffix}",
        )
        start_date = st.date_input(
            "開始日",
            value=existing.start_date if existing else None,
            min_value=_MIN_DATE,
            key=f"work_assignment_start_{suffix}",
        )
        end_date = st.date_input(
            "終了日（空欄は継続中）",
            value=existing.end_date if existing else None,
            min_value=_MIN_DATE,
            key=f"work_assignment_end_{suffix}",
        )
        submitted = st.form_submit_button(
            "更新" if existing else "登録", key=f"work_assignment_submit_{suffix}"
        )

    if not submitted:
        return
    if not isinstance(start_date, date):
        show_errors(["開始日を入力してください"])
        return

    data = AssignmentInput(
        customer_name=customer_name,
        project_name=project_name or None,
        category=category,
        start_date=start_date,
        end_date=end_date if isinstance(end_date, date) else None,
    )
    try:
        with open_connection(db_path) as connection:
            if existing is None:
                create_assignment(connection, employee_id, data)
            else:
                update_assignment(connection, existing.id, data)
    except (
        InvalidCustomerNameError,
        InvalidCategoryError,
        InvalidPeriodError,
        AssignmentEmployeeNotFoundError,
        AssignmentNotFoundError,
    ) as error:
        show_errors([str(error)])
        return
    except Exception:
        show_unexpected_error()
        return
    if existing is None:
        # 新規登録の入力欄を空に戻し、同じ参画の二重登録を防ぐ。
        for field in _ASSIGNMENT_FIELDS:
            st.session_state.pop(f"work_assignment_{field}_{suffix}", None)
    _save_and_rerun("参画を更新しました" if existing else "参画を登録しました")


def _render_assignment_tab(db_path: Path) -> None:
    employee_id = _select_employee(db_path, "work_assignment_employee")
    if employee_id is None:
        return

    with open_connection(db_path) as connection:
        assignments = list_assignments(connection, employee_id)

    if assignments:
        st.dataframe(
            [
                {
                    "顧客名": assignment.customer_name,
                    "案件名": assignment.project_name or "",
                    "区分": assignment.category,
                    "開始日": assignment.start_date.isoformat(),
                    "終了日": assignment.end_date.isoformat() if assignment.end_date else "",
                }
                for assignment in assignments
            ],
            hide_index=True,
        )
    else:
        show_status("この社員の参画は、まだありません。")

    # 選択肢はIDにする。更新で表示名が変わっても、選択が保たれる。
    by_id = {assignment.id: assignment for assignment in assignments}
    selected_id = st.selectbox(
        "編集する参画",
        [None, *by_id],
        format_func=lambda assignment_id: (
            _NEW_ASSIGNMENT
            if assignment_id is None
            else f"{assignment_id}：{by_id[assignment_id].label}"
        ),
        key=f"work_assignment_selected_{employee_id}",
    )
    _render_assignment_form(
        db_path, employee_id, by_id[selected_id] if selected_id is not None else None
    )


def _render_rate_form(db_path: Path, employee_id: int, assignments: tuple[Assignment, ...]) -> None:
    suffix = str(employee_id)
    assignment_options: dict[str, int | None] = {_EMPLOYEE_LEVEL: None}
    assignment_options.update(
        {f"{assignment.id}：{assignment.label}": assignment.id for assignment in assignments}
    )

    with st.form(f"work_rate_form_{suffix}"):
        assignment_label = st.selectbox(
            "参画（案件単位の単価のときだけ選ぶ）",
            list(assignment_options),
            key=f"work_rate_assignment_{suffix}",
        )
        effective_from = st.date_input(
            "適用開始日",
            value=None,
            min_value=_MIN_DATE,
            key=f"work_rate_effective_from_{suffix}",
        )
        unit_price_text = st.text_input("単価（円）", key=f"work_rate_unit_price_{suffix}")
        revision_basis = st.text_input("改定の根拠", key=f"work_rate_basis_{suffix}")
        note = st.text_input("備考", key=f"work_rate_note_{suffix}")
        submitted = st.form_submit_button("登録", key=f"work_rate_submit_{suffix}")

    if not submitted:
        return
    if not isinstance(effective_from, date):
        show_errors(["適用開始日を入力してください"])
        return
    try:
        unit_price = int(unit_price_text)
    except ValueError:
        show_errors(["単価は、0以上の整数で入力してください"])
        return

    data = RateHistoryInput(
        assignment_id=assignment_options[assignment_label],
        effective_from=effective_from,
        unit_price=unit_price,
        revision_basis=revision_basis or None,
        note=note or None,
    )
    try:
        with open_connection(db_path) as connection:
            register_rate_history(connection, employee_id, data)
    except (
        InvalidUnitPriceError,
        DuplicateRateHistoryError,
        AssignmentNotOwnedError,
        RateEmployeeNotFoundError,
    ) as error:
        show_errors([str(error)])
        return
    except Exception:
        show_unexpected_error()
        return
    _save_and_rerun("単価を登録しました")


def _render_rate_tab(db_path: Path) -> None:
    employee_id = _select_employee(db_path, "work_rate_employee")
    if employee_id is None:
        return

    with open_connection(db_path) as connection:
        history = list_rate_history(connection, employee_id)
        assignments = list_assignments(connection, employee_id)

    if history:
        st.dataframe(
            [
                {
                    "適用開始日": entry.effective_from.isoformat(),
                    "参画": entry.assignment_label or "（社員単位）",
                    "単価": _yen(entry.unit_price),
                    "改定の根拠": entry.revision_basis or "",
                    "備考": entry.note or "",
                }
                for entry in history
            ],
            hide_index=True,
        )
    else:
        show_status("この社員の単価は、まだありません。")

    _render_rate_form(db_path, employee_id, assignments)


def render() -> None:
    st.title("稼働")

    last_result = st.session_state.pop(_LAST_RESULT_KEY, None)
    if last_result is not None:
        show_success(last_result)

    db_path = get_app_paths().db_path
    period_tab, assignment_tab, rate_tab = st.tabs(["稼働期間", "参画", "単価"])
    with period_tab:
        show_status("稼働期間の入力は、次の段階で追加します。")
    with assignment_tab:
        _render_assignment_tab(db_path)
    with rate_tab:
        _render_rate_tab(db_path)


render()

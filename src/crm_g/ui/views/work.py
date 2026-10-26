"""稼働画面（`SCN-050`）。

2つのタブ（稼働期間・参画）で構成する。単価は必ず参画に紐づくため、参画のタブで、
選んだ参画の単価を登録・編集・削除する（社員→参画→単価。データ設計書 `DBD-018`）。
稼働期間タブはPH-09-2で作る。確認ダイアログ（`UID-053`）は、単価の削除だけに出す。
締め済みの月への警告（`UID-054`）と再計算の接続はPH-10で行う。操作ログへの記録は、
アプリケーション層で行う（`SCD-020`）。
"""

from datetime import date
from pathlib import Path
from typing import TypedDict

import streamlit as st

from crm_g.application.assignment import (
    ASSIGNMENT_CATEGORIES,
    Assignment,
    AssignmentInput,
    AssignmentNotFoundError,
    EmployeeNotFoundError,
    InvalidCategoryError,
    InvalidCustomerNameError,
    InvalidPeriodError,
    RateOutsidePeriodError,
    create_assignment,
    list_assignments,
    update_assignment,
)
from crm_g.application.employee import ENROLLMENT_STATUSES, list_employees
from crm_g.application.unit_price import AssignmentNotFoundError as RateAssignmentNotFoundError
from crm_g.application.unit_price import (
    DuplicateRateHistoryError,
    InvalidUnitPriceError,
    OutsideAssignmentPeriodError,
    RateHistory,
    RateHistoryInput,
    RateHistoryNotFoundError,
    delete_rate_history,
    list_rate_history,
    register_rate_history,
    update_rate_history,
)
from crm_g.infrastructure.db import open_connection
from crm_g.infrastructure.paths import get_app_paths
from crm_g.ui.components.confirm_dialog import confirm_dialog
from crm_g.ui.components.messages import (
    UNEXPECTED_ERROR_MESSAGE,
    show_errors,
    show_status,
    show_success,
    show_unexpected_error,
)

_LAST_RESULT_KEY = "work_last_result"
_LAST_ERROR_KEY = "work_last_error"
_PENDING_RATE_DELETE_KEY = "work_pending_rate_delete"
_NEW_ASSIGNMENT = "（新しく登録する）"
_NEW_RATE = "（新しく登録する）"
_ASSIGNMENT_FIELDS = ("customer", "project", "category", "start", "end")
_RATE_FIELDS = ("effective_from", "unit_price", "basis", "note")
# `value=None` の日付欄は、既定では10年前より古い日を選べないため、下限を明示する。
_MIN_DATE = date(2000, 1, 1)


class _PendingRateDelete(TypedDict):
    """削除の確認待ちの単価（再実行をまたいで保持するため、組み込み型で持つ）。"""

    rate_history_id: int
    description: str


def _yen(amount: int) -> str:
    return f"{amount:,}円"


def _select_employee(db_path: Path) -> int | None:
    with open_connection(db_path) as connection:
        employees = list_employees(connection, ENROLLMENT_STATUSES)
    if not employees:
        st.info("社員が登録されていません。")
        return None
    options = {f"{employee.id}：{employee.name}": employee.id for employee in employees}
    return options[st.selectbox("社員", list(options), key="work_assignment_employee")]


def _clear_inputs(prefix: str, fields: tuple[str, ...], suffix: str) -> None:
    """新規登録の入力欄を空に戻し、同じ内容の二重登録を防ぐ。"""
    for field in fields:
        st.session_state.pop(f"{prefix}_{field}_{suffix}", None)


def _finish(message: str) -> None:
    st.session_state[_LAST_RESULT_KEY] = message
    st.rerun()


# ---- 参画 ----


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
            "参画を更新" if existing else "参画を登録", key=f"work_assignment_submit_{suffix}"
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
        RateOutsidePeriodError,
        EmployeeNotFoundError,
        AssignmentNotFoundError,
    ) as error:
        show_errors([str(error)])
        return
    except Exception:
        show_unexpected_error()
        return
    if existing is None:
        _clear_inputs("work_assignment", _ASSIGNMENT_FIELDS, suffix)
    _finish("参画を更新しました" if existing else "参画を登録しました")


# ---- 単価 ----


def _render_rate_form(db_path: Path, assignment: Assignment, existing: RateHistory | None) -> None:
    suffix = f"{assignment.id}_{existing.id if existing else 'new'}"
    with st.form(f"work_rate_form_{suffix}"):
        effective_from = st.date_input(
            "適用開始日",
            value=existing.effective_from if existing else None,
            min_value=_MIN_DATE,
            key=f"work_rate_effective_from_{suffix}",
        )
        unit_price_text = st.text_input(
            "単価（円）",
            value=str(existing.unit_price) if existing else "",
            key=f"work_rate_unit_price_{suffix}",
        )
        revision_basis = st.text_input(
            "改定の根拠",
            value=(existing.revision_basis or "") if existing else "",
            key=f"work_rate_basis_{suffix}",
        )
        note = st.text_input(
            "備考", value=(existing.note or "") if existing else "", key=f"work_rate_note_{suffix}"
        )
        submitted = st.form_submit_button(
            "単価を更新" if existing else "単価を登録", key=f"work_rate_submit_{suffix}"
        )

    if existing is not None and st.button("この単価を削除", key=f"work_rate_delete_{suffix}"):
        st.session_state[_PENDING_RATE_DELETE_KEY] = _PendingRateDelete(
            rate_history_id=existing.id,
            description=(
                f"{assignment.label} の、適用開始日 {existing.effective_from.isoformat()}・"
                f"単価 {_yen(existing.unit_price)}"
            ),
        )
        st.rerun()

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
        effective_from=effective_from,
        unit_price=unit_price,
        revision_basis=revision_basis or None,
        note=note or None,
    )
    try:
        with open_connection(db_path) as connection:
            if existing is None:
                register_rate_history(connection, assignment.id, data)
            else:
                update_rate_history(connection, existing.id, data)
    except (
        InvalidUnitPriceError,
        OutsideAssignmentPeriodError,
        DuplicateRateHistoryError,
        RateAssignmentNotFoundError,
        RateHistoryNotFoundError,
    ) as error:
        show_errors([str(error)])
        return
    except Exception:
        show_unexpected_error()
        return
    if existing is None:
        _clear_inputs("work_rate", _RATE_FIELDS, suffix)
    _finish("単価を更新しました" if existing else "単価を登録しました")


def _render_rate_section(db_path: Path, assignment: Assignment) -> None:
    st.subheader("この参画の単価")
    with open_connection(db_path) as connection:
        history = list_rate_history(connection, assignment.id)

    if history:
        st.dataframe(
            [
                {
                    "適用開始日": entry.effective_from.isoformat(),
                    "単価": _yen(entry.unit_price),
                    "改定の根拠": entry.revision_basis or "",
                    "備考": entry.note or "",
                }
                for entry in history
            ],
            hide_index=True,
        )
    else:
        show_status("この参画の単価は、まだありません。")

    by_id = {entry.id: entry for entry in history}
    selected_id = st.selectbox(
        "編集する単価",
        [None, *by_id],
        format_func=lambda rate_id: (
            _NEW_RATE
            if rate_id is None
            else f"{by_id[rate_id].effective_from.isoformat()}：{_yen(by_id[rate_id].unit_price)}"
        ),
        key=f"work_rate_selected_{assignment.id}",
    )
    _render_rate_form(db_path, assignment, by_id[selected_id] if selected_id is not None else None)


def _render_pending_rate_delete(db_path: Path) -> None:
    pending: _PendingRateDelete | None = st.session_state.get(_PENDING_RATE_DELETE_KEY)
    if pending is None:
        return

    def _delete() -> None:
        st.session_state.pop(_PENDING_RATE_DELETE_KEY, None)
        try:
            with open_connection(db_path) as connection:
                delete_rate_history(connection, pending["rate_history_id"])
        except RateHistoryNotFoundError as error:
            st.session_state[_LAST_ERROR_KEY] = str(error)
        except Exception:
            st.session_state[_LAST_ERROR_KEY] = UNEXPECTED_ERROR_MESSAGE
        else:
            st.session_state[_LAST_RESULT_KEY] = "単価を削除しました"

    def _cancel() -> None:
        st.session_state.pop(_PENDING_RATE_DELETE_KEY, None)

    confirm_dialog(
        "単価の削除",
        f"{pending['description']} を削除します。削除すると元に戻せません。"
        "操作ログに記録されます。よろしいですか？",
        key="work_rate_delete_confirm",
        on_confirm=_delete,
        on_cancel=_cancel,
    )


def _render_assignment_tab(db_path: Path) -> None:
    employee_id = _select_employee(db_path)
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
        "参画を選ぶ",
        [None, *by_id],
        format_func=lambda assignment_id: (
            _NEW_ASSIGNMENT
            if assignment_id is None
            else f"{assignment_id}：{by_id[assignment_id].label}"
        ),
        key=f"work_assignment_selected_{employee_id}",
    )
    selected = by_id[selected_id] if selected_id is not None else None
    _render_assignment_form(db_path, employee_id, selected)
    if selected is not None:
        _render_rate_section(db_path, selected)


def render() -> None:
    st.title("稼働")

    last_error = st.session_state.pop(_LAST_ERROR_KEY, None)
    if last_error is not None:
        show_errors([last_error])
    last_result = st.session_state.pop(_LAST_RESULT_KEY, None)
    if last_result is not None:
        show_success(last_result)

    db_path = get_app_paths().db_path
    _render_pending_rate_delete(db_path)

    period_tab, assignment_tab = st.tabs(["稼働期間", "参画"])
    with period_tab:
        show_status("稼働期間の入力は、次の段階で追加します。")
    with assignment_tab:
        _render_assignment_tab(db_path)


render()

"""社員画面（`SCN-020` 社員一覧、`SCN-021` 社員詳細、`SCN-022` 社員の登録・編集）。

`SCN-021`・`SCN-022` は、`SCN-020` から開く画面で、メニューには出さない
（画面設計書 2.2）。このファイル1つで、一覧・詳細・登録編集の表示モードを
`st.session_state` で切り替える。確認ダイアログは設けない。
操作ログへの記録は、アプリケーション層で行う（`SCD-020`）。
"""

from datetime import date
from pathlib import Path

import streamlit as st

from crm_g.application.employee import (
    DEFAULT_ENROLLMENT_STATUSES,
    EMPLOYMENT_TYPES,
    ENROLLMENT_STATUSES,
    GENDERS,
    DuplicateEmailError,
    DuplicateEmployeeNoError,
    Employee,
    EmployeeInput,
    EmployeeNotFoundError,
    InvalidBirthDateError,
    InvalidEmailFormatError,
    InvalidEmploymentTypeError,
    InvalidEnrollmentStatusError,
    InvalidGenderError,
    InvalidNameError,
    InvalidRetireDateError,
    create_employee,
    get_employee,
    list_bonuses,
    list_employees,
    list_rate_history,
    list_salary_history,
    list_salary_history_items,
    list_work_periods,
    update_employee,
)
from crm_g.infrastructure.db import open_connection
from crm_g.infrastructure.paths import get_app_paths
from crm_g.ui.components.messages import show_errors, show_success, show_unexpected_error
from crm_g.ui.components.recalculation_notice import (
    remember_recalculation,
    show_recalculation_notice,
)
from crm_g.ui.components.view_log import record_view_if_newly_displayed

_MODE_KEY = "employee_mode"
_SELECTED_ID_KEY = "employee_selected_id"
_SCREEN_NAME = "SCN-021"
_VIEW_LOG_TABLES = ("salary_history", "salary_history_item", "bonus")
_UNSELECTED = "未選択"
_EMPLOYMENT_TYPE_OPTIONS = (_UNSELECTED, *EMPLOYMENT_TYPES)
_GENDER_OPTIONS = (_UNSELECTED, *GENDERS)


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


def _go_to_new_employee_form() -> None:
    st.session_state[_MODE_KEY] = "edit"
    st.session_state.pop(_SELECTED_ID_KEY, None)
    st.rerun()


def _go_to_edit_employee_form(employee_id: int) -> None:
    st.session_state[_SELECTED_ID_KEY] = employee_id
    st.session_state[_MODE_KEY] = "edit"
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

    if st.button("社員を登録", key="employee_open_register"):
        _go_to_new_employee_form()

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
    back_column, edit_column = st.columns(2)
    if back_column.button("一覧へ戻る", key="employee_back_to_list"):
        _go_to_list()
        return
    if edit_column.button("編集", key="employee_open_edit"):
        _go_to_edit_employee_form(employee_id)
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


def _option_index(options: tuple[str, ...], value: str | None) -> int:
    if value is None or value not in options:
        return 0
    return options.index(value)


def _render_form_fields(existing: Employee | None, key_suffix: str) -> tuple[str, bool]:
    """フォームの各項目を描画し、メールアドレスの入力値と送信の有無を返す。

    メールアドレス以外の項目は、送信後に `st.session_state` 経由（同じkeyの
    ウィジェット）で読み直すため、ここでは返さない。
    """
    with st.form(f"employee_form_{key_suffix}"):
        if existing is None:
            email_input = st.text_input("メールアドレス", key=f"employee_email_{key_suffix}")
        else:
            st.text_input(
                "メールアドレス",
                value=existing.email,
                disabled=True,
                key=f"employee_email_{key_suffix}",
            )
            email_input = existing.email

        st.text_input(
            "氏名", value=existing.name if existing else "", key=f"employee_name_{key_suffix}"
        )
        st.text_input(
            "氏名（カナ）",
            value=existing.name_kana if existing else "",
            key=f"employee_name_kana_{key_suffix}",
        )
        st.text_input(
            "社員番号",
            value=existing.employee_no if existing else "",
            key=f"employee_no_{key_suffix}",
        )
        st.selectbox(
            "雇用形態",
            _EMPLOYMENT_TYPE_OPTIONS,
            index=_option_index(
                _EMPLOYMENT_TYPE_OPTIONS, existing.employment_type if existing else None
            ),
            key=f"employee_employment_type_{key_suffix}",
        )
        st.date_input(
            "生年月日",
            value=existing.birth_date if existing else None,
            key=f"employee_birth_date_{key_suffix}",
        )
        st.selectbox(
            "性別",
            _GENDER_OPTIONS,
            index=_option_index(_GENDER_OPTIONS, existing.gender if existing else None),
            key=f"employee_gender_{key_suffix}",
        )
        st.date_input(
            "入社日",
            value=existing.join_date if existing else None,
            key=f"employee_join_date_{key_suffix}",
        )
        st.text_input(
            "最寄駅",
            value=existing.nearest_station if existing else "",
            key=f"employee_station_{key_suffix}",
        )
        st.text_input(
            "居住エリア",
            value=existing.residence_area if existing else "",
            key=f"employee_residence_{key_suffix}",
        )
        st.text_input(
            "職種（例：PG、SE、PMO）",
            value=existing.job_type if existing else "",
            key=f"employee_job_type_{key_suffix}",
        )
        st.selectbox(
            "在籍ステータス",
            ENROLLMENT_STATUSES,
            index=_option_index(
                ENROLLMENT_STATUSES, existing.enrollment_status if existing else None
            ),
            key=f"employee_enrollment_status_{key_suffix}",
        )
        st.date_input(
            "退職日",
            value=existing.retire_date if existing else None,
            key=f"employee_retire_date_{key_suffix}",
        )
        submitted = st.form_submit_button("保存", key=f"employee_submit_{key_suffix}")

    return email_input, submitted


def _build_employee_input(
    email_input: str, key_suffix: str, *, is_new: bool
) -> EmployeeInput | None:
    """送信済みのウィジェットの値を、`st.session_state` から読み直して組み立てる。

    必須項目が空のときは、エラーを表示して `None` を返す。
    """
    name = st.session_state[f"employee_name_{key_suffix}"]
    join_date = st.session_state[f"employee_join_date_{key_suffix}"]

    if is_new and not email_input:
        show_errors(["メールアドレスを入力してください"])
        return None
    if not name:
        show_errors(["氏名を入力してください"])
        return None
    if not isinstance(join_date, date):
        show_errors(["入社日を入力してください"])
        return None

    employment_type = st.session_state[f"employee_employment_type_{key_suffix}"]
    gender = st.session_state[f"employee_gender_{key_suffix}"]
    birth_date = st.session_state[f"employee_birth_date_{key_suffix}"]
    retire_date = st.session_state[f"employee_retire_date_{key_suffix}"]

    return EmployeeInput(
        email=email_input,
        name=name,
        name_kana=st.session_state[f"employee_name_kana_{key_suffix}"] or None,
        employee_no=st.session_state[f"employee_no_{key_suffix}"] or None,
        employment_type=employment_type if employment_type != _UNSELECTED else None,
        birth_date=birth_date if isinstance(birth_date, date) else None,
        gender=gender if gender != _UNSELECTED else None,
        join_date=join_date,
        nearest_station=st.session_state[f"employee_station_{key_suffix}"] or None,
        residence_area=st.session_state[f"employee_residence_{key_suffix}"] or None,
        job_type=st.session_state[f"employee_job_type_{key_suffix}"] or None,
        enrollment_status=st.session_state[f"employee_enrollment_status_{key_suffix}"],
        retire_date=retire_date if isinstance(retire_date, date) else None,
    )


def _save_employee(db_path: Path, employee_id: int | None, data: EmployeeInput) -> int | None:
    """社員を登録・変更する。失敗したときは、エラーを表示して`None`を返す。"""
    with open_connection(db_path) as connection:
        try:
            if employee_id is None:
                return create_employee(connection, data, on_recalculated=remember_recalculation)
            update_employee(connection, employee_id, data, on_recalculated=remember_recalculation)
        except (
            InvalidEmailFormatError,
            DuplicateEmailError,
            InvalidNameError,
            DuplicateEmployeeNoError,
            InvalidEmploymentTypeError,
            InvalidGenderError,
            InvalidEnrollmentStatusError,
            InvalidBirthDateError,
            InvalidRetireDateError,
            EmployeeNotFoundError,
        ) as error:
            show_errors([str(error)])
            return None
        except Exception:
            show_unexpected_error()
            return None
    return employee_id


def _render_employee_form(db_path: Path, employee_id: int | None) -> None:
    is_new = employee_id is None
    existing: Employee | None = None
    if employee_id is not None:
        with open_connection(db_path) as connection:
            existing = get_employee(connection, employee_id)
        if existing is None:
            show_errors(["社員が見つかりません。"])
            _go_to_list()
            return

    # ウィジェットのkeyに対象（新規または社員ID）を含める。固定keyのままだと、
    # 対象を切り替えてもフォームの内容が前回のまま残る（PH-06-3で見つかった
    # 不具合と同じ原因）。
    key_suffix = "new" if is_new else str(employee_id)

    st.title("社員の登録" if is_new else "社員の編集")

    email_input, submitted = _render_form_fields(existing, key_suffix)

    if st.button("キャンセル", key=f"employee_cancel_{key_suffix}"):
        if is_new:
            _go_to_list()
        else:
            _go_to_detail(employee_id)  # type: ignore[arg-type]
        return

    if not submitted:
        return

    data = _build_employee_input(email_input, key_suffix, is_new=is_new)
    if data is None:
        return

    saved_id = _save_employee(db_path, employee_id, data)
    if saved_id is None:
        return

    show_success("登録しました" if is_new else "変更しました")
    _go_to_detail(saved_id)


def render() -> None:
    show_recalculation_notice()
    db_path = get_app_paths().db_path
    mode = st.session_state.get(_MODE_KEY, "list")
    selected_id = st.session_state.get(_SELECTED_ID_KEY)

    if mode == "edit":
        _render_employee_form(db_path, selected_id)
    elif mode == "detail" and selected_id is not None:
        _render_detail(db_path, selected_id)
    else:
        _render_list(db_path)


render()

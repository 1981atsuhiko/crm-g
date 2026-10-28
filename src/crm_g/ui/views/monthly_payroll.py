"""月次給与計算画面（`SCN-032`）。

対象月の計算結果を見て、再計算・月次締め・締めの解除をする。要確認と計算エラーを
確かめ、行ごとに人事連携の対象／対象外と備考を設定する。締め・解除は、`UID-053` の
確認ダイアログの後に行う。計算エラーがある月は、確認のチェックを付けないと締められない
（`UID-076`）。締め済みの月は、再計算・人事連携の変更の部品を出さない（`UID-054`）。
"""

from pathlib import Path
from typing import TypedDict

import streamlit as st

from crm_g.application.monthly_close import (
    AlreadyClosedError,
    NoResultError,
    NotClosedError,
    UnacknowledgedErrorsError,
    close_month,
    get_month_status,
    reopen_month,
)
from crm_g.application.monthly_payroll import (
    HR_TARGETS,
    ClosedMonthError,
    InvalidHrTargetError,
    MonthlyResultRow,
    ResultNotFoundError,
    list_monthly_results,
    list_payroll_items,
    update_hr_settings,
)
from crm_g.application.recalculation import (
    CalculationError,
    list_calculation_errors,
    run_manual_recalculation,
)
from crm_g.infrastructure.db import open_connection
from crm_g.infrastructure.paths import get_app_paths
from crm_g.ui.components.confirm_dialog import confirm_dialog
from crm_g.ui.components.formatting import format_datetime
from crm_g.ui.components.messages import (
    UNEXPECTED_ERROR_MESSAGE,
    show_errors,
    show_status,
    show_success,
    show_unexpected_error,
)
from crm_g.ui.components.month_selector import select_year_month
from crm_g.ui.components.view_log import record_view_if_newly_displayed

_SCREEN_NAME = "SCN-032"
_VIEW_LOG_TABLES = ("monthly_payroll", "monthly_payroll_item")
_PENDING_KEY = "monthly_payroll_pending"
_LAST_RESULT_KEY = "monthly_payroll_last_result"
_LAST_ERROR_KEY = "monthly_payroll_last_error"
_DASH = "－"


class _Pending(TypedDict):
    """締め・解除の確認待ち（再実行をまたいで保持するため、組み込み型で持つ）。"""

    action: str
    year_month: str
    message: str


def _yen(amount: int | None) -> str:
    return _DASH if amount is None else f"{amount:,}"


def _finish(message: str) -> None:
    st.session_state[_LAST_RESULT_KEY] = message
    st.rerun()


def _render_pending(db_path: Path) -> None:
    pending: _Pending | None = st.session_state.get(_PENDING_KEY)
    if pending is None:
        return

    def _run() -> None:
        st.session_state.pop(_PENDING_KEY, None)
        try:
            with open_connection(db_path) as connection:
                if pending["action"] == "close":
                    close_month(connection, pending["year_month"], acknowledge_errors=True)
                else:
                    reopen_month(connection, pending["year_month"])
        except (
            AlreadyClosedError,
            NoResultError,
            NotClosedError,
            UnacknowledgedErrorsError,
        ) as error:
            st.session_state[_LAST_ERROR_KEY] = str(error)
        except Exception:
            st.session_state[_LAST_ERROR_KEY] = UNEXPECTED_ERROR_MESSAGE
        else:
            done = "締めました" if pending["action"] == "close" else "締めを解除しました"
            st.session_state[_LAST_RESULT_KEY] = f"{pending['year_month']} を{done}"

    def _cancel() -> None:
        st.session_state.pop(_PENDING_KEY, None)

    title = "月次締め" if pending["action"] == "close" else "締めの解除"
    confirm_dialog(
        title, pending["message"], key="monthly_payroll_confirm", on_confirm=_run, on_cancel=_cancel
    )


def _render_actions(
    year_month: str,
    *,
    is_closed: bool,
    results: tuple[MonthlyResultRow, ...],
    errors: tuple[CalculationError, ...],
    db_path: Path,
) -> None:
    review_count = sum(1 for row in results if row.check_result == "要確認")
    recalc_column, close_column = st.columns(2)

    if recalc_column.button("再計算", key="monthly_payroll_recalculate", disabled=is_closed):
        try:
            with open_connection(db_path) as connection:
                result = run_manual_recalculation(connection, year_month)
        except Exception:
            show_unexpected_error()
            return
        _finish(
            f"{year_month} を再計算しました"
            f"（{result.saved_count}人。計算エラー {len(result.errors)}件）"
        )

    if is_closed:
        if close_column.button("締めの解除", key="monthly_payroll_reopen"):
            st.session_state[_PENDING_KEY] = _Pending(
                action="reopen",
                year_month=year_month,
                message=(
                    f"{year_month} の締めを解除します。解除すると、再計算の対象に戻ります。"
                    "解除は操作ログに記録されます。よろしいですか？"
                ),
            )
            st.rerun()
        return

    acknowledged = True
    if errors:
        acknowledged = close_column.checkbox(
            "結果のない社員がいることを理解した", key=f"monthly_payroll_ack_{year_month}"
        )
    if close_column.button(
        "月次締め", key="monthly_payroll_close", disabled=not results or not acknowledged
    ):
        st.session_state[_PENDING_KEY] = _Pending(
            action="close",
            year_month=year_month,
            message=(
                f"{year_month} を締めます。対象は {len(results)}人、要確認 {review_count}件、"
                f"計算エラー {len(errors)}件です。締めると、この月の給与・売上・原価が確定し、"
                "再計算されなくなります。よろしいですか？"
            ),
        )
        st.rerun()


def _render_results(results: tuple[MonthlyResultRow, ...]) -> None:
    only_review = st.checkbox("要確認のみ", key="monthly_payroll_only_review")
    shown = [row for row in results if not only_review or row.check_result == "要確認"]
    st.dataframe(
        [
            {
                "氏名": row.employee_name,
                "月給（円）": _yen(row.monthly_salary),
                "基本給（円）": _yen(row.base_salary),
                "手当（円）": _yen(row.allowance),
                "支払額（円）": _yen(row.paid_amount),
                "減額（円）": _yen(row.deduction),
                "単価（円）": _yen(row.unit_price),
                "売上（円）": _yen(row.sales),
                "原価（円）": _yen(row.cost_total),
                "粗利（円）": _yen(row.gross_profit),
                "稼働状態の要約": row.work_status_summary or "",
                "人事連携": row.hr_target,
                "判定": row.check_result,
            }
            for row in shown
        ],
        hide_index=True,
    )


def _render_selected(
    db_path: Path, year_month: str, results: tuple[MonthlyResultRow, ...], *, is_closed: bool
) -> None:
    by_id = {row.employee_id: row for row in results}
    employee_id = st.selectbox(
        "社員を選ぶ",
        list(by_id),
        format_func=lambda option: f"{option}：{by_id[option].employee_name}",
        key=f"monthly_payroll_employee_{year_month}",
    )
    selected = by_id[employee_id]

    with open_connection(db_path) as connection:
        items = list_payroll_items(connection, employee_id, year_month)
    st.subheader("内訳")
    st.dataframe(
        [
            {
                "給与項目": item.pay_item_name,
                "支給額（円）": _yen(item.paid_amount),
                "減額（円）": _yen(item.deduction_amount),
            }
            for item in items
        ],
        hide_index=True,
    )

    st.subheader("人事連携の設定")
    suffix = f"{year_month}_{employee_id}"
    with st.form(f"monthly_payroll_hr_form_{suffix}"):
        hr_target = st.selectbox(
            "人事連携",
            HR_TARGETS,
            index=HR_TARGETS.index(selected.hr_target),
            disabled=is_closed,
            key=f"monthly_payroll_hr_target_{suffix}",
        )
        hr_note = st.text_input(
            "備考",
            value=selected.hr_note or "",
            disabled=is_closed,
            key=f"monthly_payroll_hr_note_{suffix}",
        )
        submitted = st.form_submit_button(
            "人事連携を保存", disabled=is_closed, key=f"monthly_payroll_hr_submit_{suffix}"
        )
    if is_closed:
        show_status("締め済みの月は変更できません。締めを解除してから変更してください。")
    if not submitted:
        return
    try:
        with open_connection(db_path) as connection:
            update_hr_settings(connection, employee_id, year_month, hr_target, hr_note or None)
    except (ResultNotFoundError, ClosedMonthError, InvalidHrTargetError) as error:
        show_errors([str(error)])
        return
    except Exception:
        show_unexpected_error()
        return
    # 欄の入力（前後の空白を含む）を捨て、保存済みの値から作り直させる。
    for field in ("hr_target", "hr_note"):
        st.session_state.pop(f"monthly_payroll_{field}_{suffix}", None)
    _finish("人事連携を保存しました")


def _render_errors(errors: tuple[CalculationError, ...]) -> None:
    st.subheader("計算エラー一覧")
    if not errors:
        show_status("計算エラーはありません。")
        return
    st.dataframe(
        [{"氏名": error.employee_name, "原因": error.reason.value} for error in errors],
        hide_index=True,
    )


def render() -> None:
    st.title("月次給与計算")

    last_error = st.session_state.pop(_LAST_ERROR_KEY, None)
    if last_error is not None:
        show_errors([last_error])
    last_result = st.session_state.pop(_LAST_RESULT_KEY, None)
    if last_result is not None:
        show_success(last_result)

    db_path = get_app_paths().db_path
    year_month = select_year_month(key="monthly_payroll_month")
    _render_pending(db_path)

    with open_connection(db_path) as connection:
        try:
            for table in _VIEW_LOG_TABLES:
                record_view_if_newly_displayed(connection, _SCREEN_NAME, table, year_month)
        except Exception:
            show_unexpected_error()
            return
        status = get_month_status(connection, year_month)
        results = list_monthly_results(connection, year_month)
        # 締め済みの月は結果が確定しているため、計算エラーを判定し直さない。
        errors = () if status.is_closed else list_calculation_errors(connection, year_month)

    if status.is_closed:
        closed_at = (
            f"（{format_datetime(status.closed_at)}）" if status.closed_at is not None else ""
        )
        st.write(f"状態：締め済み{closed_at}")
    else:
        st.write("状態：締め前")

    count_column, review_column, error_column = st.columns(3)
    count_column.metric("計算結果の人数", len(results))
    review_column.metric("要確認", sum(1 for row in results if row.check_result == "要確認"))
    error_column.metric("計算エラー", len(errors))

    _render_actions(
        year_month, is_closed=status.is_closed, results=results, errors=errors, db_path=db_path
    )

    if results:
        _render_results(results)
        _render_selected(db_path, year_month, results, is_closed=status.is_closed)
    else:
        show_status("この月の計算結果はありません。「再計算」で計算してください。")
    _render_errors(errors)


render()

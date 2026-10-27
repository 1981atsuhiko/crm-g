"""稼働状態画面（`SCN-061`）。

稼働状態の給与支給率を変更する。稼働状態の追加・名称変更・売上計上や原価計上の
変更は、この画面では行わない（要件定義書5.1）。操作ログへの記録は、アプリケーション層で行う（`SCD-020`）。
保存後に、再計算の通知と、締め済みの月への警告を出す（`UID-055`、`UID-054`）。
"""

from datetime import date
from pathlib import Path
from typing import TypedDict

import streamlit as st

from crm_g.application.work_status_rate import (
    InvalidRateError,
    InvalidValidFromError,
    PeriodOverlapError,
    WorkStatusSummary,
    change_rate,
    list_work_statuses,
    rate_history,
)
from crm_g.infrastructure.db import open_connection
from crm_g.infrastructure.paths import get_app_paths
from crm_g.ui.components.confirm_dialog import confirm_dialog
from crm_g.ui.components.messages import UNEXPECTED_ERROR_MESSAGE, show_errors, show_success
from crm_g.ui.components.recalculation_notice import (
    remember_recalculation,
    show_recalculation_notice,
)

_PENDING_RATE_KEY = "work_status_pending_rate"
_LAST_ERROR_KEY = "work_status_last_error"


class _PendingRate(TypedDict):
    """確認待ちの「支給率の変更」入力（複数回の再実行をまたいで保持する）。"""

    code: str
    rate_percent: str
    valid_from: str


def _format_rate(rate_bp: int | None) -> str:
    if rate_bp is None:
        return "-"
    return f"{rate_bp / 100:.2f}%"


def _render_list(statuses: tuple[WorkStatusSummary, ...]) -> str:
    st.dataframe(
        [
            {
                "コード": status.code,
                "名称": status.name,
                "売上計上": "○" if status.is_billable else "×",
                "原価計上": "○" if status.is_cost_target else "×",
                "現在の給与支給率": _format_rate(status.current_rate_bp),
                "旧名称": status.legacy_name or "",
            }
            for status in statuses
        ],
        hide_index=True,
    )

    codes = [status.code for status in statuses]
    return str(st.selectbox("稼働状態を選ぶ", codes, key="work_status_selected_code"))


def _render_history(db_path: Path, code: str) -> None:
    with open_connection(db_path) as connection:
        history = rate_history(connection, code)

    st.subheader("支給率の履歴")
    st.dataframe(
        [
            {
                "適用開始日": period.valid_from.isoformat(),
                "適用終了日": period.valid_to.isoformat() if period.valid_to else "",
                "給与支給率": _format_rate(period.payroll_rate_bp),
            }
            for period in history
        ],
        hide_index=True,
    )


def _confirm_pending_rate(db_path: Path, pending: _PendingRate) -> None:
    # `confirm_dialog` は on_confirm の直後に rerun するため、ここで st.error を呼んでも
    # 消えてしまう（`st.toast` と違い、再実行をまたいで残らない）。失敗したときは、
    # メッセージを session_state に残し、ダイアログを閉じた後の描画で表示する。
    def _save() -> None:
        with open_connection(db_path) as connection:
            try:
                change_rate(
                    connection,
                    pending["code"],
                    pending["rate_percent"],
                    date.fromisoformat(pending["valid_from"]),
                    on_recalculated=remember_recalculation,
                )
            except (InvalidRateError, InvalidValidFromError, PeriodOverlapError) as error:
                st.session_state[_LAST_ERROR_KEY] = str(error)
            except Exception:
                st.session_state[_LAST_ERROR_KEY] = UNEXPECTED_ERROR_MESSAGE
            else:
                show_success("登録しました")
        st.session_state.pop(_PENDING_RATE_KEY, None)

    def _cancel() -> None:
        st.session_state.pop(_PENDING_RATE_KEY, None)

    confirm_dialog(
        "確認",
        f"{pending['code']}の給与支給率を、{pending['valid_from']}から"
        f"{pending['rate_percent']}%に変更します。よろしいですか？",
        key="work_status_rate_confirm",
        on_confirm=_save,
        on_cancel=_cancel,
    )


def _render_rate_form(db_path: Path, code: str) -> None:
    st.subheader("支給率の変更")
    st.write(f"稼働状態：{code}")

    with st.form("work_status_rate_form"):
        rate_percent = st.text_input("給与支給率（%）", key="work_status_rate_percent")
        valid_from = st.date_input("適用開始日", key="work_status_rate_valid_from")
        submitted = st.form_submit_button("変更", key="work_status_rate_submit")

    if submitted:
        if not rate_percent:
            show_errors(["給与支給率を入力してください"])
        elif not isinstance(valid_from, date):
            show_errors(["適用開始日を入力してください"])
        else:
            st.session_state[_PENDING_RATE_KEY] = _PendingRate(
                code=code, rate_percent=rate_percent, valid_from=valid_from.isoformat()
            )

    pending = st.session_state.get(_PENDING_RATE_KEY)
    if pending is not None and pending["code"] == code:
        _confirm_pending_rate(db_path, pending)


def render() -> None:
    st.title("稼働状態")
    show_recalculation_notice()

    last_error = st.session_state.pop(_LAST_ERROR_KEY, None)
    if last_error is not None:
        show_errors([last_error])

    db_path = get_app_paths().db_path
    with open_connection(db_path) as connection:
        statuses = list_work_statuses(connection)

    code = _render_list(statuses)
    _render_history(db_path, code)
    _render_rate_form(db_path, code)


render()

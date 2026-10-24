"""計算設定画面（`SCN-063`）。

原価係数を変更する。端数処理は表示のみ（変更できない。`CLC-001`）。給与・賞与の
テーブルを変更しないため、操作ログの対象外（`SCD-020`）。再計算の接続はPH-10で
行う。
"""

from datetime import date
from pathlib import Path
from typing import TypedDict

import streamlit as st

from crm_g.application.system_setting import (
    InvalidCoefficientError,
    InvalidValidFromError,
    PeriodOverlapError,
    change_coefficient,
    coefficient_history,
    current_rounding,
)
from crm_g.infrastructure.db import open_connection
from crm_g.infrastructure.paths import get_app_paths
from crm_g.ui.components.confirm_dialog import confirm_dialog
from crm_g.ui.components.messages import UNEXPECTED_ERROR_MESSAGE, show_errors, show_success

_PENDING_KEY = "calculation_setting_pending"
_LAST_ERROR_KEY = "calculation_setting_last_error"


class _PendingCoefficient(TypedDict):
    """確認待ちの「原価係数の変更」入力（複数回の再実行をまたいで保持する）。"""

    coefficient: str
    valid_from: str


def _format_coefficient(coefficient_bp: int) -> str:
    return f"{coefficient_bp / 10000:.4f}"


def _render_history(db_path: Path) -> None:
    with open_connection(db_path) as connection:
        history = coefficient_history(connection)

    st.subheader("原価係数の履歴")
    st.dataframe(
        [
            {
                "適用開始日": period.valid_from.isoformat(),
                "適用終了日": period.valid_to.isoformat() if period.valid_to else "",
                "原価係数（倍）": _format_coefficient(period.coefficient_bp),
            }
            for period in history
        ],
        hide_index=True,
    )


def _confirm_pending(db_path: Path, pending: _PendingCoefficient) -> None:
    def _save() -> None:
        with open_connection(db_path) as connection:
            try:
                change_coefficient(
                    connection, pending["coefficient"], date.fromisoformat(pending["valid_from"])
                )
            except (InvalidCoefficientError, InvalidValidFromError, PeriodOverlapError) as error:
                st.session_state[_LAST_ERROR_KEY] = str(error)
            except Exception:
                st.session_state[_LAST_ERROR_KEY] = UNEXPECTED_ERROR_MESSAGE
            else:
                show_success("登録しました")
        st.session_state.pop(_PENDING_KEY, None)

    def _cancel() -> None:
        st.session_state.pop(_PENDING_KEY, None)

    confirm_dialog(
        "確認",
        f"原価係数を、{pending['valid_from']}から{pending['coefficient']}倍に"
        "変更します。月の途中からの変更でも、その月全体に、月初日の値を使います。"
        "よろしいですか？",
        key="calculation_setting_confirm",
        on_confirm=_save,
        on_cancel=_cancel,
    )


def _render_form(db_path: Path) -> None:
    st.subheader("原価係数の変更")
    st.caption("月の途中で切り替わる場合は、月の初日の値を、その月全体に使います。")

    with st.form("calculation_setting_form"):
        coefficient = st.text_input("原価係数（倍）", key="calculation_setting_coefficient")
        valid_from = st.date_input("適用開始日", key="calculation_setting_valid_from")
        submitted = st.form_submit_button("変更", key="calculation_setting_submit")

    if not submitted:
        return

    if not coefficient:
        show_errors(["原価係数を入力してください"])
        return
    if not isinstance(valid_from, date):
        show_errors(["適用開始日を入力してください"])
        return

    st.session_state[_PENDING_KEY] = _PendingCoefficient(
        coefficient=coefficient, valid_from=valid_from.isoformat()
    )


def render() -> None:
    st.title("計算設定")

    last_error = st.session_state.pop(_LAST_ERROR_KEY, None)
    if last_error is not None:
        show_errors([last_error])

    db_path = get_app_paths().db_path

    _render_history(db_path)
    _render_form(db_path)

    with open_connection(db_path) as connection:
        rounding = current_rounding(connection)
    st.subheader("端数処理")
    st.write(f"現在の値：{rounding}（変更できません）")

    pending = st.session_state.get(_PENDING_KEY)
    if pending is not None:
        _confirm_pending(db_path, pending)


render()

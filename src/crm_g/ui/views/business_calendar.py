"""営業日カレンダー画面（`SCN-060`）。

営業日・祝日・会社休日を見て、会社休日などを登録する。操作ログへの記録は、アプリケーション層で行う（`SCD-020`）。
再計算の接続はPH-10で行う。
"""

from datetime import date
from pathlib import Path
from typing import TypedDict

import streamlit as st

from crm_g.application.business_calendar import (
    CalendarAlreadyExistsError,
    create_year_calendar,
    list_month,
    month_business_day_count,
    refetch_holidays,
    register_manual_day,
    year_exists,
)
from crm_g.infrastructure.db import open_connection
from crm_g.infrastructure.paths import get_app_paths
from crm_g.ui.components.confirm_dialog import confirm_dialog
from crm_g.ui.components.messages import (
    UNEXPECTED_ERROR_MESSAGE,
    show_errors,
    show_success,
    show_unexpected_error,
)

_MIN_YEAR = 2025
_WEEKDAY_NAMES = ("月", "火", "水", "木", "金", "土", "日")
_PENDING_DAY_KEY = "business_calendar_pending_day"
_LAST_ERROR_KEY = "business_calendar_last_error"


class _PendingDay(TypedDict):
    """確認待ちの「日の登録」入力（複数回の再実行をまたいで保持する）。

    `st.session_state` に保存するため、組み込み型（`dict`）にする（`confirm_dialog`
    の注意書きのとおり。`dataclass` にすると、再実行のたびに別のクラスとして
    作り直され、前回保存したインスタンスと一致しなくなる）。
    """

    target_date: str
    is_business: bool
    note: str


def _max_year() -> int:
    return date.today().year + 1


def _render_create_calendar(db_path: Path, year: int) -> None:
    if st.button("カレンダーの作成", key="create_calendar"):
        with open_connection(db_path) as connection:
            try:
                create_year_calendar(connection, year)
            except CalendarAlreadyExistsError:
                show_errors([f"{year}年の営業日カレンダーは、既に作成されています"])
            except Exception:
                show_unexpected_error()
            else:
                show_success(f"{year}年の営業日カレンダーを作成しました")
                st.rerun()


def _render_refetch(db_path: Path, year: int) -> None:
    if st.button("祝日の再取込", key="refetch_holidays"):
        with open_connection(db_path) as connection:
            try:
                refetch_holidays(connection, year)
            except Exception:
                show_unexpected_error()
            else:
                show_success("祝日を再取込しました")
                st.rerun()


def _render_month_table(db_path: Path, year: int, month: int) -> None:
    with open_connection(db_path) as connection:
        days = list_month(connection, year, month)
        count = month_business_day_count(connection, year, month)

    st.write(f"営業日数：{count}日")
    st.dataframe(
        [
            {
                "日付": day.calendar_date.isoformat(),
                "曜日": _WEEKDAY_NAMES[day.calendar_date.weekday()],
                "営業日か": "○" if day.is_business_day else "×",
                "区分": day.source,
                "メモ": day.note or "",
            }
            for day in days
        ],
        hide_index=True,
    )


def _confirm_pending_day(db_path: Path, pending: _PendingDay) -> None:
    target_date = date.fromisoformat(pending["target_date"])
    is_business = pending["is_business"]
    note = pending["note"]

    # `confirm_dialog` は on_confirm の直後に rerun するため、ここで st.error 系を
    # 呼んでも消えてしまう。失敗したときは、メッセージを session_state に残し、
    # ダイアログを閉じた後の描画で表示する（`confirm_dialog` のdocstring参照）。
    def _save() -> None:
        with open_connection(db_path) as connection:
            try:
                register_manual_day(connection, target_date, is_business, note or None)
            except Exception:
                st.session_state[_LAST_ERROR_KEY] = UNEXPECTED_ERROR_MESSAGE
            else:
                show_success("登録しました")
        st.session_state.pop(_PENDING_DAY_KEY, None)

    def _cancel() -> None:
        st.session_state.pop(_PENDING_DAY_KEY, None)

    business_label = "営業日" if is_business else "非営業日"
    confirm_dialog(
        "確認",
        f"{target_date.isoformat()}を{business_label}として登録します。よろしいですか？",
        key="register_day_confirm",
        on_confirm=_save,
        on_cancel=_cancel,
    )


def _render_register_form(db_path: Path) -> None:
    st.subheader("日の登録")
    with st.form("register_day_form"):
        target_date = st.date_input("日付", key="register_day_date")
        choice = st.radio("営業日か", ["営業日", "非営業日"], key="register_day_choice")
        note = st.text_input("メモ", key="register_day_note")
        submitted = st.form_submit_button("登録", key="register_day_submit")

    if submitted:
        if not isinstance(target_date, date):
            show_errors(["日付を入力してください"])
        else:
            st.session_state[_PENDING_DAY_KEY] = _PendingDay(
                target_date=target_date.isoformat(), is_business=choice == "営業日", note=note
            )

    pending = st.session_state.get(_PENDING_DAY_KEY)
    if pending is not None:
        _confirm_pending_day(db_path, pending)


def render() -> None:
    st.title("営業日カレンダー")

    last_error = st.session_state.pop(_LAST_ERROR_KEY, None)
    if last_error is not None:
        show_errors([last_error])

    year = st.selectbox("年", list(range(_MIN_YEAR, _max_year() + 1)), key="calendar_year")
    db_path = get_app_paths().db_path

    with open_connection(db_path) as connection:
        exists = year_exists(connection, year)

    if not exists:
        _render_create_calendar(db_path, year)
        return

    _render_refetch(db_path, year)

    month = st.selectbox("月", list(range(1, 13)), key="calendar_month")
    _render_month_table(db_path, year, month)
    _render_register_form(db_path)


render()

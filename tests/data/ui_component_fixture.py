"""UI共通部品（messages・two_step_form・confirm_dialog）の試験用ページ。"""

import streamlit as st

from crm_g.ui.components.confirm_dialog import confirm_dialog
from crm_g.ui.components.messages import show_errors, show_status, show_warning
from crm_g.ui.components.two_step_form import (
    discard,
    is_awaiting_confirmation,
    start_confirmation,
)

_FORM_KEY = "fixture_form"

if st.button("show_errors", key="show_errors_button"):
    show_errors(["エラー1", "エラー2"])

if st.button("show_warning", key="show_warning_button"):
    show_warning("警告メッセージ")

if st.button("show_status", key="show_status_button"):
    show_status("状態メッセージ")

if st.button("start_confirmation", key="start_confirmation_button"):
    start_confirmation(_FORM_KEY)

if st.button("discard", key="discard_button"):
    discard(_FORM_KEY)

st.write(f"awaiting={is_awaiting_confirmation(_FORM_KEY)}")

if st.button("open_dialog", key="open_dialog_button"):
    confirm_dialog("確認", "本当に実行しますか", key="fixture_dialog", on_confirm=lambda: None)

# 確認待ちの状態を持ち、再実行をまたいでダイアログを開いたままにする使い方（画面と同じ）。
_PENDING_KEY = "fixture_pending"
_CANCEL_COUNT_KEY = "fixture_cancel_count"

if st.button("open_pending_dialog", key="open_pending_dialog_button"):
    st.session_state[_PENDING_KEY] = True


def _cancel_pending() -> None:
    """取り消し。確認待ちの状態を破棄し、呼ばれた回数を数える。"""
    st.session_state.pop(_PENDING_KEY, None)
    st.session_state[_CANCEL_COUNT_KEY] = st.session_state.get(_CANCEL_COUNT_KEY, 0) + 1


if st.session_state.get(_PENDING_KEY):
    confirm_dialog(
        "確認待ち",
        "確認待ちのダイアログです",
        key="fixture_pending_dialog",
        on_confirm=lambda: st.session_state.pop(_PENDING_KEY, None),
        on_cancel=_cancel_pending,
    )

st.write(f"pending={bool(st.session_state.get(_PENDING_KEY))}")
st.write(f"cancel_count={st.session_state.get(_CANCEL_COUNT_KEY, 0)}")

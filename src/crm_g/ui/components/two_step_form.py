"""2段階の登録の共通部品（`UID-052`）。

登録・更新で、続行可能な警告（ハードエラーではない）があるときに使う。
「警告を確認して登録する」ボタンを押すまで保存しない。入力を変えたときは、
途中状態を破棄し、最初からやり直しになる（最初の利用はPH-08以降）。
"""

import streamlit as st


def _state_key(form_key: str) -> str:
    return f"crm_g_two_step_{form_key}"


def discard(form_key: str) -> None:
    """入力を変えたときなど、警告の確認待ちの状態を破棄する。"""
    st.session_state.pop(_state_key(form_key), None)


def is_awaiting_confirmation(form_key: str) -> bool:
    """警告の確認待ちの状態かどうかを返す。"""
    return bool(st.session_state.get(_state_key(form_key), False))


def start_confirmation(form_key: str) -> None:
    """警告を表示したことを記録し、確認待ちの状態にする。"""
    st.session_state[_state_key(form_key)] = True

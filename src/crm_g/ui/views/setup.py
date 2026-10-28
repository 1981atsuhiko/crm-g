"""パスコード設定画面（`SCN-001`、`SCD-009`）。

パスコードが未設定のときだけ表示する（`app.py` の関門）。設定するまで、
他の画面は表示しない。入力値は、送信のたびに消す（`UID-050`、`SCD-011`）。
"""

import streamlit as st

from crm_g.application.auth import (
    InvalidPasscodeFormatError,
    PasscodeMismatchError,
    setup_passcode,
)
from crm_g.infrastructure.db import open_connection
from crm_g.infrastructure.paths import get_app_paths
from crm_g.ui.components.messages import show_unexpected_error
from crm_g.ui.session import AUTHENTICATED_KEY


def render() -> None:
    st.title("パスコード設定")

    with st.form("setup_form", clear_on_submit=True):
        passcode = st.text_input("パスコード", type="password", key="setup_passcode")
        confirm = st.text_input("パスコード（確認）", type="password", key="setup_confirm")
        submitted = st.form_submit_button("設定", key="setup_submit")

    if not submitted:
        return
    # `clear_on_submit` はブラウザ側の消去のため、サーバーの `st.session_state` からも消す。
    for key in ("setup_passcode", "setup_confirm"):
        st.session_state.pop(key, None)

    db_path = get_app_paths().db_path
    try:
        with open_connection(db_path) as connection:
            setup_passcode(connection, passcode, confirm)
    except PasscodeMismatchError:
        st.error("確認用の入力が一致しません")
        return
    except InvalidPasscodeFormatError:
        st.error("パスコードは、メールアドレス形式の文字列にしてください")
        return
    except Exception:
        # 想定外の例外から、原因を画面に出さないための最終防波堤（ARC-018）。
        show_unexpected_error()
        return

    st.session_state[AUTHENTICATED_KEY] = True
    st.rerun()


render()

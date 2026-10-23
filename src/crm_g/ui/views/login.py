"""パスコード入力画面（`SCN-002`、`SCD-010`）。

認証前は、この画面だけを表示する。成功したときだけ、認証の通過状態
（`st.session_state`。タブごと）を立てる。
"""

import streamlit as st

from crm_g.application.auth import (
    AccountLockedError,
    InvalidPasscodeError,
    PasscodeNotSetError,
    verify_passcode,
)
from crm_g.infrastructure.db import open_connection
from crm_g.infrastructure.paths import get_app_paths
from crm_g.ui.components.messages import show_unexpected_error
from crm_g.ui.session import AUTHENTICATED_KEY


def render() -> None:
    st.title("ログイン")

    with st.form("login_form"):
        passcode = st.text_input("パスコード", type="password", key="login_passcode")
        submitted = st.form_submit_button("ログイン", key="login_submit")

    if not submitted:
        return

    db_path = get_app_paths().db_path
    try:
        with open_connection(db_path) as connection:
            verify_passcode(connection, passcode)
    except AccountLockedError:
        # 解除時刻は出さない（権限・セキュリティ設計書 4.2）。
        st.error("現在、入力を受け付けていません。時間をおいてやり直してください")
        return
    except (InvalidPasscodeError, PasscodeNotSetError):
        # 一致しなかった理由の手がかりは出さない（`SCD-010`）。
        st.error("パスコードが正しくありません")
        return
    except Exception:
        # 想定外の例外から、原因を画面に出さないための最終防波堤（ARC-018）。
        show_unexpected_error()
        return

    st.session_state[AUTHENTICATED_KEY] = True
    st.rerun()


render()

"""パスコード変更画面（`SCN-066`、`SCD-011`）。

現在のパスコードを確認したうえで、新しいパスコードに変更する。検証と操作ログの
記録（`認証失敗`、`パスコード設定`）は、アプリケーション層で行う（`SCD-008`、
`SCD-011`）。この画面は給与・賞与のテーブルを読まないため、`閲覧` は記録しない
（`UID-060`）。

入力値を `st.session_state` に残さないため、フォームは送信のたびに入力を消す
（`clear_on_submit=True`）。そのため、検証のエラーの後も、入力はフォームに残らない。
"""

import streamlit as st

from crm_g.application.auth import (
    AccountLockedError,
    InvalidPasscodeError,
    InvalidPasscodeFormatError,
    PasscodeMismatchError,
    change_passcode,
)
from crm_g.infrastructure.db import open_connection
from crm_g.infrastructure.paths import get_app_paths
from crm_g.ui.components.messages import show_errors, show_success, show_unexpected_error

_CURRENT_KEY = "passcode_change_current"
_NEW_KEY = "passcode_change_new"
_CONFIRM_KEY = "passcode_change_confirm"
_INPUT_KEYS = (_CURRENT_KEY, _NEW_KEY, _CONFIRM_KEY)


def _change(current_passcode: str, new_passcode: str, confirm: str) -> None:
    """パスコードを変更し、結果をメッセージで表示する（`UID-051`）。"""
    db_path = get_app_paths().db_path
    try:
        # scrypt の計算に時間がかかるため、処理中であることを示す。
        with st.spinner("変更しています…"), open_connection(db_path) as connection:
            change_passcode(connection, current_passcode, new_passcode, confirm)
    except PasscodeMismatchError:
        show_errors(["新しいパスコードと、確認用の入力が一致しません"])
        return
    except InvalidPasscodeFormatError:
        show_errors(["新しいパスコードは、メールアドレス形式の文字列にしてください"])
        return
    except InvalidPasscodeError:
        # 一致しなかった理由の手がかりは出さない（`SCD-010`）。
        show_errors(["現在のパスコードが正しくありません"])
        return
    except AccountLockedError:
        # 解除時刻は出さない（権限・セキュリティ設計書 4.2）。
        show_errors(["現在、入力を受け付けていません。時間をおいてやり直してください"])
        return
    except Exception:
        # 想定外の例外から、原因を画面に出さないための最終防波堤（ARC-018）。
        show_unexpected_error()
        return

    show_success("パスコードを変更しました")


def render() -> None:
    """パスコード変更画面を描画する。"""
    st.title("パスコード変更")

    with st.form("passcode_change_form", clear_on_submit=True):
        current_passcode = st.text_input("現在のパスコード", type="password", key=_CURRENT_KEY)
        new_passcode = st.text_input("新しいパスコード", type="password", key=_NEW_KEY)
        confirm = st.text_input("新しいパスコード（確認）", type="password", key=_CONFIRM_KEY)
        submitted = st.form_submit_button("変更", key="passcode_change_submit")

    if not submitted:
        return

    # `clear_on_submit` による入力の消去は、ブラウザから次に送られる値で反映される。
    # それまでの間も、サーバーの `st.session_state` に入力値を残さないため、ここで消す。
    for key in _INPUT_KEYS:
        st.session_state.pop(key, None)

    _change(current_passcode, new_passcode, confirm)


render()

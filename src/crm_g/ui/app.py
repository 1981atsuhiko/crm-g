"""Streamlit のエントリポイント（ARC-014、`UID-030` 認証の関門）。

パスコードが未設定・未認証のときは、設定・ログインの画面だけを
`st.navigation` に渡す。他の画面は、一切登録しない（存在自体を伝えない）。
"""

from pathlib import Path

import streamlit as st

from crm_g.application.auth import is_passcode_set
from crm_g.infrastructure.db import open_connection
from crm_g.infrastructure.paths import get_app_paths
from crm_g.ui.session import AUTHENTICATED_KEY

_VIEWS_DIR = Path(__file__).parent / "views"

_SETUP_PATH = "setup"
_LOGIN_PATH = "login"
_HOME_PATH = "home"


def _is_passcode_set() -> bool:
    db_path = get_app_paths().db_path
    with open_connection(db_path) as connection:
        return is_passcode_set(connection)


def _is_authenticated() -> bool:
    return bool(st.session_state.get(AUTHENTICATED_KEY, False))


def _page(file_name: str, url_path: str, *, title: str) -> st.Page:
    return st.Page(_VIEWS_DIR / file_name, title=title, url_path=url_path, default=True)


def _build_pages() -> list[st.Page]:
    if not _is_passcode_set():
        return [_page("setup.py", _SETUP_PATH, title="パスコード設定")]
    if not _is_authenticated():
        return [_page("login.py", _LOGIN_PATH, title="ログイン")]
    # PH-06 以降で、正式な画面（社員・給与・賞与など）を追加する。
    return [_page("home.py", _HOME_PATH, title="ダッシュボード")]


def main() -> None:
    st.navigation(_build_pages()).run()


main()

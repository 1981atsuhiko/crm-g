"""PoC 用の Streamlit アプリ（POC-03・04・05・07・08 の確認対象）。"""

from __future__ import annotations

from pathlib import Path

import streamlit as st

from crm_poc import settings

PAGES_DIRECTORY = Path(__file__).parent / "ui_pages"


def _is_authenticated() -> bool:
    return bool(st.session_state.get(settings.SESSION_AUTHENTICATED_KEY, False))


def _page(file_name: str, url_path: str, *, default: bool = False) -> st.Page:
    return st.Page(PAGES_DIRECTORY / file_name, title=url_path, url_path=url_path, default=default)


def _build_pages() -> list[st.Page]:
    if not _is_authenticated():
        return [_page("login.py", settings.LOGIN_PATH, default=True)]
    return [
        _page("home.py", settings.HOME_PATH, default=True),
        _page("secret.py", settings.PROTECTED_PATH),
        _page("download.py", settings.DOWNLOAD_PATH),
        _page("widgets.py", settings.WIDGETS_PATH),
    ]


def main() -> None:
    st.navigation(_build_pages()).run()


main()

"""Streamlit のエントリポイント（ARC-014、`UID-030` 認証の関門）。

パスコードが未設定・未認証のときは、設定・ログインの画面だけを
`st.navigation` に渡す。他の画面は、一切登録しない（存在自体を伝えない）。
"""

from pathlib import Path

import streamlit as st

from crm_g.application.auth import is_passcode_set
from crm_g.infrastructure.db import open_connection
from crm_g.infrastructure.paths import get_app_paths
from crm_g.ui.session import AUTHENTICATED_KEY, EXPORT_STATE_KEY

_VIEWS_DIR = Path(__file__).parent / "views"

_SETUP_PATH = "setup"
_LOGIN_PATH = "login"
_HOME_PATH = "home"
_BUSINESS_CALENDAR_PATH = "business_calendar"
_WORK_STATUS_PATH = "work_status"
_PAY_ITEM_PATH = "pay_item"
_CALCULATION_SETTING_PATH = "calculation_setting"
_BONUS_RULE_PATH = "bonus_rule"
_EMPLOYEE_PATH = "employee"
_SALARY_HISTORY_PATH = "salary_history"
_RAISE_LIST_PATH = "raise_list"
_MONTHLY_PAYROLL_PATH = "monthly_payroll"
_BONUS_PATH = "bonus"
_WORK_PATH = "work"
_SALES_PATH = "sales"
_EXPORT_PATH = "export"
_PASSCODE_CHANGE_PATH = "passcode_change"
_OPERATION_LOG_PATH = "operation_log"


def _is_passcode_set() -> bool:
    db_path = get_app_paths().db_path
    with open_connection(db_path) as connection:
        return is_passcode_set(connection)


def _is_authenticated() -> bool:
    return bool(st.session_state.get(AUTHENTICATED_KEY, False))


def _page(file_name: str, url_path: str, *, title: str, default: bool = False) -> st.Page:
    return st.Page(_VIEWS_DIR / file_name, title=title, url_path=url_path, default=default)


def _build_pages() -> list[st.Page] | dict[str, list[st.Page]]:
    if not _is_passcode_set():
        return [_page("setup.py", _SETUP_PATH, title="パスコード設定", default=True)]
    if not _is_authenticated():
        return [_page("login.py", _LOGIN_PATH, title="ログイン", default=True)]
    # PH-14 以降で、バックアップ・データ取込の画面を追加する（パスコード変更・操作ログは追加済み）。
    return {
        "ダッシュボード": [_page("home.py", _HOME_PATH, title="ダッシュボード", default=True)],
        "社員": [_page("employee.py", _EMPLOYEE_PATH, title="社員")],
        "給与": [
            _page("salary_history.py", _SALARY_HISTORY_PATH, title="給与履歴の登録"),
            _page("raise_list.py", _RAISE_LIST_PATH, title="昇給一覧"),
            _page("monthly_payroll.py", _MONTHLY_PAYROLL_PATH, title="月次給与計算"),
        ],
        "賞与": [_page("bonus.py", _BONUS_PATH, title="賞与")],
        "稼働・売上": [
            _page("work.py", _WORK_PATH, title="稼働"),
            _page("sales.py", _SALES_PATH, title="売上"),
        ],
        "データ": [_page("export.py", _EXPORT_PATH, title="出力")],
        "設定": [
            _page("business_calendar.py", _BUSINESS_CALENDAR_PATH, title="営業日カレンダー"),
            _page("work_status.py", _WORK_STATUS_PATH, title="稼働状態"),
            _page("pay_item.py", _PAY_ITEM_PATH, title="給与項目"),
            _page("calculation_setting.py", _CALCULATION_SETTING_PATH, title="計算設定"),
            _page("bonus_rule.py", _BONUS_RULE_PATH, title="賞与ルール・掛け率"),
            _page("passcode_change.py", _PASSCODE_CHANGE_PATH, title="パスコード変更"),
            _page("operation_log.py", _OPERATION_LOG_PATH, title="操作ログ"),
        ],
    }


def main() -> None:
    page = st.navigation(_build_pages())
    if page.url_path != _EXPORT_PATH:
        # 出力画面を離れたら、作成済みの CSV を破棄する（画面設計書 7.2）。
        st.session_state.pop(EXPORT_STATE_KEY, None)
    page.run()


main()

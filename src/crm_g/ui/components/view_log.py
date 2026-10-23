"""閲覧ログの記録（`UID-060`）。

Streamlit は、操作のたびにスクリプトを再実行するため、画面は「対象を新しく
表示したか」を、直前に記録した対象（`st.session_state`）と比較して判定し、
新しく表示したときだけ、この関数を呼ぶ。1回の表示で複数のテーブルを読むときは、
テーブルごとに呼ぶ（テーブルごとに1行を記録する）。記録に失敗したときは、
例外を送出するため、呼び出し側はデータを表示してはいけない（`SCD-021`、
`SCD-005`）。最初の利用はPH-07以降（`SCN-021` など）。
"""

import sqlite3  # noqa: TID251 ui層はTID251の例外対象（ARC-002）

import streamlit as st

from crm_g.application.operation_log import Operation, OperationLogDetail, record_operation_log
from crm_g.infrastructure.db import transaction

_SESSION_KEY = "crm_g_last_logged_view_target"


def record_view_if_newly_displayed(
    connection: sqlite3.Connection, screen: str, target_table: str, target_key: str | None
) -> None:
    """対象（画面・テーブル・キー）を、前回と比較し、新しく表示したときだけ記録する。"""
    targets: dict[str, tuple[str, str | None]] = st.session_state.setdefault(_SESSION_KEY, {})
    current_target = (screen, target_key)
    if targets.get(target_table) == current_target:
        return

    with transaction(connection):
        record_operation_log(
            connection,
            Operation.VIEW,
            target_table,
            OperationLogDetail(target_key=target_key, detail=screen),
        )

    targets[target_table] = current_target

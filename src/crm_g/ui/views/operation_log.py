"""操作ログ画面（`SCN-067`）。

期間（日本時間の日付）と操作の種類で絞り込み、操作ログを新しい順に一覧にする。
1回の表示は最大1,000件で、超えるときは「絞り込んでください」と表示する（`UID-080`）。
変更前・変更後は、JSON の文字列のまま表示する（`SCD-022`）。この画面を開いても、
絞り込みの条件を変えても、`閲覧` は記録しない（`UID-060`。ユーザー承認 2026-10-27）。
CSV の出力・編集・削除は設けない（`SCD-024`）。
"""

from datetime import date

import streamlit as st

from crm_g.application.operation_log import (
    JST,
    OPERATION_LOG_DISPLAY_LIMIT,
    Operation,
    OperationLogRow,
    search_operation_logs,
)
from crm_g.infrastructure.db import open_connection
from crm_g.infrastructure.paths import get_app_paths
from crm_g.ui.components.formatting import DASH
from crm_g.ui.components.messages import show_errors, show_status, show_warning

# 操作の種類の選択肢。`Operation` の値が増えたときは、自動で選択肢に加わる。
_OPERATION_OPTIONS = [operation.value for operation in Operation]
_SENSITIVE_NOTICE = (
    "操作ログには、給与・賞与などの機微情報（変更前・変更後の値）が含まれます。"
    "画面の取り扱いに注意してください。"
)
_EXCEEDS_LIMIT_MESSAGE = (
    f"該当が{OPERATION_LOG_DISPLAY_LIMIT:,}件を超えています。"
    f"新しい順に{OPERATION_LOG_DISPLAY_LIMIT:,}件だけを表示しています。"
    "期間または操作の種類で絞り込んでください。"
)


def _text_or_dash(value: str | None) -> str:
    """値がないときは「－」を返す。"""
    if value is None:
        return DASH
    return value


def _render_table(rows: tuple[OperationLogRow, ...]) -> None:
    """操作ログの一覧を、新しい順に表示する。日時は日本時間で表示する（`UID-041` の形式）。"""
    st.dataframe(
        [
            {
                "日時": row.occurred_at.astimezone(JST).strftime("%Y/%m/%d %H:%M"),
                "操作": row.operation,
                "対象テーブル": row.target_table,
                "対象キー": _text_or_dash(row.target_key),
                "詳細": _text_or_dash(row.detail),
                "変更前": _text_or_dash(row.before_value),
                "変更後": _text_or_dash(row.after_value),
            }
            for row in rows
        ],
        hide_index=True,
    )


def render() -> None:
    """画面を描画する。"""
    st.title("操作ログ")
    show_warning(_SENSITIVE_NOTICE)

    today = date.today()
    start_column, end_column = st.columns(2)
    with start_column:
        start_date = st.date_input(
            "開始日", value=today.replace(day=1), key="operation_log_start_date"
        )
    with end_column:
        end_date = st.date_input("終了日", value=today, key="operation_log_end_date")
    selected_operations = st.multiselect(
        "操作の種類",
        _OPERATION_OPTIONS,
        default=_OPERATION_OPTIONS,
        key="operation_log_operations",
    )

    if start_date > end_date:
        show_errors(["開始日は、終了日以前の日付にしてください。"])
        return

    with open_connection(get_app_paths().db_path) as connection:
        result = search_operation_logs(
            connection,
            start_date,
            end_date,
            [Operation(value) for value in selected_operations],
        )

    if result.exceeds_limit:
        show_warning(_EXCEEDS_LIMIT_MESSAGE)
    if not result.rows:
        show_status("該当する操作ログがありません。")
        return
    st.caption(f"{len(result.rows):,}件")
    _render_table(result.rows)


render()

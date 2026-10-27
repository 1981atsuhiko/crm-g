"""出力画面（`SCN-080`）。

出力の種類と条件を選び、「出力の準備」で CSV を作成する。作成に成功したときだけ、
ダウンロードのボタンを出す（`st.download_button` には作成済みの `bytes` を渡す。画面設計書 7.2）。
給与・賞与を含む出力は、準備の時点で `出力` を記録し、記録に失敗したときはダウンロードの
ボタンを出さない（`SCD-021`）。作成済みの CSV は、出力の種類・条件を変えたとき、または画面を
離れたときに破棄する（`app.py` で破棄する）。
"""

from typing import TypedDict

import streamlit as st

from crm_g.application.hr_export import (
    HR_EXPORT_KIND,
    NoPayrollResultError,
    build_hr_export,
    is_unclosed_month_with_results,
)
from crm_g.infrastructure.db import open_connection
from crm_g.infrastructure.paths import get_app_paths
from crm_g.ui.components.messages import (
    show_errors,
    show_status,
    show_unexpected_error,
    show_warning,
)
from crm_g.ui.components.month_selector import select_year_month
from crm_g.ui.session import EXPORT_STATE_KEY

_KINDS = (HR_EXPORT_KIND,)
_SENSITIVE_NOTICE = (
    "出力するファイルには、給与・賞与などの機微情報を含む場合があります。"
    "保管場所と取り扱いに注意してください。"
)


class _Prepared(TypedDict):
    """作成済みの CSV（再実行をまたいで保持するため、組み込み型で持つ）。"""

    kind: str
    condition: str
    file_name: str
    content: bytes


def _prepare(kind: str, condition: str) -> None:
    try:
        with open_connection(get_app_paths().db_path) as connection:
            export_file = build_hr_export(connection, condition)
    except NoPayrollResultError as error:
        show_errors([str(error)])
        return
    except Exception:
        # `出力` の記録の失敗を含む。CSV は渡さない（`SCD-021`）。
        show_unexpected_error()
        return
    st.session_state[EXPORT_STATE_KEY] = _Prepared(
        kind=kind,
        condition=condition,
        file_name=export_file.file_name,
        content=export_file.content,
    )


def render() -> None:
    st.title("出力")
    kind: str = st.selectbox("出力の種類", _KINDS, key="export_kind")
    condition = select_year_month(key="export_month")

    with open_connection(get_app_paths().db_path) as connection:
        # 計算結果がない月は、準備でエラーを出すため、締め前の案内は出さない。
        if is_unclosed_month_with_results(connection, condition):
            show_status("締め前の月（未確定の値）")

    prepared: _Prepared | None = st.session_state.get(EXPORT_STATE_KEY)
    if prepared is not None and (prepared["kind"], prepared["condition"]) != (kind, condition):
        # 出力の種類・条件を変えたときは、作成済みの CSV を破棄する。
        st.session_state.pop(EXPORT_STATE_KEY)
        prepared = None

    show_warning(_SENSITIVE_NOTICE)
    if st.button("出力の準備", key="export_prepare"):
        st.session_state.pop(EXPORT_STATE_KEY, None)
        _prepare(kind, condition)
        prepared = st.session_state.get(EXPORT_STATE_KEY)

    if prepared is not None:
        st.download_button(
            "ダウンロード",
            data=prepared["content"],
            file_name=prepared["file_name"],
            mime="text/csv",
            key="export_download",
        )


render()

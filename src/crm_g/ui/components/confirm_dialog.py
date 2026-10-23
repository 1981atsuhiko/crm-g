"""確認ダイアログの共通部品（`UID-053`）。

月次締め・取込の実行など、取り消しにくい操作の前に、確認のダイアログを
表示する画面で使う（最初の利用はPH-10以降）。
"""

from collections.abc import Callable

import streamlit as st


def confirm_dialog(title: str, message: str, *, key: str, on_confirm: Callable[[], None]) -> None:
    """確認ダイアログを開く。「実行」を押したときだけ `on_confirm` を呼ぶ。"""

    @st.dialog(title)
    def _dialog() -> None:
        st.write(message)
        confirm_column, cancel_column = st.columns(2)
        if confirm_column.button("実行", key=f"{key}_confirm"):
            on_confirm()
            st.rerun()
        if cancel_column.button("キャンセル", key=f"{key}_cancel"):
            st.rerun()

    _dialog()

"""確認ダイアログの共通部品（`UID-053`）。

月次締め・取込の実行など、取り消しにくい操作の前に、確認のダイアログを
表示する画面で使う（最初の利用はPH-10以降）。
"""

from collections.abc import Callable

import streamlit as st


def confirm_dialog(
    title: str,
    message: str,
    *,
    key: str,
    on_confirm: Callable[[], None],
    on_cancel: Callable[[], None] | None = None,
) -> None:
    """確認ダイアログを開く。「実行」を押したときだけ `on_confirm` を呼ぶ。

    `st.dialog` は、呼び出し側がこの関数を呼ぶたびに開いた状態を保つ。複数回の
    再実行（rerun）をまたいでダイアログを開いたままにするには、呼び出し側が
    `st.session_state` に確認待ちの状態を持ち、その状態がある間は毎回この関数を
    呼ぶ必要がある（`UID-052` の2段階の登録と同様の考え方）。「キャンセル」を
    押したときは、`on_cancel` を呼んでから閉じる（呼び出し側は、ここで確認待ちの
    状態を破棄する）。

    注意：確認待ちの状態を `st.session_state` に保存するときは、`dict` や文字列
    など組み込み型にすること。スクリプトのファイルごと再実行されるたびに、画面の
    コードで定義したクラス（`dataclass` など）は別のクラスとして作り直されるため、
    前回の再実行で保存したインスタンスを `isinstance` で判定すると、一致しなくなる。
    """

    @st.dialog(title)
    def _dialog() -> None:
        st.write(message)
        confirm_column, cancel_column = st.columns(2)
        if confirm_column.button("実行", key=f"{key}_confirm"):
            on_confirm()
            st.rerun()
        if cancel_column.button("キャンセル", key=f"{key}_cancel"):
            if on_cancel is not None:
                on_cancel()
            st.rerun()

    _dialog()

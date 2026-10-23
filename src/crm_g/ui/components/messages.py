"""メッセージ表示の共通部品（`UID-051`）。

画面に出すメッセージは、この関数を通す。スタックトレース・SQL・パス・金額・
個人情報など、内部の情報は一切含めない（`ARC-018`、`SCD-042`）。
"""

from collections.abc import Iterable

import streamlit as st


def show_errors(messages: Iterable[str]) -> None:
    """検証エラー・計算エラー・保存失敗など、やり直しが必要なエラーを表示する。"""
    for message in messages:
        st.error(message)


def show_warning(message: str) -> None:
    """続行可能だが、確認が必要な警告を表示する（`UID-052` の2段階の登録で使う）。"""
    st.warning(message)


def show_success(message: str) -> None:
    """登録・更新などの成功を表示する。"""
    st.toast(message)


def show_status(message: str) -> None:
    """締め済み・未計算など、状態の案内を表示する。"""
    st.info(message)


def show_unexpected_error() -> None:
    """想定外の例外を、原因を特定できない一般的なメッセージで表示する（`ARC-018`）。"""
    st.error("エラーが発生しました。しばらくしてからやり直してください。")

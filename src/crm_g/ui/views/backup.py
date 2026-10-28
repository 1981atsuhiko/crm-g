"""バックアップ・復元画面（`SCN-065`、`ARC-021`）。

手動でバックアップを作り、一覧を見て、選んだバックアップから復元する。復元は
`UID-053` の確認ダイアログの後に行う。作成先は、既定の保存場所（データ領域の
`backups`）か、利用者が指定したフォルダ（`SCD-040`。14章-16）。一覧と復元の対象は、
既定の保存場所のファイルだけ（任意のパスから復元しない。8.6）。

復元すると、パスコードも復元したバックアップの時点のものに戻るため、復元したタブは
ログアウトする（ユーザー承認 2026-10-27）。この画面は給与・賞与のテーブルを読まない
ため、`閲覧` は記録しない（`UID-060`）。
"""

import math
from pathlib import Path
from typing import TypedDict

import streamlit as st

from crm_g.application.backup import (
    BackupCreationError,
    BackupDestinationError,
    BackupEntry,
    BackupNotFoundError,
    InvalidBackupError,
    RestoreFailedError,
    create_manual_backup,
    list_backups,
    parse_backup_destination,
    restore_from_backup,
)
from crm_g.application.operation_log import JST
from crm_g.infrastructure.migration import BUNDLED_DDL_DIR
from crm_g.infrastructure.paths import get_app_paths
from crm_g.ui.components.confirm_dialog import confirm_dialog
from crm_g.ui.components.messages import (
    UNEXPECTED_ERROR_MESSAGE,
    show_errors,
    show_status,
    show_success,
    show_unexpected_error,
    show_warning,
)
from crm_g.ui.session import AUTHENTICATED_KEY

_PENDING_KEY = "backup_restore_pending"
_LAST_RESULT_KEY = "backup_last_result"
_LAST_ERROR_KEY = "backup_last_error"
_DESTINATION_KEY = "backup_destination"
_SELECTED_KEY = "backup_selected"

# `UID-061` の文言。
_SENSITIVE_NOTICE = (
    "バックアップのファイルには、給与・賞与・評価・個人情報・操作ログが含まれます。"
    "保管場所と取り扱いに注意してください。"
)


class _Pending(TypedDict):
    """復元の確認待ち（再実行をまたいで保持するため、組み込み型で持つ）。"""

    file_name: str
    created_at_text: str


def _format_created_at(entry: BackupEntry) -> str:
    """作成日時を、日本時間で表示する。"""
    return entry.created_at.astimezone(JST).strftime("%Y/%m/%d %H:%M:%S")


def _format_size(size_bytes: int) -> str:
    """ファイルのサイズを、KB（切り上げ）で表示する。"""
    return f"{math.ceil(size_bytes / 1024):,} KB"


def _restore_message(pending: _Pending) -> str:
    """確認ダイアログの文言（`UID-053`）。"""
    return (
        f"バックアップ「{pending['file_name']}」（作成日時 {pending['created_at_text']}）から"
        "復元します。\n\n"
        "- 現在のデータは、すべてこのバックアップの時点の内容に置き換わります"
        "（操作ログも置き換わります）。\n"
        "- 復元の前に、現在のデータのバックアップを自動で作ります。\n"
        "- パスコードも、このバックアップの時点のものに戻ります。"
        "復元の後は、ログアウトします。\n\n"
        "よろしいですか？"
    )


def _render_pending(db_path: Path, backup_dir: Path) -> None:
    """復元の確認ダイアログを出す。確認待ちの状態がないときは、何もしない。"""
    pending: _Pending | None = st.session_state.get(_PENDING_KEY)
    if pending is None:
        return

    def _run() -> None:
        st.session_state.pop(_PENDING_KEY, None)
        try:
            with st.spinner("復元しています…"):
                result = restore_from_backup(
                    db_path, backup_dir, BUNDLED_DDL_DIR, pending["file_name"]
                )
        except (
            BackupNotFoundError,
            InvalidBackupError,
            BackupCreationError,
            RestoreFailedError,
        ) as error:
            st.session_state[_LAST_ERROR_KEY] = str(error)
        except Exception:
            st.session_state[_LAST_ERROR_KEY] = UNEXPECTED_ERROR_MESSAGE
        else:
            # パスコードも復元の時点に戻るため、このタブはログアウトする。
            st.session_state.pop(AUTHENTICATED_KEY, None)
            show_success(f"{result.restored_name} から復元しました。もう一度ログインしてください")

    def _cancel() -> None:
        st.session_state.pop(_PENDING_KEY, None)

    confirm_dialog(
        "バックアップからの復元",
        _restore_message(pending),
        key="backup_restore_confirm",
        on_confirm=_run,
        on_cancel=_cancel,
    )


def _render_create(db_path: Path, backup_dir: Path) -> None:
    """バックアップの作成。作成先を空欄にすると、既定の保存場所に作る。"""
    st.subheader("バックアップの作成")
    with st.form("backup_create_form"):
        destination_text = st.text_input(
            "作成先のフォルダ（空欄のときは、上の保存場所に作ります）",
            key=_DESTINATION_KEY,
            placeholder="例：D:\\backup",
        )
        submitted = st.form_submit_button("バックアップを作成", key="backup_create_submit")
    if not submitted:
        return

    try:
        destination = parse_backup_destination(destination_text)
        with st.spinner("バックアップを作成しています…"):
            file_name = create_manual_backup(db_path, backup_dir, destination)
    except (BackupDestinationError, BackupCreationError) as error:
        show_errors([str(error)])
        return
    except Exception:
        # 想定外の例外から、原因を画面に出さないための最終防波堤（ARC-018）。
        show_unexpected_error()
        return

    if destination is None:
        message = f"バックアップを作成しました（{file_name}）"
    else:
        message = f"指定したフォルダに、バックアップを作成しました（{file_name}）"
    st.session_state[_LAST_RESULT_KEY] = message
    # 一覧の選択を、作ったばかりの最新のバックアップに戻す。
    st.session_state.pop(_SELECTED_KEY, None)
    st.rerun()


def _render_list(backup_dir: Path) -> None:
    """既定の保存場所のバックアップの一覧と、復元の操作。"""
    st.subheader("バックアップの一覧")
    entries = list_backups(backup_dir)
    if not entries:
        show_status("バックアップはありません。")
        return

    st.dataframe(
        [
            {
                "ファイル名": entry.name,
                "作成日時": _format_created_at(entry),
                "サイズ": _format_size(entry.size_bytes),
            }
            for entry in entries
        ],
        hide_index=True,
    )

    by_name = {entry.name: entry for entry in entries}
    selected_name = st.selectbox(
        "復元するバックアップ",
        list(by_name),
        format_func=lambda name: f"{name}（{_format_created_at(by_name[name])}）",
        key=_SELECTED_KEY,
    )
    if st.button("復元", key="backup_restore") and selected_name is not None:
        st.session_state[_PENDING_KEY] = _Pending(
            file_name=selected_name,
            created_at_text=_format_created_at(by_name[selected_name]),
        )
        st.rerun()


def render() -> None:
    """画面を描画する。"""
    st.title("バックアップ・復元")
    show_warning(_SENSITIVE_NOTICE)

    last_error = st.session_state.pop(_LAST_ERROR_KEY, None)
    if last_error is not None:
        show_errors([last_error])
    last_result = st.session_state.pop(_LAST_RESULT_KEY, None)
    if last_result is not None:
        show_success(last_result)

    paths = get_app_paths()
    st.markdown("**保存場所**")
    st.code(str(paths.backup_dir), language=None)

    _render_pending(paths.db_path, paths.backup_dir)
    _render_create(paths.db_path, paths.backup_dir)
    _render_list(paths.backup_dir)


render()

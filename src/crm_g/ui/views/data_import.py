"""データ取込画面（`SCN-070`、移行設計書 6章・7章）。

取込の種類と取込用 CSV を選び、「確認の実行」で読み込み・名寄せ・整形・検証をして、結果を
表示する（DB は変更しない。`MIG-004`）。エラーがないときだけ「取込の実行」を押せる。
取込の実行は、`UID-053` の確認ダイアログの後に行う。取込の処理は、移行コマンドと同じ
アプリケーション層のユースケースを呼ぶ（`MIG-006`）。取込前のバックアップは、アプリの
既定の保存場所に作る（`MIG-053`）。

確認した CSV のバイト列と確認の結果は、取込の実行まで UI 状態として持つ（`UID-031`）。
取込を実行したとき、取込の種類・ファイルを変えたとき、画面を離れたとき（`app.py`）に破棄する。
エラーの一覧には、値を出さない（行番号・項目・原因だけ。`MIG-005`）。一致しなかった人の
氏名は、画面にだけ出す（`MIG-032`）。
"""

from datetime import date
from typing import TypedDict

import streamlit as st

from crm_g.application.data_import import (
    ERROR_DISPLAY_LIMIT,
    ImportKind,
    ImportReport,
    check_import,
    run_import,
)
from crm_g.application.import_common import ImportErrorCode
from crm_g.infrastructure.db import open_connection
from crm_g.infrastructure.paths import get_app_paths
from crm_g.ui.components.confirm_dialog import confirm_dialog
from crm_g.ui.components.formatting import DASH
from crm_g.ui.components.messages import (
    UNEXPECTED_ERROR_MESSAGE,
    show_errors,
    show_status,
    show_success,
    show_unexpected_error,
    show_warning,
)
from crm_g.ui.session import IMPORT_RESULT_KEY, IMPORT_STATE_KEY

_KIND_KEY = "import_kind"
_UPLOADER_KEY_PREFIX = "import_file"
# 取込の後に、ファイルの選択を空に戻すため、アップローダーのキーを変える世代の番号。
_UPLOADER_GENERATION_KEY = "import_uploader_generation"
_PENDING_KEY = "import_execute_pending"

_SENSITIVE_NOTICE = (
    "取込用 CSV には、給与・個人情報などの機微情報が含まれます。"
    "保管場所と取り扱いに注意し、取り込んだ後は削除してください。"
)
_ORDER_NOTICE = (
    "取り込む順は、社員 → 給与履歴 → 稼働期間 → 単価履歴 → 月次売上原価 → 賞与です。"
    "社員より前に、他の種類は取り込めません。"
)
_RESTORE_GUIDE = (
    "取り込みを取り消すときは、「設定」の「バックアップ・復元」で、"
    "取込前のバックアップから復元してください。"
)
_FAILURE_GUIDE = (
    "取り込みは行っていません。データの状態が気になるときは、「設定」の「バックアップ・復元」で、"
    "取込前のバックアップから復元できます。"
)


class _Checked(TypedDict):
    """確認した CSV と、確認の結果（再実行をまたいで保持するため、組み込み型の辞書で持つ）。

    `report` は、アプリケーション層の型で、画面のコードで定義したクラスではない。
    """

    kind: str
    file_id: str
    content: bytes
    report: ImportReport


class _Result(TypedDict):
    """取込の実行の結果。`report` は、例外で失敗したときは `None`。"""

    kind: str
    report: ImportReport | None


def _code_text(code: ImportErrorCode) -> str:
    """エラーの種類の表示（コードと説明）。"""
    return f"{code.value} {code.description}"


def _render_matching(report: ImportReport) -> None:
    """名寄せの結果（一致した行数と、一致しなかった人の一覧。`MIG-032`）。"""
    if report.matched_row_count is None:
        return
    st.markdown(f"**名寄せの結果**：一致した行数 {report.matched_row_count:,} 行")
    if not report.unmatched:
        return
    st.markdown(f"一致しなかった人：{len(report.unmatched):,} 件")
    st.dataframe(
        [
            {
                "氏名": DASH if person.name is None else person.name,
                "メールアドレス": DASH if person.email is None else person.email,
                "行番号": "、".join(str(line) for line in person.line_numbers),
                "原因": _code_text(person.code),
            }
            for person in report.unmatched
        ],
        hide_index=True,
        key="import_unmatched_table",
    )


def _render_issues(report: ImportReport) -> None:
    """エラーの一覧（行番号・項目・原因。先頭の 1,000 件と総数。`MIG-061`、`MGD-008`）。"""
    show_errors(
        [
            f"エラーが {report.error_count:,} 件あります。何も取り込めません。"
            "CSV を修正して、確認からやり直してください。"
        ]
    )
    if report.error_count > ERROR_DISPLAY_LIMIT:
        show_status(
            f"先頭の {ERROR_DISPLAY_LIMIT:,} 件を表示しています（全 {report.error_count:,} 件）。"
        )
    st.dataframe(
        [
            {
                "行番号": DASH if issue.line_number is None else str(issue.line_number),
                "項目": DASH if issue.column is None else issue.column,
                "原因": _code_text(issue.code),
            }
            for issue in report.displayed_issues
        ],
        hide_index=True,
        key="import_issue_table",
    )


def _render_report(report: ImportReport) -> None:
    """確認の結果（`MIG-061`）。値（金額・個人情報）は、エラーの一覧に出さない。"""
    excluded = "、".join(report.excluded_columns) if report.excluded_columns else "なし"
    st.markdown(f"**取込の種類**：{report.kind.label}")
    st.markdown(f"**読み込んだ行数**：{report.row_count:,} 行")
    st.markdown(f"**取り込まなかった列**：{excluded}")
    _render_matching(report)
    if report.has_errors:
        _render_issues(report)
    else:
        show_status("エラーはありません。「取込の実行」で取り込めます。")


def _render_result(result: _Result) -> None:
    """取込の実行の結果。失敗したときは、一般的なメッセージと復元の案内を出す（`ARC-018`）。"""
    st.subheader("取込の結果")
    report = result["report"]
    if report is None:
        show_errors([UNEXPECTED_ERROR_MESSAGE])
        show_status(_FAILURE_GUIDE)
        return
    if not report.executed:
        # 確認の後に DB が変わり、取込の実行の中の再検証でエラーになったとき（MIG-050）。
        show_warning("確認の後にデータが変わったため、取り込みませんでした。")
        _render_report(report)
        return
    st.markdown(f"**取込の種類**：{report.kind.label}")
    st.markdown(f"**取り込んだ行数**：{report.imported_count:,} 行")
    st.markdown(f"**再計算した月数**：{report.recalculated_month_count:,} か月")
    st.markdown(f"**計算のエラー**：{report.calculation_error_count:,} 件")
    if report.calculation_error_count:
        # 計算のエラーは、取込のエラーではない（MIG-052）。エラーの一覧へ案内する（UID-055）。
        show_warning(
            "再計算で計算のエラーが出ました。「給与」の「月次給与計算」で、"
            "エラーの一覧を確認してください。"
        )
    if report.backup_path is not None:
        st.markdown(f"**取込前のバックアップ**：{report.backup_path.name}")
    show_status(_RESTORE_GUIDE)


def _execute(checked: _Checked) -> None:
    """取込の実行（確認ダイアログの「実行」）。結果は、ダイアログが閉じた後に表示する。"""
    kind = ImportKind(checked["kind"])
    paths = get_app_paths()
    st.session_state.pop(_PENDING_KEY, None)
    # 実行したら、確認した CSV を破棄し、ファイルの選択を空に戻す（UID-031）。
    st.session_state.pop(IMPORT_STATE_KEY, None)
    st.session_state[_UPLOADER_GENERATION_KEY] = (
        st.session_state.get(_UPLOADER_GENERATION_KEY, 0) + 1
    )
    report: ImportReport | None
    try:
        with (
            st.spinner("取り込んでいます…"),
            open_connection(paths.db_path) as connection,
        ):
            report = run_import(
                connection,
                kind,
                checked["content"],
                date.today(),
                db_path=paths.db_path,
                backup_dir=paths.backup_dir,
            )
    except Exception:
        # 原因はアプリケーション層がログに残す。画面には一般的なメッセージを出す（ARC-018）。
        report = None
    st.session_state[IMPORT_RESULT_KEY] = _Result(kind=kind.value, report=report)
    if report is not None and report.executed:
        show_success(f"{kind.label}を取り込みました")


def _confirm_message(checked: _Checked) -> str:
    """確認ダイアログの文言（`UID-053`：取込の種類、件数、エラーの件数）。"""
    report = checked["report"]
    return (
        f"「{report.kind.label}」の CSV（{report.row_count:,} 行、エラー "
        f"{report.error_count:,} 件）を取り込みます。\n\n"
        "- 取込の前に、現在のデータのバックアップを自動で作ります。\n"
        "- 取り込んだデータは、画面から削除できません。取り消すときは、"
        "取込前のバックアップから復元します。\n"
        "- 取り込んだ社員の、締め前の月を再計算します。\n\n"
        "よろしいですか？"
    )


def _render_pending(checked: _Checked | None) -> None:
    """取込の実行の確認ダイアログ。確認待ちでないときは、何もしない。"""
    if not st.session_state.get(_PENDING_KEY) or checked is None:
        st.session_state.pop(_PENDING_KEY, None)
        return

    def _cancel() -> None:
        st.session_state.pop(_PENDING_KEY, None)

    confirm_dialog(
        "取込の実行",
        _confirm_message(checked),
        key="import_execute_confirm",
        on_confirm=lambda: _execute(checked),
        on_cancel=_cancel,
    )


def _check(kind: ImportKind, file_id: str, content: bytes) -> None:
    """確認の実行（`MIG-004`）。DB を変更しない。結果を UI 状態に置く。"""
    try:
        with open_connection(get_app_paths().db_path) as connection:
            report = check_import(connection, kind, content, date.today())
    except Exception:
        show_unexpected_error()
        return
    st.session_state[IMPORT_STATE_KEY] = _Checked(
        kind=kind.value, file_id=file_id, content=content, report=report
    )


def _discard_stale(kind: ImportKind, file_id: str | None) -> None:
    """取込の種類・ファイルを変えたら、確認した CSV と、前回の取込の結果を破棄する。"""
    checked: _Checked | None = st.session_state.get(IMPORT_STATE_KEY)
    if checked is not None and (checked["kind"], checked["file_id"]) != (kind.value, file_id):
        st.session_state.pop(IMPORT_STATE_KEY)
        st.session_state.pop(_PENDING_KEY, None)
    result: _Result | None = st.session_state.get(IMPORT_RESULT_KEY)
    if result is not None and (result["kind"] != kind.value or file_id is not None):
        st.session_state.pop(IMPORT_RESULT_KEY)


def render() -> None:
    """画面を描画する。"""
    st.title("データ取込")
    show_warning(_SENSITIVE_NOTICE)
    st.caption(_ORDER_NOTICE)

    kinds = list(ImportKind)
    kind = st.selectbox(
        "取込の種類", kinds, index=0, format_func=lambda item: item.label, key=_KIND_KEY
    )
    generation = st.session_state.get(_UPLOADER_GENERATION_KEY, 0)
    uploaded = st.file_uploader(
        "CSV ファイル（UTF-8）", type=["csv"], key=f"{_UPLOADER_KEY_PREFIX}_{generation}"
    )
    file_id = None if uploaded is None else uploaded.file_id
    _discard_stale(kind, file_id)

    if st.button("確認の実行", key="import_check", disabled=uploaded is None):
        st.session_state.pop(IMPORT_STATE_KEY, None)
        st.session_state.pop(IMPORT_RESULT_KEY, None)
        if uploaded is not None:
            _check(kind, uploaded.file_id, uploaded.getvalue())

    checked: _Checked | None = st.session_state.get(IMPORT_STATE_KEY)
    if checked is not None:
        st.subheader("確認の結果")
        _render_report(checked["report"])

    can_execute = checked is not None and not checked["report"].has_errors
    if st.button("取込の実行", key="import_execute", disabled=not can_execute):
        st.session_state[_PENDING_KEY] = True
    _render_pending(checked)

    result: _Result | None = st.session_state.get(IMPORT_RESULT_KEY)
    if result is not None:
        _render_result(result)


render()

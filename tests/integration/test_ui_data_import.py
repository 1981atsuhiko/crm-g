"""データ取込画面の試験（`SCN-070`、移行設計書 13章・7.2、テスト計画書 `TC-MIG-048`）。

CSV は試験の中でバイト列として組み立てる。値はすべて架空である。
"""

import logging
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import replace
from datetime import date
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest
from streamlit.testing.v1.element_tree import Block

import crm_g.application.data_import as data_import_app
from crm_g.application.auth import setup_passcode
from crm_g.application.data_import import (
    ERROR_DISPLAY_LIMIT,
    ImportInTransactionError,
    ImportKind,
    ImportKindDefinition,
    ImportReport,
    ValidatedImport,
)
from crm_g.application.import_common import ImportErrorCode, ImportIssue
from crm_g.cli.launcher import main
from crm_g.infrastructure.db import open_connection
from crm_g.infrastructure.migration import apply_migrations
from crm_g.ui.components.messages import UNEXPECTED_ERROR_MESSAGE
from crm_g.ui.session import AUTHENTICATED_KEY, IMPORT_RESULT_KEY, IMPORT_STATE_KEY

_VIEW_PATH = (
    Path(__file__).resolve().parents[2] / "src" / "crm_g" / "ui" / "views" / "data_import.py"
)
_DDL_DIR = Path(__file__).resolve().parents[2] / "DDL"
_APPTEST_TIMEOUT_SECONDS = 60
_PASSCODE = "taro@3sss.co.jp"
_EMPLOYEE_HEADER = (
    "メールアドレス,社員番号,氏名,氏名カナ,雇用形態,生年月日,性別,入社日,"
    "最寄駅,居住エリア,職種,在籍ステータス,退職日,パスワード"
)
_TARO = (
    "taro.yamada@example.com,E0001,山田 太郎,ヤマダ タロウ,正社員,1990/04/01,男性,"
    "2020/04/01,東京駅,東京都,SE,在籍,,secret-value"
)
_HANAKO = "hanako.suzuki@example.com,E0002,鈴木 花子,,契約社員,,女性,2021-4-1,,,,在籍,,secret-value"
_SALARY_HEADER = "氏名,適用開始日,給与体系,雇用形態,改定区分,基本給"
_UNKNOWN_NAME = "架空 存在しない"
# 画面の確認待ちの状態のキー（`data_import.py` の `_PENDING_KEY`）。
_PENDING_KEY = "import_execute_pending"


def _csv(header: str, *lines: str) -> bytes:
    """見出しと行から、取込用 CSV（UTF-8、CRLF）のバイト列を作る。"""
    return "\r\n".join((header, *lines)).encode("utf-8") + b"\r\n"


_EMPLOYEE_CSV = _csv(_EMPLOYEE_HEADER, _TARO, _HANAKO)


@pytest.fixture
def data_dir(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """`get_app_paths()` が一時ディレクトリを指すようにし、パスコードを設定した DB を作る。"""
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    path = tmp_path / "crm-g"
    path.mkdir(parents=True, exist_ok=True)
    db_path = path / "crm-g.db"
    with open_connection(db_path) as connection:
        apply_migrations(connection, _DDL_DIR, db_path, path / "backups")
        setup_passcode(connection, _PASSCODE, _PASSCODE)
    # マイグレーションの前の自動のバックアップは、試験の対象外のため消す。
    for file in (path / "backups").glob("*.db"):
        file.unlink()
    return path


def _new_app() -> AppTest:
    """認証済みのタブとして、画面を初回実行した `AppTest` を返す。"""
    app = AppTest.from_file(str(_VIEW_PATH), default_timeout=_APPTEST_TIMEOUT_SECONDS)
    app.session_state[AUTHENTICATED_KEY] = True
    return app.run()


def _upload(app: AppTest, content: bytes, name: str = "取込.csv") -> AppTest:
    app.file_uploader[0].set_value((name, content, "text/csv"))
    return app.run()


def _check(app: AppTest) -> AppTest:
    app.button(key="import_check").click()
    return app.run()


def _select_kind(app: AppTest, kind: ImportKind) -> AppTest:
    app.selectbox(key="import_kind").set_value(kind)
    return app.run()


def _execute(app: AppTest) -> AppTest:
    """「取込の実行」を押し、確認ダイアログで「実行」を押す。"""
    app.button(key="import_execute").click()
    app = app.run()
    app.button(key="import_execute_confirm_confirm").click()
    return app.run()


def _texts(app: AppTest) -> str:
    """画面の文言（マークダウン・警告・情報・エラー・キャプション）をつなげて返す。"""
    parts = [element.value for element in app.markdown]
    parts += [element.value for element in app.warning]
    parts += [element.value for element in app.info]
    parts += [element.value for element in app.error]
    parts += [element.value for element in app.caption]
    return "\n".join(str(part) for part in parts)


def _table(app: AppTest, first_column: str) -> list[dict[str, object]]:
    """最初の列の名前で、表を探す（エラーの一覧は「行番号」、一致しなかった人は「氏名」）。"""
    for element in app.dataframe:
        if list(element.value.columns)[0] == first_column:
            records: list[dict[str, object]] = element.value.to_dict("records")
            return records
    raise AssertionError(f"表がありません: {first_column}")


def _count(data_dir: Path, table: str) -> int:
    with open_connection(data_dir / "crm-g.db") as connection:
        row = connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()  # noqa: S608
    return int(row[0])


def _import_logs(data_dir: Path) -> list[str]:
    """操作ログ `取込` の `detail`。"""
    with open_connection(data_dir / "crm-g.db") as connection:
        rows = connection.execute(
            "SELECT detail FROM operation_log WHERE operation = '取込' ORDER BY id"
        ).fetchall()
    return [str(row[0]) for row in rows]


def _backups(data_dir: Path) -> list[Path]:
    return sorted((data_dir / "backups").glob("*.db"))


def _import_employees_directly(data_dir: Path, content: bytes = _EMPLOYEE_CSV) -> None:
    """画面を通さずに、社員を取り込む（試験の前提のデータ）。"""
    db_path = data_dir / "crm-g.db"
    with open_connection(db_path) as connection:
        report = data_import_app.run_import(
            connection,
            ImportKind.EMPLOYEE,
            content,
            date.today(),
            db_path=db_path,
            backup_dir=data_dir / "pre-backups",
        )
    assert report.executed


@pytest.fixture
def captured_log(caplog: pytest.LogCaptureFixture) -> Iterator[pytest.LogCaptureFixture]:
    """crm_g のロガーの出力を、伝播の設定によらず捕まえる。"""
    logger = logging.getLogger("crm_g")
    logger.addHandler(caplog.handler)
    original_level = logger.level
    logger.setLevel(logging.DEBUG)
    try:
        yield caplog
    finally:
        logger.removeHandler(caplog.handler)
        logger.setLevel(original_level)


def _error_messages(captured_log: pytest.LogCaptureFixture) -> list[str]:
    """ERROR のログのメッセージ（伝播の設定により2回捕まえた同じ記録は、1回だけ数える）。"""
    records = {id(record): record for record in captured_log.records}.values()
    return [record.getMessage() for record in records if record.levelno == logging.ERROR]


def _register_failing_validator(monkeypatch: pytest.MonkeyPatch) -> None:
    """社員の検証が、値を含む文言の例外で止まるようにする。"""

    def _fail(_connection: sqlite3.Connection, _content: bytes, _today: date) -> ValidatedImport:
        raise ValueError("山田 太郎 の値を含む擬似的な失敗")

    monkeypatch.setitem(
        data_import_app._DEFINITIONS,
        ImportKind.EMPLOYEE,
        ImportKindDefinition(label="社員", target_table="employee", validate=_fail),
    )


def _blocks(block: Block) -> list[Block]:
    """ブロックと、その中のブロックを、深さ優先で返す。"""
    found = [block]
    for child in block.children.values():
        if isinstance(child, Block):
            found.extend(_blocks(child))
    return found


def _has_dialog(app: AppTest) -> bool:
    return any(node.type == "dialog" for node in _blocks(app._tree))


def _dismiss_dialog(app: AppTest) -> AppTest:
    """開いているダイアログを、右上の「×」・Esc キーで閉じた操作を再現して、再実行する。

    `AppTest` には、ダイアログを閉じる操作がない。`on_dismiss` を渡した `st.dialog` は、
    ダイアログのブロックの ID のウィジェットとして登録され、閉じるとフロントエンドが、その
    ウィジェットの trigger の値を送る。ここでは、同じ値を組み立てて、再実行に渡す。
    """
    dialogs = [node for node in _blocks(app._tree) if node.type == "dialog"]
    assert len(dialogs) == 1, "開いているダイアログが1つではありません"
    widget_states = app._tree.get_widget_states()
    dismissed = widget_states.widgets.add()
    dismissed.id = dialogs[0].proto.dialog.id
    dismissed.trigger_value = True
    return app._run(widget_states)


def test_initial_screen_has_kinds_and_disabled_buttons(data_dir: Path) -> None:
    app = _new_app()

    assert not app.exception
    assert [title.value for title in app.title] == ["データ取込"]
    assert "機微情報" in app.warning[0].value
    assert "社員 → 給与履歴" in _texts(app)
    kind_box = app.selectbox(key="import_kind")
    assert kind_box.options == [kind.label for kind in ImportKind]
    assert kind_box.value == ImportKind.EMPLOYEE
    assert app.button(key="import_check").disabled
    assert app.button(key="import_execute").disabled


def test_check_without_errors_shows_report_and_does_not_change_db(data_dir: Path) -> None:
    """確認の実行は DB を変えない（`MIG-004`）。除外列は名称だけを出す（`REQ-MIG-006`）。"""
    before = (data_dir / "crm-g.db").read_bytes()
    app = _check(_upload(_new_app(), _EMPLOYEE_CSV))

    assert not app.exception
    texts = _texts(app)
    assert "読み込んだ行数**：2 行" in texts
    assert "取り込まなかった列**：パスワード" in texts
    assert "エラーはありません" in texts
    assert "secret-value" not in texts
    # 社員は名寄せをしないため、名寄せの結果は出さない。
    assert "名寄せの結果" not in texts
    assert not app.button(key="import_execute").disabled
    assert app.session_state[IMPORT_STATE_KEY]["content"] == _EMPLOYEE_CSV
    assert (data_dir / "crm-g.db").read_bytes() == before
    assert _backups(data_dir) == []


def test_check_with_errors_lists_issues_without_values_and_disables_execute(
    data_dir: Path,
) -> None:
    """エラーの一覧は、行番号・項目・原因だけ。値（氏名・金額）は出さない（`MIG-005`）。"""
    broken = _HANAKO.replace("2021-4-1", "").replace("契約社員", "アルバイト")
    app = _check(_upload(_new_app(), _csv(_EMPLOYEE_HEADER, _TARO, broken)))

    assert not app.exception
    assert "エラーが 2 件あります" in _texts(app)
    issues = _table(app, "行番号")
    assert issues == [
        {"行番号": "3", "項目": "雇用形態", "原因": "E008 値が選択肢にない"},
        {"行番号": "3", "項目": "入社日", "原因": "E006 必須の値が空"},
    ]
    assert "鈴木" not in str(issues)
    assert "アルバイト" not in str(issues)
    assert app.button(key="import_execute").disabled


def test_file_level_error_has_dash_for_line_and_column(data_dir: Path) -> None:
    app = _check(_upload(_new_app(), "氏名,入社日\r\n山田,2020/04/01\r\n".encode("cp932")))

    assert not app.exception
    issues = _table(app, "行番号")
    assert issues[0] == {
        "行番号": "－",
        "項目": "－",
        "原因": "E001 文字コードが UTF-8 ではない",
    }


def test_unmatched_people_are_listed_with_names(data_dir: Path) -> None:
    """名寄せの結果に、一致した行数と、一致しなかった人の一覧を出す（`MIG-032`）。"""
    _import_employees_directly(data_dir)
    content = _csv(
        _SALARY_HEADER,
        "山田 太郎,2026/04/01,通常評価制度,正社員,入社,250000",
        f"{_UNKNOWN_NAME},2026/04/01,通常評価制度,正社員,入社,260000",
        f"{_UNKNOWN_NAME},2026/05/01,通常評価制度,正社員,定期,270000",
    )
    app = _select_kind(_new_app(), ImportKind.SALARY_HISTORY)
    app = _check(_upload(app, content))

    assert not app.exception
    texts = _texts(app)
    assert "名寄せの結果**：一致した行数 1 行" in texts
    assert "一致しなかった人：1 件" in texts
    unmatched = _table(app, "氏名")
    assert unmatched == [
        {
            "氏名": _UNKNOWN_NAME,
            "メールアドレス": "－",
            "行番号": "3、4",
            "原因": "E010 社員が見つからない",
        }
    ]
    assert "260000" not in str(_table(app, "行番号"))
    assert app.button(key="import_execute").disabled


def test_error_list_is_limited_to_first_1000_with_total(data_dir: Path) -> None:
    """エラーは先頭の 1,000 件を表示し、総数を併記する（`MGD-008`）。"""
    lines = [
        _TARO.replace("taro.yamada", f"user{index}")
        .replace("E0001", f"E{index:05d}")
        .replace("2020/04/01", "")
        for index in range(ERROR_DISPLAY_LIMIT + 5)
    ]
    app = _check(_upload(_new_app(), _csv(_EMPLOYEE_HEADER, *lines)))

    assert not app.exception
    total = ERROR_DISPLAY_LIMIT + 5
    texts = _texts(app)
    assert f"エラーが {total:,} 件あります" in texts
    assert f"先頭の {ERROR_DISPLAY_LIMIT:,} 件を表示しています（全 {total:,} 件）" in texts
    assert len(_table(app, "行番号")) == ERROR_DISPLAY_LIMIT


def test_execute_imports_after_confirmation_and_discards_csv(data_dir: Path) -> None:
    """確認ダイアログの後に取り込み、結果とバックアップのファイル名を出す。CSV は破棄する。"""
    app = _check(_upload(_new_app(), _EMPLOYEE_CSV))
    app.button(key="import_execute").click()
    app = app.run()

    # 確認ダイアログに、取込の種類・件数・エラーの件数を書く（UID-053）。
    message = _texts(app)
    assert "「社員」の CSV（2 行、エラー 0 件）を取り込みます" in message
    assert "バックアップを自動で作ります" in message
    assert _count(data_dir, "employee") == 0

    app.button(key="import_execute_confirm_confirm").click()
    app = app.run()

    assert not app.exception
    assert _count(data_dir, "employee") == 2
    assert _import_logs(data_dir) == ["種類=employee 行数=2 再計算した月数=0 計算エラー=0"]
    backups = _backups(data_dir)
    assert len(backups) == 1
    texts = _texts(app)
    assert "取り込んだ行数**：2 行" in texts
    assert "再計算した月数**：0 か月" in texts
    assert "計算のエラー**：0 件" in texts
    assert f"取込前のバックアップ**：{backups[0].name}" in texts
    assert "バックアップ・復元" in texts
    assert str(data_dir) not in texts
    assert any("社員を取り込みました" in toast.value for toast in app.toast)
    # 実行したら、確認した CSV を破棄し、ファイルの選択を空に戻す（UID-031）。
    assert IMPORT_STATE_KEY not in app.session_state
    assert app.file_uploader[0].value is None
    assert app.button(key="import_check").disabled
    assert app.button(key="import_execute").disabled


def test_cancel_keeps_data_and_checked_csv(data_dir: Path) -> None:
    app = _check(_upload(_new_app(), _EMPLOYEE_CSV))
    app.button(key="import_execute").click()
    app = app.run()
    app.button(key="import_execute_confirm_cancel").click()
    app = app.run()

    assert not app.exception
    assert _count(data_dir, "employee") == 0
    assert _backups(data_dir) == []
    assert IMPORT_STATE_KEY in app.session_state
    assert not app.button(key="import_execute").disabled


def test_dismissing_dialog_discards_pending_and_recheck_does_not_reopen_it(
    data_dir: Path,
) -> None:
    """確認ダイアログを「×」・Esc キーで閉じると、確認待ちを破棄する（キャンセルと同じ）。

    その後に確認をやり直しても、頼んでいないのにダイアログが開かない。
    """
    app = _check(_upload(_new_app(), _EMPLOYEE_CSV))
    app.button(key="import_execute").click()
    app = app.run()
    assert _has_dialog(app)

    app = _dismiss_dialog(app)

    assert not app.exception
    assert not _has_dialog(app)
    assert _PENDING_KEY not in app.session_state
    assert _count(data_dir, "employee") == 0
    assert _backups(data_dir) == []
    assert IMPORT_STATE_KEY in app.session_state

    app = _check(app)

    assert not app.exception
    assert not _has_dialog(app)
    assert _count(data_dir, "employee") == 0


def test_check_discards_pending_confirmation(data_dir: Path) -> None:
    """「確認の実行」を押すと、確認待ちが残っていても破棄し、ダイアログを開かない。"""
    app = _check(_upload(_new_app(), _EMPLOYEE_CSV))
    app.session_state[_PENDING_KEY] = True

    app = _check(app)

    assert not app.exception
    assert not _has_dialog(app)
    assert _PENDING_KEY not in app.session_state


def test_pending_with_errors_in_check_result_does_not_open_dialog(data_dir: Path) -> None:
    """確認の結果にエラーがあるときは、確認待ちを破棄し、ダイアログから取り込ませない。"""
    broken = _TARO.replace("2020/04/01", "")
    app = _check(_upload(_new_app(), _csv(_EMPLOYEE_HEADER, broken)))
    assert app.button(key="import_execute").disabled
    app.session_state[_PENDING_KEY] = True

    app = app.run()

    assert not app.exception
    assert not _has_dialog(app)
    assert _PENDING_KEY not in app.session_state
    assert _count(data_dir, "employee") == 0
    assert _backups(data_dir) == []


def test_changing_kind_or_file_discards_checked_csv(data_dir: Path) -> None:
    app = _check(_upload(_new_app(), _EMPLOYEE_CSV))
    assert IMPORT_STATE_KEY in app.session_state

    app = _upload(app, _csv(_EMPLOYEE_HEADER, _TARO), name="別.csv")
    assert not app.exception
    assert IMPORT_STATE_KEY not in app.session_state
    assert app.button(key="import_execute").disabled

    app = _check(app)
    assert IMPORT_STATE_KEY in app.session_state
    app = _select_kind(app, ImportKind.BONUS)
    assert not app.exception
    assert IMPORT_STATE_KEY not in app.session_state
    assert app.button(key="import_execute").disabled


def test_result_is_cleared_when_a_new_file_is_selected(data_dir: Path) -> None:
    app = _execute(_check(_upload(_new_app(), _EMPLOYEE_CSV)))
    assert IMPORT_RESULT_KEY in app.session_state

    app = _upload(app, _csv(_EMPLOYEE_HEADER, _TARO))

    assert not app.exception
    assert IMPORT_RESULT_KEY not in app.session_state
    assert "取込の結果" not in [header.value for header in app.subheader]


def test_revalidation_error_is_shown_when_db_changed_after_check(data_dir: Path) -> None:
    """確認の後に DB が変わると、実行の中の再検証でエラーになり、取り込まない（`MIG-050`）。"""
    app = _check(_upload(_new_app(), _EMPLOYEE_CSV))
    _import_employees_directly(data_dir)

    app = _execute(app)

    assert not app.exception
    assert _count(data_dir, "employee") == 2
    texts = _texts(app)
    assert "確認の後にデータが変わったため、取り込みませんでした" in texts
    assert "E013" in str(_table(app, "行番号"))


def test_unexpected_exception_shows_general_message_and_restore_guide(
    data_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """取込の中の例外は、一般的なメッセージと、復元の案内を出す（`ARC-018`）。"""

    def _raise(*_args: object, **_kwargs: object) -> ImportReport:
        raise RuntimeError("山田 太郎 の値を含む擬似的な失敗")

    app = _check(_upload(_new_app(), _EMPLOYEE_CSV))
    monkeypatch.setattr(data_import_app, "run_import", _raise)
    app = _execute(app)

    assert not app.exception
    texts = _texts(app)
    assert UNEXPECTED_ERROR_MESSAGE in texts
    # 取込前のバックアップを作る前に止まったため、以前のバックアップからの復元を案内する。
    assert "以前のバックアップから復元できます" in texts
    assert "取込前のバックアップから復元" not in texts
    assert "山田" not in texts
    assert _count(data_dir, "employee") == 0
    assert IMPORT_STATE_KEY not in app.session_state


def test_exception_after_backup_guides_to_the_backup(
    data_dir: Path, monkeypatch: pytest.MonkeyPatch, captured_log: pytest.LogCaptureFixture
) -> None:
    """取込前のバックアップを作った後の例外は、そのファイル名と、そこからの復元を案内する。"""

    def _raise(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("山田 太郎 の値を含む擬似的な失敗")

    app = _check(_upload(_new_app(), _EMPLOYEE_CSV))
    monkeypatch.setattr(data_import_app, "record_operation_log", _raise)
    app = _execute(app)

    assert not app.exception
    texts = _texts(app)
    backups = _backups(data_dir)
    assert len(backups) == 1
    assert UNEXPECTED_ERROR_MESSAGE in texts
    assert f"取込前のバックアップ**：{backups[0].name}" in texts
    assert "取込前のバックアップから復元できます" in texts
    assert "以前のバックアップ" not in texts
    assert "山田" not in texts
    assert str(data_dir) not in texts
    assert _count(data_dir, "employee") == 0
    assert app.session_state[IMPORT_RESULT_KEY]["failure"] == "rolled_back"
    assert _error_messages(captured_log) == [
        "取込に失敗し、ロールバックしました（種類=employee 例外=RuntimeError）"
    ]


def test_validation_exception_on_execute_is_logged_once(
    data_dir: Path, monkeypatch: pytest.MonkeyPatch, captured_log: pytest.LogCaptureFixture
) -> None:
    """取込の実行の、検証の途中の例外は、型名だけを1回ログに出し、以前のバックアップを案内する。"""
    app = _check(_upload(_new_app(), _EMPLOYEE_CSV))
    _register_failing_validator(monkeypatch)
    app = _execute(app)

    assert not app.exception
    texts = _texts(app)
    assert UNEXPECTED_ERROR_MESSAGE in texts
    assert "以前のバックアップから復元できます" in texts
    assert _backups(data_dir) == []
    assert _error_messages(captured_log) == [
        "取込の検証の途中で失敗しました（種類=employee 例外=ValueError）"
    ]
    assert "山田" not in captured_log.text


def test_validation_exception_on_check_is_logged_once(
    data_dir: Path, monkeypatch: pytest.MonkeyPatch, captured_log: pytest.LogCaptureFixture
) -> None:
    """確認の実行の、検証の途中の例外は、型名だけを1回ログに出す（画面は重ねて出さない）。"""
    app = _upload(_new_app(), _EMPLOYEE_CSV)
    _register_failing_validator(monkeypatch)
    app = _check(app)

    assert not app.exception
    assert UNEXPECTED_ERROR_MESSAGE in _texts(app)
    assert _error_messages(captured_log) == [
        "取込の検証の途中で失敗しました（種類=employee 例外=ValueError）"
    ]
    assert "山田" not in captured_log.text


@pytest.mark.parametrize("execute", [False, True])
def test_connection_failure_is_logged_once_by_the_screen(
    data_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
    captured_log: pytest.LogCaptureFixture,
    execute: bool,  # noqa: FBT001 pytest のパラメーター
) -> None:
    """DB の接続を開けないときは、画面が型名だけを1回ログに出す（値・パスは出さない）。"""

    def _fail(_db_path: Path) -> Iterator[sqlite3.Connection]:
        raise sqlite3.OperationalError(f"開けません: {data_dir}")

    app = _upload(_new_app(), _EMPLOYEE_CSV)
    if execute:
        app = _check(app)
    monkeypatch.setattr("crm_g.infrastructure.db.open_connection", contextmanager(_fail))
    app = _execute(app) if execute else _check(app)

    assert not app.exception
    texts = _texts(app)
    assert UNEXPECTED_ERROR_MESSAGE in texts
    if execute:
        assert "以前のバックアップから復元できます" in texts
    assert str(data_dir) not in texts
    assert _error_messages(captured_log) == [
        "取込の画面で、DB の接続に失敗しました（例外=OperationalError）"
    ]
    assert str(data_dir) not in captured_log.text


def test_check_exception_shows_general_message(
    data_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def _raise(*_args: object, **_kwargs: object) -> ImportReport:
        raise RuntimeError("擬似的な失敗")

    app = _upload(_new_app(), _EMPLOYEE_CSV)
    monkeypatch.setattr(data_import_app, "check_import", _raise)
    app = _check(app)

    assert not app.exception
    assert UNEXPECTED_ERROR_MESSAGE in _texts(app)
    assert IMPORT_STATE_KEY not in app.session_state


def test_calculation_errors_after_import_guide_to_monthly_payroll(data_dir: Path) -> None:
    """再計算の計算のエラーは、取込のエラーではない。件数と、エラーの一覧への案内を出す。"""
    _import_employees_directly(data_dir)
    first_day = date.today().replace(day=1)
    content = _csv(
        _SALARY_HEADER, f"山田 太郎,{first_day:%Y/%m/%d},通常評価制度,正社員,入社,250000"
    )
    app = _select_kind(_new_app(), ImportKind.SALARY_HISTORY)
    app = _execute(_check(_upload(app, content)))

    assert not app.exception
    texts = _texts(app)
    assert "取り込んだ行数**：1 行" in texts
    assert "再計算した月数**：1 か月" in texts
    assert "計算のエラー**：1 件" in texts
    assert "月次給与計算" in texts


def _employee_rows(db_path: Path) -> list[tuple[object, ...]]:
    """社員の行（作成日時・更新日時を除く）。"""
    with open_connection(db_path) as connection:
        rows = connection.execute(
            "SELECT employee_no, name, name_kana, email, employment_type, birth_date, gender, "
            "join_date, nearest_station, residence_area, job_type, enrollment_status, "
            "retire_date FROM employee ORDER BY employee_no"
        ).fetchall()
    return [tuple(row) for row in rows]


def test_tc_mig_048_screen_and_command_give_the_same_result(
    data_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """画面と移行コマンドで、同じ CSV を、別々の DB に取り込むと、同じ結果になる（`MIG-006`）。"""
    app = _execute(_check(_upload(_new_app(), _EMPLOYEE_CSV)))
    assert not app.exception
    screen_rows = _employee_rows(data_dir / "crm-g.db")

    other_root = tmp_path / "other"
    other_dir = other_root / "crm-g"
    other_dir.mkdir(parents=True)
    other_db = other_dir / "crm-g.db"
    with open_connection(other_db) as connection:
        apply_migrations(connection, _DDL_DIR, other_db, other_dir / "migration-backups")
    csv_path = tmp_path / "employee.csv"
    csv_path.write_bytes(_EMPLOYEE_CSV)
    monkeypatch.setenv("LOCALAPPDATA", str(other_root))

    assert main(["import", "employee", str(csv_path)]) == 0

    assert len(screen_rows) == 2
    assert _employee_rows(other_db) == screen_rows
    assert _import_logs(other_dir) == _import_logs(data_dir)
    assert len(_import_logs(data_dir)) == 1


def test_backup_failure_shows_general_message_without_path(
    data_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """取込前のバックアップを作れないときは、取り込まず、パスを出さずに案内する（`MIG-053`）。"""

    def _fail_copy(_source: Path, destination: Path) -> None:
        destination.write_bytes(b"partial")
        raise OSError(f"書き込めません: {destination}")

    app = _check(_upload(_new_app(), _EMPLOYEE_CSV))
    monkeypatch.setattr("crm_g.infrastructure.backup._copy_database", _fail_copy)
    app = _execute(app)

    assert not app.exception
    texts = _texts(app)
    assert "取込前のバックアップを作成できなかったため、取り込みませんでした" in texts
    assert "以前のバックアップから復元できます" in texts
    assert "crm-g_" not in texts
    assert str(data_dir) not in texts
    assert UNEXPECTED_ERROR_MESSAGE not in texts
    assert _count(data_dir, "employee") == 0
    assert _backups(data_dir) == []
    assert _import_logs(data_dir) == []
    assert app.session_state[IMPORT_RESULT_KEY]["failure"] == "backup"


def test_in_transaction_error_shows_general_message(
    data_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """取込を始める前に止まったとき（`ImportInTransactionError`）は、一般的なメッセージを出す。"""

    def _raise(*_args: object, **_kwargs: object) -> ImportReport:
        raise ImportInTransactionError("取込の実行は、トランザクションの外で呼んでください")

    app = _check(_upload(_new_app(), _EMPLOYEE_CSV))
    monkeypatch.setattr(data_import_app, "run_import", _raise)
    app = _execute(app)

    assert not app.exception
    texts = _texts(app)
    assert UNEXPECTED_ERROR_MESSAGE in texts
    assert "以前のバックアップから復元できます" in texts
    assert "トランザクション" not in texts
    assert _count(data_dir, "employee") == 0


def test_revalidation_failure_under_lock_keeps_backup_and_imports_nothing(
    data_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """書き込みのロックの後の再検証で失敗すると、取り込まず、取込前のバックアップの名前を示す。"""
    original = data_import_app._DEFINITIONS[ImportKind.EMPLOYEE]

    def _validate(connection: sqlite3.Connection, content: bytes, today: date) -> ValidatedImport:
        validated = original.validate(connection, content, today)
        if not connection.in_transaction:
            return validated
        # ロックの後で読み直すと、ほかのタブが同じ社員を登録していた状態にする。
        issue = ImportIssue(2, "メールアドレス", ImportErrorCode.DUPLICATE_KEY_IN_DB)
        return replace(validated, issues=(issue,))

    monkeypatch.setitem(
        data_import_app._DEFINITIONS,
        ImportKind.EMPLOYEE,
        ImportKindDefinition(label="社員", target_table="employee", validate=_validate),
    )
    app = _execute(_check(_upload(_new_app(), _EMPLOYEE_CSV)))

    assert not app.exception
    texts = _texts(app)
    assert "確認の後にデータが変わったため、取り込みませんでした" in texts
    assert "取込前のバックアップは作成しましたが、データは変更していません" in texts
    backups = _backups(data_dir)
    assert len(backups) == 1
    assert backups[0].name in texts
    assert str(data_dir) not in texts
    assert "E013" in str(_table(app, "行番号"))
    assert _count(data_dir, "employee") == 0
    assert _import_logs(data_dir) == []


def test_no_header_error_shows_hint(data_dir: Path) -> None:
    """見出し行がない（どの列にも一致しない）ときは、ファイル全体の `E002` と直し方を出す。"""
    app = _check(_upload(_new_app(), _csv(_TARO, _HANAKO)))

    assert not app.exception
    issues = _table(app, "行番号")
    assert issues == [
        {
            "行番号": "－",
            "項目": "－",
            "原因": ImportErrorCode.NO_HEADER.value + " " + ImportErrorCode.NO_HEADER.description,
        }
    ]
    texts = _texts(app)
    assert "1行目が見出し行（列名）になっているか" in texts
    assert "山田" not in texts
    assert "taro" not in texts


def test_ambiguous_pay_item_column_shows_hint(data_dir: Path) -> None:
    """給与項目の列が1件に決まらないときは、見出し行の `E015` と、給与項目の見直しを案内する。"""
    with open_connection(data_dir / "crm-g.db") as connection:
        for code in ("COMMUTE_A", "COMMUTE_B"):
            connection.execute(
                "INSERT INTO pay_item (code, name, category, in_monthly_salary, cost_direct, "
                "sort_order, valid_from, created_at, updated_at) "
                "VALUES (?, '通勤手当', '固定支給', 1, 0, 99, '2020-04-01', ?, ?)",
                (code, "2026-10-27T00:00:00.000000Z", "2026-10-27T00:00:00.000000Z"),
            )
        connection.commit()
    content = _csv(
        f"{_SALARY_HEADER},通勤手当", "山田 太郎,2025/04/01,通常評価制度,正社員,入社,1,2"
    )
    app = _check(_upload(_select_kind(_new_app(), ImportKind.SALARY_HISTORY), content))

    assert not app.exception
    issues = _table(app, "行番号")
    assert {
        "行番号": "1",
        "項目": "通勤手当",
        "原因": "E015 " + ImportErrorCode.REFERENCE_NOT_RESOLVED.description,
    } in issues
    assert "どの給与項目の列か決まりません" in _texts(app)

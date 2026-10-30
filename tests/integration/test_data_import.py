"""データ取込の確認・実行と、`IMP-01` 社員の取込の試験（移行設計書 `MIG-040`、`MIG-050`〜）。

テスト計画書の `TC-MIG-018`〜`TC-MIG-021`、`TC-MIG-039`〜`TC-MIG-042`、`TC-MIG-044`、
`TC-MIG-045`、`TC-MIG-049` を含む。CSV は試験の中でバイト列として組み立てる。値はすべて架空である。
"""

import logging
import shutil
import sqlite3
from collections.abc import Iterator, Sequence
from datetime import date
from pathlib import Path

import pytest

from crm_g.application import data_import
from crm_g.application.data_import import (
    ERROR_DISPLAY_LIMIT,
    ImportInTransactionError,
    ImportKind,
    ImportKindDefinition,
    ImportRolledBackError,
    InsertOutcome,
    RecalculationRequest,
    ValidatedImport,
    check_import,
    run_import,
)
from crm_g.application.import_common import ImportErrorCode, ImportIssue
from crm_g.application.operation_log import (
    Operation,
    OperationLogDetail,
    record_operation_log,
)
from crm_g.infrastructure.backup import BackupWriteError, restore_database, write_backup
from crm_g.infrastructure.db import open_connection
from crm_g.infrastructure.migration import apply_migrations

_DDL_DIR = Path(__file__).resolve().parents[2] / "DDL"
_TIMESTAMP = "2026-10-27T00:00:00.000000Z"
_TODAY = date(2026, 10, 27)
_HEADER = (
    "メールアドレス,社員番号,氏名,氏名カナ,雇用形態,生年月日,性別,入社日,"
    "最寄駅,居住エリア,職種,在籍ステータス,退職日"
)
_TARO = (
    "Taro.Yamada@Example.com,E0001,山田 太郎,ヤマダ タロウ,正社員,1990/04/01,男性,"
    "2020/04/01,東京駅,東京都,SE,在籍,"
)
_HANAKO = "hanako.suzuki@example.com,E0002,鈴木 花子,,契約社員,,女性,2021-4-1,,,,退職,2025/3/31"
_EMPLOYEE = ImportKind.EMPLOYEE


def _csv(*lines: str, header: str = _HEADER) -> bytes:
    """見出しと行から、取込用 CSV（UTF-8、CRLF）のバイト列を作る。"""
    return "\r\n".join((header, *lines)).encode("utf-8") + b"\r\n"


@pytest.fixture(scope="module")
def migrated_db(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """マイグレーションを適用した DB を、試験のモジュールで1回だけ作る（複写の元）。"""
    directory = tmp_path_factory.mktemp("migrated")
    path = directory / "template.db"
    with open_connection(path) as connection:
        apply_migrations(connection, _DDL_DIR, path, directory / "migration-backups")
    return path


@pytest.fixture
def db_path(tmp_path: Path, migrated_db: Path) -> Path:
    """マイグレーションを適用した一時 DB（複写）を作る。"""
    path = tmp_path / "test.db"
    shutil.copyfile(migrated_db, path)
    return path


@pytest.fixture
def connection(db_path: Path) -> Iterator[sqlite3.Connection]:
    """一時 DB への接続。"""
    with open_connection(db_path) as connection:
        yield connection


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


def _run(connection: sqlite3.Connection, db_path: Path, content: bytes) -> data_import.ImportReport:
    """取込の実行を、試験用のバックアップの作成先で呼ぶ。"""
    return run_import(
        connection,
        _EMPLOYEE,
        content,
        _TODAY,
        db_path=db_path,
        backup_dir=db_path.parent / "backups",
    )


def _issues(report: data_import.ImportReport) -> list[tuple[int | None, str | None, str]]:
    """エラーを、行番号・列名・コードの組にする。"""
    return [(issue.line_number, issue.column, issue.code.value) for issue in report.issues]


def _employees(connection: sqlite3.Connection) -> list[tuple[object, ...]]:
    """社員を、メールアドレスの順に返す。"""
    rows: list[tuple[object, ...]] = connection.execute(
        "SELECT email, employee_no, name, name_kana, employment_type, birth_date, gender, "
        "join_date, nearest_station, residence_area, job_type, enrollment_status, retire_date "
        "FROM employee ORDER BY email"
    ).fetchall()
    return rows


def _operation_logs(connection: sqlite3.Connection) -> list[tuple[object, ...]]:
    """操作ログを、記録の順に返す。"""
    rows: list[tuple[object, ...]] = connection.execute(
        "SELECT operation, target_table, target_key, detail, before_value, after_value "
        "FROM operation_log ORDER BY id"
    ).fetchall()
    return rows


def _insert_employee(connection: sqlite3.Connection, email: str, employee_no: str | None) -> None:
    """DB に、既存の社員を1人入れる。"""
    connection.execute(
        "INSERT INTO employee (email, employee_no, name, enrollment_status, join_date, "
        "created_at, updated_at) VALUES (?, ?, '既存 社員', '在籍', '2019-04-01', ?, ?)",
        (email, employee_no, _TIMESTAMP, _TIMESTAMP),
    )
    connection.commit()


# ---------------------------------------------------------------------------
# 取込の実行（正常系）
# ---------------------------------------------------------------------------


def test_run_import_inserts_employees_with_normalized_values(
    connection: sqlite3.Connection, db_path: Path
) -> None:
    """検証を通った CSV を取り込む。メールアドレスは小文字、日付は `YYYY-MM-DD` で保存する。"""
    report = _run(connection, db_path, _csv(_TARO, _HANAKO))

    assert report.issues == ()
    assert (report.executed, report.row_count, report.imported_count) == (True, 2, 2)
    assert (report.recalculation, report.recalculated_month_count) == (None, 0)
    assert report.calculation_error_count == 0
    assert report.matched_row_count is None
    assert report.unmatched == ()
    assert report.backup_path is not None
    assert report.backup_path.exists()
    assert _employees(connection) == [
        (
            "hanako.suzuki@example.com",
            "E0002",
            "鈴木 花子",
            None,
            "契約社員",
            None,
            "女性",
            "2021-04-01",
            None,
            None,
            None,
            "退職",
            "2025-03-31",
        ),
        (
            "taro.yamada@example.com",
            "E0001",
            "山田 太郎",
            "ヤマダ タロウ",
            "正社員",
            "1990-04-01",
            "男性",
            "2020-04-01",
            "東京駅",
            "東京都",
            "SE",
            "在籍",
            None,
        ),
    ]


def test_tc_mig_044_operation_log_has_one_import_row_with_counts_only(
    connection: sqlite3.Connection, db_path: Path
) -> None:
    """操作ログに `取込` が1件。`detail` は件数だけで、氏名・メールアドレスを含まない。"""
    _run(connection, db_path, _csv(_TARO, _HANAKO))

    assert _operation_logs(connection) == [
        (
            "取込",
            "employee",
            None,
            "種類=employee 行数=2 再計算した月数=0 計算エラー=0",
            None,
            None,
        )
    ]


def test_tc_mig_049_formula_like_name_is_stored_as_is(
    connection: sqlite3.Connection, db_path: Path
) -> None:
    """`=` で始まる氏名は、数式として扱わず、そのまま保存する（`'` を付けない）。"""
    _run(connection, db_path, _csv("formula@example.com,,=SUM(A1),,,,,2020/4/1,,,,在籍,"))

    row = connection.execute("SELECT name FROM employee").fetchone()
    assert row == ("=SUM(A1)",)


def test_only_required_columns_and_normalized_choices_are_accepted(
    connection: sqlite3.Connection, db_path: Path
) -> None:
    """必須の列だけでも取り込める。区分値・メールアドレスは、NFKC 正規化して照合する。"""
    header = "氏名,メールアドレス,入社日,在籍ステータス"
    content = _csv("\u3000山田 太郎\u3000,ｔａｒｏ＠example.com,２０２０/４/１,在籍", header=header)

    report = _run(connection, db_path, content)

    assert report.issues == ()
    row = connection.execute("SELECT email, name, join_date, employee_no FROM employee").fetchone()
    assert row == ("taro@example.com", "山田 太郎", "2020-04-01", None)


def test_excluded_column_is_reported_and_not_stored(
    connection: sqlite3.Connection, db_path: Path
) -> None:
    """除外列（`パスワード`）は読み捨て、列名を「取り込まなかった列」に出す。"""
    header = "メールアドレス,氏名,入社日,在籍ステータス,パスワード"
    content = _csv("taro@example.com,山田 太郎,2020/4/1,在籍,secret-value", header=header)

    report = _run(connection, db_path, content)

    assert report.excluded_columns == ("パスワード",)
    assert report.imported_count == 1
    dump = "\n".join(connection.iterdump())
    assert "secret-value" not in dump


# ---------------------------------------------------------------------------
# 検証のエラー
# ---------------------------------------------------------------------------


def test_tc_mig_019_empty_join_date_is_e006(connection: sqlite3.Connection, db_path: Path) -> None:
    """入社日が空の行は `E006`。何も取り込まない。"""
    report = _run(connection, db_path, _csv(_TARO, "jiro@example.com,,鈴木 次郎,,,,,,,,,在籍,"))

    assert _issues(report) == [(3, "入社日", "E006")]
    assert report.executed is False
    assert report.imported_count == 0
    assert _employees(connection) == []
    assert _operation_logs(connection) == []


def test_tc_mig_020_same_email_twice_in_file_is_e012(
    connection: sqlite3.Connection, db_path: Path
) -> None:
    """同じメールアドレス（大文字小文字の違いを含む）の行が2行あると、2行目以降が `E012`。"""
    report = _run(
        connection,
        db_path,
        _csv(
            "taro@example.com,,山田 太郎,,,,,2020/4/1,,,,在籍,",
            "TARO@example.com,,山田 太郎,,,,,2020/4/1,,,,在籍,",
        ),
    )

    assert _issues(report) == [(3, "メールアドレス", "E012")]
    assert _employees(connection) == []


def test_same_employee_no_twice_in_file_is_e012(connection: sqlite3.Connection) -> None:
    """社員番号が、ファイル内で重複すると `E012`（`UID-072`）。"""
    report = check_import(
        connection,
        _EMPLOYEE,
        _csv(
            "a@example.com,E0001,山田 太郎,,,,,2020/4/1,,,,在籍,",
            "b@example.com,Ｅ０００１,鈴木 花子,,,,,2020/4/1,,,,在籍,",
        ),
        _TODAY,
    )

    assert _issues(report) == [(3, "社員番号", "E012")]


def test_tc_mig_021_reimporting_the_same_csv_is_e013(
    connection: sqlite3.Connection, db_path: Path
) -> None:
    """取り込み済みの社員を、同じ CSV でもう一度取り込むと `E013`。何も追加されない。"""
    content = _csv(_TARO, _HANAKO)
    _run(connection, db_path, content)

    report = _run(connection, db_path, content)

    assert _issues(report) == [
        (2, "メールアドレス", "E013"),
        (2, "社員番号", "E013"),
        (3, "メールアドレス", "E013"),
        (3, "社員番号", "E013"),
    ]
    assert report.executed is False
    assert len(_employees(connection)) == 2
    assert len(_operation_logs(connection)) == 1


def test_employee_no_existing_in_db_is_e013(connection: sqlite3.Connection) -> None:
    """社員番号が、DB の社員（退職者を含む）と重複すると `E013`。"""
    _insert_employee(connection, "other@example.com", "E0001")

    report = check_import(connection, _EMPLOYEE, _csv(_TARO), _TODAY)

    assert _issues(report) == [(2, "社員番号", "E013")]


def test_db_keys_are_normalized_like_csv_before_comparison(
    connection: sqlite3.Connection,
) -> None:
    """画面で保存した、正規化していない DB の値（全角・後ろの空白・全角のメール）も `E013`。"""
    _insert_employee(connection, "a@example.com", "\uff25\uff10\uff10\uff10\uff11")
    _insert_employee(connection, "b@example.com", "E0002 ")
    _insert_employee(connection, "\uff43@example.com", None)
    # 正規化すると空になる社員番号は、照合のキーにしない。
    _insert_employee(connection, "d@example.com", "\u3000")

    report = check_import(
        connection,
        _EMPLOYEE,
        _csv(
            "x@example.com,E0001,山田 太郎,,,,,2020/4/1,,,,在籍,",
            "y@example.com,E0002,鈴木 花子,,,,,2020/4/1,,,,在籍,",
            "C@example.com,,佐藤 次郎,,,,,2020/4/1,,,,在籍,",
        ),
        _TODAY,
    )

    assert _issues(report) == [
        (2, "社員番号", "E013"),
        (3, "社員番号", "E013"),
        (4, "メールアドレス", "E013"),
    ]


@pytest.mark.parametrize(
    ("line", "expected"),
    [
        ("no-at-mark.example.com,,山田 太郎,,,,,2020/4/1,,,,在籍,", (2, "メールアドレス", "E007")),
        ("a@b@example.com,,山田 太郎,,,,,2020/4/1,,,,在籍,", (2, "メールアドレス", "E007")),
        ("a b@example.com,,山田 太郎,,,,,2020/4/1,,,,在籍,", (2, "メールアドレス", "E007")),
        (",,山田 太郎,,,,,2020/4/1,,,,在籍,", (2, "メールアドレス", "E006")),
        ("a@example.com,,,,,,,2020/4/1,,,,在籍,", (2, "氏名", "E006")),
        ("a@example.com,,山田 太郎,,パート,,,2020/4/1,,,,在籍,", (2, "雇用形態", "E008")),
        ("a@example.com,,山田 太郎,,,,不明,2020/4/1,,,,在籍,", (2, "性別", "E008")),
        ("a@example.com,,山田 太郎,,,,,2020/4/1,,,,休職,", (2, "在籍ステータス", "E008")),
        ("a@example.com,,山田 太郎,,,,,2020/4/1,,,,,", (2, "在籍ステータス", "E006")),
        ("a@example.com,,山田 太郎,,,,,2020/13/01,,,,在籍,", (2, "入社日", "E007")),
        ("a@example.com,,山田 太郎,,,2026/10/28,,2020/4/1,,,,在籍,", (2, "生年月日", "E009")),
        ("a@example.com,,山田 太郎,,,2026/10/27,,2020/4/1,,,,在籍,", (2, "生年月日", "E009")),
        ("a@example.com,,山田 太郎,,,1990-02-30,,2020/4/1,,,,在籍,", (2, "生年月日", "E007")),
        ("a@example.com,,山田 太郎,,,,,2020/4/1,,,,退職,2020/3/31", (2, "退職日", "E016")),
        ("a@example.com,,山田 太郎,,,,,2020/4/1,,,,退職,2020/3/32", (2, "退職日", "E007")),
    ],
)
def test_row_validation_errors(
    connection: sqlite3.Connection,
    line: str,
    expected: tuple[int, str, str],
) -> None:
    """行ごとの検証（形式・必須・選択肢・範囲・組み合わせ）。"""
    report = check_import(connection, _EMPLOYEE, _csv(line), _TODAY)

    assert _issues(report) == [expected]


def test_boundary_values_are_accepted(connection: sqlite3.Connection) -> None:
    """生年月日が今日の前日、退職日が入社日と同じ日は、エラーにしない。

    生年月日が今日以降は `E009`（画面 `SCN-022` の「今日より前」に合わせる）。
    """
    line = "a@example.com,,山田 太郎,,,2026/10/26,その他,2020/4/1,,,,退職,2020/4/1"

    report = check_import(connection, _EMPLOYEE, _csv(line), _TODAY)

    assert report.issues == ()


def test_all_errors_are_collected_in_line_order(connection: sqlite3.Connection) -> None:
    """最初の1件で止めずに、すべてのエラーを行番号の順に集める（`MIG-050`）。"""
    report = check_import(
        connection,
        _EMPLOYEE,
        _csv(
            "a@example.com,,山田 太郎,,,,,,,,,在籍,",
            "bad,,,,,,,2020/4/1,,,,在籍,",
            "a@example.com,,鈴木 花子,,,,,2020/4/1,,,,在籍,,extra",
        ),
        _TODAY,
    )

    assert _issues(report) == [
        (2, "入社日", "E006"),
        (3, "メールアドレス", "E007"),
        (3, "氏名", "E006"),
        (4, None, "E004"),
        (4, "メールアドレス", "E012"),
    ]


def test_file_and_header_errors_are_reported_without_rows(connection: sqlite3.Connection) -> None:
    """ファイル全体（`E001`）・見出し（`E003`）のエラーは、行を読まない。"""
    shift_jis = _HEADER.encode("cp932") + b"\r\n"
    not_utf8 = check_import(connection, _EMPLOYEE, shift_jis, _TODAY)
    missing = check_import(
        connection, _EMPLOYEE, _csv("a@example.com,山田 太郎", header="メールアドレス,氏名"), _TODAY
    )

    assert (_issues(not_utf8), not_utf8.row_count) == ([(None, None, "E001")], 0)
    assert _issues(missing) == [(1, "入社日", "E003"), (1, "在籍ステータス", "E003")]
    assert missing.row_count == 0


def test_tc_mig_018_error_list_has_no_values(
    connection: sqlite3.Connection,
    db_path: Path,
    captured_log: pytest.LogCaptureFixture,
) -> None:
    """エラーの一覧とログには、行番号・項目・種類だけが出る。氏名・メールアドレスの値が出ない。"""
    content = _csv(
        "secret.person@example.com,,秘密 太郎,,派遣,,,,,,,在籍,",
        "secret.person@example.com,,秘密 太郎,,,2999/1/1,,2020/4/1,,,,在籍,",
    )

    report = _run(connection, db_path, content)

    assert all(isinstance(issue, ImportIssue) for issue in report.issues)
    assert {issue.code for issue in report.issues} == {
        ImportErrorCode.NOT_IN_CHOICES,
        ImportErrorCode.REQUIRED_EMPTY,
        ImportErrorCode.OUT_OF_RANGE,
        ImportErrorCode.DUPLICATE_KEY_IN_FILE,
    }
    shown = repr(report.issues) + captured_log.text
    for value in ("secret.person", "秘密", "派遣", "2999"):
        assert value not in shown
    assert "エラー=4" in captured_log.text


def test_displayed_issues_are_limited_and_total_is_kept(connection: sqlite3.Connection) -> None:
    """エラーの表示は先頭の1,000件まで。総数は別に持つ（`MGD-008`）。"""
    lines = [f"p{number}@example.com,,氏名,,,,,,,,,在籍," for number in range(1001)]

    report = check_import(connection, _EMPLOYEE, _csv(*lines), _TODAY)

    assert report.error_count == 1001
    assert len(report.displayed_issues) == ERROR_DISPLAY_LIMIT
    assert report.has_errors


# ---------------------------------------------------------------------------
# 確認の実行・トランザクション・バックアップ
# ---------------------------------------------------------------------------


def test_tc_mig_039_check_does_not_change_db(connection: sqlite3.Connection, db_path: Path) -> None:
    """確認の実行は、DB を変えない。操作ログも増えない。バックアップも作らない。"""
    changes_before = connection.total_changes

    report = check_import(connection, _EMPLOYEE, _csv(_TARO, _HANAKO), _TODAY)

    assert (report.issues, report.row_count, report.executed) == ((), 2, False)
    assert report.backup_path is None
    assert connection.total_changes == changes_before
    assert _employees(connection) == []
    assert _operation_logs(connection) == []
    assert not (db_path.parent / "backups").exists()


def test_run_with_errors_makes_no_backup(connection: sqlite3.Connection, db_path: Path) -> None:
    """検証のエラーがあるときは、バックアップを作らずに終わる（`MIG-050` の流れ）。"""
    report = _run(connection, db_path, _csv("a@example.com,,山田 太郎,,,,,,,,,在籍,"))

    assert report.backup_path is None
    assert not (db_path.parent / "backups").exists()


def test_tc_mig_040_exception_rolls_back_data_and_operation_log(
    connection: sqlite3.Connection,
    db_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    captured_log: pytest.LogCaptureFixture,
) -> None:
    """取込の実行の途中の例外で、業務データも操作ログもロールバックされ、例外が送出される。

    取込前のバックアップを作った後の例外は、`ImportRolledBackError`（元の例外を `__cause__` に
    持つ）になり、取込前のバックアップのパスを持つ。ログは、元の例外の型名を1回だけ出す。
    """

    def record_then_fail(
        target: sqlite3.Connection,
        operation: Operation,
        target_table: str,
        detail: OperationLogDetail | None = None,
    ) -> None:
        record_operation_log(target, operation, target_table, detail)
        raise RuntimeError("試験のための失敗")

    monkeypatch.setattr(data_import, "record_operation_log", record_then_fail)

    with pytest.raises(ImportRolledBackError) as raised:
        _run(connection, db_path, _csv(_TARO, _HANAKO))

    assert isinstance(raised.value.__cause__, RuntimeError)
    assert raised.value.backup_path.is_file()
    assert raised.value.backup_path.parent == db_path.parent / "backups"
    assert not connection.in_transaction
    assert _employees(connection) == []
    assert _operation_logs(connection) == []
    assert _error_messages(captured_log) == [
        "取込に失敗し、ロールバックしました（種類=employee 例外=RuntimeError）"
    ]
    assert "taro" not in captured_log.text.lower()


def test_tc_mig_041_backup_failure_prevents_import(
    connection: sqlite3.Connection,
    db_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    captured_log: pytest.LogCaptureFixture,
) -> None:
    """取込前のバックアップを作れないときは、取り込まない（`MIG-053`）。

    作りかけのファイルは残らない。ログには例外の種類だけが出て、文言にパスを含まない。
    """
    backup_dir = db_path.parent / "secret-backup-folder"

    def fail_copy(source: Path, destination: Path) -> None:
        # 作りかけのファイルができた後で、書き込みに失敗した状態にする。
        destination.write_bytes(b"partial")
        raise OSError(f"書き込めません: {destination}")

    monkeypatch.setattr("crm_g.infrastructure.backup._copy_database", fail_copy)

    with pytest.raises(BackupWriteError) as raised:
        run_import(
            connection,
            _EMPLOYEE,
            _csv(_TARO),
            _TODAY,
            db_path=db_path,
            backup_dir=backup_dir,
        )

    assert "secret-backup-folder" not in str(raised.value)
    assert list(backup_dir.iterdir()) == []
    assert not connection.in_transaction
    assert _employees(connection) == []
    assert _operation_logs(connection) == []
    error_messages = [
        record.getMessage() for record in captured_log.records if record.levelno == logging.ERROR
    ]
    assert any("例外=BackupWriteError" in message for message in error_messages)
    assert "secret-backup-folder" not in captured_log.text
    assert "taro" not in captured_log.text.lower()


def test_backup_is_made_while_holding_the_write_lock(
    connection: sqlite3.Connection, db_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """取込前のバックアップは、`BEGIN IMMEDIATE` の後（ほかの接続が書き込めない間）に作る。"""
    lock_states: list[bool] = []

    def backup_under_lock(db_file: Path, directory: Path, *, create_directory: bool) -> Path:
        other = sqlite3.connect(db_file, timeout=0)
        try:
            other.execute("BEGIN IMMEDIATE")
        except sqlite3.OperationalError:
            lock_states.append(True)
        else:  # pragma: no cover 失敗したときだけ通る
            other.rollback()
            lock_states.append(False)
        finally:
            other.close()
        return write_backup(db_file, directory, create_directory=create_directory)

    monkeypatch.setattr(data_import, "write_backup", backup_under_lock)

    report = _run(connection, db_path, _csv(_TARO))

    assert lock_states == [True]
    assert report.executed is True
    assert report.backup_path is not None
    with open_connection(report.backup_path) as backup:
        assert _employees(backup) == []


def test_run_import_inside_transaction_is_rejected(
    connection: sqlite3.Connection, db_path: Path, captured_log: pytest.LogCaptureFixture
) -> None:
    """呼び出し側のトランザクションの中では、取込の実行を拒む（ロールバックできないため）。

    拒んだことは、例外の型名だけでログ（ERROR）に出す。
    """
    connection.execute("BEGIN")
    try:
        with pytest.raises(ImportInTransactionError) as raised:
            _run(connection, db_path, _csv(_TARO))
    finally:
        connection.rollback()

    assert "taro" not in str(raised.value).lower()
    assert _employees(connection) == []
    assert not (db_path.parent / "backups").exists()
    assert _error_messages(captured_log) == [
        "取込を開始できませんでした（種類=employee 例外=ImportInTransactionError）"
    ]


def _error_messages(captured_log: pytest.LogCaptureFixture) -> list[str]:
    """ERROR のログのメッセージ（ログの出力の回数を数えるため、同じ記録は1回だけ数える）。

    `captured_log` は、伝播の設定によっては、同じ記録を2回捕まえるため。
    """
    records = {id(record): record for record in captured_log.records}.values()
    return [record.getMessage() for record in records if record.levelno == logging.ERROR]


def _register_failing_validator(monkeypatch: pytest.MonkeyPatch) -> None:
    """社員の検証が、値を含む文言の例外で止まるようにする。"""

    def fail(_connection: sqlite3.Connection, _content: bytes, _today: date) -> ValidatedImport:
        raise ValueError("山田 太郎 の値を含む擬似的な失敗")

    monkeypatch.setitem(
        data_import._DEFINITIONS,
        ImportKind.EMPLOYEE,
        ImportKindDefinition(label="社員", target_table="employee", validate=fail),
    )


def test_check_exception_is_logged_with_type_only(
    connection: sqlite3.Connection,
    monkeypatch: pytest.MonkeyPatch,
    captured_log: pytest.LogCaptureFixture,
) -> None:
    """確認の実行の、検証の途中の例外は、型名だけを1回ログに出して、再送出する（`MIG-056`）。"""
    _register_failing_validator(monkeypatch)

    with pytest.raises(ValueError, match="擬似的な失敗"):
        check_import(connection, _EMPLOYEE, _csv(_TARO), _TODAY)

    assert _error_messages(captured_log) == [
        "取込の検証の途中で失敗しました（種類=employee 例外=ValueError）"
    ]
    assert "山田" not in captured_log.text


def test_run_validation_exception_before_transaction_is_logged_once(
    connection: sqlite3.Connection,
    db_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    captured_log: pytest.LogCaptureFixture,
) -> None:
    """取込の実行の、トランザクションの前の検証の例外は、型名だけを1回ログに出して、再送出する。

    取込前のバックアップは作らない（元の例外のまま送出する）。
    """
    _register_failing_validator(monkeypatch)

    with pytest.raises(ValueError, match="擬似的な失敗"):
        _run(connection, db_path, _csv(_TARO))

    assert _error_messages(captured_log) == [
        "取込の検証の途中で失敗しました（種類=employee 例外=ValueError）"
    ]
    assert "山田" not in captured_log.text
    assert not (db_path.parent / "backups").exists()
    assert not connection.in_transaction


def test_tc_mig_042_restore_from_backup_returns_to_state_before_import(
    db_path: Path,
) -> None:
    """取り込みの後に、取込前のバックアップから復元すると、取り込み前の状態に戻る。

    復元は、画面（`SCN-065`）と同じ `restore_database` で行う。
    """
    with open_connection(db_path) as connection:
        _insert_employee(connection, "before@example.com", None)
        report = _run(connection, db_path, _csv(_TARO, _HANAKO))
        assert len(_employees(connection)) == 3
    assert report.backup_path is not None

    restore_database(report.backup_path, db_path)

    with open_connection(db_path) as connection:
        assert [row[0] for row in _employees(connection)] == ["before@example.com"]
        assert _operation_logs(connection) == []


def test_tc_mig_045_revalidation_in_transaction_reads_db_again(
    connection: sqlite3.Connection, db_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """確認の後（実行の前）に DB が変わると、トランザクションの中の再検証で `E013` になる。"""
    content = _csv(_TARO, _HANAKO)
    assert check_import(connection, _EMPLOYEE, content, _TODAY).issues == ()

    original = data_import._DEFINITIONS[ImportKind.EMPLOYEE]
    calls: list[int] = []

    def validate_then_change_db(
        target: sqlite3.Connection, csv_content: bytes, today: date
    ) -> ValidatedImport:
        validated = original.validate(target, csv_content, today)
        calls.append(1)
        if len(calls) == 1:
            # 実行の検証からトランザクションの開始までの間に、別の取り込みが入った状態にする。
            _insert_employee(connection, "taro.yamada@example.com", None)
        return validated

    monkeypatch.setitem(
        data_import._DEFINITIONS,
        ImportKind.EMPLOYEE,
        ImportKindDefinition(
            label="社員", target_table="employee", validate=validate_then_change_db
        ),
    )

    report = _run(connection, db_path, content)

    assert _issues(report) == [(2, "メールアドレス", "E013")]
    assert report.executed is False
    assert report.backup_path is not None
    assert not connection.in_transaction
    assert [row[0] for row in _employees(connection)] == ["taro.yamada@example.com"]
    assert _operation_logs(connection) == []


# ---------------------------------------------------------------------------
# 種類の登録表と、取込の後の再計算（後続の種類で使う枠組み）
# ---------------------------------------------------------------------------


def test_kind_label_and_identifier() -> None:
    """取込の種類の識別子と、画面の名称（`MIG-010`）。"""
    assert (ImportKind("employee"), ImportKind.EMPLOYEE.label) == (ImportKind.EMPLOYEE, "社員")


def _register_recalculating_kind(
    monkeypatch: pytest.MonkeyPatch, employee_ids: Sequence[int] | None
) -> None:
    """社員の検証を使い、投入の後に再計算を求める、試験用の種類に差し替える。

    `employee_ids` が `None` なら、取り込んだ全社員を再計算の対象にする。
    """
    original = data_import._DEFINITIONS[ImportKind.EMPLOYEE]

    def validate(connection: sqlite3.Connection, content: bytes, today: date) -> ValidatedImport:
        validated = original.validate(connection, content, today)

        def insert(target: sqlite3.Connection) -> InsertOutcome:
            outcome = validated.insert(target)
            ids = employee_ids
            if ids is None:
                ids = tuple(row[0] for row in target.execute("SELECT id FROM employee"))
            return InsertOutcome(
                outcome.imported_count, RecalculationRequest(tuple(ids), date(2026, 8, 1))
            )

        return ValidatedImport(
            row_count=validated.row_count,
            excluded_columns=validated.excluded_columns,
            issues=validated.issues,
            insert=insert,
            matches=(),
        )

    monkeypatch.setitem(
        data_import._DEFINITIONS,
        ImportKind.EMPLOYEE,
        ImportKindDefinition(label="試験", target_table="employee", validate=validate),
    )


def test_recalculation_after_insert_is_counted_and_committed(
    connection: sqlite3.Connection, db_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """投入の後に、最も早い月から今月まで再計算する。計算エラーがあってもコミットする。

    営業日カレンダーがないため、3か月（8〜10月）すべてが計算エラーになる（`MIG-052`）。
    """
    _register_recalculating_kind(monkeypatch, None)

    report = _run(connection, db_path, _csv(_TARO))

    assert report.executed is True
    assert report.matched_row_count == 0
    assert (report.recalculated_month_count, report.calculation_error_count) == (3, 3)
    assert len(_employees(connection)) == 1
    assert _operation_logs(connection)[0][3] == (
        "種類=employee 行数=1 再計算した月数=3 計算エラー=3"
    )


def test_recalculation_is_skipped_when_no_employee_is_targeted(
    connection: sqlite3.Connection, db_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """再計算の対象の社員がいなければ、再計算しない。"""
    _register_recalculating_kind(monkeypatch, ())

    report = _run(connection, db_path, _csv(_TARO))

    assert report.recalculation is None
    assert _operation_logs(connection)[0][3] == (
        "種類=employee 行数=1 再計算した月数=0 計算エラー=0"
    )

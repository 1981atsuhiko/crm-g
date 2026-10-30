"""移行コマンドの試験（移行設計書 8.1 `MIG-070`、テスト計画書 `TC-MIG-046`・`TC-MIG-047`）。

DB は、一時ディレクトリ（`LOCALAPPDATA` を差し替える）に作る。CSV の値はすべて架空である。
"""

import os
import shutil
import sqlite3
import subprocess
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import replace
from datetime import date
from pathlib import Path

import pytest

import crm_g.application.data_import as data_import_app
from crm_g.application.data_import import (
    ERROR_DISPLAY_LIMIT,
    ImportKind,
    ImportKindDefinition,
    ImportReport,
    ValidatedImport,
)
from crm_g.application.import_common import ImportErrorCode, ImportIssue
from crm_g.application.import_matching import UnmatchedPerson
from crm_g.application.recalculation import (
    CalculationError,
    CalculationErrorReason,
    RecalculationResult,
)
from crm_g.cli import import_command
from crm_g.cli.launcher import main
from crm_g.infrastructure.db import open_connection
from crm_g.infrastructure.migration import apply_migrations
from crm_g.infrastructure.single_instance import LOCK_FILE_NAME, acquire_lock

_REPO_ROOT = Path(__file__).resolve().parents[2]
_DDL_DIR = _REPO_ROOT / "DDL"
_SRC_DIR = _REPO_ROOT / "src"
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
_SUBPROCESS_TIMEOUT_SECONDS = 120


def _csv(header: str, *lines: str) -> bytes:
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
def data_dir(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, migrated_db: Path) -> Path:
    """`get_app_paths()` が一時ディレクトリを指すようにし、最新の版の DB を置く。"""
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    path = tmp_path / "crm-g"
    path.mkdir()
    shutil.copyfile(migrated_db, path / "crm-g.db")
    return path


def _write_csv(directory: Path, name: str, content: bytes) -> Path:
    path = directory / name
    path.write_bytes(content)
    return path


def _employee_count(data_dir: Path) -> int:
    with open_connection(data_dir / "crm-g.db") as connection:
        row = connection.execute("SELECT COUNT(*) FROM employee").fetchone()
    return int(row[0])


def _operation_count(data_dir: Path) -> int:
    with open_connection(data_dir / "crm-g.db") as connection:
        row = connection.execute("SELECT COUNT(*) FROM operation_log").fetchone()
    return int(row[0])


def _backups(data_dir: Path) -> list[Path]:
    return sorted((data_dir / "backups").glob("*.db"))


def _log_text(data_dir: Path) -> str:
    log_path = data_dir / "logs" / "crm-g.log"
    return log_path.read_text(encoding="utf-8") if log_path.exists() else ""


def test_tc_mig_046_check_without_errors_does_not_change_db(
    data_dir: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """`--check` は DB を変えず、エラーがなければ終了コード 0（`TC-MIG-046`）。"""
    csv_path = _write_csv(tmp_path, "employee.csv", _csv(_EMPLOYEE_HEADER, _TARO, _HANAKO))
    before = (data_dir / "crm-g.db").read_bytes()

    exit_code = main(["import", "employee", str(csv_path), "--check"])

    output = capsys.readouterr().out
    assert exit_code == 0
    assert (data_dir / "crm-g.db").read_bytes() == before
    assert _backups(data_dir) == []
    assert "取込の種類: 社員（employee）" in output
    assert "処理: 確認の実行" in output
    assert "読み込んだ行数: 2" in output
    assert "取り込まなかった列: パスワード" in output
    assert "エラー: 0 件" in output
    assert "DB は変更していません" in output
    # 除外列の値は、出力にもログにも出さない（REQ-MIG-006）。
    assert "secret-value" not in output
    assert "secret-value" not in _log_text(data_dir)


def test_tc_mig_046_check_with_errors_exits_with_1(
    data_dir: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """`--check` でエラーがあれば終了コード 1。行番号・項目・原因だけを出す（値は出さない）。"""
    broken = _TARO.replace("2020/04/01", "")
    csv_path = _write_csv(tmp_path, "employee.csv", _csv(_EMPLOYEE_HEADER, broken))
    before = (data_dir / "crm-g.db").read_bytes()

    exit_code = main(["import", "--check", "employee", str(csv_path)])

    output = capsys.readouterr().out
    assert exit_code == 1
    assert (data_dir / "crm-g.db").read_bytes() == before
    assert "エラー: 1 件" in output
    assert "行 2\t入社日\tE006 必須の値が空" in output
    assert "取り込みませんでした" in output
    assert "山田" not in output


def test_import_inserts_rows_and_reports_backup(
    data_dir: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """`--check` がなければ、取り込む。取込前のバックアップのファイル名を出す（`MIG-053`）。"""
    csv_path = _write_csv(tmp_path, "employee.csv", _csv(_EMPLOYEE_HEADER, _TARO, _HANAKO))

    exit_code = main(["import", "employee", str(csv_path)])

    output = capsys.readouterr().out
    assert exit_code == 0
    assert _employee_count(data_dir) == 2
    assert _operation_count(data_dir) == 1
    backups = _backups(data_dir)
    assert len(backups) == 1
    assert "処理: 取込の実行" in output
    assert "取り込んだ行数: 2" in output
    assert "再計算した月数: 0" in output
    assert f"取込前のバックアップ: {backups[0].name}" in output
    assert "バックアップ・復元" in output
    # バックアップのフォルダのパスは出さない（ファイル名だけ）。
    assert str(data_dir) not in output


def test_import_with_errors_exits_with_1_and_imports_nothing(
    data_dir: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    broken = _HANAKO.replace("契約社員", "アルバイト")
    csv_path = _write_csv(tmp_path, "employee.csv", _csv(_EMPLOYEE_HEADER, _TARO, broken))

    exit_code = main(["import", "employee", str(csv_path)])

    output = capsys.readouterr().out
    assert exit_code == 1
    assert _employee_count(data_dir) == 0
    assert _backups(data_dir) == []
    assert "行 3\t雇用形態\tE008 値が選択肢にない" in output


def test_unmatched_people_are_printed_but_not_logged(
    data_dir: Path,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """一致しなかった人の氏名は、標準出力にだけ出す。ログには件数だけ（`MIG-005`、`MIG-032`）。"""
    employee_csv = _write_csv(tmp_path, "employee.csv", _csv(_EMPLOYEE_HEADER, _TARO))
    assert main(["import", "employee", str(employee_csv)]) == 0
    capsys.readouterr()
    salary_csv = _write_csv(
        tmp_path,
        "salary.csv",
        _csv(
            _SALARY_HEADER,
            "山田 太郎,2026/04/01,通常評価制度,正社員,入社,250000",
            f"{_UNKNOWN_NAME},2026/04/01,通常評価制度,正社員,入社,260000",
        ),
    )

    exit_code = main(["import", "salary-history", str(salary_csv), "--check"])

    output = capsys.readouterr().out
    assert exit_code == 1
    assert "名寄せで一致した行数: 1" in output
    assert "名寄せで一致しなかった人: 1 件" in output
    assert f"  {_UNKNOWN_NAME}\t-\t行 3\tE010 社員が見つからない" in output
    log_text = _log_text(data_dir)
    assert "一致しなかった人=1" in log_text
    assert _UNKNOWN_NAME not in log_text
    assert "260000" not in output


def test_import_with_recalculation_reports_months_and_calculation_errors(
    data_dir: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """給与履歴の取込は、再計算する。カレンダーが未作成の月は、計算のエラーとして数える。"""
    employee_csv = _write_csv(tmp_path, "employee.csv", _csv(_EMPLOYEE_HEADER, _TARO))
    assert main(["import", "employee", str(employee_csv)]) == 0
    capsys.readouterr()
    first_day = date.today().replace(day=1)
    salary_csv = _write_csv(
        tmp_path,
        "salary.csv",
        _csv(
            _SALARY_HEADER,
            f"山田 太郎,{first_day:%Y/%m/%d},通常評価制度,正社員,入社,250000",
        ),
    )

    exit_code = main(["import", "salary-history", str(salary_csv)])

    output = capsys.readouterr().out
    assert exit_code == 0
    assert "取り込んだ行数: 1" in output
    assert "再計算した月数: 1" in output
    assert "計算のエラー: 1 件" in output
    assert "月次給与計算" in output


def test_missing_db_exits_with_2_without_creating_it(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """DB がないときは、作らずに、アプリの起動を案内する（ユーザー承認 2026-10-27）。"""
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    csv_path = _write_csv(tmp_path, "employee.csv", _csv(_EMPLOYEE_HEADER, _TARO))

    exit_code = main(["import", "employee", str(csv_path)])

    assert exit_code == 2
    assert "アプリを一度起動してから" in capsys.readouterr().out
    assert not (tmp_path / "crm-g" / "crm-g.db").exists()


def test_pending_migration_exits_with_2_without_changing_db(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """未適用の連番SQLがあるときは、適用せずに（DB を変えずに）終了コード 2。"""
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    older_ddl = tmp_path / "older-ddl"
    older_ddl.mkdir()
    for sql_file in sorted(_DDL_DIR.glob("*.sql"))[:-1]:
        shutil.copyfile(sql_file, older_ddl / sql_file.name)
    data_dir = tmp_path / "crm-g"
    data_dir.mkdir()
    db_path = data_dir / "crm-g.db"
    with open_connection(db_path) as connection:
        apply_migrations(connection, older_ddl, db_path, tmp_path / "migration-backups")
    before = db_path.read_bytes()
    csv_path = _write_csv(tmp_path, "employee.csv", _csv(_EMPLOYEE_HEADER, _TARO))

    exit_code = main(["import", "employee", str(csv_path)])

    assert exit_code == 2
    assert "アプリを一度起動してから" in capsys.readouterr().out
    assert db_path.read_bytes() == before


def test_newer_db_exits_with_2(
    data_dir: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """DB の版が、アプリより新しいときは、終了コード 2。"""
    older_ddl = tmp_path / "older-ddl"
    older_ddl.mkdir()
    for sql_file in sorted(_DDL_DIR.glob("*.sql"))[:-1]:
        shutil.copyfile(sql_file, older_ddl / sql_file.name)
    monkeypatch.setattr(import_command, "BUNDLED_DDL_DIR", older_ddl)
    csv_path = _write_csv(tmp_path, "employee.csv", _csv(_EMPLOYEE_HEADER, _TARO))

    exit_code = main(["import", "employee", str(csv_path)])

    assert exit_code == 2
    assert "スキーマの版が" in capsys.readouterr().out
    assert _employee_count(data_dir) == 0


def test_invalid_db_file_exits_with_2_and_guides_to_check_the_file(
    data_dir: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """このアプリの DB でないファイルのときは、終了コード 2。アプリの起動ではなく、DB ファイルの
    確認とバックアップからの復元を案内する。ファイルは変えない。
    """
    db_path = data_dir / "crm-g.db"
    db_path.write_bytes(b"this is not a sqlite database file" * 100)
    before = db_path.read_bytes()
    csv_path = _write_csv(tmp_path, "employee.csv", _csv(_EMPLOYEE_HEADER, _TARO))

    exit_code = main(["import", "employee", str(csv_path)])

    output = capsys.readouterr().out
    assert exit_code == 2
    assert "DB ファイルが、このアプリの DB として読めません" in output
    assert "必要なら、バックアップから復元してください" in output
    assert "アプリを一度起動してから" not in output
    assert db_path.read_bytes() == before
    assert "例外=InvalidDatabaseFileError" in _log_text(data_dir)


def test_checksum_mismatch_exits_with_2_with_version_message(
    data_dir: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """適用済みの連番SQLのチェックサムが合わないときは、終了コード 2。版が合わないと案内する。"""
    changed_ddl = tmp_path / "changed-ddl"
    shutil.copytree(_DDL_DIR, changed_ddl)
    first_sql = sorted(changed_ddl.glob("*.sql"))[0]
    first_sql.write_text(
        first_sql.read_text(encoding="utf-8") + "\n-- 試験のための変更\n", encoding="utf-8"
    )
    monkeypatch.setattr(import_command, "BUNDLED_DDL_DIR", changed_ddl)
    before = (data_dir / "crm-g.db").read_bytes()
    csv_path = _write_csv(tmp_path, "employee.csv", _csv(_EMPLOYEE_HEADER, _TARO))

    exit_code = main(["import", "employee", str(csv_path)])

    output = capsys.readouterr().out
    assert exit_code == 2
    assert "DB のスキーマの版が、このアプリと合いません" in output
    assert (data_dir / "crm-g.db").read_bytes() == before
    assert "例外=ChecksumMismatchError" in _log_text(data_dir)


def test_missing_csv_exits_with_2(
    data_dir: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    exit_code = main(["import", "employee", str(tmp_path / "missing.csv")])

    assert exit_code == 2
    assert "CSV ファイルを開けません" in capsys.readouterr().out
    assert _employee_count(data_dir) == 0


@pytest.mark.parametrize(
    ("arguments", "message"),
    [
        (["import"], "1つずつ指定してください"),
        (["import", "employee"], "1つずつ指定してください"),
        (["import", "employee", "a.csv", "b.csv"], "1つずつ指定してください"),
        (["import", "unknown", "a.csv"], "取込の種類が正しくありません: unknown"),
        (["import", "employee", "a.csv", "--force"], "知らないオプションです: --force"),
    ],
)
def test_invalid_arguments_exit_with_2(
    data_dir: Path,
    arguments: list[str],
    message: str,
    capsys: pytest.CaptureFixture[str],
) -> None:
    exit_code = main(arguments)

    error = capsys.readouterr().err
    assert exit_code == 2
    assert message in error
    assert "使い方: crm-g import" in error
    assert _operation_count(data_dir) == 0


def test_help_prints_usage_with_kinds(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["import", "--help"]) == 0
    output = capsys.readouterr().out
    assert "使い方: crm-g import" in output
    for kind in ImportKind:
        assert f"{kind.value}（{kind.label}）" in output


def test_tc_mig_047_running_app_lock_exits_with_2(
    data_dir: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """アプリと同じロックが取られているときは、実行せずに終了コード 2（`TC-MIG-047`）。"""
    csv_path = _write_csv(tmp_path, "employee.csv", _csv(_EMPLOYEE_HEADER, _TARO))

    with acquire_lock(data_dir / LOCK_FILE_NAME):
        exit_code = main(["import", "employee", str(csv_path)])

    assert exit_code == 2
    assert "アプリまたは別の移行コマンドが実行中です" in capsys.readouterr().out
    assert _employee_count(data_dir) == 0


def test_exception_after_backup_exits_with_2_and_guides_to_the_backup(
    data_dir: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """取込前のバックアップを作った後の例外は、終了コード 2。そのバックアップからの復元を案内する。

    ログには型名だけを1回出す（`ARC-018`、`MIG-056`）。
    """
    csv_path = _write_csv(tmp_path, "employee.csv", _csv(_EMPLOYEE_HEADER, _TARO))

    def _raise(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("山田 太郎 の値を含む擬似的な失敗")

    monkeypatch.setattr(data_import_app, "record_operation_log", _raise)

    exit_code = main(["import", "employee", str(csv_path)])

    assert exit_code == 2
    output = capsys.readouterr().out
    backups = _backups(data_dir)
    assert len(backups) == 1
    assert "取込に失敗しました" in output
    assert f"取込前のバックアップ: {backups[0].name}" in output
    assert "取込前のバックアップから復元できます" in output
    assert "以前のバックアップ" not in output
    assert "山田" not in output
    assert str(data_dir) not in output
    assert _employee_count(data_dir) == 0
    log_text = _log_text(data_dir)
    assert log_text.count("例外=RuntimeError") == 1
    assert "山田" not in log_text


@pytest.mark.parametrize("check_only", [True, False])
def test_validation_exception_exits_with_2_and_guides_to_earlier_backup(
    data_dir: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    check_only: bool,  # noqa: FBT001 pytest のパラメーター
) -> None:
    """検証の途中の例外（取込前のバックアップの前）は、以前のバックアップからの復元を案内する。

    ログには型名だけを1回出す（アプリケーション層が出し、コマンドは重ねて出さない）。
    """
    csv_path = _write_csv(tmp_path, "employee.csv", _csv(_EMPLOYEE_HEADER, _TARO))

    def _fail(_connection: sqlite3.Connection, _content: bytes, _today: date) -> ValidatedImport:
        raise ValueError("山田 太郎 の値を含む擬似的な失敗")

    monkeypatch.setitem(
        data_import_app._DEFINITIONS,
        ImportKind.EMPLOYEE,
        ImportKindDefinition(label="社員", target_table="employee", validate=_fail),
    )
    arguments = ["import", "employee", str(csv_path)] + (["--check"] if check_only else [])

    exit_code = main(arguments)

    assert exit_code == 2
    output = capsys.readouterr().out
    assert "取込に失敗しました" in output
    assert "以前のバックアップから復元できます" in output
    assert "取込前のバックアップから復元" not in output
    assert "山田" not in output
    assert _backups(data_dir) == []
    log_text = _log_text(data_dir)
    assert log_text.count("例外=ValueError") == 1
    assert "取込の検証の途中で失敗しました" in log_text
    assert "山田" not in log_text


def test_connection_failure_exits_with_2_and_logs_the_type_once(
    data_dir: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """DB の接続を開けないときは、終了コード 2。コマンドが型名だけを1回ログに出す。"""
    csv_path = _write_csv(tmp_path, "employee.csv", _csv(_EMPLOYEE_HEADER, _TARO))

    def _fail(_db_path: Path) -> Iterator[sqlite3.Connection]:
        raise sqlite3.OperationalError(f"開けません: {data_dir}")

    monkeypatch.setattr(import_command, "open_connection", contextmanager(_fail))

    exit_code = main(["import", "employee", str(csv_path)])

    assert exit_code == 2
    output = capsys.readouterr().out
    assert "取込に失敗しました" in output
    assert "以前のバックアップから復元できます" in output
    assert str(data_dir) not in output
    log_text = _log_text(data_dir)
    assert log_text.count("例外=OperationalError") == 1
    assert "DB の接続に失敗しました" in log_text
    assert str(data_dir) not in log_text


def _subprocess_env(local_app_data: Path) -> dict[str, str]:
    """子のプロセスの環境変数。worktree の src を先に読み込み、データ領域を差し替える。"""
    env = dict(os.environ)
    env["LOCALAPPDATA"] = str(local_app_data)
    env["PYTHONPATH"] = os.pathsep.join(filter(None, [str(_SRC_DIR), env.get("PYTHONPATH")]))
    env["PYTHONIOENCODING"] = "utf-8"
    return env


def _run_command(local_app_data: Path, *arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "crm_g.cli.launcher", *arguments],
        env=_subprocess_env(local_app_data),
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=_SUBPROCESS_TIMEOUT_SECONDS,
        check=False,
    )


def test_tc_mig_047_process_exits_with_2_while_another_process_holds_the_lock(
    data_dir: Path, tmp_path: Path
) -> None:
    """試験のプロセスがロックを保持している間、移行コマンドのプロセスは終了コード 2。"""
    csv_path = _write_csv(tmp_path, "employee.csv", _csv(_EMPLOYEE_HEADER, _TARO))

    with acquire_lock(data_dir / LOCK_FILE_NAME):
        result = _run_command(tmp_path, "import", "employee", str(csv_path))

    assert result.returncode == 2, result.stderr
    assert "アプリまたは別の移行コマンドが実行中です" in result.stdout
    assert _employee_count(data_dir) == 0


def test_tc_mig_046_process_exit_codes_for_check(data_dir: Path, tmp_path: Path) -> None:
    """別のプロセスとして `--check` を実行する。エラーなしは 0、ありは 1。DB は変わらない。"""
    good = _write_csv(tmp_path, "good.csv", _csv(_EMPLOYEE_HEADER, _TARO))
    bad = _write_csv(tmp_path, "bad.csv", _csv(_EMPLOYEE_HEADER, _TARO.replace("2020/04/01", "")))
    before = (data_dir / "crm-g.db").read_bytes()

    good_result = _run_command(tmp_path, "import", "employee", str(good), "--check")
    bad_result = _run_command(tmp_path, "import", "employee", str(bad), "--check")

    assert good_result.returncode == 0, good_result.stderr
    assert "エラー: 0 件" in good_result.stdout
    assert bad_result.returncode == 1, bad_result.stderr
    assert "E006" in bad_result.stdout
    assert (data_dir / "crm-g.db").read_bytes() == before


def test_cp932_stdout_replaces_unencodable_characters_and_keeps_going(
    data_dir: Path, tmp_path: Path
) -> None:
    """標準出力が cp932 のとき、表せない文字（氏名の異体字など）は置き換え、出力を止めない。

    名寄せで一致しなかった人の氏名に、cp932 にない文字（U+20BB7）を含める。
    """
    unencodable_name = "\U00020bb7野 架空"
    salary_csv = _write_csv(
        tmp_path,
        "salary.csv",
        _csv(_SALARY_HEADER, f"{unencodable_name},2026/04/01,通常評価制度,正社員,入社,250000"),
    )
    env = _subprocess_env(tmp_path)
    env["PYTHONIOENCODING"] = "cp932"

    result = subprocess.run(
        [sys.executable, "-m", "crm_g.cli.launcher", "import", "salary-history", str(salary_csv)],
        env=env,
        capture_output=True,
        text=True,
        encoding="cp932",
        timeout=_SUBPROCESS_TIMEOUT_SECONDS,
        check=False,
    )

    assert result.returncode == 1, result.stderr
    assert "UnicodeEncodeError" not in result.stderr
    assert "名寄せで一致しなかった人: 1 件" in result.stdout
    assert "  ?野 架空\t-\t行 2\tE010 社員が見つからない" in result.stdout
    # 置き換えた行の後も、最後まで出力する。
    assert "結果: エラーがあるため、取り込みませんでした" in result.stdout


def _report(**overrides: object) -> ImportReport:
    """書式の試験用のレポート。"""
    values: dict[str, object] = {
        "kind": ImportKind.SALARY_HISTORY,
        "row_count": 3,
        "excluded_columns": ("パスワード", "支払額"),
        "issues": (),
    }
    values.update(overrides)
    return ImportReport(**values)  # type: ignore[arg-type]


def test_format_report_limits_displayed_errors_and_shows_total() -> None:
    """エラーは先頭の 1,000 件を出し、総数を併記する（`MGD-008`）。ファイル全体は「-」。"""
    issues = (
        ImportIssue(None, None, ImportErrorCode.NOT_UTF8),
        *(
            ImportIssue(line, "基本給", ImportErrorCode.OUT_OF_RANGE)
            for line in range(2, ERROR_DISPLAY_LIMIT + 3)
        ),
    )

    text = import_command.format_report(_report(issues=issues), check_only=True)

    total = ERROR_DISPLAY_LIMIT + 2
    assert f"エラー: {total:,} 件" in text
    assert f"先頭の {ERROR_DISPLAY_LIMIT:,} 件を表示します。全 {total:,} 件" in text
    assert "  行 -\t-\tE001 文字コードが UTF-8 ではない" in text
    assert text.count("E009") == ERROR_DISPLAY_LIMIT - 1
    assert "取り込まなかった列: パスワード、支払額" in text


def test_format_report_shows_unmatched_people_with_email() -> None:
    person = UnmatchedPerson(
        name=None,
        email="nobody@example.com",
        line_numbers=(2, 5),
        code=ImportErrorCode.EMPLOYEE_AMBIGUOUS,
    )
    issue = ImportIssue(2, "メールアドレス", ImportErrorCode.EMPLOYEE_AMBIGUOUS)

    text = import_command.format_report(
        _report(issues=(issue,), matched_row_count=1, unmatched=(person,)), check_only=False
    )

    assert "  -\tnobody@example.com\t行 2, 5\tE011 社員が複数見つかる" in text
    assert "処理: 取込の実行" in text


def test_format_report_of_executed_import_without_backup_or_errors() -> None:
    """取り込んだ結果。計算のエラーがなければ、月次給与計算への案内は出さない。"""
    recalculation = RecalculationResult(
        year_months=("2026-09", "2026-10"), saved_count=2, errors=()
    )

    text = import_command.format_report(
        _report(
            kind=ImportKind.EMPLOYEE,
            executed=True,
            imported_count=3,
            recalculation=recalculation,
        ),
        check_only=False,
    )

    assert "取り込みました" in text
    assert "再計算した月数: 2" in text
    assert "計算のエラー: 0 件" in text
    assert "月次給与計算" not in text
    assert "取込前のバックアップ" not in text
    assert "名寄せ" not in text


def test_format_report_counts_calculation_errors() -> None:
    error = CalculationError(
        employee_id=1,
        employee_name="架空 一郎",
        year_month="2026-10",
        reason=CalculationErrorReason.NO_BUSINESS_DAY,
    )
    recalculation = RecalculationResult(year_months=("2026-10",), saved_count=0, errors=(error,))

    text = import_command.format_report(
        _report(executed=True, imported_count=1, recalculation=recalculation),
        check_only=False,
    )

    assert "計算のエラー: 1 件" in text
    assert "月次給与計算" in text


def test_launcher_does_not_import_streamlit_for_the_import_command(tmp_path: Path) -> None:
    """移行コマンドは streamlit を読み込まない（launcher の import を遅延にした。`ARC-002`）。"""
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys, crm_g.cli.launcher; sys.exit(1 if 'streamlit' in sys.modules else 0)",
        ],
        env=_subprocess_env(tmp_path),
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=_SUBPROCESS_TIMEOUT_SECONDS,
        check=False,
    )

    assert result.returncode == 0, result.stderr


def test_sqlite_connection_is_not_left_open(data_dir: Path, tmp_path: Path) -> None:
    """コマンドの後に、DB ファイルを消せる（接続を閉じている。Windows のファイルロック）。"""
    csv_path = _write_csv(tmp_path, "employee.csv", _csv(_EMPLOYEE_HEADER, _TARO))
    assert main(["import", "employee", str(csv_path), "--check"]) == 0

    connection = sqlite3.connect(data_dir / "crm-g.db")
    connection.close()
    (data_dir / "crm-g.db").unlink()
    assert not (data_dir / "crm-g.db").exists()


def test_backup_failure_exits_with_2_without_path(
    data_dir: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """取込前のバックアップを作れないと、取り込まず終了コード 2。パスは出さない（`MIG-053`）。"""
    csv_path = _write_csv(tmp_path, "employee.csv", _csv(_EMPLOYEE_HEADER, _TARO))

    def _fail_copy(_source: Path, destination: Path) -> None:
        destination.write_bytes(b"partial")
        raise OSError(f"書き込めません: {destination}")

    monkeypatch.setattr("crm_g.infrastructure.backup._copy_database", _fail_copy)

    exit_code = main(["import", "employee", str(csv_path)])

    output = capsys.readouterr().out
    assert exit_code == 2
    assert "取込前のバックアップを作成できなかったため、取り込みませんでした" in output
    assert "以前のバックアップから復元できます" in output
    assert "取込に失敗しました" not in output
    assert str(data_dir) not in output
    assert "crm-g_" not in output
    assert _employee_count(data_dir) == 0
    assert _operation_count(data_dir) == 0
    assert _backups(data_dir) == []
    log_text = _log_text(data_dir)
    assert "例外=BackupWriteError" in log_text
    assert str(data_dir / "backups") not in log_text


def test_in_transaction_error_exits_with_2(
    data_dir: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """取込を始める前に止まったとき（`ImportInTransactionError`）は、終了コード 2。

    接続がトランザクションの中の状態を作る。ログは、アプリケーション層が型名だけを1回出す。
    """
    csv_path = _write_csv(tmp_path, "employee.csv", _csv(_EMPLOYEE_HEADER, _TARO))

    @contextmanager
    def _open_in_transaction(db_path: Path) -> Iterator[sqlite3.Connection]:
        with open_connection(db_path) as connection:
            connection.execute("BEGIN")
            try:
                yield connection
            finally:
                connection.rollback()

    monkeypatch.setattr(import_command, "open_connection", _open_in_transaction)

    exit_code = main(["import", "employee", str(csv_path)])

    output = capsys.readouterr().out
    assert exit_code == 2
    assert "取込を開始できませんでした" in output
    assert "以前のバックアップから復元できます" in output
    assert "トランザクション" not in output
    assert _log_text(data_dir).count("例外=ImportInTransactionError") == 1
    assert _employee_count(data_dir) == 0


def test_revalidation_failure_under_lock_exits_with_1_and_reports_backup(
    data_dir: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """書き込みのロックの後の再検証で失敗すると、取り込まず終了コード 1。バックアップは残る。"""
    csv_path = _write_csv(tmp_path, "employee.csv", _csv(_EMPLOYEE_HEADER, _TARO))
    original = data_import_app._DEFINITIONS[ImportKind.EMPLOYEE]

    def _validate(connection: sqlite3.Connection, content: bytes, today: date) -> ValidatedImport:
        validated = original.validate(connection, content, today)
        if not connection.in_transaction:
            return validated
        issue = ImportIssue(2, "メールアドレス", ImportErrorCode.DUPLICATE_KEY_IN_DB)
        return replace(validated, issues=(issue,))

    monkeypatch.setitem(
        data_import_app._DEFINITIONS,
        ImportKind.EMPLOYEE,
        ImportKindDefinition(label="社員", target_table="employee", validate=_validate),
    )

    exit_code = main(["import", "employee", str(csv_path)])

    output = capsys.readouterr().out
    assert exit_code == 1
    backups = _backups(data_dir)
    assert len(backups) == 1
    assert "実行の直前にデータが変わり、エラーになったため、取り込みませんでした" in output
    assert f"取込前のバックアップ: {backups[0].name}" in output
    assert "データは変更していません" in output
    assert "行 2	メールアドレス	E013" in output
    assert str(data_dir) not in output
    assert _employee_count(data_dir) == 0
    assert _operation_count(data_dir) == 0


def test_no_header_csv_reports_e002_with_hint(
    data_dir: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """見出し行がない CSV は、ファイル全体の `E002` だけを出し、直し方を案内する（値なし）。"""
    csv_path = _write_csv(tmp_path, "employee.csv", _csv(_TARO, _HANAKO))

    exit_code = main(["import", "employee", str(csv_path), "--check"])

    output = capsys.readouterr().out
    assert exit_code == 1
    assert "エラー: 1 件" in output
    assert f"  行 -	-	E002 {ImportErrorCode.NO_HEADER.description}" in output
    assert "1行目が見出し行（列名）になっているか" in output
    assert "山田" not in output
    assert "taro" not in output


def test_format_report_hints_ambiguous_pay_item_column() -> None:
    """見出し行の `E015`（給与項目の列が決まらない）には、給与項目の見直しを案内する。"""
    header_issue = ImportIssue(1, "通勤手当", ImportErrorCode.REFERENCE_NOT_RESOLVED)
    row_issue = ImportIssue(2, "案件名", ImportErrorCode.REFERENCE_NOT_RESOLVED)

    with_header = import_command.format_report(
        _report(kind=ImportKind.SALARY_HISTORY, issues=(header_issue,)), check_only=True
    )
    rows_only = import_command.format_report(
        _report(kind=ImportKind.RATE_HISTORY, issues=(row_issue,)), check_only=True
    )

    assert "  行 1	通勤手当	E015" in with_header
    assert "どの給与項目の列か決まりません" in with_header
    assert "どの給与項目の列か決まりません" not in rows_only
    assert "1行目が見出し行" not in rows_only


def test_format_report_of_check_with_errors_does_not_mention_backup() -> None:
    """取込の前の検証のエラー（バックアップを作っていない）は、通常のエラーの結果を出す。"""
    issue = ImportIssue(2, "入社日", ImportErrorCode.REQUIRED_EMPTY)

    text = import_command.format_report(_report(issues=(issue,)), check_only=False)

    assert "エラーがあるため、取り込みませんでした" in text
    assert "取込前のバックアップ" not in text

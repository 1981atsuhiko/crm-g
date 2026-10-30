"""移行コマンド（移行設計書 8.1 `MIG-070`、アーキテクチャ設計書 `ARC-013`）。

`crm-g import <取込の種類> <CSV のパス> [--check]` で、取込用 CSV を確認・取り込む。
処理は、画面（`SCN-070`）と同じアプリケーション層のユースケース（`check_import`、
`run_import`）を呼ぶ（`MIG-006`）。

- パスコードの認証を求めない（権限・セキュリティ設計書 `SCD-063`）
- アプリと同じ排他ロックを取る。アプリの起動中は、実行しない（`ARC-007`）
- マイグレーションは適用しない。DB がないとき・未適用の連番SQLがあるときは、DB を変えずに、
  アプリを一度起動するよう案内する（ユーザー承認 2026-10-27）
- 終了コードは、`0`：エラーなし、`1`：検証のエラーがある（取り込んでいない）、
  `2`：実行できない（引数の誤り、アプリの起動中、DB の版が合わない、取込前のバックアップを
  作れない、例外。`MGD-009`）。`2` のときは、値・パスを出さない一般的なメッセージと、
  復元の案内を出す
- レポート（`MIG-061`）は、標準出力に出す。名寄せで一致しなかった人の氏名は、標準出力にだけ
  出し、ログには件数だけを出す（`MIG-005`、`SCD-042`）

Streamlit・UI 層には依存しない（`ARC-002`）。
"""

import io
import logging
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from crm_g.application.data_import import (
    ERROR_DISPLAY_LIMIT,
    ImportInTransactionError,
    ImportKind,
    ImportReport,
    check_import,
    run_import,
)
from crm_g.application.import_common import HEADER_LINE_NUMBER, ImportErrorCode, ImportIssue
from crm_g.application.import_matching import UnmatchedPerson
from crm_g.infrastructure.backup import BackupWriteError
from crm_g.infrastructure.db import open_connection
from crm_g.infrastructure.logging_setup import setup_logging
from crm_g.infrastructure.migration import (
    BUNDLED_DDL_DIR,
    ChecksumMismatchError,
    DatabaseNotFoundError,
    InvalidDatabaseFileError,
    PendingMigrationError,
    UnknownMigrationError,
    verify_schema_is_current,
)
from crm_g.infrastructure.paths import AppPaths, get_app_paths
from crm_g.infrastructure.single_instance import (
    LOCK_FILE_NAME,
    AlreadyRunningError,
    acquire_lock,
)

_logger = logging.getLogger(__name__)

IMPORT_COMMAND = "import"
"""移行コマンドのサブコマンド名（`crm-g import`）。"""

EXIT_SUCCESS = 0
"""エラーなし（確認だけ、または取り込み成功）。"""
EXIT_VALIDATION_ERROR = 1
"""検証のエラーがある（取り込んでいない）。"""
EXIT_CANNOT_RUN = 2
"""実行できない（引数の誤り、アプリの起動中、ファイルが開けない、DB の版が合わない、
取込前のバックアップを作れない、例外）。"""

_CHECK_OPTION = "--check"
_HELP_OPTIONS = ("-h", "--help")
_KIND_CHOICES = "、".join(f"{kind.value}（{kind.label}）" for kind in ImportKind)
USAGE = (
    "使い方: crm-g import <取込の種類> <CSV のパス> [--check]\n"
    f"  取込の種類: {_KIND_CHOICES}\n"
    "  --check   確認の実行だけを行います（DB を変更しません）"
)

_SETUP_REQUIRED_MESSAGE = (
    "DB の準備ができていません。アプリを一度起動してから、やり直してください。"
)
_VERSION_MISMATCH_MESSAGE = (
    "DB のスキーマの版が、このアプリと合いません。アプリの版を確認してください。"
)
_ALREADY_RUNNING_MESSAGE = "アプリが起動中です。アプリを終了してから、やり直してください。"
_CANNOT_OPEN_MESSAGE = "CSV ファイルを開けません。パスを確認してください。"
_UNEXPECTED_MESSAGE = (
    "取込に失敗しました。詳しくはログを確認してください。\n"
    "  取り込みは行っていません。データの状態が気になるときは、アプリの「設定」の"
    "「バックアップ・復元」で、取込前のバックアップから復元できます。"
)
_NOT_STARTED_GUIDE = (
    "  取り込みは行っていません。データの状態が気になるときは、アプリの「設定」の"
    "「バックアップ・復元」で、以前のバックアップから復元できます。"
)
_BACKUP_FAILURE_MESSAGE = (
    "取込前のバックアップを作成できなかったため、取り込みませんでした。"
    "保存先の空き容量と書き込みの権限を確認してから、やり直してください。\n" + _NOT_STARTED_GUIDE
)
_IN_TRANSACTION_MESSAGE = (
    "取込を開始できませんでした。詳しくはログを確認してください。\n" + _NOT_STARTED_GUIDE
)
_NO_HEADER_HINT = (
    "  E002: 1行目が見出し行（列名）になっているか、ファイルが CSV として読めるか"
    "（閉じていない引用符・大きすぎるセルなどがないか）を確認してください。"
)
_AMBIGUOUS_PAY_ITEM_HINT = (
    "  見出し行の E015: 給与項目マスタに、表記をそろえると同じになる名称（またはほかの列と"
    "同じになる名称）があるため、どの給与項目の列か決まりません。"
    "アプリの「給与項目」で、名称を見直してください。"
)


class _UsageError(Exception):
    """引数が正しくないときの内部の例外。メッセージは、利用者に示す原因。"""


@dataclass(frozen=True)
class ImportArguments:
    """移行コマンドの引数。`check_only` は、`--check` の指定があるとき真。"""

    kind: ImportKind
    csv_path: Path
    check_only: bool


def parse_arguments(arguments: Sequence[str]) -> ImportArguments:
    """`import` に続く引数を解釈する。誤りがあれば `_UsageError` を送出する。

    位置引数は、取込の種類と CSV のパスの2つ。`--check` は、どの位置にあってもよい。
    """
    check_only = False
    positionals: list[str] = []
    for argument in arguments:
        if argument == _CHECK_OPTION:
            check_only = True
        elif argument.startswith("-"):
            raise _UsageError(f"知らないオプションです: {argument}")
        else:
            positionals.append(argument)
    if len(positionals) != 2:
        raise _UsageError("取込の種類と CSV のパスを、1つずつ指定してください。")
    kind_text, path_text = positionals
    try:
        kind = ImportKind(kind_text)
    except ValueError:
        raise _UsageError(f"取込の種類が正しくありません: {kind_text}") from None
    return ImportArguments(kind=kind, csv_path=Path(path_text), check_only=check_only)


def _line_text(line_number: int | None) -> str:
    """行番号の表示。ファイル全体のエラーは「-」とする。"""
    return "-" if line_number is None else str(line_number)


def _issue_line(issue: ImportIssue) -> str:
    """エラー1件の表示（行番号・項目・原因。値は出さない。`MIG-060`）。"""
    column = "-" if issue.column is None else issue.column
    return (
        f"  行 {_line_text(issue.line_number)}\t{column}\t"
        f"{issue.code.value} {issue.code.description}"
    )


def _unmatched_line(person: UnmatchedPerson) -> str:
    """一致しなかった人の1行（氏名・メールアドレス・行番号・原因。`MIG-032`）。"""
    name = "-" if person.name is None else person.name
    email = "-" if person.email is None else person.email
    lines = ", ".join(str(line) for line in person.line_numbers)
    return f"  {name}\t{email}\t行 {lines}\t{person.code.value} {person.code.description}"


def _matching_lines(report: ImportReport) -> list[str]:
    """名寄せの結果の行。名寄せをしない種類は、出さない。"""
    if report.matched_row_count is None:
        return []
    output = [f"名寄せで一致した行数: {report.matched_row_count:,}"]
    if report.unmatched:
        output.append(f"名寄せで一致しなかった人: {len(report.unmatched):,} 件")
        output.append("  氏名\tメールアドレス\t行番号\t原因")
        output.extend(_unmatched_line(person) for person in report.unmatched)
    return output


def _issue_hints(report: ImportReport) -> list[str]:
    """エラーの種類に応じた、直し方の案内（ファイル全体の `E002`、給与項目の列の `E015`）。"""
    hints: list[str] = []
    if any(issue.code is ImportErrorCode.NO_HEADER for issue in report.issues):
        hints.append(_NO_HEADER_HINT)
    if any(
        issue.code is ImportErrorCode.REFERENCE_NOT_RESOLVED
        and issue.line_number == HEADER_LINE_NUMBER
        for issue in report.issues
    ):
        hints.append(_AMBIGUOUS_PAY_ITEM_HINT)
    return hints


def _error_lines(report: ImportReport) -> list[str]:
    """エラーの一覧の行。先頭の `ERROR_DISPLAY_LIMIT` 件と、総数を出す（`MGD-008`）。"""
    output = [f"エラー: {report.error_count:,} 件"]
    if not report.has_errors:
        return output
    output.extend(_issue_hints(report))
    if report.error_count > ERROR_DISPLAY_LIMIT:
        output.append(
            f"  （先頭の {ERROR_DISPLAY_LIMIT:,} 件を表示します。全 {report.error_count:,} 件）"
        )
    output.append("  行番号\t項目\t原因")
    output.extend(_issue_line(issue) for issue in report.displayed_issues)
    return output


def _result_lines(report: ImportReport, *, check_only: bool) -> list[str]:
    """結果の行（取り込んだときは、取り込み結果。`MIG-061`）。"""
    if report.has_errors and not check_only and report.backup_path is not None:
        # 書き込みのロックの後の再検証で失敗した。取込前のバックアップだけが残る（MIG-050）。
        return [
            "結果: 実行の直前にデータが変わり、エラーになったため、取り込みませんでした。"
            "修正して、確認からやり直してください。",
            f"取込前のバックアップ: {report.backup_path.name}",
            "  取込前のバックアップは作成しましたが、データは変更していません。",
        ]
    if report.has_errors:
        return [
            "結果: エラーがあるため、取り込みませんでした。修正して、確認からやり直してください。"
        ]
    if check_only or not report.executed:
        return ["結果: エラーはありません（確認の実行だけを行いました。DB は変更していません）。"]
    output = [
        "結果: 取り込みました。",
        f"取り込んだ行数: {report.imported_count:,}",
        f"再計算した月数: {report.recalculated_month_count:,}",
        f"計算のエラー: {report.calculation_error_count:,} 件",
    ]
    if report.calculation_error_count:
        output.append(
            "  計算のエラーは、アプリの「月次給与計算」の、エラーの一覧で確認してください。"
        )
    if report.backup_path is not None:
        output.append(f"取込前のバックアップ: {report.backup_path.name}")
        output.append(
            "  取り込みを取り消すときは、アプリの「設定」の「バックアップ・復元」で、"
            "このバックアップから復元してください。"
        )
    return output


def format_report(report: ImportReport, *, check_only: bool) -> str:
    """レポート（移行設計書 7.2 `MIG-061`）を、標準出力に出す文字列にする。"""
    excluded = "、".join(report.excluded_columns) if report.excluded_columns else "なし"
    output = [
        f"取込の種類: {report.kind.label}（{report.kind.value}）",
        f"処理: {'確認の実行' if check_only else '取込の実行'}",
        f"読み込んだ行数: {report.row_count:,}",
        f"取り込まなかった列: {excluded}",
        *_matching_lines(report),
        *_error_lines(report),
        *_result_lines(report, check_only=check_only),
    ]
    return "\n".join(output)


def _execute(parsed: ImportArguments, paths: AppPaths) -> int:
    """ロックを取った後の処理。CSV を読み、DB の版を確かめてから、確認・取込をする。"""
    try:
        content = parsed.csv_path.read_bytes()
    except OSError as error:
        # パスは氏名などを含みうるため、ログには例外の型名だけを出す（SCD-042）。
        _logger.error("CSV ファイルを開けませんでした（例外=%s）", type(error).__name__)
        print(_CANNOT_OPEN_MESSAGE)
        return EXIT_CANNOT_RUN

    try:
        verify_schema_is_current(paths.db_path, BUNDLED_DDL_DIR)
    except (DatabaseNotFoundError, PendingMigrationError, InvalidDatabaseFileError) as error:
        _logger.error("DB の準備ができていません（例外=%s）", type(error).__name__)
        print(_SETUP_REQUIRED_MESSAGE)
        return EXIT_CANNOT_RUN
    except (UnknownMigrationError, ChecksumMismatchError) as error:
        _logger.error("DB のスキーマの版が合いません（例外=%s）", type(error).__name__)
        print(_VERSION_MISMATCH_MESSAGE)
        return EXIT_CANNOT_RUN

    today = date.today()
    try:
        with open_connection(paths.db_path) as connection:
            if parsed.check_only:
                report = check_import(connection, parsed.kind, content, today)
            else:
                report = run_import(
                    connection,
                    parsed.kind,
                    content,
                    today,
                    db_path=paths.db_path,
                    backup_dir=paths.backup_dir,
                )
    except BackupWriteError:
        # 原因はアプリケーション層がログに残す。文言はパスを含みうるため、出さない（ARC-018）。
        print(_BACKUP_FAILURE_MESSAGE)
        return EXIT_CANNOT_RUN
    except ImportInTransactionError:
        _logger.error("取込を開始できませんでした（例外=ImportInTransactionError）")
        print(_IN_TRANSACTION_MESSAGE)
        return EXIT_CANNOT_RUN
    print(format_report(report, check_only=parsed.check_only))
    # 一致しなかった人の一覧は氏名を含むため、ログには件数だけを出す（MIG-005）。
    _logger.info(
        "移行コマンドの結果（種類=%s エラー=%d 一致しなかった人=%d 取り込んだ行数=%d）",
        parsed.kind.value,
        report.error_count,
        len(report.unmatched),
        report.imported_count,
    )
    return EXIT_VALIDATION_ERROR if report.has_errors else EXIT_SUCCESS


def _prepare_stdout() -> None:
    """標準出力に出せない文字（氏名の異体字など）で、出力が止まらないようにする。"""
    if isinstance(sys.stdout, io.TextIOWrapper):
        sys.stdout.reconfigure(errors="replace")


def run_import_command(arguments: Sequence[str]) -> int:
    """移行コマンドを実行し、終了コードを返す。`arguments` は `import` に続く引数。"""
    _prepare_stdout()
    if any(argument in _HELP_OPTIONS for argument in arguments):
        print(USAGE)
        return EXIT_SUCCESS
    try:
        parsed = parse_arguments(arguments)
    except _UsageError as error:
        print(f"{error}\n{USAGE}", file=sys.stderr)
        return EXIT_CANNOT_RUN

    try:
        paths = get_app_paths()
        with acquire_lock(paths.data_dir / LOCK_FILE_NAME):
            setup_logging(paths.log_dir)
            _logger.info(
                "移行コマンドを開始しました（種類=%s 確認だけ=%s）",
                parsed.kind.value,
                parsed.check_only,
            )
            exit_code = _execute(parsed, paths)
            _logger.info("移行コマンドを終了しました（終了コード=%d）", exit_code)
            return exit_code
    except AlreadyRunningError:
        print(_ALREADY_RUNNING_MESSAGE)
        return EXIT_CANNOT_RUN
    except Exception as error:
        # 例外のメッセージは値を含みうるため、ログには型名だけを出す（MIG-056、ARC-018）。
        _logger.error("移行コマンドに失敗しました（例外=%s）", type(error).__name__)
        print(_UNEXPECTED_MESSAGE)
        return EXIT_CANNOT_RUN

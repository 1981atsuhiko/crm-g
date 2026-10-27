"""CSV 出力の共通処理（アーキテクチャ設計書 `ARC-011`、画面設計書 9章）。

- ファイルの形式（`UID-090`）は、インフラ層の `write_csv` が受け持つ
- 数式インジェクションの対策（`UID-091`）：利用者が入力した文字列の列と、見出し（給与項目名など、
  利用者が入力した名称を含む）で、先頭が数式として解釈されうる文字のセルに、単一引用符 `'` を付ける
- 給与・賞与を含む出力は、CSV を作ってから、対象テーブルごとに `出力` を記録する（`SCD-021`）。
  `detail` には出力の種類と条件だけを入れ、氏名・金額を入れない（`SCD-023`）
"""

import sqlite3  # noqa: TID251 呼び出し側が開いた接続の型として使うだけで、接続はインフラ層に閉じ込める
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from crm_g.application.operation_log import Operation, OperationLogDetail, record_operation_log
from crm_g.infrastructure.csv_writer import write_csv
from crm_g.infrastructure.db import transaction

# 先頭にあると、表計算ソフトが数式として解釈しうる文字（OWASP「CSV Injection」。全角を含む）。
_FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r", "\n", "＝", "＋", "－", "＠")

CsvValue = str | int | None


@dataclass(frozen=True)
class ExportFile:
    """作成済みの CSV。`content` は、BOM 付きの UTF-8 のバイト列。"""

    file_name: str
    content: bytes


def neutralize_formula(text: str) -> str:
    """先頭が数式として解釈されうる文字なら、単一引用符を付ける（`UID-091`）。"""
    return f"'{text}" if text.startswith(_FORMULA_PREFIXES) else text


def export_file_name(kind: str, condition: str) -> str:
    """ファイル名 `種類_条件.csv`（画面設計書 7.2）。氏名・金額を含めない。"""
    return f"{kind}_{condition}.csv"


def build_csv(
    headers: Sequence[str],
    rows: Iterable[Sequence[CsvValue]],
    text_columns: Iterable[int] = (),
) -> bytes:
    """CSV のバイト列を作る。`text_columns` は、利用者が入力した文字列の列の位置（`UID-091`）。

    見出しは、利用者が入力した名称（給与項目名など）を含みうるため、すべて対策の対象にする。
    """
    neutralized = set(text_columns)
    return write_csv(
        [neutralize_formula(header) for header in headers],
        (
            [
                neutralize_formula(value)
                if index in neutralized and isinstance(value, str)
                else value
                for index, value in enumerate(row)
            ]
            for row in rows
        ),
    )


def record_export(connection: sqlite3.Connection, tables: Sequence[str], detail: str) -> None:
    """`出力` を、対象テーブルごとに1行ずつ、1つのトランザクションで記録する（`SCD-021`）。"""
    with transaction(connection):
        for table in tables:
            record_operation_log(
                connection, Operation.EXPORT, table, OperationLogDetail(detail=detail)
            )

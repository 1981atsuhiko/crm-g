"""操作ログの書き込み（SCD-020〜024）。

呼び出し側は、業務データの変更と同じトランザクション内でこの関数を呼ぶ
（`transaction()` の中で呼ぶこと。関数自体はトランザクションを開始しない）。
これにより、業務データの変更が失敗したときに、操作ログの記録もロールバック
される（`SCD-021`）。
"""

import json
import sqlite3  # noqa: TID251 呼び出し側が開いた接続の型として使うだけで、接続はインフラ層に閉じ込める
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from crm_g.infrastructure.datetime_utils import to_iso8601, utc_now


class Operation(StrEnum):
    """操作ログの `operation` 列に記録する値（DBD-020、CHECK制約と一致させる）。"""

    VIEW = "閲覧"
    EXPORT = "出力"
    CREATE = "登録"
    UPDATE = "更新"
    DELETE = "削除"
    MONTHLY_CLOSE = "月次締め"
    MONTHLY_REOPEN = "月次締め解除"
    IMPORT = "取込"
    AUTH_SUCCESS = "認証成功"
    AUTH_FAILURE = "認証失敗"
    PASSCODE_SET = "パスコード設定"


@dataclass(frozen=True)
class OperationLogDetail:
    """`record_operation_log` の省略可能な項目（`SCD-020`）。"""

    target_key: str | None = None
    detail: str | None = None
    before_value: dict[str, Any] | None = None
    after_value: dict[str, Any] | None = None


def changed_values(
    before: Mapping[str, Any], after: Mapping[str, Any]
) -> tuple[dict[str, Any], dict[str, Any]]:
    """`更新` の記録用に、値が変わった列だけを、変更前・変更後の組にして返す（`SCD-020`）。"""
    keys = [key for key in after if before.get(key) != after[key]]
    return {key: before.get(key) for key in keys}, {key: after[key] for key in keys}


def record_operation_log(
    connection: sqlite3.Connection,
    operation: Operation,
    target_table: str,
    detail: OperationLogDetail | None = None,
) -> None:
    """操作ログに1行を記録する。

    `before_value`・`after_value` は、列名をキーにした変更前・変更後の値のみとし、
    パスコード・ハッシュ・ソルトなどの機微情報は含めない（`SCD-023`）。
    """
    detail = detail if detail is not None else OperationLogDetail()
    connection.execute(
        "INSERT INTO operation_log "
        "(occurred_at, operation, target_table, target_key, detail, before_value, after_value) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (
            to_iso8601(utc_now()),
            operation.value,
            target_table,
            detail.target_key,
            detail.detail,
            json.dumps(detail.before_value, ensure_ascii=False)
            if detail.before_value is not None
            else None,
            json.dumps(detail.after_value, ensure_ascii=False)
            if detail.after_value is not None
            else None,
        ),
    )

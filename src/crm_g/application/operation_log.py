"""操作ログの書き込み（SCD-020〜024）と、操作ログ画面（`SCN-067`）の照会。

呼び出し側は、業務データの変更と同じトランザクション内でこの関数を呼ぶ
（`transaction()` の中で呼ぶこと。関数自体はトランザクションを開始しない）。
これにより、業務データの変更が失敗したときに、操作ログの記録もロールバック
される（`SCD-021`）。
"""

import json
import sqlite3  # noqa: TID251 呼び出し側が開いた接続の型として使うだけで、接続はインフラ層に閉じ込める
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from enum import StrEnum
from typing import Any

from crm_g.infrastructure.datetime_utils import JST, from_iso8601, to_iso8601, utc_now

# 操作ログ画面の1回の表示の上限（設計判断 `UID-080`）。
OPERATION_LOG_DISPLAY_LIMIT = 1000


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


@dataclass(frozen=True)
class OperationLogRow:
    """操作ログ画面の一覧の1行（`SCN-067`）。

    `occurred_at` は、タイムゾーン付き（UTC）の datetime。変更前・変更後は、
    保存されている JSON の文字列のまま持つ（`SCD-022`）。
    """

    occurred_at: datetime
    operation: str
    target_table: str
    target_key: str | None
    detail: str | None
    before_value: str | None
    after_value: str | None


@dataclass(frozen=True)
class OperationLogSearchResult:
    """操作ログの照会の結果（`SCN-067`）。

    `rows` は新しい順で、最大 `OPERATION_LOG_DISPLAY_LIMIT` 件。`exceeds_limit` は、
    該当が上限を超えたとき `True`（画面は「絞り込んでください」と表示する。`UID-080`）。
    """

    rows: tuple[OperationLogRow, ...]
    exceeds_limit: bool


def jst_date_range_to_utc(start_date: date, end_date: date) -> tuple[str, str]:
    """日本時間の日付の範囲を、UTC の半開区間 [開始, 終了) の ISO 8601 文字列にする。

    開始は開始日の 0時（日本時間）、終了は終了日の翌日の 0時（日本時間）。
    `occurred_at` と同じ形式（UTC、末尾 Z、マイクロ秒6桁）のため、文字列の比較が
    時系列の比較と一致する（`to_iso8601`）。
    """
    range_start = datetime.combine(start_date, time.min, tzinfo=JST)
    range_end = datetime.combine(end_date + timedelta(days=1), time.min, tzinfo=JST)
    return to_iso8601(range_start), to_iso8601(range_end)


def search_operation_logs(
    connection: sqlite3.Connection,
    start_date: date,
    end_date: date,
    operations: Sequence[Operation],
) -> OperationLogSearchResult:
    """期間（日本時間の日付）と操作の種類で、操作ログを新しい順に照会する（`SCN-067`）。

    上限を超えたかを判定するため、上限より1件多く読む（`UID-080`）。操作の種類を
    1つも選ばないときは、該当なしとする。開始日が終了日より後のときは `ValueError`。
    照会しても `閲覧` は記録しない（`UID-060`。ユーザー承認 2026-10-27）。
    """
    if start_date > end_date:
        raise ValueError("開始日は、終了日以前の日付にしてください。")
    if not operations:
        return OperationLogSearchResult(rows=(), exceeds_limit=False)

    range_start, range_end = jst_date_range_to_utc(start_date, end_date)
    # placeholders は "?" だけで構成される（利用者の入力は含まない）。値は、
    # params を通じてプレースホルダに束縛するため、SQLインジェクションの
    # リスクはない。
    placeholders = ", ".join("?" for _ in operations)
    query = (
        "SELECT occurred_at, operation, target_table, target_key, detail, "
        "before_value, after_value FROM operation_log "
        "WHERE occurred_at >= ? AND occurred_at < ?"
    )
    query += " AND operation IN (" + placeholders + ")"
    query += " ORDER BY occurred_at DESC, id DESC LIMIT ?"
    params: list[object] = [range_start, range_end]
    params.extend(operation.value for operation in operations)
    params.append(OPERATION_LOG_DISPLAY_LIMIT + 1)

    fetched = connection.execute(query, params).fetchall()
    rows = tuple(
        OperationLogRow(
            occurred_at=from_iso8601(row[0]),
            operation=row[1],
            target_table=row[2],
            target_key=row[3],
            detail=row[4],
            before_value=row[5],
            after_value=row[6],
        )
        for row in fetched[:OPERATION_LOG_DISPLAY_LIMIT]
    )
    return OperationLogSearchResult(
        rows=rows, exceeds_limit=len(fetched) > OPERATION_LOG_DISPLAY_LIMIT
    )

"""月次締めと解除（`ARC-010`、画面設計書 `SCN-032`）。

締めは、その月の `monthly_payroll`・`monthly_pl` を確定値とし、`is_closed` を立てる。
1つのトランザクションで行う。締め済みの月は、再計算の対象から除く（`recalculation`）。

- 計算エラーがある月は、結果のない社員がいる。確認（`acknowledge_errors`）がないと
  締められない（`UID-076`）。要確認があるだけの月は、確認なしで締められる
- 計算結果が1件もない月は、締められない（先に再計算する）
- 解除すると、再計算の対象に戻る。`source` が `取込` の `monthly_pl`（移行元の確定値）は、
  締め済みのまま残す（移行設計書 `MGD-005`）
- 締めは `月次締め`、解除は `月次締め解除` として、`monthly_payroll`・`monthly_pl` の
  それぞれに操作ログを記録する。`detail` は対象月（`SCD-020`）
"""

import sqlite3  # noqa: TID251 呼び出し側が開いた接続の型として使うだけで、接続はインフラ層に閉じ込める
from dataclasses import dataclass
from datetime import datetime

from crm_g.application.operation_log import Operation, OperationLogDetail, record_operation_log
from crm_g.application.recalculation import is_month_closed, list_calculation_errors
from crm_g.infrastructure.datetime_utils import from_iso8601, to_iso8601, utc_now
from crm_g.infrastructure.db import transaction

_LOGGED_TABLES = ("monthly_payroll", "monthly_pl")
_SOURCE_IMPORTED = "取込"


class AlreadyClosedError(RuntimeError):
    """その月が、すでに締め済みのときのエラー。"""


class NotClosedError(RuntimeError):
    """解除しようとした月が、締め済みでないときのエラー。"""


class NoResultError(RuntimeError):
    """締めようとした月に、計算結果が1件もないときのエラー。"""


class UnacknowledgedErrorsError(RuntimeError):
    """計算エラーがある月を、確認なしで締めようとしたときのエラー（`UID-076`）。"""


@dataclass(frozen=True)
class MonthStatus:
    """対象月の締めの状態（画面設計書 `SCN-032` の「状態」）。"""

    is_closed: bool
    closed_at: datetime | None


def get_month_status(connection: sqlite3.Connection, year_month: str) -> MonthStatus:
    """対象月が締め済みか、締めた日時とあわせて返す。"""
    row = connection.execute(
        "SELECT MAX(closed_at) FROM monthly_payroll WHERE year_month = ? AND is_closed = 1",
        (year_month,),
    ).fetchone()
    closed_at = from_iso8601(row[0]) if row[0] else None
    return MonthStatus(is_closed=is_month_closed(connection, year_month), closed_at=closed_at)


def _log(connection: sqlite3.Connection, operation: Operation, year_month: str) -> None:
    for table in _LOGGED_TABLES:
        record_operation_log(connection, operation, table, OperationLogDetail(detail=year_month))


def close_month(
    connection: sqlite3.Connection, year_month: str, *, acknowledge_errors: bool = False
) -> None:
    """対象月を締める（`ARC-010`）。1つのトランザクションで行う。"""
    with transaction(connection):
        if is_month_closed(connection, year_month):
            raise AlreadyClosedError(f"{year_month} は、すでに締め済みです")
        if (
            connection.execute(
                "SELECT 1 FROM monthly_payroll WHERE year_month = ? LIMIT 1", (year_month,)
            ).fetchone()
            is None
        ):
            raise NoResultError(f"{year_month} の計算結果がありません。先に再計算してください")
        errors = list_calculation_errors(connection, year_month)
        if errors and not acknowledge_errors:
            raise UnacknowledgedErrorsError(
                f"計算エラーが {len(errors)} 件あります。結果のない社員がいることを確認してください"
            )

        now = to_iso8601(utc_now())
        connection.execute(
            "UPDATE monthly_payroll SET is_closed = 1, closed_at = ?, updated_at = ? "
            "WHERE year_month = ?",
            (now, now, year_month),
        )
        connection.execute(
            "UPDATE monthly_pl SET is_closed = 1, updated_at = ? "
            "WHERE year_month = ? AND source != ?",
            (now, year_month, _SOURCE_IMPORTED),
        )
        _log(connection, Operation.MONTHLY_CLOSE, year_month)


def reopen_month(connection: sqlite3.Connection, year_month: str) -> None:
    """対象月の締めを解除する。再計算の対象に戻る。1つのトランザクションで行う。"""
    with transaction(connection):
        if (
            connection.execute(
                "SELECT 1 FROM monthly_payroll WHERE year_month = ? AND is_closed = 1 LIMIT 1",
                (year_month,),
            ).fetchone()
            is None
        ):
            raise NotClosedError(f"{year_month} は、締め済みではありません")

        now = to_iso8601(utc_now())
        connection.execute(
            "UPDATE monthly_payroll SET is_closed = 0, closed_at = NULL, updated_at = ? "
            "WHERE year_month = ?",
            (now, year_month),
        )
        connection.execute(
            "UPDATE monthly_pl SET is_closed = 0, updated_at = ? "
            "WHERE year_month = ? AND source != ?",
            (now, year_month, _SOURCE_IMPORTED),
        )
        _log(connection, Operation.MONTHLY_REOPEN, year_month)

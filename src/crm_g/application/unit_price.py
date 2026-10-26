"""単価履歴（`SCN-050` 参画のタブ、`TBL-017`）。

単価は、必ず参画に紐づく（データ設計書 `DBD-018`）。適用開始日は、参画の開始日〜
終了日の中にする（データ設計書 10章 No.12）。同じ参画・適用開始日の組は登録できない。
登録・編集・削除ができ、それぞれ `登録`・`更新`・`削除` として操作ログに記録する
（`SCD-020`）。削除は、誤りの訂正のための例外である（データ設計書 10章 No.10）。
再計算の接続はPH-10で行う。
"""

import sqlite3  # noqa: TID251 呼び出し側が開いた接続の型として使うだけで、接続はインフラ層に閉じ込める
from dataclasses import dataclass
from datetime import date
from typing import Any

from crm_g.application.operation_log import (
    Operation,
    OperationLogDetail,
    changed_values,
    record_operation_log,
)
from crm_g.infrastructure.datetime_utils import to_iso8601, utc_now
from crm_g.infrastructure.db import transaction

_EDITABLE_COLUMNS = ("effective_from", "unit_price", "revision_basis", "note")


class AssignmentNotFoundError(RuntimeError):
    """指定したIDの参画が、存在しないときのエラー。"""


class RateHistoryNotFoundError(RuntimeError):
    """指定したIDの単価が、存在しないときのエラー。"""


class InvalidUnitPriceError(RuntimeError):
    """単価が、0未満のときのエラー。"""


class OutsideAssignmentPeriodError(RuntimeError):
    """適用開始日が、参画の開始日〜終了日の外にあるときのエラー。"""


class DuplicateRateHistoryError(RuntimeError):
    """同じ参画・適用開始日の単価が、既にあるときのエラー（`TBL-017`）。"""


@dataclass(frozen=True)
class RateHistory:
    """単価履歴の1行。"""

    id: int
    assignment_id: int
    effective_from: date
    unit_price: int
    revision_basis: str | None
    note: str | None


@dataclass(frozen=True)
class RateHistoryInput:
    """単価の登録・編集で使う入力値。"""

    effective_from: date
    unit_price: int
    revision_basis: str | None
    note: str | None


def _values(data: RateHistoryInput) -> dict[str, Any]:
    return {
        "effective_from": data.effective_from.isoformat(),
        "unit_price": data.unit_price,
        "revision_basis": (data.revision_basis or "").strip() or None,
        "note": (data.note or "").strip() or None,
    }


def _check_within_assignment(
    connection: sqlite3.Connection, assignment_id: int, effective_from: date
) -> int:
    """参画の期間の中かを確かめ、参画の社員のIDを返す。"""
    row = connection.execute(
        "SELECT employee_id, start_date, end_date FROM assignment WHERE id = ?",
        (assignment_id,),
    ).fetchone()
    if row is None:
        raise AssignmentNotFoundError(f"参画（id={assignment_id}）が見つかりません")
    employee_id, start_date, end_date = row
    day = effective_from.isoformat()
    if day < start_date or (end_date is not None and day > end_date):
        raise OutsideAssignmentPeriodError("適用開始日は、参画の開始日〜終了日の中にしてください")
    return int(employee_id)


def _check_duplicate(
    connection: sqlite3.Connection,
    assignment_id: int,
    effective_from: date,
    *,
    exclude_id: int | None,
) -> None:
    query = "SELECT 1 FROM rate_history WHERE assignment_id = ? AND effective_from = ?"
    params: list[object] = [assignment_id, effective_from.isoformat()]
    if exclude_id is not None:
        query += " AND id != ?"
        params.append(exclude_id)
    if connection.execute(query, params).fetchone() is not None:
        raise DuplicateRateHistoryError("同じ適用開始日の単価が、既に登録されています")


def _validate_price(data: RateHistoryInput) -> None:
    if data.unit_price < 0:
        raise InvalidUnitPriceError("単価は、0以上の整数にしてください")


def list_rate_history(
    connection: sqlite3.Connection, assignment_id: int
) -> tuple[RateHistory, ...]:
    """指定した参画の単価を、適用開始日の昇順で返す。"""
    rows = connection.execute(
        "SELECT id, assignment_id, effective_from, unit_price, revision_basis, note "
        "FROM rate_history WHERE assignment_id = ? ORDER BY effective_from, id",
        (assignment_id,),
    ).fetchall()
    return tuple(
        RateHistory(
            id=row[0],
            assignment_id=row[1],
            effective_from=date.fromisoformat(row[2]),
            unit_price=row[3],
            revision_basis=row[4],
            note=row[5],
        )
        for row in rows
    )


def register_rate_history(
    connection: sqlite3.Connection, assignment_id: int, data: RateHistoryInput
) -> int:
    """参画に単価を登録する。登録したIDを返す。"""
    _validate_price(data)
    with transaction(connection):
        employee_id = _check_within_assignment(connection, assignment_id, data.effective_from)
        _check_duplicate(connection, assignment_id, data.effective_from, exclude_id=None)

        values = {"employee_id": employee_id, "assignment_id": assignment_id, **_values(data)}
        now = to_iso8601(utc_now())
        cursor = connection.execute(
            "INSERT INTO rate_history (employee_id, assignment_id, effective_from, unit_price, "
            "revision_basis, note, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (*values.values(), now, now),
        )
        new_id = cursor.lastrowid
        assert new_id is not None  # noqa: S101 AUTOINCREMENT主キーのINSERT直後のため必ず取得できる
        record_operation_log(
            connection,
            Operation.CREATE,
            "rate_history",
            OperationLogDetail(target_key=str(new_id), after_value=values),
        )
    return new_id


def update_rate_history(
    connection: sqlite3.Connection, rate_history_id: int, data: RateHistoryInput
) -> None:
    """単価を編集する。参画は変えない。"""
    _validate_price(data)
    with transaction(connection):
        row = connection.execute(
            "SELECT assignment_id, effective_from, unit_price, revision_basis, note "
            "FROM rate_history WHERE id = ?",
            (rate_history_id,),
        ).fetchone()
        if row is None:
            raise RateHistoryNotFoundError(f"単価（id={rate_history_id}）が見つかりません")
        assignment_id = row[0]
        _check_within_assignment(connection, assignment_id, data.effective_from)
        _check_duplicate(connection, assignment_id, data.effective_from, exclude_id=rate_history_id)

        values = _values(data)
        now = to_iso8601(utc_now())
        connection.execute(
            "UPDATE rate_history SET effective_from = ?, unit_price = ?, revision_basis = ?, "
            "note = ?, updated_at = ? WHERE id = ?",
            (*values.values(), now, rate_history_id),
        )
        before_value, after_value = changed_values(
            dict(zip(_EDITABLE_COLUMNS, row[1:], strict=True)), values
        )
        if after_value:
            record_operation_log(
                connection,
                Operation.UPDATE,
                "rate_history",
                OperationLogDetail(
                    target_key=str(rate_history_id),
                    before_value=before_value,
                    after_value=after_value,
                ),
            )


def delete_rate_history(connection: sqlite3.Connection, rate_history_id: int) -> None:
    """単価を削除する（誤りの訂正のための例外。データ設計書 10章 No.10）。"""
    with transaction(connection):
        row = connection.execute(
            "SELECT employee_id, assignment_id, effective_from, unit_price, revision_basis, note "
            "FROM rate_history WHERE id = ?",
            (rate_history_id,),
        ).fetchone()
        if row is None:
            raise RateHistoryNotFoundError(f"単価（id={rate_history_id}）が見つかりません")

        connection.execute("DELETE FROM rate_history WHERE id = ?", (rate_history_id,))
        record_operation_log(
            connection,
            Operation.DELETE,
            "rate_history",
            OperationLogDetail(
                target_key=str(rate_history_id),
                before_value=dict(
                    zip(
                        (
                            "employee_id",
                            "assignment_id",
                            "effective_from",
                            "unit_price",
                            "revision_basis",
                            "note",
                        ),
                        row,
                        strict=True,
                    )
                ),
            ),
        )

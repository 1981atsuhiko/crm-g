"""単価履歴（`SCN-050` 単価タブ、`TBL-017`）。

単価は、社員単位（参画なし）または案件単位（参画あり。`DBD-018`）で、適用開始日
ごとに登録する。同じ社員・参画・適用開始日の組は登録できない（`TBL-017`）。
登録だけを設け、編集・削除は設けない（画面設計書 6.1）。登録は、`登録` として
操作ログに記録する（`SCD-020`）。再計算の接続はPH-10で行う。
"""

import sqlite3  # noqa: TID251 呼び出し側が開いた接続の型として使うだけで、接続はインフラ層に閉じ込める
from dataclasses import dataclass
from datetime import date

from crm_g.application.operation_log import Operation, OperationLogDetail, record_operation_log
from crm_g.infrastructure.datetime_utils import to_iso8601, utc_now
from crm_g.infrastructure.db import transaction


class EmployeeNotFoundError(RuntimeError):
    """指定したIDの社員が、存在しないときのエラー。"""


class AssignmentNotOwnedError(RuntimeError):
    """指定した参画が、存在しないか、その社員の参画でないときのエラー。"""


class InvalidUnitPriceError(RuntimeError):
    """単価が、0未満のときのエラー。"""


class DuplicateRateHistoryError(RuntimeError):
    """同じ社員・参画・適用開始日の単価が、既にあるときのエラー（`TBL-017`）。"""


@dataclass(frozen=True)
class RateHistory:
    """単価履歴の1行。`assignment_label` は、社員単位の単価のとき `None`。"""

    id: int
    assignment_id: int | None
    assignment_label: str | None
    effective_from: date
    unit_price: int
    revision_basis: str | None
    note: str | None


@dataclass(frozen=True)
class RateHistoryInput:
    """単価の登録で使う入力値。"""

    assignment_id: int | None
    effective_from: date
    unit_price: int
    revision_basis: str | None
    note: str | None


def list_rate_history(connection: sqlite3.Connection, employee_id: int) -> tuple[RateHistory, ...]:
    """指定した社員の単価履歴を、適用開始日の昇順で返す。"""
    rows = connection.execute(
        "SELECT r.id, r.assignment_id, a.customer_name, a.project_name, r.effective_from, "
        "r.unit_price, r.revision_basis, r.note "
        "FROM rate_history r LEFT JOIN assignment a ON a.id = r.assignment_id "
        "WHERE r.employee_id = ? ORDER BY r.effective_from, r.id",
        (employee_id,),
    ).fetchall()
    return tuple(
        RateHistory(
            id=row[0],
            assignment_id=row[1],
            assignment_label=(f"{row[2]}／{row[3]}" if row[3] else row[2]) if row[1] else None,
            effective_from=date.fromisoformat(row[4]),
            unit_price=row[5],
            revision_basis=row[6],
            note=row[7],
        )
        for row in rows
    )


def register_rate_history(
    connection: sqlite3.Connection, employee_id: int, data: RateHistoryInput
) -> int:
    """単価を登録する。登録したIDを返す。"""
    if data.unit_price < 0:
        raise InvalidUnitPriceError("単価は、0以上の整数にしてください")

    with transaction(connection):
        if (
            connection.execute("SELECT 1 FROM employee WHERE id = ?", (employee_id,)).fetchone()
            is None
        ):
            raise EmployeeNotFoundError(f"社員（id={employee_id}）が見つかりません")
        if (
            data.assignment_id is not None
            and connection.execute(
                "SELECT 1 FROM assignment WHERE id = ? AND employee_id = ?",
                (data.assignment_id, employee_id),
            ).fetchone()
            is None
        ):
            raise AssignmentNotOwnedError("選んだ参画は、この社員の参画ではありません")
        if connection.execute(
            "SELECT 1 FROM rate_history WHERE employee_id = ? "
            "AND COALESCE(assignment_id, 0) = COALESCE(?, 0) AND effective_from = ?",
            (employee_id, data.assignment_id, data.effective_from.isoformat()),
        ).fetchone():
            raise DuplicateRateHistoryError("同じ参画・適用開始日の単価が、既に登録されています")

        values = {
            "employee_id": employee_id,
            "assignment_id": data.assignment_id,
            "effective_from": data.effective_from.isoformat(),
            "unit_price": data.unit_price,
            "revision_basis": data.revision_basis or None,
            "note": data.note or None,
        }
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

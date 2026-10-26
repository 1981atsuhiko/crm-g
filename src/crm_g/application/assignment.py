"""参画（`SCN-050` 参画タブ、`TBL-015`）。

参画は、社員が参加する顧客・案件の期間である。登録と編集だけを設け、削除は
設けない（`DBD-010`）。編集では社員を変えない。登録・編集は、`登録`・`更新`
として操作ログに記録する（`SCD-020`）。再計算の接続はPH-10で行う。
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

ASSIGNMENT_CATEGORIES = ("SES", "受託", "既存アカプラ", "教育原価")

_EDITABLE_COLUMNS = ("customer_name", "project_name", "category", "start_date", "end_date")


class EmployeeNotFoundError(RuntimeError):
    """指定したIDの社員が、存在しないときのエラー。"""


class AssignmentNotFoundError(RuntimeError):
    """指定したIDの参画が、存在しないときのエラー。"""


class InvalidCustomerNameError(RuntimeError):
    """顧客名が、空のときのエラー。"""


class InvalidCategoryError(RuntimeError):
    """区分が、定義した値以外のときのエラー。"""


class InvalidPeriodError(RuntimeError):
    """終了日が、開始日より前のときのエラー。"""


class RateOutsidePeriodError(RuntimeError):
    """期間の変更で、この参画の単価の適用開始日が期間の外に出るときのエラー。"""


@dataclass(frozen=True)
class Assignment:
    """参画の1行。"""

    id: int
    employee_id: int
    customer_name: str
    project_name: str | None
    category: str
    start_date: date
    end_date: date | None

    @property
    def label(self) -> str:
        """選択欄に出す表示名（顧客名・案件名・期間）。"""
        name = self.customer_name
        if self.project_name:
            name += f"／{self.project_name}"
        end = self.end_date.isoformat() if self.end_date else ""
        return f"{name}（{self.start_date.isoformat()}〜{end}）"


@dataclass(frozen=True)
class AssignmentInput:
    """参画の登録・編集で使う入力値。"""

    customer_name: str
    project_name: str | None
    category: str
    start_date: date
    end_date: date | None


def _validate(data: AssignmentInput) -> None:
    if not data.customer_name.strip():
        raise InvalidCustomerNameError("顧客名を入力してください")
    if data.category not in ASSIGNMENT_CATEGORIES:
        raise InvalidCategoryError(
            f"区分は、{'、'.join(ASSIGNMENT_CATEGORIES)}のいずれかにしてください"
        )
    if data.end_date is not None and data.end_date < data.start_date:
        raise InvalidPeriodError("終了日は、開始日以後にしてください")


def _values(data: AssignmentInput) -> dict[str, Any]:
    return {
        "customer_name": data.customer_name.strip(),
        "project_name": (data.project_name or "").strip() or None,
        "category": data.category,
        "start_date": data.start_date.isoformat(),
        "end_date": data.end_date.isoformat() if data.end_date else None,
    }


def _to_assignment(row: sqlite3.Row | tuple[Any, ...]) -> Assignment:
    return Assignment(
        id=row[0],
        employee_id=row[1],
        customer_name=row[2],
        project_name=row[3],
        category=row[4],
        start_date=date.fromisoformat(row[5]),
        end_date=date.fromisoformat(row[6]) if row[6] else None,
    )


def list_assignments(connection: sqlite3.Connection, employee_id: int) -> tuple[Assignment, ...]:
    """指定した社員の参画を、開始日の昇順で返す。"""
    rows = connection.execute(
        "SELECT id, employee_id, customer_name, project_name, category, start_date, end_date "
        "FROM assignment WHERE employee_id = ? ORDER BY start_date, id",
        (employee_id,),
    ).fetchall()
    return tuple(_to_assignment(row) for row in rows)


def create_assignment(
    connection: sqlite3.Connection, employee_id: int, data: AssignmentInput
) -> int:
    """参画を登録する。登録したIDを返す。"""
    _validate(data)
    with transaction(connection):
        if (
            connection.execute("SELECT 1 FROM employee WHERE id = ?", (employee_id,)).fetchone()
            is None
        ):
            raise EmployeeNotFoundError(f"社員（id={employee_id}）が見つかりません")

        values = _values(data)
        now = to_iso8601(utc_now())
        cursor = connection.execute(
            "INSERT INTO assignment (employee_id, customer_name, project_name, category, "
            "start_date, end_date, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (employee_id, *values.values(), now, now),
        )
        new_id = cursor.lastrowid
        assert new_id is not None  # noqa: S101 AUTOINCREMENT主キーのINSERT直後のため必ず取得できる
        record_operation_log(
            connection,
            Operation.CREATE,
            "assignment",
            OperationLogDetail(
                target_key=str(new_id), after_value={"employee_id": employee_id, **values}
            ),
        )
    return new_id


def update_assignment(
    connection: sqlite3.Connection, assignment_id: int, data: AssignmentInput
) -> None:
    """参画を編集する。社員は変えない。"""
    _validate(data)
    with transaction(connection):
        row = connection.execute(
            "SELECT customer_name, project_name, category, start_date, end_date "
            "FROM assignment WHERE id = ?",
            (assignment_id,),
        ).fetchone()
        if row is None:
            raise AssignmentNotFoundError(f"参画（id={assignment_id}）が見つかりません")

        values = _values(data)
        # 単価の適用開始日は、参画の期間の中にする（データ設計書 10章 No.12）。
        outside = connection.execute(
            "SELECT 1 FROM rate_history WHERE assignment_id = ? "
            "AND (effective_from < ? OR (? IS NOT NULL AND effective_from > ?))",
            (assignment_id, values["start_date"], values["end_date"], values["end_date"]),
        ).fetchone()
        if outside is not None:
            raise RateOutsidePeriodError(
                "この参画の単価の適用開始日が、期間の外になります。先に単価を直してください"
            )

        now = to_iso8601(utc_now())
        connection.execute(
            "UPDATE assignment SET customer_name = ?, project_name = ?, category = ?, "
            "start_date = ?, end_date = ?, updated_at = ? WHERE id = ?",
            (*values.values(), now, assignment_id),
        )
        before_value, after_value = changed_values(
            dict(zip(_EDITABLE_COLUMNS, row, strict=True)), values
        )
        if after_value:
            record_operation_log(
                connection,
                Operation.UPDATE,
                "assignment",
                OperationLogDetail(
                    target_key=str(assignment_id),
                    before_value=before_value,
                    after_value=after_value,
                ),
            )

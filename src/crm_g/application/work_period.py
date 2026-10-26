"""稼働期間（`SCN-050` 稼働期間タブ、`TBL-016`）。

稼働期間の一覧・登録・編集と、社員×月の表を扱う。削除は設けない（画面設計書 6.1）。
検証は、データ設計書 10章 No.2（同じ社員の期間が重ならない。編集では自身を除く）と
No.3（売上計上の状態は参画を持つ）による。参画の選択肢は、その社員の参画だけ
（画面設計書 6.1）。売上計上でない状態では、参画を保存しない。登録・編集は、
`登録`・`更新` として操作ログに記録する（`SCD-020`）。再計算の接続はPH-10で行う。
"""

import calendar
import sqlite3  # noqa: TID251 呼び出し側が開いた接続の型として使うだけで、接続はインフラ層に閉じ込める
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from typing import Any

from crm_g.application.employee import list_employees
from crm_g.application.operation_log import (
    Operation,
    OperationLogDetail,
    changed_values,
    record_operation_log,
)
from crm_g.application.percent import InvalidPercentError, parse_percent_to_bp
from crm_g.domain.business_days import is_present
from crm_g.domain.period import half_months
from crm_g.domain.period_overlap import periods_overlap
from crm_g.domain.work_period_summary import StatusPeriod, summarize_month
from crm_g.infrastructure.datetime_utils import to_iso8601, utc_now
from crm_g.infrastructure.db import transaction

_EDITABLE_COLUMNS = (
    "start_date",
    "end_date",
    "work_status_code",
    "assignment_id",
    "billing_ratio_bp",
    "note",
)


class EmployeeNotFoundError(RuntimeError):
    """指定したIDの社員が、存在しないときのエラー。"""


class WorkPeriodNotFoundError(RuntimeError):
    """指定したIDの稼働期間が、存在しないときのエラー。"""


class InvalidWorkStatusError(RuntimeError):
    """稼働状態が、定義した値以外のときのエラー。"""


class InvalidPeriodError(RuntimeError):
    """終了日が、開始日より前のときのエラー。"""


class MissingAssignmentError(RuntimeError):
    """売上計上の状態なのに、参画が選ばれていないときのエラー（データ設計書 10章 No.3）。"""


class AssignmentNotOwnedError(RuntimeError):
    """選んだ参画が、存在しないか、その社員の参画でないときのエラー。"""


class InvalidBillingRatioError(RuntimeError):
    """稼働率が、0〜100・小数第2位までの数値でないときのエラー。"""


class PeriodOverlapError(RuntimeError):
    """同じ社員の他の稼働期間と、期間が重なるときのエラー（データ設計書 10章 No.2）。"""


@dataclass(frozen=True)
class WorkStatusOption:
    """稼働状態の選択肢。"""

    code: str
    name: str
    is_billable: bool


@dataclass(frozen=True)
class WorkPeriod:
    """稼働期間の1行。"""

    id: int
    employee_id: int
    start_date: date
    end_date: date | None
    work_status_code: str
    work_status_name: str
    assignment_id: int | None
    assignment_label: str | None
    billing_ratio_bp: int
    note: str | None


@dataclass(frozen=True)
class WorkPeriodInput:
    """稼働期間の登録・編集で使う入力値。稼働率は、画面の「%」の文字列のまま受け取る。"""

    start_date: date
    end_date: date | None
    work_status_code: str
    assignment_id: int | None
    billing_ratio_percent: str
    note: str | None


@dataclass(frozen=True)
class MonthGridRow:
    """社員×月の表の1行。`cells` は、半期の6か月の順。"""

    employee_id: int
    employee_name: str
    cells: tuple[str, ...]


def list_work_statuses(connection: sqlite3.Connection) -> tuple[WorkStatusOption, ...]:
    """稼働状態の選択肢を、表示順で返す。"""
    rows = connection.execute(
        "SELECT code, name, is_billable FROM work_status ORDER BY sort_order"
    ).fetchall()
    return tuple(
        WorkStatusOption(code=row[0], name=row[1], is_billable=bool(row[2])) for row in rows
    )


def list_work_periods(connection: sqlite3.Connection, employee_id: int) -> tuple[WorkPeriod, ...]:
    """指定した社員の稼働期間を、開始日の昇順で返す。"""
    rows = connection.execute(
        "SELECT p.id, p.employee_id, p.start_date, p.end_date, p.work_status_code, s.name, "
        "p.assignment_id, a.customer_name, a.project_name, p.billing_ratio_bp, p.note "
        "FROM work_period p JOIN work_status s ON s.code = p.work_status_code "
        "LEFT JOIN assignment a ON a.id = p.assignment_id "
        "WHERE p.employee_id = ? ORDER BY p.start_date, p.id",
        (employee_id,),
    ).fetchall()
    return tuple(
        WorkPeriod(
            id=row[0],
            employee_id=row[1],
            start_date=date.fromisoformat(row[2]),
            end_date=date.fromisoformat(row[3]) if row[3] else None,
            work_status_code=row[4],
            work_status_name=row[5],
            assignment_id=row[6],
            assignment_label=(f"{row[7]}／{row[8]}" if row[8] else row[7]) if row[6] else None,
            billing_ratio_bp=row[9],
            note=row[10],
        )
        for row in rows
    )


def _validate(
    connection: sqlite3.Connection,
    employee_id: int,
    data: WorkPeriodInput,
    *,
    exclude_id: int | None,
) -> dict[str, Any]:
    """入力を検証し、保存する列の値を返す。トランザクションの中で呼ぶ。"""
    if data.end_date is not None and data.end_date < data.start_date:
        raise InvalidPeriodError("終了日は、開始日以後にしてください")
    try:
        billing_ratio_bp = parse_percent_to_bp(data.billing_ratio_percent, "稼働率")
    except InvalidPercentError as error:
        raise InvalidBillingRatioError(str(error)) from error

    status = connection.execute(
        "SELECT is_billable FROM work_status WHERE code = ?", (data.work_status_code,)
    ).fetchone()
    if status is None:
        raise InvalidWorkStatusError("稼働状態を選んでください")
    is_billable = bool(status[0])

    assignment_id = data.assignment_id if is_billable else None
    if is_billable:
        if assignment_id is None:
            raise MissingAssignmentError("売上計上の稼働状態では、参画を選んでください")
        if (
            connection.execute(
                "SELECT 1 FROM assignment WHERE id = ? AND employee_id = ?",
                (assignment_id, employee_id),
            ).fetchone()
            is None
        ):
            raise AssignmentNotOwnedError("選んだ参画は、この社員の参画ではありません")

    for other_id, other_start, other_end in connection.execute(
        "SELECT id, start_date, end_date FROM work_period WHERE employee_id = ?", (employee_id,)
    ).fetchall():
        if other_id == exclude_id:
            continue
        if periods_overlap(
            date.fromisoformat(other_start),
            date.fromisoformat(other_end) if other_end else None,
            data.start_date,
            data.end_date,
        ):
            raise PeriodOverlapError(
                f"他の稼働期間（{other_start}〜{other_end or ''}）と、期間が重なっています"
            )

    return {
        "start_date": data.start_date.isoformat(),
        "end_date": data.end_date.isoformat() if data.end_date else None,
        "work_status_code": data.work_status_code,
        "assignment_id": assignment_id,
        "billing_ratio_bp": billing_ratio_bp,
        "note": (data.note or "").strip() or None,
    }


def create_work_period(
    connection: sqlite3.Connection, employee_id: int, data: WorkPeriodInput
) -> int:
    """稼働期間を登録する。登録したIDを返す。"""
    with transaction(connection):
        if (
            connection.execute("SELECT 1 FROM employee WHERE id = ?", (employee_id,)).fetchone()
            is None
        ):
            raise EmployeeNotFoundError(f"社員（id={employee_id}）が見つかりません")
        values = {
            "employee_id": employee_id,
            **_validate(connection, employee_id, data, exclude_id=None),
        }
        now = to_iso8601(utc_now())
        cursor = connection.execute(
            "INSERT INTO work_period (employee_id, start_date, end_date, work_status_code, "
            "assignment_id, billing_ratio_bp, note, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (*values.values(), now, now),
        )
        new_id = cursor.lastrowid
        assert new_id is not None  # noqa: S101 AUTOINCREMENT主キーのINSERT直後のため必ず取得できる
        record_operation_log(
            connection,
            Operation.CREATE,
            "work_period",
            OperationLogDetail(target_key=str(new_id), after_value=values),
        )
    return new_id


def update_work_period(
    connection: sqlite3.Connection, work_period_id: int, data: WorkPeriodInput
) -> None:
    """稼働期間を編集する。社員は変えない。"""
    with transaction(connection):
        row = connection.execute(
            "SELECT employee_id, start_date, end_date, work_status_code, assignment_id, "
            "billing_ratio_bp, note FROM work_period WHERE id = ?",
            (work_period_id,),
        ).fetchone()
        if row is None:
            raise WorkPeriodNotFoundError(f"稼働期間（id={work_period_id}）が見つかりません")
        values = _validate(connection, row[0], data, exclude_id=work_period_id)
        now = to_iso8601(utc_now())
        connection.execute(
            "UPDATE work_period SET start_date = ?, end_date = ?, work_status_code = ?, "
            "assignment_id = ?, billing_ratio_bp = ?, note = ?, updated_at = ? WHERE id = ?",
            (*values.values(), now, work_period_id),
        )
        before_value, after_value = changed_values(
            dict(zip(_EDITABLE_COLUMNS, row[1:], strict=True)), values
        )
        if after_value:
            record_operation_log(
                connection,
                Operation.UPDATE,
                "work_period",
                OperationLogDetail(
                    target_key=str(work_period_id),
                    before_value=before_value,
                    after_value=after_value,
                ),
            )


def _business_days(connection: sqlite3.Connection, first: date, last: date) -> tuple[date, ...]:
    rows = connection.execute(
        "SELECT calendar_date FROM business_calendar "
        "WHERE calendar_date BETWEEN ? AND ? AND is_business_day = 1 ORDER BY calendar_date",
        (first.isoformat(), last.isoformat()),
    ).fetchall()
    return tuple(date.fromisoformat(row[0]) for row in rows)


def list_month_grid(
    connection: sqlite3.Connection,
    fiscal_year: int,
    half: int,
    enrollment_statuses: Sequence[str],
    search: str = "",
) -> tuple[MonthGridRow, ...]:
    """社員×月の表を返す（`SCN-050`）。営業日カレンダーがない月は、未設定を判定しない。"""
    months = half_months(fiscal_year, half)
    month_ranges = [
        (date(year, month, 1), date(year, month, calendar.monthrange(year, month)[1]))
        for year, month in months
    ]
    business_days = [_business_days(connection, first, last) for first, last in month_ranges]

    grid: list[MonthGridRow] = []
    for employee in list_employees(connection, tuple(enrollment_statuses), search):
        # 絞り込みは氏名の部分一致だけとする（画面設計書 6.1。社員一覧はメールアドレスも見る）。
        if search and search.casefold() not in employee.name.casefold():
            continue
        periods = tuple(
            StatusPeriod(period.start_date, period.end_date, period.work_status_name)
            for period in list_work_periods(connection, employee.id)
        )
        cells = tuple(
            summarize_month(
                first,
                last,
                periods,
                tuple(
                    day for day in days if is_present(day, employee.join_date, employee.retire_date)
                ),
            )
            for (first, last), days in zip(month_ranges, business_days, strict=True)
        )
        grid.append(MonthGridRow(employee.id, employee.name, cells))
    return tuple(grid)

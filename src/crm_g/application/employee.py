"""社員（`SCN-020`、`SCN-021`、`TBL-001`）。

一覧・詳細の閲覧に必要な参照機能をまとめる。登録・編集はPH-07-2で追加する。
"""

import sqlite3  # noqa: TID251 呼び出し側が開いた接続の型として使うだけで、接続はインフラ層に閉じ込める
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date

ENROLLMENT_STATUSES = ("在籍", "退職予定", "退職", "契約満了", "移管")
DEFAULT_ENROLLMENT_STATUSES = ("在籍", "退職予定")


@dataclass(frozen=True)
class EmployeeSummary:
    """社員一覧の1行分（`SCN-020`。生年月日・性別・居住エリアを含めない）。"""

    id: int
    name: str
    name_kana: str | None
    email: str
    employee_no: str | None
    employment_type: str | None
    job_type: str | None
    enrollment_status: str
    join_date: date | None
    retire_date: date | None


@dataclass(frozen=True)
class Employee:
    """社員の基本情報（`SCN-021`、`TBL-001`の全項目）。"""

    id: int
    email: str
    name: str
    name_kana: str | None
    employee_no: str | None
    employment_type: str | None
    birth_date: date | None
    gender: str | None
    join_date: date | None
    nearest_station: str | None
    residence_area: str | None
    job_type: str | None
    enrollment_status: str
    retire_date: date | None


@dataclass(frozen=True)
class SalaryHistoryRow:
    """給与履歴の1行分（`SCN-021` 給与履歴タブ）。"""

    id: int
    effective_from: date
    effective_to: date | None
    pay_system: str
    employment_type: str
    grade: str | None
    revision_type: str
    note: str | None
    monthly_salary: int


@dataclass(frozen=True)
class SalaryHistoryItemRow:
    """給与履歴の内訳1行分。"""

    pay_item_code: str
    pay_item_name: str
    amount: int


@dataclass(frozen=True)
class RateHistoryRow:
    """単価履歴の1行分（`SCN-021` 単価履歴タブ）。"""

    effective_from: date
    customer_name: str | None
    project_name: str | None
    unit_price: int
    revision_basis: str | None
    note: str | None


@dataclass(frozen=True)
class WorkPeriodRow:
    """稼働の1行分（`SCN-021` 稼働タブ）。"""

    start_date: date
    end_date: date | None
    work_status_name: str
    customer_name: str | None
    project_name: str | None
    billing_ratio_bp: int
    note: str | None


@dataclass(frozen=True)
class BonusRow:
    """賞与の1行分（`SCN-021` 賞与タブ）。"""

    eval_half: str
    pay_month: str
    base_type: str
    base_amount: int
    eval_rank: str | None
    rate_bp: int | None
    bonus_amount: int
    is_closed: bool


_SORT_KEY_COLUMN = "COALESCE(name_kana, name)"


def list_employees(
    connection: sqlite3.Connection,
    enrollment_statuses: Sequence[str] = DEFAULT_ENROLLMENT_STATUSES,
    search: str = "",
) -> tuple[EmployeeSummary, ...]:
    """社員の一覧を、氏名（カナがあればカナ）の昇順で返す（`SCN-020`）。"""
    if not enrollment_statuses:
        return ()

    # placeholders は "?" だけで構成される（利用者の入力は含まない）。値は、
    # 下の params を通じてプレースホルダに束縛するため、SQLインジェクションの
    # リスクはない。
    placeholders = ", ".join("?" for _ in enrollment_statuses)
    query = (
        "SELECT id, name, name_kana, email, employee_no, employment_type, job_type, "
        "enrollment_status, join_date, retire_date "
        "FROM employee WHERE enrollment_status IN (" + placeholders + ")"
    )
    params: list[object] = list(enrollment_statuses)

    if search:
        query += " AND (name LIKE ? ESCAPE '\\' OR email LIKE ? ESCAPE '\\')"
        pattern = f"%{_escape_like(search)}%"
        params.extend([pattern, pattern])

    query += f" ORDER BY {_SORT_KEY_COLUMN}"

    rows = connection.execute(query, params).fetchall()
    return tuple(
        EmployeeSummary(
            id=row[0],
            name=row[1],
            name_kana=row[2],
            email=row[3],
            employee_no=row[4],
            employment_type=row[5],
            job_type=row[6],
            enrollment_status=row[7],
            join_date=date.fromisoformat(row[8]) if row[8] else None,
            retire_date=date.fromisoformat(row[9]) if row[9] else None,
        )
        for row in rows
    )


def _escape_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def get_employee(connection: sqlite3.Connection, employee_id: int) -> Employee | None:
    """指定した社員の基本情報を返す。存在しなければ`None`。"""
    row = connection.execute(
        "SELECT id, email, name, name_kana, employee_no, employment_type, birth_date, "
        "gender, join_date, nearest_station, residence_area, job_type, "
        "enrollment_status, retire_date FROM employee WHERE id = ?",
        (employee_id,),
    ).fetchone()
    if row is None:
        return None
    return Employee(
        id=row[0],
        email=row[1],
        name=row[2],
        name_kana=row[3],
        employee_no=row[4],
        employment_type=row[5],
        birth_date=date.fromisoformat(row[6]) if row[6] else None,
        gender=row[7],
        join_date=date.fromisoformat(row[8]) if row[8] else None,
        nearest_station=row[9],
        residence_area=row[10],
        job_type=row[11],
        enrollment_status=row[12],
        retire_date=date.fromisoformat(row[13]) if row[13] else None,
    )


def list_salary_history(
    connection: sqlite3.Connection, employee_id: int
) -> tuple[SalaryHistoryRow, ...]:
    """指定した社員の給与履歴を、適用開始日の昇順で返す（`SCN-021`）。

    月給合計は、内訳（`salary_history_item`）の合計をここで計算して返す
    （保存しない。`REQ-DAT-009`）。
    """
    rows = connection.execute(
        "SELECT h.id, h.effective_from, h.effective_to, h.pay_system, "
        "h.employment_type, h.grade, h.revision_type, h.note, "
        "COALESCE((SELECT SUM(i.amount) FROM salary_history_item i "
        "WHERE i.salary_history_id = h.id), 0) "
        "FROM salary_history h WHERE h.employee_id = ? ORDER BY h.effective_from",
        (employee_id,),
    ).fetchall()
    return tuple(
        SalaryHistoryRow(
            id=row[0],
            effective_from=date.fromisoformat(row[1]),
            effective_to=date.fromisoformat(row[2]) if row[2] else None,
            pay_system=row[3],
            employment_type=row[4],
            grade=row[5],
            revision_type=row[6],
            note=row[7],
            monthly_salary=row[8],
        )
        for row in rows
    )


def list_salary_history_items(
    connection: sqlite3.Connection, salary_history_id: int
) -> tuple[SalaryHistoryItemRow, ...]:
    """指定した給与履歴の内訳を、給与項目の表示順で返す。"""
    rows = connection.execute(
        "SELECT i.pay_item_code, p.name, i.amount FROM salary_history_item i "
        "JOIN pay_item p ON p.code = i.pay_item_code "
        "WHERE i.salary_history_id = ? ORDER BY p.sort_order",
        (salary_history_id,),
    ).fetchall()
    return tuple(
        SalaryHistoryItemRow(pay_item_code=row[0], pay_item_name=row[1], amount=row[2])
        for row in rows
    )


def list_rate_history(
    connection: sqlite3.Connection, employee_id: int
) -> tuple[RateHistoryRow, ...]:
    """指定した社員の単価履歴を、適用開始日の昇順で返す（`SCN-021`）。"""
    rows = connection.execute(
        "SELECT r.effective_from, a.customer_name, a.project_name, r.unit_price, "
        "r.revision_basis, r.note FROM rate_history r "
        "LEFT JOIN assignment a ON a.id = r.assignment_id "
        "WHERE r.employee_id = ? ORDER BY r.effective_from",
        (employee_id,),
    ).fetchall()
    return tuple(
        RateHistoryRow(
            effective_from=date.fromisoformat(row[0]),
            customer_name=row[1],
            project_name=row[2],
            unit_price=row[3],
            revision_basis=row[4],
            note=row[5],
        )
        for row in rows
    )


def list_work_periods(
    connection: sqlite3.Connection, employee_id: int
) -> tuple[WorkPeriodRow, ...]:
    """指定した社員の稼働を、開始日の昇順で返す（`SCN-021`）。"""
    rows = connection.execute(
        "SELECT w.start_date, w.end_date, s.name, a.customer_name, a.project_name, "
        "w.billing_ratio_bp, w.note FROM work_period w "
        "JOIN work_status s ON s.code = w.work_status_code "
        "LEFT JOIN assignment a ON a.id = w.assignment_id "
        "WHERE w.employee_id = ? ORDER BY w.start_date",
        (employee_id,),
    ).fetchall()
    return tuple(
        WorkPeriodRow(
            start_date=date.fromisoformat(row[0]),
            end_date=date.fromisoformat(row[1]) if row[1] else None,
            work_status_name=row[2],
            customer_name=row[3],
            project_name=row[4],
            billing_ratio_bp=row[5],
            note=row[6],
        )
        for row in rows
    )


def list_bonuses(connection: sqlite3.Connection, employee_id: int) -> tuple[BonusRow, ...]:
    """指定した社員の賞与を、半期の昇順で返す（`SCN-021`）。"""
    rows = connection.execute(
        "SELECT eval_half, pay_month, base_type, base_amount, eval_rank, rate_bp, "
        "bonus_amount, is_closed FROM bonus WHERE employee_id = ? ORDER BY eval_half",
        (employee_id,),
    ).fetchall()
    return tuple(
        BonusRow(
            eval_half=row[0],
            pay_month=row[1],
            base_type=row[2],
            base_amount=row[3],
            eval_rank=row[4],
            rate_bp=row[5],
            bonus_amount=row[6],
            is_closed=bool(row[7]),
        )
        for row in rows
    )

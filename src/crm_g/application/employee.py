"""社員（`SCN-020`、`SCN-021`、`SCN-022`、`TBL-001`）。

一覧・詳細の閲覧（PH-07-1）に加え、登録・編集（PH-07-2）をまとめる。登録・
編集は、`登録`・`更新` として操作ログに記録する（`SCD-020`）。再計算の接続は
PH-10で行う。
"""

import sqlite3  # noqa: TID251 呼び出し側が開いた接続の型として使うだけで、接続はインフラ層に閉じ込める
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from typing import Any

from crm_g.application.operation_log import (
    Operation,
    OperationLogDetail,
    changed_values,
    record_operation_log,
)
from crm_g.domain.email_format import is_valid_email_format
from crm_g.infrastructure.datetime_utils import to_iso8601, utc_now
from crm_g.infrastructure.db import transaction

ENROLLMENT_STATUSES = ("在籍", "退職予定", "退職", "契約満了", "移管")
DEFAULT_ENROLLMENT_STATUSES = ("在籍", "退職予定")
EMPLOYMENT_TYPES = ("正社員", "契約社員")
GENDERS = ("男性", "女性", "その他")


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


class InvalidEmailFormatError(RuntimeError):
    """メールアドレスが、メールアドレス形式でないときのエラー。"""


class DuplicateEmailError(RuntimeError):
    """メールアドレスが、既存の社員と重複するときのエラー。"""


class InvalidNameError(RuntimeError):
    """氏名が、空のときのエラー。"""


class DuplicateEmployeeNoError(RuntimeError):
    """社員番号が、既存の社員と重複するときのエラー。"""


class InvalidEmploymentTypeError(RuntimeError):
    """雇用形態が、定義した値以外のときのエラー。"""


class InvalidGenderError(RuntimeError):
    """性別が、定義した値以外のときのエラー。"""


class InvalidEnrollmentStatusError(RuntimeError):
    """在籍ステータスが、定義した値以外のときのエラー。"""


class InvalidBirthDateError(RuntimeError):
    """生年月日が、今日以後のときのエラー。"""


class InvalidRetireDateError(RuntimeError):
    """退職日が、入社日より前のときのエラー。"""


class EmployeeNotFoundError(RuntimeError):
    """指定したIDの社員が、存在しないときのエラー。"""


@dataclass(frozen=True)
class EmployeeInput:
    """社員の登録・変更で使う入力値（`SCN-022`）。

    `email` は、登録のときだけ使う。変更では、既存の値を保つ（業務キーのため、
    変更できない。`update_employee` はこの項目を書き込まない）。
    """

    email: str
    name: str
    name_kana: str | None
    employee_no: str | None
    employment_type: str | None
    birth_date: date | None
    gender: str | None
    join_date: date
    nearest_station: str | None
    residence_area: str | None
    job_type: str | None
    enrollment_status: str
    retire_date: date | None


def _validate_employee_fields(data: EmployeeInput) -> None:
    if not data.name:
        raise InvalidNameError("氏名を入力してください")
    if data.employment_type is not None and data.employment_type not in EMPLOYMENT_TYPES:
        raise InvalidEmploymentTypeError(
            f"雇用形態は、{'、'.join(EMPLOYMENT_TYPES)}のいずれかにしてください"
        )
    if data.gender is not None and data.gender not in GENDERS:
        raise InvalidGenderError(f"性別は、{'、'.join(GENDERS)}のいずれかにしてください")
    if data.enrollment_status not in ENROLLMENT_STATUSES:
        raise InvalidEnrollmentStatusError(
            f"在籍ステータスは、{'、'.join(ENROLLMENT_STATUSES)}のいずれかにしてください"
        )
    if data.birth_date is not None and data.birth_date >= date.today():
        raise InvalidBirthDateError("生年月日は、今日より前にしてください")
    if data.retire_date is not None and data.retire_date < data.join_date:
        raise InvalidRetireDateError("退職日は、入社日以後にしてください")


def _check_duplicate_employee_no(
    connection: sqlite3.Connection, employee_no: str | None, *, exclude_id: int | None
) -> None:
    if not employee_no:
        return
    query = "SELECT 1 FROM employee WHERE employee_no = ?"
    params: list[object] = [employee_no]
    if exclude_id is not None:
        query += " AND id != ?"
        params.append(exclude_id)
    if connection.execute(query, params).fetchone() is not None:
        raise DuplicateEmployeeNoError(f"社員番号 {employee_no} は、既に使われています")


_EDITABLE_COLUMNS = (
    "name",
    "name_kana",
    "employee_no",
    "employment_type",
    "birth_date",
    "gender",
    "join_date",
    "nearest_station",
    "residence_area",
    "job_type",
    "enrollment_status",
    "retire_date",
)


def _editable_values(data: EmployeeInput) -> dict[str, Any]:
    return {
        "name": data.name,
        "name_kana": data.name_kana,
        "employee_no": data.employee_no,
        "employment_type": data.employment_type,
        "birth_date": data.birth_date.isoformat() if data.birth_date else None,
        "gender": data.gender,
        "join_date": data.join_date.isoformat(),
        "nearest_station": data.nearest_station,
        "residence_area": data.residence_area,
        "job_type": data.job_type,
        "enrollment_status": data.enrollment_status,
        "retire_date": data.retire_date.isoformat() if data.retire_date else None,
    }


def create_employee(connection: sqlite3.Connection, data: EmployeeInput) -> int:
    """社員を、新しいメールアドレスで登録する（`SCN-022`）。登録したIDを返す。"""
    email = data.email.strip().lower()
    if not is_valid_email_format(email):
        raise InvalidEmailFormatError("メールアドレスは、メールアドレス形式の文字列にしてください")
    _validate_employee_fields(data)

    with transaction(connection):
        if connection.execute("SELECT 1 FROM employee WHERE email = ?", (email,)).fetchone():
            raise DuplicateEmailError(f"メールアドレス {email} は、既に使われています")
        _check_duplicate_employee_no(connection, data.employee_no, exclude_id=None)

        now = to_iso8601(utc_now())
        values = {"email": email, **_editable_values(data)}
        cursor = connection.execute(
            "INSERT INTO employee "
            "(email, name, name_kana, employee_no, employment_type, birth_date, gender, "
            "join_date, nearest_station, residence_area, job_type, enrollment_status, "
            "retire_date, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (*values.values(), now, now),
        )
        new_id = cursor.lastrowid
        assert new_id is not None  # noqa: S101 AUTOINCREMENT主キーのINSERT直後のため必ず取得できる
        record_operation_log(
            connection,
            Operation.CREATE,
            "employee",
            OperationLogDetail(target_key=str(new_id), after_value=values),
        )

    return new_id


def update_employee(connection: sqlite3.Connection, employee_id: int, data: EmployeeInput) -> None:
    """既存の社員を変更する（`SCN-022`）。メールアドレスは変更しない（業務キーのため）。"""
    _validate_employee_fields(data)

    with transaction(connection):
        row = connection.execute(
            "SELECT name, name_kana, employee_no, employment_type, birth_date, gender, "
            "join_date, nearest_station, residence_area, job_type, enrollment_status, "
            "retire_date FROM employee WHERE id = ?",
            (employee_id,),
        ).fetchone()
        if row is None:
            raise EmployeeNotFoundError(f"社員（id={employee_id}）が見つかりません")
        _check_duplicate_employee_no(connection, data.employee_no, exclude_id=employee_id)

        values = _editable_values(data)
        now = to_iso8601(utc_now())
        connection.execute(
            "UPDATE employee SET name = ?, name_kana = ?, employee_no = ?, "
            "employment_type = ?, birth_date = ?, gender = ?, join_date = ?, "
            "nearest_station = ?, residence_area = ?, job_type = ?, "
            "enrollment_status = ?, retire_date = ?, updated_at = ? WHERE id = ?",
            (*values.values(), now, employee_id),
        )
        before_value, after_value = changed_values(
            dict(zip(_EDITABLE_COLUMNS, row, strict=True)), values
        )
        if after_value:
            record_operation_log(
                connection,
                Operation.UPDATE,
                "employee",
                OperationLogDetail(
                    target_key=str(employee_id),
                    before_value=before_value,
                    after_value=after_value,
                ),
            )

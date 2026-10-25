"""給与履歴の登録（`SCN-030`、`TBL-009`、データ設計書10章-1）。

既存の履歴の編集・削除は設けない。改定は、新しい履歴の追加で行う
（`REQ-CAL-015`）。登録すると、現在の履歴（`effective_to` が空の行）の
終了日が、新しい履歴の適用開始日の前日に設定される（`CLC-017`）。
"""

import sqlite3  # noqa: TID251 呼び出し側が開いた接続の型として使うだけで、接続はインフラ層に閉じ込める
from dataclasses import dataclass
from datetime import date

from crm_g.application.operation_log import Operation, OperationLogDetail, record_operation_log
from crm_g.domain.salary_revision import determine_previous_effective_to, needs_revision_warning
from crm_g.infrastructure.datetime_utils import to_iso8601, utc_now
from crm_g.infrastructure.db import transaction

PAY_SYSTEMS = ("通常評価制度", "年俸制", "単価連動")
EMPLOYMENT_TYPES = ("正社員", "契約社員")
REVISION_TYPES = ("定期（4月）", "定期（10月）", "年俸改定（4月）", "制度移行", "入社", "その他")


class EmployeeNotFoundError(RuntimeError):
    """指定したIDの社員が、存在しないときのエラー。"""


class InvalidPaySystemError(RuntimeError):
    """給与体系が、定義した値以外のときのエラー。"""


class InvalidEmploymentTypeError(RuntimeError):
    """雇用形態が、定義した値以外のときのエラー。"""


class InvalidRevisionTypeError(RuntimeError):
    """改定区分が、定義した値以外のときのエラー。"""


class MissingAnnualSalaryError(RuntimeError):
    """給与体系が年俸制なのに、年俸年収が未入力のときのエラー（`TBL-009`）。"""


class UnexpectedAnnualSalaryError(RuntimeError):
    """給与体系が年俸制でないのに、年俸年収が入力されているときのエラー（`TBL-009`）。"""


class InvalidItemAmountError(RuntimeError):
    """内訳の金額が、0未満のときのエラー。"""


class InvalidValidFromError(RuntimeError):
    """適用開始日が、現在の履歴の適用開始日以前のときのエラー（`CLC-017`）。"""


@dataclass(frozen=True)
class CurrentSalaryHistory:
    """選んだ社員の、現在の給与履歴（`effective_to` が空の行）。"""

    id: int
    effective_from: date
    monthly_salary: int


@dataclass(frozen=True)
class AvailablePayItem:
    """適用開始日に有効な給与項目（内訳の入力欄に出す）。"""

    code: str
    name: str


@dataclass(frozen=True)
class SalaryItemInput:
    """内訳の1行分の入力値。"""

    pay_item_code: str
    amount: int


@dataclass(frozen=True)
class SalaryHistoryInput:
    """給与履歴の登録で使う入力値。"""

    effective_from: date
    pay_system: str
    employment_type: str
    grade: str | None
    annual_salary: int | None
    revision_type: str
    note: str | None
    items: tuple[SalaryItemInput, ...]


@dataclass(frozen=True)
class RegistrationResult:
    """登録の結果（`UID-004`：送信後にだけ表示する情報）。"""

    salary_history_id: int
    monthly_salary: int
    previous_effective_to: date | None


@dataclass(frozen=True)
class RaiseRow:
    """昇給一覧の1行（`SCN-031`）。

    昇給額は、新しい月給合計から直前の履歴の月給合計を引いた値（`CLC-016`）。
    直前の履歴が無いときは、`previous_monthly_salary`・`raise_amount` ともに
    `None`（画面では「－」と表示する）。
    """

    employee_name: str
    effective_from: date
    revision_type: str
    pay_system: str
    previous_monthly_salary: int | None
    monthly_salary: int
    raise_amount: int | None


def get_current_salary_history(
    connection: sqlite3.Connection, employee_id: int
) -> CurrentSalaryHistory | None:
    """指定した社員の、現在の給与履歴を返す。無ければ`None`。"""
    row = connection.execute(
        "SELECT h.id, h.effective_from, "
        "COALESCE((SELECT SUM(i.amount) FROM salary_history_item i "
        "WHERE i.salary_history_id = h.id), 0) "
        "FROM salary_history h WHERE h.employee_id = ? AND h.effective_to IS NULL",
        (employee_id,),
    ).fetchone()
    if row is None:
        return None
    return CurrentSalaryHistory(
        id=row[0], effective_from=date.fromisoformat(row[1]), monthly_salary=row[2]
    )


def list_available_pay_items(
    connection: sqlite3.Connection, effective_from: date
) -> tuple[AvailablePayItem, ...]:
    """指定した適用開始日に有効な給与項目を、表示順で返す（`TBL-005`）。"""
    rows = connection.execute(
        "SELECT code, name FROM pay_item "
        "WHERE valid_from <= ? AND (valid_to IS NULL OR valid_to >= ?) "
        "ORDER BY sort_order",
        (effective_from.isoformat(), effective_from.isoformat()),
    ).fetchall()
    return tuple(AvailablePayItem(code=row[0], name=row[1]) for row in rows)


def check_needs_warning(
    connection: sqlite3.Connection, employee_id: int, data: SalaryHistoryInput
) -> bool:
    """改定月以外への登録に、警告を出すか判定する（`CLC-018`）。`employee_id`は未使用。"""
    del connection, employee_id  # このデータだけで判定できる（将来の拡張に備えて引数は残す）。
    return needs_revision_warning(data.pay_system, data.revision_type, data.effective_from.month)


def _validate(data: SalaryHistoryInput) -> None:
    if data.pay_system not in PAY_SYSTEMS:
        raise InvalidPaySystemError(f"給与体系は、{'、'.join(PAY_SYSTEMS)}のいずれかにしてください")
    if data.employment_type not in EMPLOYMENT_TYPES:
        raise InvalidEmploymentTypeError(
            f"雇用形態は、{'、'.join(EMPLOYMENT_TYPES)}のいずれかにしてください"
        )
    if data.revision_type not in REVISION_TYPES:
        raise InvalidRevisionTypeError(
            f"改定区分は、{'、'.join(REVISION_TYPES)}のいずれかにしてください"
        )

    if data.pay_system == "年俸制" and data.annual_salary is None:
        raise MissingAnnualSalaryError("給与体系が年俸制のときは、年俸年収を入力してください")
    if data.pay_system != "年俸制" and data.annual_salary is not None:
        raise UnexpectedAnnualSalaryError("給与体系が年俸制でないときは、年俸年収を入力できません")

    for item in data.items:
        if item.amount < 0:
            raise InvalidItemAmountError("内訳の金額は、0以上にしてください")


def register_salary_history(
    connection: sqlite3.Connection, employee_id: int, data: SalaryHistoryInput
) -> RegistrationResult:
    """給与履歴を登録する（`SCN-030`）。"""
    _validate(data)

    with transaction(connection):
        if (
            connection.execute("SELECT 1 FROM employee WHERE id = ?", (employee_id,)).fetchone()
            is None
        ):
            raise EmployeeNotFoundError(f"社員（id={employee_id}）が見つかりません")

        current = get_current_salary_history(connection, employee_id)
        now = to_iso8601(utc_now())
        previous_effective_to: date | None = None

        if current is not None:
            try:
                previous_effective_to = determine_previous_effective_to(
                    data.effective_from, current.effective_from
                )
            except ValueError as error:
                raise InvalidValidFromError(
                    "適用開始日は、現在の履歴の適用開始日より後にしてください"
                ) from error

            connection.execute(
                "UPDATE salary_history SET effective_to = ?, updated_at = ? WHERE id = ?",
                (previous_effective_to.isoformat(), now, current.id),
            )
            record_operation_log(
                connection,
                Operation.UPDATE,
                "salary_history",
                OperationLogDetail(
                    target_key=str(current.id),
                    before_value={"effective_to": None},
                    after_value={"effective_to": previous_effective_to.isoformat()},
                ),
            )

        cursor = connection.execute(
            "INSERT INTO salary_history "
            "(employee_id, effective_from, pay_system, employment_type, grade, "
            "annual_salary, revision_type, note, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                employee_id,
                data.effective_from.isoformat(),
                data.pay_system,
                data.employment_type,
                data.grade,
                data.annual_salary,
                data.revision_type,
                data.note,
                now,
                now,
            ),
        )
        new_id = cursor.lastrowid
        assert new_id is not None  # noqa: S101 AUTOINCREMENT主キーのINSERT直後のため必ず取得できる

        for item in data.items:
            connection.execute(
                "INSERT INTO salary_history_item "
                "(salary_history_id, pay_item_code, amount, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (new_id, item.pay_item_code, item.amount, now, now),
            )

        monthly_salary = sum(item.amount for item in data.items)
        record_operation_log(
            connection,
            Operation.CREATE,
            "salary_history",
            OperationLogDetail(
                target_key=str(new_id),
                after_value={
                    "effective_from": data.effective_from.isoformat(),
                    "pay_system": data.pay_system,
                    "employment_type": data.employment_type,
                    "revision_type": data.revision_type,
                    "monthly_salary": monthly_salary,
                },
            ),
        )

    return RegistrationResult(
        salary_history_id=new_id,
        monthly_salary=monthly_salary,
        previous_effective_to=previous_effective_to,
    )


def _escape_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def list_raises(
    connection: sqlite3.Connection, period_start: date, period_end: date, search: str = ""
) -> tuple[RaiseRow, ...]:
    """指定した期間に適用開始日がある昇給を、適用開始日の降順で返す（`SCN-031`）。

    直前の履歴が無い行は、`previous_monthly_salary`・`raise_amount` ともに
    `None`（`CLC-016`）。月給合計は保存せず、都度内訳の合計から求める
    （`REQ-DAT-009`、`get_current_salary_history`と同様の方針）。
    """
    query = (
        "WITH previous AS ("
        "  SELECT h.id AS history_id, ("
        "    SELECT h2.id FROM salary_history h2"
        "    WHERE h2.employee_id = h.employee_id AND h2.effective_from < h.effective_from"
        "    ORDER BY h2.effective_from DESC LIMIT 1"
        "  ) AS previous_id"
        "  FROM salary_history h"
        ") "
        "SELECT e.name, h.effective_from, h.revision_type, h.pay_system, "
        "COALESCE((SELECT SUM(i.amount) FROM salary_history_item i "
        "WHERE i.salary_history_id = h.id), 0) AS monthly_salary, "
        "CASE WHEN p.previous_id IS NULL THEN NULL "
        "ELSE COALESCE((SELECT SUM(i2.amount) FROM salary_history_item i2 "
        "WHERE i2.salary_history_id = p.previous_id), 0) END AS previous_monthly_salary "
        "FROM salary_history h "
        "JOIN employee e ON e.id = h.employee_id "
        "JOIN previous p ON p.history_id = h.id "
        "WHERE h.effective_from BETWEEN ? AND ?"
    )
    params: list[object] = [period_start.isoformat(), period_end.isoformat()]
    if search:
        query += " AND e.name LIKE ? ESCAPE '\\'"
        params.append(f"%{_escape_like(search)}%")
    query += " ORDER BY h.effective_from DESC"

    rows = connection.execute(query, params).fetchall()
    result = []
    for row in rows:
        previous_monthly_salary = row[5]
        monthly_salary = row[4]
        raise_amount = None
        if previous_monthly_salary is not None:
            raise_amount = monthly_salary - previous_monthly_salary
        result.append(
            RaiseRow(
                employee_name=row[0],
                effective_from=date.fromisoformat(row[1]),
                revision_type=row[2],
                pay_system=row[3],
                previous_monthly_salary=previous_monthly_salary,
                monthly_salary=monthly_salary,
                raise_amount=raise_amount,
            )
        )
    return tuple(result)

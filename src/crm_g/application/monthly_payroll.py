"""月次給与計算の結果の表示と、人事連携の設定（画面設計書 `SCN-032`）。

結果の一覧・内訳を返し、人事連携の対象／対象外と備考を変更する。締め済みの月の
人事連携は変更できない（`UID-054`）。変更は `更新` として操作ログに記録する
（`SCD-020`。対象テーブル `monthly_payroll`）。
"""

import sqlite3  # noqa: TID251 呼び出し側が開いた接続の型として使うだけで、接続はインフラ層に閉じ込める
from dataclasses import dataclass

from crm_g.application.operation_log import (
    Operation,
    OperationLogDetail,
    changed_values,
    record_operation_log,
)
from crm_g.application.recalculation import last_billable_unit_price
from crm_g.infrastructure.datetime_utils import to_iso8601, utc_now
from crm_g.infrastructure.db import transaction

HR_TARGETS = ("対象", "対象外")
_BASE_SALARY_CATEGORY = "基本給"


class ResultNotFoundError(RuntimeError):
    """指定した社員・月の計算結果が、存在しないときのエラー。"""


class ClosedMonthError(RuntimeError):
    """締め済みの月の人事連携を、変更しようとしたときのエラー（`UID-054`）。"""


class InvalidHrTargetError(RuntimeError):
    """人事連携が、`対象`／`対象外` 以外のときのエラー。"""


@dataclass(frozen=True)
class MonthlyResultRow:
    """結果の一覧の1行（画面設計書 `SCN-032` の「結果の一覧の列」）。"""

    employee_id: int
    employee_name: str
    monthly_salary: int
    base_salary: int
    allowance: int
    paid_amount: int
    deduction: int
    unit_price: int | None
    sales: int | None
    cost_total: int | None
    gross_profit: int | None
    work_status_summary: str | None
    hr_target: str
    hr_note: str | None
    check_result: str


@dataclass(frozen=True)
class PayrollItemRow:
    """内訳の1行（給与項目ごとの支給額と減額。`TBL-013`）。"""

    pay_item_name: str
    paid_amount: int
    deduction_amount: int


def list_monthly_results(
    connection: sqlite3.Connection, year_month: str
) -> tuple[MonthlyResultRow, ...]:
    """対象月の計算結果を、社員の表示順（カナ、なければ氏名）で返す。"""
    rows = connection.execute(
        "SELECT p.employee_id, e.name, p.monthly_salary, "
        "COALESCE((SELECT SUM(i.paid_amount) FROM monthly_payroll_item i "
        "  JOIN pay_item c ON c.code = i.pay_item_code "
        "  WHERE i.employee_id = p.employee_id AND i.year_month = p.year_month "
        "  AND c.category = ?), 0), "
        "COALESCE((SELECT SUM(i.paid_amount) FROM monthly_payroll_item i "
        "  JOIN pay_item c ON c.code = i.pay_item_code "
        "  WHERE i.employee_id = p.employee_id AND i.year_month = p.year_month "
        "  AND c.category != ?), 0), "
        "p.paid_amount, p.deduction, l.sales, l.cost_total, l.gross_profit, "
        "p.work_status_summary, p.hr_target, p.hr_note, p.check_result "
        "FROM monthly_payroll p JOIN employee e ON e.id = p.employee_id "
        "LEFT JOIN monthly_pl l ON l.employee_id = p.employee_id AND l.year_month = p.year_month "
        "WHERE p.year_month = ? ORDER BY COALESCE(e.name_kana, e.name), e.id",
        (_BASE_SALARY_CATEGORY, _BASE_SALARY_CATEGORY, year_month),
    ).fetchall()
    return tuple(
        MonthlyResultRow(
            employee_id=row[0],
            employee_name=row[1],
            monthly_salary=row[2],
            base_salary=row[3],
            allowance=row[4],
            paid_amount=row[5],
            deduction=row[6],
            unit_price=last_billable_unit_price(connection, row[0], year_month),
            sales=row[7],
            cost_total=row[8],
            gross_profit=row[9],
            work_status_summary=row[10],
            hr_target=row[11],
            hr_note=row[12],
            check_result=row[13],
        )
        for row in rows
    )


def list_payroll_items(
    connection: sqlite3.Connection, employee_id: int, year_month: str
) -> tuple[PayrollItemRow, ...]:
    """選んだ社員・月の内訳を、給与項目の表示順で返す。"""
    rows = connection.execute(
        "SELECT c.name, i.paid_amount, i.deduction_amount FROM monthly_payroll_item i "
        "JOIN pay_item c ON c.code = i.pay_item_code "
        "WHERE i.employee_id = ? AND i.year_month = ? ORDER BY c.sort_order, c.code",
        (employee_id, year_month),
    ).fetchall()
    return tuple(PayrollItemRow(row[0], row[1], row[2]) for row in rows)


def update_hr_settings(
    connection: sqlite3.Connection,
    employee_id: int,
    year_month: str,
    hr_target: str,
    hr_note: str | None,
) -> None:
    """人事連携の対象／対象外と備考を変更する。締め済みの月は変更できない。"""
    if hr_target not in HR_TARGETS:
        raise InvalidHrTargetError(f"人事連携は、{'、'.join(HR_TARGETS)}のどちらかにしてください")
    with transaction(connection):
        row = connection.execute(
            "SELECT hr_target, hr_note, is_closed FROM monthly_payroll "
            "WHERE employee_id = ? AND year_month = ?",
            (employee_id, year_month),
        ).fetchone()
        if row is None:
            raise ResultNotFoundError("計算結果が見つかりません")
        if row[2]:
            raise ClosedMonthError(
                "締め済みの月は変更できません。締めを解除してから変更してください"
            )

        values = {"hr_target": hr_target, "hr_note": (hr_note or "").strip() or None}
        before_value, after_value = changed_values({"hr_target": row[0], "hr_note": row[1]}, values)
        if not after_value:
            return
        connection.execute(
            "UPDATE monthly_payroll SET hr_target = ?, hr_note = ?, updated_at = ? "
            "WHERE employee_id = ? AND year_month = ?",
            (
                values["hr_target"],
                values["hr_note"],
                to_iso8601(utc_now()),
                employee_id,
                year_month,
            ),
        )
        record_operation_log(
            connection,
            Operation.UPDATE,
            "monthly_payroll",
            OperationLogDetail(
                target_key=f"{employee_id}:{year_month}",
                before_value=before_value,
                after_value=after_value,
            ),
        )

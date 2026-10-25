"""給与項目・手当マスタ（`SCN-062`、`TBL-005`、データ設計書4.5節）。

給与項目の削除は設けない。使わなくなった項目は、有効期間の終了日を設定して
無効にする（`DBD-010`）。`code` は主キーのため、追加後は変更できない。
"""

import re
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

_CODE_PATTERN = re.compile(r"^[A-Z0-9_]+$")
CATEGORIES = ("基本給", "固定支給", "一時手当")

_SELECT_SQL = (
    "SELECT code, name, category, in_monthly_salary, cost_direct, deduction_order, "
    "sort_order, valid_from, valid_to FROM pay_item"
)


class InvalidCodeError(RuntimeError):
    """コードが、英大文字・数字・`_` 以外を含むときのエラー。"""


class InvalidCategoryError(RuntimeError):
    """区分が、定義した値以外のときのエラー。"""


class DuplicateCodeError(RuntimeError):
    """コードが、既存の給与項目と重複するときのエラー。"""


class CodeNotFoundError(RuntimeError):
    """指定したコードの給与項目が、存在しないときのエラー。"""


class MutuallyExclusiveFlagsError(RuntimeError):
    """「月給に含む」と「原価に直接加算」が、同時に`○`のときのエラー（`TBL-005`）。"""


class InvalidDeductionOrderError(RuntimeError):
    """減額順が、1未満、または他の項目と重複するときのエラー。"""


class InvalidPeriodError(RuntimeError):
    """有効期間の終了日が、開始日より前のときのエラー。"""


@dataclass(frozen=True)
class PayItem:
    """給与項目1件分の情報。"""

    code: str
    name: str
    category: str
    in_monthly_salary: bool
    cost_direct: bool
    deduction_order: int | None
    sort_order: int
    valid_from: date
    valid_to: date | None


@dataclass(frozen=True)
class PayItemInput:
    """給与項目の登録・変更で使う入力値（`code` を除く）。"""

    name: str
    category: str
    in_monthly_salary: bool
    cost_direct: bool
    deduction_order: int | None
    sort_order: int
    valid_from: date
    valid_to: date | None


def _row_to_pay_item(row: tuple[Any, ...]) -> PayItem:
    (
        code,
        name,
        category,
        in_monthly_salary,
        cost_direct,
        deduction_order,
        sort_order,
        valid_from,
        valid_to,
    ) = row
    return PayItem(
        code=str(code),
        name=str(name),
        category=str(category),
        in_monthly_salary=bool(in_monthly_salary),
        cost_direct=bool(cost_direct),
        deduction_order=int(deduction_order) if deduction_order is not None else None,
        sort_order=int(sort_order),
        valid_from=date.fromisoformat(str(valid_from)),
        valid_to=date.fromisoformat(str(valid_to)) if valid_to else None,
    )


def list_pay_items(connection: sqlite3.Connection) -> tuple[PayItem, ...]:
    """給与項目の一覧を、表示順で返す。"""
    rows = connection.execute(f"{_SELECT_SQL} ORDER BY sort_order").fetchall()
    return tuple(_row_to_pay_item(row) for row in rows)


def get_pay_item(connection: sqlite3.Connection, code: str) -> PayItem | None:
    """指定したコードの給与項目を返す。存在しなければ`None`。"""
    row = connection.execute(f"{_SELECT_SQL} WHERE code = ?", (code,)).fetchone()
    return _row_to_pay_item(row) if row is not None else None


def _validate(
    connection: sqlite3.Connection, data: PayItemInput, *, exclude_code: str | None
) -> None:
    if data.category not in CATEGORIES:
        raise InvalidCategoryError(f"区分は、{'、'.join(CATEGORIES)}のいずれかにしてください")

    if data.in_monthly_salary and data.cost_direct:
        raise MutuallyExclusiveFlagsError(
            "「月給に含む」と「原価に直接加算」は、同時に○にできません"
        )

    if data.valid_to is not None and data.valid_to < data.valid_from:
        raise InvalidPeriodError("有効期間の終了日は、開始日以後にしてください")

    if data.deduction_order is not None:
        if data.deduction_order < 1:
            raise InvalidDeductionOrderError("減額順は、1以上の整数にしてください")

        query = "SELECT 1 FROM pay_item WHERE deduction_order = ?"
        params: tuple[object, ...] = (data.deduction_order,)
        if exclude_code is not None:
            query += " AND code != ?"
            params = (data.deduction_order, exclude_code)
        if connection.execute(query, params).fetchone() is not None:
            raise InvalidDeductionOrderError("減額順は、他の給与項目と重複できません")


_PAY_ITEM_COLUMNS = (
    "name",
    "category",
    "in_monthly_salary",
    "cost_direct",
    "deduction_order",
    "sort_order",
    "valid_from",
    "valid_to",
)


def _pay_item_values(data: PayItemInput) -> dict[str, object]:
    return {
        "name": data.name,
        "category": data.category,
        "in_monthly_salary": int(data.in_monthly_salary),
        "cost_direct": int(data.cost_direct),
        "deduction_order": data.deduction_order,
        "sort_order": data.sort_order,
        "valid_from": data.valid_from.isoformat(),
        "valid_to": data.valid_to.isoformat() if data.valid_to else None,
    }


def create_pay_item(connection: sqlite3.Connection, code: str, data: PayItemInput) -> None:
    """給与項目を、新しいコードで追加する（`SCN-062`）。"""
    if not _CODE_PATTERN.fullmatch(code):
        raise InvalidCodeError("コードは、英大文字・数字・_だけで入力してください")

    with transaction(connection):
        if connection.execute("SELECT 1 FROM pay_item WHERE code = ?", (code,)).fetchone():
            raise DuplicateCodeError(f"コード {code} は、既に使われています")

        _validate(connection, data, exclude_code=None)

        now = to_iso8601(utc_now())
        values = _pay_item_values(data)
        connection.execute(
            "INSERT INTO pay_item "
            "(code, name, category, in_monthly_salary, cost_direct, deduction_order, "
            "sort_order, valid_from, valid_to, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (code, *values.values(), now, now),
        )
        record_operation_log(
            connection,
            Operation.CREATE,
            "pay_item",
            OperationLogDetail(target_key=code, after_value={"code": code, **values}),
        )


def update_pay_item(connection: sqlite3.Connection, code: str, data: PayItemInput) -> None:
    """既存の給与項目を変更する（`SCN-062`）。コード自体は変更できない。"""
    with transaction(connection):
        row = connection.execute(
            "SELECT name, category, in_monthly_salary, cost_direct, deduction_order, "
            "sort_order, valid_from, valid_to FROM pay_item WHERE code = ?",
            (code,),
        ).fetchone()
        if row is None:
            raise CodeNotFoundError(f"コード {code} の給与項目が見つかりません")

        _validate(connection, data, exclude_code=code)

        now = to_iso8601(utc_now())
        values = _pay_item_values(data)
        connection.execute(
            "UPDATE pay_item SET name = ?, category = ?, in_monthly_salary = ?, "
            "cost_direct = ?, deduction_order = ?, sort_order = ?, valid_from = ?, "
            "valid_to = ?, updated_at = ? WHERE code = ?",
            (*values.values(), now, code),
        )
        before_value, after_value = changed_values(
            dict(zip(_PAY_ITEM_COLUMNS, row, strict=True)), values
        )
        if after_value:
            record_operation_log(
                connection,
                Operation.UPDATE,
                "pay_item",
                OperationLogDetail(
                    target_key=code, before_value=before_value, after_value=after_value
                ),
            )

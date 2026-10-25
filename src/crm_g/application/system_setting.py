"""計算設定（`SCN-063`、`system_setting`、`DBD-004`）。

原価係数は、有効期間付き（`work_status_rate`と同様の、新しい期間を追加する形の
変更）。万分率の整数で保存する（1.2倍＝12000）。端数処理は、画面からは変更
できない（`CLC-001`。`切り上げ`だけを定義している）。
"""

import sqlite3  # noqa: TID251 呼び出し側が開いた接続の型として使うだけで、接続はインフラ層に閉じ込める
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation

from crm_g.application.operation_log import Operation, OperationLogDetail, record_operation_log
from crm_g.domain.period_overlap import periods_overlap
from crm_g.infrastructure.datetime_utils import to_iso8601, utc_now
from crm_g.infrastructure.db import transaction

_COST_COEFFICIENT_KEY = "原価係数"
_ROUNDING_KEY = "端数処理"
_SCALE = Decimal("10000")  # 小数第4位まで


class InvalidCoefficientError(RuntimeError):
    """原価係数が、数値でない、0以下、または小数第4位を超えるときのエラー。"""


class InvalidValidFromError(RuntimeError):
    """適用開始日が、現在の原価係数の適用開始日以前のときのエラー。"""


class PeriodOverlapError(RuntimeError):
    """有効期間が、既存の期間と重なるときのエラー（データ設計書10章-9）。"""


@dataclass(frozen=True)
class CoefficientPeriod:
    """原価係数の履歴1行分。"""

    valid_from: date
    valid_to: date | None
    coefficient_bp: int


def coefficient_history(connection: sqlite3.Connection) -> tuple[CoefficientPeriod, ...]:
    """原価係数の履歴を、適用開始日の昇順で返す。"""
    rows = connection.execute(
        "SELECT valid_from, valid_to, value_integer FROM system_setting "
        "WHERE setting_key = ? ORDER BY valid_from",
        (_COST_COEFFICIENT_KEY,),
    ).fetchall()
    return tuple(
        CoefficientPeriod(
            valid_from=date.fromisoformat(row[0]),
            valid_to=date.fromisoformat(row[1]) if row[1] is not None else None,
            coefficient_bp=row[2],
        )
        for row in rows
    )


def current_rounding(connection: sqlite3.Connection) -> str:
    """現在の端数処理の値を返す（表示のみ。画面からは変更できない）。"""
    row = connection.execute(
        "SELECT value_text FROM system_setting WHERE setting_key = ? AND valid_to IS NULL",
        (_ROUNDING_KEY,),
    ).fetchone()
    return str(row[0]) if row is not None else ""


def _parse_coefficient(coefficient: str) -> int:
    """「原価係数（倍）」の入力文字列を、万分率の整数に変換する。"""
    try:
        value = Decimal(coefficient)
    except InvalidOperation as error:
        raise InvalidCoefficientError("原価係数は、数値で入力してください") from error

    if value.is_nan() or value.is_infinite():
        raise InvalidCoefficientError("原価係数は、数値で入力してください")
    if value <= 0:
        raise InvalidCoefficientError("原価係数は、0より大きい値にしてください")

    scaled = value * _SCALE
    if scaled != scaled.to_integral_value():
        raise InvalidCoefficientError("原価係数は、小数第4位までで入力してください")

    return int(scaled)


def change_coefficient(connection: sqlite3.Connection, coefficient: str, valid_from: date) -> None:
    """原価係数の新しい適用期間を登録する（`SCN-063`）。

    直前の期間がある場合は、その終了日を適用開始日の前日に設定してから、新しい行を
    追加する。既存の期間と重なるときは、登録しない（10章-9）。
    """
    new_coefficient_bp = _parse_coefficient(coefficient)

    with transaction(connection):
        existing_periods = coefficient_history(connection)
        current = next((period for period in existing_periods if period.valid_to is None), None)

        if current is not None and valid_from <= current.valid_from:
            raise InvalidValidFromError(
                "適用開始日は、現在の原価係数の適用開始日より後にしてください"
            )

        new_end = valid_from - timedelta(days=1) if current is not None else None
        adjusted_periods = [
            CoefficientPeriod(period.valid_from, new_end, period.coefficient_bp)
            if current is not None and period.valid_from == current.valid_from
            else period
            for period in existing_periods
        ]
        for period in adjusted_periods:
            if periods_overlap(period.valid_from, period.valid_to, valid_from, None):
                raise PeriodOverlapError("有効期間が、既存の期間と重なっています")

        now = to_iso8601(utc_now())
        if current is not None:
            connection.execute(
                "UPDATE system_setting SET valid_to = ?, updated_at = ? "
                "WHERE setting_key = ? AND valid_from = ?",
                (
                    new_end.isoformat() if new_end is not None else None,
                    now,
                    _COST_COEFFICIENT_KEY,
                    current.valid_from.isoformat(),
                ),
            )
            record_operation_log(
                connection,
                Operation.UPDATE,
                "system_setting",
                OperationLogDetail(
                    target_key=f"{_COST_COEFFICIENT_KEY}:{current.valid_from.isoformat()}",
                    before_value={"valid_to": None},
                    after_value={"valid_to": new_end.isoformat() if new_end else None},
                ),
            )

        connection.execute(
            "INSERT INTO system_setting "
            "(setting_key, valid_from, valid_to, value_integer, created_at, updated_at) "
            "VALUES (?, ?, NULL, ?, ?, ?)",
            (_COST_COEFFICIENT_KEY, valid_from.isoformat(), new_coefficient_bp, now, now),
        )
        record_operation_log(
            connection,
            Operation.CREATE,
            "system_setting",
            OperationLogDetail(
                target_key=f"{_COST_COEFFICIENT_KEY}:{valid_from.isoformat()}",
                after_value={
                    "setting_key": _COST_COEFFICIENT_KEY,
                    "valid_from": valid_from.isoformat(),
                    "valid_to": None,
                    "value_integer": new_coefficient_bp,
                },
            ),
        )

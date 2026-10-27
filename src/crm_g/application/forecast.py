"""見込みの入力と一覧（画面設計書 `SCN-051` の「見込み」、計算仕様書 `CLC-023`）。

- 見込みは、社員と年月で1件（`TBL-018`）。同じ社員・年月の見込みがあるときは、更新として扱う
- 見込み売上は、見込み単価 × 見込み稼働率の切り上げ。どちらかが空なら計算しない（保存しない）
- 登録・更新は、`登録`・`更新`（対象テーブル `forecast`）として操作ログに記録する（`SCD-020`）
"""

import sqlite3  # noqa: TID251 呼び出し側が開いた接続の型として使うだけで、接続はインフラ層に閉じ込める
from dataclasses import dataclass
from fractions import Fraction
from typing import Any

from crm_g.application.operation_log import (
    Operation,
    OperationLogDetail,
    changed_values,
    record_operation_log,
)
from crm_g.application.percent import InvalidPercentError, parse_percent_to_bp
from crm_g.domain.rounding import ceil_yen
from crm_g.infrastructure.datetime_utils import to_iso8601, utc_now
from crm_g.infrastructure.db import transaction

CONFIDENCES = ("確定", "高", "低", "待機")
_BP = 10000
_COLUMNS = ("confidence", "forecast_unit_price", "forecast_billing_ratio_bp")


class InvalidForecastError(ValueError):
    """見込みの入力が正しくないときのエラー。"""


@dataclass(frozen=True)
class ForecastInput:
    """見込みの入力。金額・稼働率は、画面の文字列のまま受け取る（空なら未入力）。"""

    employee_id: int
    year_month: str
    confidence: str
    unit_price: str
    billing_ratio_percent: str


@dataclass(frozen=True)
class ForecastRow:
    """見込みの一覧の1行。"""

    employee_id: int
    employee_name: str
    year_month: str
    confidence: str
    unit_price: int | None
    billing_ratio_bp: int | None

    @property
    def forecast_sales(self) -> int | None:
        """見込み売上（`CLC-023`）。見込み単価・見込み稼働率のどちらかが空なら None。"""
        return forecast_sales(self.unit_price, self.billing_ratio_bp)


@dataclass(frozen=True)
class ConfidenceTotal:
    """確度別の表の1行。人数は、その確度の見込みがある社員の数（重複を除く）。"""

    confidence: str
    employee_count: int
    forecast_sales: int


def forecast_sales(unit_price: int | None, billing_ratio_bp: int | None) -> int | None:
    """見込み売上 = ceil(見込み単価 × 見込み稼働率)（`CLC-023`）。"""
    if unit_price is None or billing_ratio_bp is None:
        return None
    return ceil_yen(unit_price * Fraction(billing_ratio_bp, _BP))


def _parse_unit_price(text: str) -> int | None:
    value = text.strip().replace(",", "")
    if not value:
        return None
    if not value.isdecimal():
        raise InvalidForecastError("見込み単価は、0 以上の整数で入力してください")
    return int(value)


def _parse_ratio(text: str) -> int | None:
    value = text.strip()
    if not value:
        return None
    try:
        return parse_percent_to_bp(value, "見込み稼働率")
    except InvalidPercentError as error:
        raise InvalidForecastError(str(error)) from error


def _values(data: ForecastInput) -> dict[str, Any]:
    if data.confidence not in CONFIDENCES:
        raise InvalidForecastError(f"確度は、{'、'.join(CONFIDENCES)}のいずれかにしてください")
    return {
        "confidence": data.confidence,
        "forecast_unit_price": _parse_unit_price(data.unit_price),
        "forecast_billing_ratio_bp": _parse_ratio(data.billing_ratio_percent),
    }


def save_forecast(connection: sqlite3.Connection, data: ForecastInput) -> Operation | None:
    """見込みを登録する。同じ社員・年月があれば更新する。記録した操作（登録・更新）を返す。

    既存の見込みと値が変わらないときは、何もせず None を返す（操作ログも記録しない）。
    """
    values = _values(data)
    target_key = f"{data.employee_id}:{data.year_month}"
    with transaction(connection):
        if (
            connection.execute(
                "SELECT 1 FROM employee WHERE id = ?", (data.employee_id,)
            ).fetchone()
            is None
        ):
            raise InvalidForecastError("社員が見つかりません")
        existing = connection.execute(
            "SELECT confidence, forecast_unit_price, forecast_billing_ratio_bp FROM forecast "
            "WHERE employee_id = ? AND year_month = ?",
            (data.employee_id, data.year_month),
        ).fetchone()
        now = to_iso8601(utc_now())
        if existing is None:
            connection.execute(
                "INSERT INTO forecast (employee_id, year_month, confidence, forecast_unit_price, "
                "forecast_billing_ratio_bp, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (data.employee_id, data.year_month, *values.values(), now, now),
            )
            record_operation_log(
                connection,
                Operation.CREATE,
                "forecast",
                OperationLogDetail(target_key=target_key, after_value=values),
            )
            return Operation.CREATE
        before, after = changed_values(dict(zip(_COLUMNS, existing, strict=True)), values)
        if not after:
            return None
        connection.execute(
            "UPDATE forecast SET confidence = ?, forecast_unit_price = ?, "
            "forecast_billing_ratio_bp = ?, updated_at = ? "
            "WHERE employee_id = ? AND year_month = ?",
            (*values.values(), now, data.employee_id, data.year_month),
        )
        record_operation_log(
            connection,
            Operation.UPDATE,
            "forecast",
            OperationLogDetail(target_key=target_key, before_value=before, after_value=after),
        )
        return Operation.UPDATE


def list_forecasts(
    connection: sqlite3.Connection, first_month: str, last_month: str
) -> tuple[ForecastRow, ...]:
    """対象月の範囲の見込みを、年月・氏名（カナ）の順で返す。"""
    return tuple(
        ForecastRow(*row)
        for row in connection.execute(
            "SELECT f.employee_id, e.name, f.year_month, f.confidence, f.forecast_unit_price, "
            "f.forecast_billing_ratio_bp FROM forecast f JOIN employee e ON e.id = f.employee_id "
            "WHERE f.year_month BETWEEN ? AND ? "
            "ORDER BY f.year_month, COALESCE(e.name_kana, e.name), f.employee_id",
            (first_month, last_month),
        ).fetchall()
    )


def total_by_confidence(rows: tuple[ForecastRow, ...]) -> tuple[ConfidenceTotal, ...]:
    """確度ごとの人数と見込み売上の合計（見込み売上が空の行は合計に含めない）。"""
    return tuple(
        ConfidenceTotal(
            confidence,
            len({row.employee_id for row in rows if row.confidence == confidence}),
            sum(row.forecast_sales or 0 for row in rows if row.confidence == confidence),
        )
        for confidence in CONFIDENCES
    )

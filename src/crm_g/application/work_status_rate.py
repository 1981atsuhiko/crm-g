"""稼働状態の給与支給率（`SCN-061`、`DBD-015`、計算仕様書3.3）。

給与支給率は、新しい有効期間を追加する形でしか変更しない（稼働状態自体の
追加・名称変更・売上計上/原価計上の変更は、この画面では行わない）。
"""

import sqlite3  # noqa: TID251 呼び出し側が開いた接続の型として使うだけで、接続はインフラ層に閉じ込める
from dataclasses import dataclass
from datetime import date, timedelta

from crm_g.application.operation_log import Operation, OperationLogDetail, record_operation_log
from crm_g.application.percent import InvalidPercentError, parse_percent_to_bp
from crm_g.application.recalculation import (
    RecalculationListener,
    notify_recalculated,
    recalculate_after_change,
)
from crm_g.domain.period_overlap import periods_overlap
from crm_g.infrastructure.datetime_utils import to_iso8601, utc_now
from crm_g.infrastructure.db import transaction


class InvalidRateError(RuntimeError):
    """給与支給率が、数値でない、0〜100の範囲外、または小数第2位を超えるときのエラー。"""


class InvalidValidFromError(RuntimeError):
    """適用開始日が、現在の支給率の適用開始日以前のときのエラー。"""


class PeriodOverlapError(RuntimeError):
    """有効期間が、既存の期間と重なるときのエラー（データ設計書10章-9）。"""


@dataclass(frozen=True)
class WorkStatusSummary:
    """稼働状態の一覧表示用の情報（`SCN-061` 一覧）。"""

    code: str
    name: str
    is_billable: bool
    is_cost_target: bool
    legacy_name: str | None
    sort_order: int
    current_rate_bp: int | None


@dataclass(frozen=True)
class RatePeriod:
    """給与支給率の履歴1行分。"""

    valid_from: date
    valid_to: date | None
    payroll_rate_bp: int


def list_work_statuses(connection: sqlite3.Connection) -> tuple[WorkStatusSummary, ...]:
    """稼働状態の一覧を、表示順で返す。現在の支給率（`valid_to IS NULL`）も含む。"""
    rows = connection.execute(
        "SELECT w.code, w.name, w.is_billable, w.is_cost_target, w.legacy_name, w.sort_order, "
        "(SELECT r.payroll_rate_bp FROM work_status_rate r "
        " WHERE r.work_status_code = w.code AND r.valid_to IS NULL) "
        "FROM work_status w ORDER BY w.sort_order"
    ).fetchall()
    return tuple(
        WorkStatusSummary(
            code=row[0],
            name=row[1],
            is_billable=bool(row[2]),
            is_cost_target=bool(row[3]),
            legacy_name=row[4],
            sort_order=row[5],
            current_rate_bp=row[6],
        )
        for row in rows
    )


def rate_history(connection: sqlite3.Connection, work_status_code: str) -> tuple[RatePeriod, ...]:
    """指定した稼働状態の、支給率の履歴を、適用開始日の昇順で返す。"""
    rows = connection.execute(
        "SELECT valid_from, valid_to, payroll_rate_bp FROM work_status_rate "
        "WHERE work_status_code = ? ORDER BY valid_from",
        (work_status_code,),
    ).fetchall()
    return tuple(
        RatePeriod(
            valid_from=date.fromisoformat(row[0]),
            valid_to=date.fromisoformat(row[1]) if row[1] is not None else None,
            payroll_rate_bp=row[2],
        )
        for row in rows
    )


def _parse_rate_percent(rate_percent: str) -> int:
    """「給与支給率（%）」の入力文字列を、`payroll_rate_bp`（万分率）に変換する。"""
    try:
        return parse_percent_to_bp(rate_percent, "給与支給率")
    except InvalidPercentError as error:
        raise InvalidRateError(str(error)) from error


def _change_rate(
    connection: sqlite3.Connection, work_status_code: str, rate_percent: str, valid_from: date
) -> None:
    """給与支給率の新しい適用期間を登録する（`SCN-061`）。

    直前の期間がある場合は、その終了日を適用開始日の前日に設定してから、新しい行を
    追加する（`TBL-004`）。既存の期間と重なるときは、登録しない（10章-9）。
    """
    new_rate_bp = _parse_rate_percent(rate_percent)

    with transaction(connection):
        existing_periods = rate_history(connection, work_status_code)
        current = next((period for period in existing_periods if period.valid_to is None), None)

        if current is not None and valid_from <= current.valid_from:
            raise InvalidValidFromError(
                "適用開始日は、現在の支給率の適用開始日より後にしてください"
            )

        new_end = valid_from - timedelta(days=1) if current is not None else None
        adjusted_periods = [
            RatePeriod(period.valid_from, new_end, period.payroll_rate_bp)
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
                "UPDATE work_status_rate SET valid_to = ?, updated_at = ? "
                "WHERE work_status_code = ? AND valid_from = ?",
                (
                    new_end.isoformat() if new_end is not None else None,
                    now,
                    work_status_code,
                    current.valid_from.isoformat(),
                ),
            )
            record_operation_log(
                connection,
                Operation.UPDATE,
                "work_status_rate",
                OperationLogDetail(
                    target_key=f"{work_status_code}:{current.valid_from.isoformat()}",
                    before_value={"valid_to": None},
                    after_value={"valid_to": new_end.isoformat() if new_end else None},
                ),
            )

        connection.execute(
            "INSERT INTO work_status_rate "
            "(work_status_code, valid_from, valid_to, payroll_rate_bp, created_at, updated_at) "
            "VALUES (?, ?, NULL, ?, ?, ?)",
            (work_status_code, valid_from.isoformat(), new_rate_bp, now, now),
        )
        record_operation_log(
            connection,
            Operation.CREATE,
            "work_status_rate",
            OperationLogDetail(
                target_key=f"{work_status_code}:{valid_from.isoformat()}",
                after_value={
                    "work_status_code": work_status_code,
                    "valid_from": valid_from.isoformat(),
                    "valid_to": None,
                    "payroll_rate_bp": new_rate_bp,
                },
            ),
        )


def change_rate(  # noqa: PLR0913 再計算の結果を受け取る引数を加えたため
    connection: sqlite3.Connection,
    work_status_code: str,
    rate_percent: str,
    valid_from: date,
    *,
    on_recalculated: RecalculationListener | None = None,
) -> None:
    """支給率を変更し、適用開始日以降の計算済みの月を全社員分、再計算する（`ARC-009`）。"""
    with transaction(connection):
        _change_rate(connection, work_status_code, rate_percent, valid_from)
        recalculation = recalculate_after_change(connection, valid_from, None)
    notify_recalculated(on_recalculated, recalculation)

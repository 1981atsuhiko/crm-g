"""評価の保存、賞与の計算結果の保存・確定、支給済みの賞与の手入力（`SCN-040`）。

- 評価の入力は、20期上期（`2026-H1`）以降だけ（`REQ-DAT-010`）。対象者の評価だけを保存し、
  確定済みの賞与がある社員の評価は変更しない。`登録`・`更新`（`evaluation`）を記録する
- 計算結果は、`source = アプリ計算` で保存する。掛け率は複写する（`CLC-021`）。確定済み・
  取込・手入力の行は上書きしない
- 確定すると、支給月の原価に反映するため、同じトランザクションで支給月以降の計算済みの
  月を再計算する（`CLC-022`）。支給月が締め済みのときも確定でき、画面は警告を出す
- 手入力は、確定と同じ扱い（対象者の検査と再計算。ユーザー承認 2026-10-27）。`source =
  手入力`、確定済みとして登録する（`UID-078`）
"""

import sqlite3  # noqa: TID251 呼び出し側が開いた接続の型として使うだけで、接続はインフラ層に閉じ込める
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any

from crm_g.application.bonus import (
    calculate_bonuses,
    check_bonus_target,
    list_bonus_targets,
    pay_month_of,
)
from crm_g.application.operation_log import (
    Operation,
    OperationLogDetail,
    changed_values,
    record_operation_log,
)
from crm_g.application.recalculation import (
    RecalculationListener,
    notify_recalculated,
    recalculate_after_change,
)
from crm_g.infrastructure.datetime_utils import to_iso8601, utc_now
from crm_g.infrastructure.db import transaction

EVAL_RANKS = ("S", "A", "B", "C", "D")
BASE_TYPES = ("基本給", "月給合計")
FIRST_EVALUATION_HALF = "2026-H1"
_SOURCE_CALCULATED = "アプリ計算"
_SOURCE_MANUAL = "手入力"
_BP = Decimal(10000)


class EvaluationNotAllowedError(RuntimeError):
    """20期上期より前の半期の評価を入力しようとしたときのエラー（`REQ-DAT-010`）。"""


class InvalidEvaluationError(RuntimeError):
    """評価ランク・評価者・コメントが不正なときのエラー。"""


class NothingToConfirmError(RuntimeError):
    """確定できる計算結果（未確定のアプリ計算の行）がないときのエラー。"""


class StaleBonusCalculationError(RuntimeError):
    """保存済みの計算結果が、今の評価・掛け率・対象者と食い違うときのエラー（確定の前の検査）。"""


class DuplicateBonusError(RuntimeError):
    """同じ社員・半期の賞与が、すでにあるときのエラー（手入力）。"""


class InvalidManualBonusError(RuntimeError):
    """手入力の値（基準額の種類・金額・掛け率・評価ランク）が不正なときのエラー。"""


@dataclass(frozen=True)
class EvaluationInput:
    """1人分の評価の入力。評価ランクが空の行は、保存しない（未入力のまま）。"""

    employee_id: int
    eval_rank: str | None
    evaluator: str | None
    comment: str | None


@dataclass(frozen=True)
class ManualBonusInput:
    """支給済みの賞与の手入力。掛け率は「倍」の文字列（小数第4位まで）。"""

    employee_id: int
    eval_half: str
    base_type: str
    base_amount: int
    eval_rank: str
    rate: str
    bonus_amount: int


def _closed_employee_ids(connection: sqlite3.Connection, eval_half: str) -> set[int]:
    rows = connection.execute(
        "SELECT employee_id FROM bonus WHERE eval_half = ? AND is_closed = 1", (eval_half,)
    ).fetchall()
    return {row[0] for row in rows}


def save_evaluations(
    connection: sqlite3.Connection, eval_half: str, entries: Sequence[EvaluationInput]
) -> int:
    """評価を保存する。保存（登録・更新）した人数を返す。"""
    if eval_half < FIRST_EVALUATION_HALF:
        raise EvaluationNotAllowedError(
            f"評価の入力は、20期上期（{FIRST_EVALUATION_HALF}）以降だけです"
        )
    filled = [entry for entry in entries if entry.eval_rank]
    for entry in filled:
        if entry.eval_rank not in EVAL_RANKS:
            raise InvalidEvaluationError(
                f"評価ランクは、{'、'.join(EVAL_RANKS)}のいずれかにしてください"
            )

    saved = 0
    with transaction(connection):
        target_ids = {target.employee_id for target in list_bonus_targets(connection, eval_half)}
        closed_ids = _closed_employee_ids(connection, eval_half)
        now = to_iso8601(utc_now())
        for entry in filled:
            if entry.employee_id not in target_ids or entry.employee_id in closed_ids:
                continue
            values: dict[str, Any] = {
                "eval_rank": entry.eval_rank,
                "evaluator": (entry.evaluator or "").strip() or None,
                "comment": (entry.comment or "").strip() or None,
            }
            key = f"{entry.employee_id}:{eval_half}"
            existing = connection.execute(
                "SELECT eval_rank, evaluator, comment FROM evaluation "
                "WHERE employee_id = ? AND eval_half = ?",
                (entry.employee_id, eval_half),
            ).fetchone()
            if existing is None:
                connection.execute(
                    "INSERT INTO evaluation (employee_id, eval_half, eval_rank, evaluator, "
                    "comment, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (entry.employee_id, eval_half, *values.values(), now, now),
                )
                record_operation_log(
                    connection,
                    Operation.CREATE,
                    "evaluation",
                    OperationLogDetail(
                        target_key=key,
                        after_value={
                            "employee_id": entry.employee_id,
                            "eval_half": eval_half,
                            **values,
                        },
                    ),
                )
                saved += 1
                continue
            before_value, after_value = changed_values(
                dict(zip(("eval_rank", "evaluator", "comment"), existing, strict=True)), values
            )
            if not after_value:
                continue
            connection.execute(
                "UPDATE evaluation SET eval_rank = ?, evaluator = ?, comment = ?, updated_at = ? "
                "WHERE employee_id = ? AND eval_half = ?",
                (*values.values(), now, entry.employee_id, eval_half),
            )
            record_operation_log(
                connection,
                Operation.UPDATE,
                "evaluation",
                OperationLogDetail(
                    target_key=key, before_value=before_value, after_value=after_value
                ),
            )
            saved += 1
    return saved


def save_bonus_calculation(connection: sqlite3.Connection, eval_half: str) -> int:
    """賞与を計算して保存する（未確定のアプリ計算の行として）。保存した人数を返す。"""
    saved = 0
    with transaction(connection):
        calculation = calculate_bonuses(connection, eval_half)
        now = to_iso8601(utc_now())
        for row in calculation.rows:
            if row.bonus_amount is None:
                continue
            existing = connection.execute(
                "SELECT source, is_closed, base_type, base_amount, eval_rank, rate_bp, "
                "bonus_amount FROM bonus WHERE employee_id = ? AND eval_half = ?",
                (row.target.employee_id, eval_half),
            ).fetchone()
            if existing is not None and (existing[0] != _SOURCE_CALCULATED or existing[1]):
                continue
            values: dict[str, Any] = {
                "base_type": row.target.base_type,
                "base_amount": row.target.base_amount,
                "eval_rank": row.eval_rank,
                "rate_bp": row.rate_bp,
                "bonus_amount": row.bonus_amount,
            }
            key = f"{row.target.employee_id}:{eval_half}"
            if existing is None:
                connection.execute(
                    "INSERT INTO bonus (employee_id, eval_half, pay_month, base_type, base_amount, "
                    "eval_rank, rate_bp, bonus_amount, source, is_closed, created_at, updated_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?)",
                    (
                        row.target.employee_id,
                        eval_half,
                        calculation.pay_month,
                        *values.values(),
                        _SOURCE_CALCULATED,
                        now,
                        now,
                    ),
                )
                record_operation_log(
                    connection,
                    Operation.CREATE,
                    "bonus",
                    OperationLogDetail(target_key=key, after_value=values),
                )
                saved += 1
                continue
            before_value, after_value = changed_values(
                dict(
                    zip(
                        ("base_type", "base_amount", "eval_rank", "rate_bp", "bonus_amount"),
                        existing[2:],
                        strict=True,
                    )
                ),
                values,
            )
            if not after_value:
                continue
            connection.execute(
                "UPDATE bonus SET base_type = ?, base_amount = ?, eval_rank = ?, rate_bp = ?, "
                "bonus_amount = ?, updated_at = ? WHERE employee_id = ? AND eval_half = ?",
                (*values.values(), now, row.target.employee_id, eval_half),
            )
            record_operation_log(
                connection,
                Operation.UPDATE,
                "bonus",
                OperationLogDetail(
                    target_key=key, before_value=before_value, after_value=after_value
                ),
            )
            saved += 1
    return saved


def _check_up_to_date(
    connection: sqlite3.Connection, eval_half: str, employee_ids: Sequence[int]
) -> None:
    """確定する計算結果が、今の評価・掛け率・対象者で計算し直した値と同じかを確かめる。

    計算のあとに評価ランク・掛け率が変わった、または社員が対象者でなくなったときに、
    古い賞与を確定しないため（データ設計書 10章-8）。
    """
    current = {
        row.target.employee_id: (
            row.target.base_type,
            row.target.base_amount,
            row.eval_rank,
            row.rate_bp,
            row.bonus_amount,
        )
        for row in calculate_bonuses(connection, eval_half).rows
        if row.bonus_amount is not None
    }
    for employee_id in employee_ids:
        saved = connection.execute(
            "SELECT base_type, base_amount, eval_rank, rate_bp, bonus_amount FROM bonus "
            "WHERE employee_id = ? AND eval_half = ?",
            (employee_id, eval_half),
        ).fetchone()
        if current.get(employee_id) != tuple(saved):
            raise StaleBonusCalculationError(
                "計算結果が、今の評価・掛け率・対象者と食い違っています。"
                "もう一度「賞与を計算」してから確定してください"
            )


def _pay_first(eval_half: str) -> date:
    return date.fromisoformat(f"{pay_month_of(eval_half)}-01")


def confirm_bonuses(
    connection: sqlite3.Connection,
    eval_half: str,
    *,
    on_recalculated: RecalculationListener | None = None,
) -> int:
    """未確定のアプリ計算の賞与を確定し、支給月以降を再計算する（`CLC-022`）。確定した人数を返す。"""
    with transaction(connection):
        employee_ids = [
            row[0]
            for row in connection.execute(
                "SELECT employee_id FROM bonus WHERE eval_half = ? AND source = ? "
                "AND is_closed = 0 ORDER BY employee_id",
                (eval_half, _SOURCE_CALCULATED),
            ).fetchall()
        ]
        if not employee_ids:
            raise NothingToConfirmError("確定できる賞与がありません。先に賞与を計算してください")
        _check_up_to_date(connection, eval_half, employee_ids)
        now = to_iso8601(utc_now())
        for employee_id in employee_ids:
            connection.execute(
                "UPDATE bonus SET is_closed = 1, updated_at = ? "
                "WHERE employee_id = ? AND eval_half = ?",
                (now, employee_id, eval_half),
            )
            record_operation_log(
                connection,
                Operation.UPDATE,
                "bonus",
                OperationLogDetail(
                    target_key=f"{employee_id}:{eval_half}",
                    before_value={"is_closed": 0},
                    after_value={"is_closed": 1},
                ),
            )
        recalculation = recalculate_after_change(connection, _pay_first(eval_half), employee_ids)
    notify_recalculated(on_recalculated, recalculation)
    return len(employee_ids)


def _parse_rate(rate: str) -> int:
    try:
        value = Decimal(rate)
    except InvalidOperation as error:
        raise InvalidManualBonusError("掛け率は、数値で入力してください") from error
    if value.is_nan() or value.is_infinite() or value < 0:
        raise InvalidManualBonusError("掛け率は、0 以上の数値で入力してください")
    scaled = value * _BP
    if scaled != scaled.to_integral_value():
        raise InvalidManualBonusError("掛け率は、小数第4位までで入力してください")
    return int(scaled)


def register_manual_bonus(
    connection: sqlite3.Connection,
    data: ManualBonusInput,
    *,
    on_recalculated: RecalculationListener | None = None,
) -> None:
    """支給済みの賞与を、確定済みとして手入力する（`UID-078`）。確定と同じく再計算する。"""
    if data.base_type not in BASE_TYPES:
        raise InvalidManualBonusError(
            f"基準額の種類は、{'、'.join(BASE_TYPES)}のどちらかにしてください"
        )
    if data.eval_rank not in EVAL_RANKS:
        raise InvalidManualBonusError(
            f"評価ランクは、{'、'.join(EVAL_RANKS)}のいずれかにしてください"
        )
    if data.base_amount < 0 or data.bonus_amount < 0:
        raise InvalidManualBonusError("基準額と賞与額は、0 以上の整数で入力してください")
    rate_bp = _parse_rate(data.rate)

    with transaction(connection):
        check_bonus_target(connection, data.eval_half, data.employee_id)
        if connection.execute(
            "SELECT 1 FROM bonus WHERE employee_id = ? AND eval_half = ?",
            (data.employee_id, data.eval_half),
        ).fetchone():
            raise DuplicateBonusError("この社員・半期の賞与は、すでに登録されています")
        values: dict[str, Any] = {
            "employee_id": data.employee_id,
            "eval_half": data.eval_half,
            "pay_month": pay_month_of(data.eval_half),
            "base_type": data.base_type,
            "base_amount": data.base_amount,
            "eval_rank": data.eval_rank,
            "rate_bp": rate_bp,
            "bonus_amount": data.bonus_amount,
            "source": _SOURCE_MANUAL,
            "is_closed": 1,
        }
        now = to_iso8601(utc_now())
        connection.execute(
            "INSERT INTO bonus (employee_id, eval_half, pay_month, base_type, base_amount, "
            "eval_rank, rate_bp, bonus_amount, source, is_closed, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (*values.values(), now, now),
        )
        record_operation_log(
            connection,
            Operation.CREATE,
            "bonus",
            OperationLogDetail(
                target_key=f"{data.employee_id}:{data.eval_half}", after_value=values
            ),
        )
        recalculation = recalculate_after_change(
            connection, _pay_first(data.eval_half), [data.employee_id]
        )
    notify_recalculated(on_recalculated, recalculation)

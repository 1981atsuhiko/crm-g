"""評価ランクの掛け率（`SCN-064`、`bonus_rate`、`TBL-008`）。

半期・評価ランクごとの掛け率を登録する。空欄は未登録（行がないこと）を表す。
掛け率を変えても、計算済みの賞与は変わらない（`CLC-021`。計算時に複写して
保存するため）。
"""

import re
import sqlite3  # noqa: TID251 呼び出し側が開いた接続の型として使うだけで、接続はインフラ層に閉じ込める
from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

from crm_g.application.operation_log import (
    Operation,
    OperationLogDetail,
    record_operation_log,
)
from crm_g.infrastructure.datetime_utils import to_iso8601, utc_now
from crm_g.infrastructure.db import transaction

EVAL_RANKS = ("S", "A", "B", "C", "D")
_EVAL_HALF_PATTERN = re.compile(r"^\d{4}-H[12]$")
_SCALE = Decimal("10000")  # 小数第4位まで


class InvalidEvalHalfError(RuntimeError):
    """半期の形式が、`YYYY-H1`／`YYYY-H2` でないときのエラー。"""


class InvalidRateError(RuntimeError):
    """掛け率が、数値でない、0未満、または小数第4位を超えるときのエラー。"""


@dataclass(frozen=True)
class RankRate:
    """評価ランク1件分の掛け率。未登録のときは`rate_bp`が`None`。"""

    eval_rank: str
    rate_bp: int | None


def _parse_rate(rate: str) -> int:
    """「掛け率（倍）」の入力文字列を、万分率の整数に変換する。"""
    try:
        value = Decimal(rate)
    except InvalidOperation as error:
        raise InvalidRateError("掛け率は、数値で入力してください") from error

    if value.is_nan() or value.is_infinite():
        raise InvalidRateError("掛け率は、数値で入力してください")
    if value < 0:
        raise InvalidRateError("掛け率は、0以上の値にしてください")

    scaled = value * _SCALE
    if scaled != scaled.to_integral_value():
        raise InvalidRateError("掛け率は、小数第4位までで入力してください")

    return int(scaled)


def list_rates(connection: sqlite3.Connection, eval_half: str) -> tuple[RankRate, ...]:
    """指定した半期の、評価ランクごとの掛け率を返す（`S`〜`D`の順、未登録は`None`）。"""
    rows = dict(
        connection.execute(
            "SELECT eval_rank, rate_bp FROM bonus_rate WHERE eval_half = ?", (eval_half,)
        ).fetchall()
    )
    return tuple(RankRate(eval_rank=rank, rate_bp=rows.get(rank)) for rank in EVAL_RANKS)


def save_rates(
    connection: sqlite3.Connection, eval_half: str, rates: Mapping[str, str | None]
) -> None:
    """指定した半期の掛け率を、評価ランクごとにまとめて保存する（`SCN-064`）。

    空欄（`None`または空文字列）の評価ランクは、未登録として扱う（既存の行が
    あれば削除する）。
    """
    if not _EVAL_HALF_PATTERN.fullmatch(eval_half):
        raise InvalidEvalHalfError("半期は、YYYY-H1またはYYYY-H2の形式にしてください")

    parsed: dict[str, int | None] = {}
    for rank, rate in rates.items():
        if rank not in EVAL_RANKS:
            raise ValueError(f"eval_rank は {EVAL_RANKS} のいずれかにしてください")
        parsed[rank] = _parse_rate(rate) if rate else None

    now = to_iso8601(utc_now())
    with transaction(connection):
        for rank, rate_bp in parsed.items():
            _save_rate(connection, eval_half, rank, rate_bp, now)


def _save_rate(
    connection: sqlite3.Connection, eval_half: str, rank: str, rate_bp: int | None, now: str
) -> None:
    """1つの評価ランクの掛け率を保存し、変わったときだけ操作ログに記録する（`SCD-020`）。"""
    existing = connection.execute(
        "SELECT rate_bp FROM bonus_rate WHERE eval_half = ? AND eval_rank = ?",
        (eval_half, rank),
    ).fetchone()
    current_bp = existing[0] if existing is not None else None
    if rate_bp == current_bp:
        return

    target_key = f"{eval_half}:{rank}"
    if rate_bp is None:
        connection.execute(
            "DELETE FROM bonus_rate WHERE eval_half = ? AND eval_rank = ?",
            (eval_half, rank),
        )
        record_operation_log(
            connection,
            Operation.DELETE,
            "bonus_rate",
            OperationLogDetail(
                target_key=target_key,
                before_value={"eval_half": eval_half, "eval_rank": rank, "rate_bp": current_bp},
            ),
        )
        return

    connection.execute(
        "INSERT INTO bonus_rate "
        "(eval_half, eval_rank, rate_bp, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?) "
        "ON CONFLICT (eval_half, eval_rank) DO UPDATE SET "
        "rate_bp = excluded.rate_bp, updated_at = excluded.updated_at",
        (eval_half, rank, rate_bp, now, now),
    )
    if current_bp is None:
        record_operation_log(
            connection,
            Operation.CREATE,
            "bonus_rate",
            OperationLogDetail(
                target_key=target_key,
                after_value={"eval_half": eval_half, "eval_rank": rank, "rate_bp": rate_bp},
            ),
        )
    else:
        record_operation_log(
            connection,
            Operation.UPDATE,
            "bonus_rate",
            OperationLogDetail(
                target_key=target_key,
                before_value={"rate_bp": current_bp},
                after_value={"rate_bp": rate_bp},
            ),
        )

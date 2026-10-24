"""賞与ルール（`SCN-064`、`bonus_rule`、データ設計書4.7節）。

対象の給与体系（`target_pay_system`）ごとに、有効期間付きの基準額の種類
（`基本給`／`月給合計`）を持つ。新しいルールを追加すると、同じ対象の給与体系の
直前のルールの終了日が、適用開始日の前日に設定される（`work_status_rate`と
同じパターン）。この画面からは、`通常評価制度`のルールだけを追加する。
"""

import sqlite3  # noqa: TID251 呼び出し側が開いた接続の型として使うだけで、接続はインフラ層に閉じ込める
from dataclasses import dataclass
from datetime import date, timedelta

from crm_g.domain.period_overlap import periods_overlap
from crm_g.infrastructure.datetime_utils import to_iso8601, utc_now
from crm_g.infrastructure.db import transaction

BASE_TYPES = ("基本給", "月給合計")
TARGET_PAY_SYSTEMS = ("通常評価制度", "年俸制", "単価連動")


class InvalidBaseTypeError(RuntimeError):
    """基準額の種類が、定義した値以外のときのエラー。"""


class InvalidValidFromError(RuntimeError):
    """適用開始日が、同じ対象の給与体系の現在のルールの適用開始日以前のときのエラー。"""


class PeriodOverlapError(RuntimeError):
    """有効期間が、同じ対象の給与体系の既存の期間と重なるときのエラー（データ設計書10章-9）。"""


@dataclass(frozen=True)
class BonusRule:
    """賞与ルール1行分の情報。"""

    valid_from: date
    valid_to: date | None
    base_type: str
    target_pay_system: str


def list_bonus_rules(
    connection: sqlite3.Connection, target_pay_system: str = "通常評価制度"
) -> tuple[BonusRule, ...]:
    """指定した対象の給与体系の賞与ルールを、適用開始日の昇順で返す。"""
    rows = connection.execute(
        "SELECT valid_from, valid_to, base_type, target_pay_system FROM bonus_rule "
        "WHERE target_pay_system = ? ORDER BY valid_from",
        (target_pay_system,),
    ).fetchall()
    return tuple(
        BonusRule(
            valid_from=date.fromisoformat(row[0]),
            valid_to=date.fromisoformat(row[1]) if row[1] is not None else None,
            base_type=row[2],
            target_pay_system=row[3],
        )
        for row in rows
    )


def add_bonus_rule(
    connection: sqlite3.Connection,
    valid_from: date,
    base_type: str,
    target_pay_system: str = "通常評価制度",
) -> None:
    """賞与ルールを追加する（`SCN-064`）。

    同じ対象の給与体系の直前のルールがある場合は、その終了日を適用開始日の前日に
    設定してから、新しい行を追加する。既存の期間と重なるときは、追加しない
    （10章-9）。
    """
    if base_type not in BASE_TYPES:
        raise InvalidBaseTypeError(
            f"基準額の種類は、{'、'.join(BASE_TYPES)}のいずれかにしてください"
        )

    with transaction(connection):
        existing_rules = list_bonus_rules(connection, target_pay_system)
        current = next((rule for rule in existing_rules if rule.valid_to is None), None)

        if current is not None and valid_from <= current.valid_from:
            raise InvalidValidFromError(
                "適用開始日は、現在のルールの適用開始日より後にしてください"
            )

        new_end = valid_from - timedelta(days=1) if current is not None else None
        adjusted_rules = [
            BonusRule(rule.valid_from, new_end, rule.base_type, rule.target_pay_system)
            if current is not None and rule.valid_from == current.valid_from
            else rule
            for rule in existing_rules
        ]
        for rule in adjusted_rules:
            if periods_overlap(rule.valid_from, rule.valid_to, valid_from, None):
                raise PeriodOverlapError("有効期間が、既存の期間と重なっています")

        now = to_iso8601(utc_now())
        if current is not None:
            connection.execute(
                "UPDATE bonus_rule SET valid_to = ?, updated_at = ? "
                "WHERE target_pay_system = ? AND valid_from = ?",
                (
                    new_end.isoformat() if new_end is not None else None,
                    now,
                    target_pay_system,
                    current.valid_from.isoformat(),
                ),
            )

        connection.execute(
            "INSERT INTO bonus_rule "
            "(valid_from, valid_to, base_type, target_pay_system, created_at, updated_at) "
            "VALUES (?, NULL, ?, ?, ?, ?)",
            (valid_from.isoformat(), base_type, target_pay_system, now, now),
        )

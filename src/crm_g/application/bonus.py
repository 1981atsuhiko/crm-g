"""賞与の対象者と計算（画面設計書 `SCN-040`、計算仕様書 `CLC-019`〜`CLC-021`）。

- 支給月・基準月は、半期から決める（`CLC-019`）。支給月が半期と対応しない賞与は登録
  しない（データ設計書 10章-7）
- 対象者は、通常評価制度の在籍者（`CLC-020`、データ設計書 10章-8）。在籍の判定は、
  支給月の初日に退職していないこと（暫定）。給与体系は、基準月の基準日（在籍営業日の
  最後の日）に有効な給与履歴で判定する。基準月に在籍営業日がない社員は対象にしない（暫定）
- 基準額は、支給月の初日に有効な賞与ルールの種類に従う（`基本給`／`月給合計`）
- 賞与額は、基準額 × 掛け率の切り上げ（`CLC-021`）。評価ランクが未入力、または掛け率が
  未登録の社員は計算しない（未入力の一覧に出す）

このモジュールは、計算結果を保存しない（保存・確定はPH-11-2）。
"""

import sqlite3  # noqa: TID251 呼び出し側が開いた接続の型として使うだけで、接続はインフラ層に閉じ込める
from calendar import monthrange
from dataclasses import dataclass
from datetime import date
from fractions import Fraction

from crm_g.application.recalculation import (
    load_business_calendar,
    load_pay_items,
    year_month_text,
)
from crm_g.domain.bonus import (
    BonusRulePeriod,
    bonus_schedule,
    calculate_bonus_amount,
    determine_bonus_base_amount,
    select_effective_bonus_rule,
)
from crm_g.domain.business_days import is_present
from crm_g.domain.pay_item import PayItem
from crm_g.domain.salary_history import (
    SalaryHistory,
    SalaryHistoryItem,
    select_effective_salary_history,
)

TARGET_PAY_SYSTEM = "通常評価制度"
_BP = 10000


class NoBaseMonthCalendarError(RuntimeError):
    """基準月の営業日カレンダーがなく、基準日を決められないときのエラー。"""


class NoBonusRuleError(RuntimeError):
    """支給月の初日に有効な賞与ルールがないときのエラー。"""


class InvalidPayMonthError(RuntimeError):
    """支給月が、半期と対応しないときのエラー（データ設計書 10章-7）。"""


class NotBonusTargetError(RuntimeError):
    """賞与の対象者でない社員の賞与を登録しようとしたときのエラー（データ設計書 10章-8）。"""


@dataclass(frozen=True)
class BonusTarget:
    """賞与の対象者1人分（基準額まで求めたもの）。"""

    employee_id: int
    employee_name: str
    base_type: str
    base_amount: int


@dataclass(frozen=True)
class BonusCalculationRow:
    """対象者1人分の計算。評価ランク・掛け率がそろわないときは、賞与額が `None`。"""

    target: BonusTarget
    eval_rank: str | None
    rate_bp: int | None
    bonus_amount: int | None


@dataclass(frozen=True)
class BonusCalculation:
    """半期の賞与の計算（保存しない）。`missing` は、未入力の一覧に出す対象者。"""

    eval_half: str
    pay_month: str
    rows: tuple[BonusCalculationRow, ...]

    @property
    def missing(self) -> tuple[BonusCalculationRow, ...]:
        return tuple(row for row in self.rows if row.bonus_amount is None)


def pay_month_of(eval_half: str) -> str:
    """半期の賞与の支給月を `YYYY-MM` で返す（`CLC-019`）。"""
    schedule = bonus_schedule(eval_half)
    return year_month_text(schedule.pay_year, schedule.pay_month)


def check_pay_month(eval_half: str, pay_month: str) -> None:
    """支給月が半期と対応するかを確かめる（データ設計書 10章-7）。"""
    expected = pay_month_of(eval_half)
    if pay_month != expected:
        raise InvalidPayMonthError(
            f"{eval_half} の賞与の支給月は {expected} です（{pay_month} は登録できません）"
        )


def _effective_rule(connection: sqlite3.Connection, pay_first: date) -> BonusRulePeriod:
    rules = [
        BonusRulePeriod(
            valid_from=date.fromisoformat(row[0]),
            valid_to=date.fromisoformat(row[1]) if row[1] else None,
            base_type=row[2],
            target_pay_system=row[3],
        )
        for row in connection.execute(
            "SELECT valid_from, valid_to, base_type, target_pay_system FROM bonus_rule"
        ).fetchall()
    ]
    rule = select_effective_bonus_rule(rules, pay_first, TARGET_PAY_SYSTEM)
    if rule is None:
        raise NoBonusRuleError(f"{pay_first.isoformat()} に有効な賞与ルールがありません")
    return rule


def _histories_with_pay_system(
    connection: sqlite3.Connection, employee_id: int, pay_items: dict[str, PayItem]
) -> list[tuple[SalaryHistory, str]]:
    histories = []
    for history_id, effective_from, effective_to, pay_system in connection.execute(
        "SELECT id, effective_from, effective_to, pay_system FROM salary_history "
        "WHERE employee_id = ?",
        (employee_id,),
    ).fetchall():
        items = tuple(
            SalaryHistoryItem(pay_item=pay_items[code], amount=amount)
            for code, amount in connection.execute(
                "SELECT pay_item_code, amount FROM salary_history_item "
                "WHERE salary_history_id = ? ORDER BY pay_item_code",
                (history_id,),
            ).fetchall()
        )
        history = SalaryHistory(
            date.fromisoformat(effective_from),
            date.fromisoformat(effective_to) if effective_to else None,
            items,
        )
        histories.append((history, pay_system))
    return histories


def list_bonus_targets(connection: sqlite3.Connection, eval_half: str) -> tuple[BonusTarget, ...]:
    """半期の賞与の対象者と基準額を、氏名（カナ）の順で返す（`CLC-020`）。"""
    schedule = bonus_schedule(eval_half)
    pay_first = date(schedule.pay_year, schedule.pay_month, 1)
    base_first = date(schedule.base_year, schedule.base_month, 1)
    base_last = date(
        schedule.base_year,
        schedule.base_month,
        monthrange(schedule.base_year, schedule.base_month)[1],
    )
    business_calendar = load_business_calendar(connection, base_first, base_last)
    if business_calendar.day_count == 0:
        raise NoBaseMonthCalendarError(
            f"基準月（{year_month_text(schedule.base_year, schedule.base_month)}）の"
            "営業日カレンダーがありません"
        )
    rule = _effective_rule(connection, pay_first)
    pay_items = load_pay_items(connection)

    targets = []
    for employee_id, name, join_date, retire_date in connection.execute(
        "SELECT id, name, join_date, retire_date FROM employee "
        "WHERE retire_date IS NULL OR retire_date >= ? ORDER BY COALESCE(name_kana, name), id",
        (pay_first.isoformat(),),
    ).fetchall():
        joined = date.fromisoformat(join_date) if join_date else None
        retired = date.fromisoformat(retire_date) if retire_date else None
        present_days = [
            day for day in business_calendar.business_days if is_present(day, joined, retired)
        ]
        if not present_days:
            continue
        histories = _histories_with_pay_system(connection, employee_id, pay_items)
        history = select_effective_salary_history(
            [history for history, _ in histories], present_days[-1]
        )
        if history is None:
            continue
        pay_system = next(system for candidate, system in histories if candidate is history)
        if pay_system != TARGET_PAY_SYSTEM:
            continue
        targets.append(
            BonusTarget(
                employee_id=employee_id,
                employee_name=name,
                base_type=rule.base_type,
                base_amount=determine_bonus_base_amount(rule.base_type, history),
            )
        )
    return tuple(targets)


def check_bonus_target(connection: sqlite3.Connection, eval_half: str, employee_id: int) -> None:
    """社員が、半期の賞与の対象者かを確かめる（データ設計書 10章-8）。"""
    if all(
        target.employee_id != employee_id for target in list_bonus_targets(connection, eval_half)
    ):
        raise NotBonusTargetError(
            "この社員は、賞与の対象者ではありません（通常評価制度の在籍者だけが対象です）"
        )


def calculate_bonuses(connection: sqlite3.Connection, eval_half: str) -> BonusCalculation:
    """半期の賞与を計算する（保存しない。`CLC-021`）。"""
    ranks = dict(
        connection.execute(
            "SELECT employee_id, eval_rank FROM evaluation WHERE eval_half = ?", (eval_half,)
        ).fetchall()
    )
    rates = dict(
        connection.execute(
            "SELECT eval_rank, rate_bp FROM bonus_rate WHERE eval_half = ?", (eval_half,)
        ).fetchall()
    )
    rows = []
    for target in list_bonus_targets(connection, eval_half):
        rank = ranks.get(target.employee_id)
        rate_bp = rates.get(rank) if rank is not None else None
        amount = (
            calculate_bonus_amount(target.base_amount, Fraction(rate_bp, _BP))
            if rate_bp is not None
            else None
        )
        rows.append(BonusCalculationRow(target, rank, rate_bp, amount))
    return BonusCalculation(eval_half, pay_month_of(eval_half), tuple(rows))

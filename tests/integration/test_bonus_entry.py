"""評価の保存、賞与の計算結果の保存・確定、手入力の試験（`SCN-040`、`CLC-021`、`CLC-022`）。

`TC-CAL-007` のうち、賞与の確定による再計算を含む（ほかの7つの変更は
`test_recalculation_hooks.py`）。
"""

import json
import sqlite3
from collections.abc import Iterator
from datetime import date
from pathlib import Path

import pytest

from crm_g.application.bonus import NotBonusTargetError
from crm_g.application.bonus_entry import (
    DuplicateBonusError,
    EvaluationInput,
    EvaluationNotAllowedError,
    InvalidEvaluationError,
    InvalidManualBonusError,
    ManualBonusInput,
    NothingToConfirmError,
    StaleBonusCalculationError,
    confirm_bonuses,
    load_bonus_screen,
    register_manual_bonus,
    save_bonus_calculation,
    save_evaluations,
)
from crm_g.application.business_calendar import create_year_calendar
from crm_g.application.monthly_close import close_month
from crm_g.application.recalculation import RecalculationResult, recalculate_month
from crm_g.application.salary_history import (
    SalaryHistoryInput,
    SalaryItemInput,
    register_salary_history,
)
from crm_g.application.work_period import WorkPeriodInput, create_work_period
from crm_g.infrastructure.db import open_connection
from crm_g.infrastructure.migration import apply_migrations

_DDL_DIR = Path(__file__).resolve().parents[2] / "DDL"
_TIMESTAMP = "2026-10-25T00:00:00.000000Z"
_HALF = "2026-H1"  # 支給月 2026-10


@pytest.fixture
def connection(tmp_path: Path) -> Iterator[sqlite3.Connection]:
    """P：通常評価制度（月給 300,000）、Q：年俸制。10月は計算済み。掛け率 A=1.25、B=1.0。"""
    path = tmp_path / "test.db"
    with open_connection(path) as connection:
        apply_migrations(connection, _DDL_DIR, path, tmp_path / "backups")
        for employee_id, name in ((1, "P"), (2, "Q")):
            connection.execute(
                "INSERT INTO employee (id, email, name, enrollment_status, join_date, "
                "created_at, updated_at) VALUES (?, ?, ?, '在籍', '2020-04-01', ?, ?)",
                (employee_id, f"e{employee_id}@example.com", name, _TIMESTAMP, _TIMESTAMP),
            )
        for rank, rate_bp in (("A", 12500), ("B", 10000)):
            connection.execute(
                "INSERT INTO bonus_rate (eval_half, eval_rank, rate_bp, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (_HALF, rank, rate_bp, _TIMESTAMP, _TIMESTAMP),
            )
        connection.commit()
        create_year_calendar(connection, 2026)
        for employee_id, pay_system in ((1, "通常評価制度"), (2, "年俸制")):
            register_salary_history(
                connection,
                employee_id,
                SalaryHistoryInput(
                    effective_from=date(2026, 4, 1),
                    pay_system=pay_system,
                    employment_type="正社員",
                    grade=None,
                    annual_salary=3600000 if pay_system == "年俸制" else None,
                    revision_type="定期（4月）"
                    if pay_system == "通常評価制度"
                    else "年俸改定（4月）",
                    note=None,
                    items=(SalaryItemInput("BASE_SALARY", 300000),),
                ),
            )
            create_work_period(
                connection,
                employee_id,
                WorkPeriodInput(
                    start_date=date(2026, 4, 1),
                    end_date=None,
                    work_status_code="WAIT_FULL",
                    assignment_id=None,
                    billing_ratio_percent="100",
                    note=None,
                ),
            )
        recalculate_month(connection, "2026-10")
        connection.commit()
        yield connection


def _evaluation(employee_id: int = 1, rank: str | None = "A") -> EvaluationInput:
    return EvaluationInput(employee_id, rank, "上長", "期待以上")


def _bonus(connection: sqlite3.Connection, employee_id: int = 1) -> tuple[object, ...] | None:
    row: tuple[object, ...] | None = connection.execute(
        "SELECT eval_rank, rate_bp, bonus_amount, source, is_closed, pay_month FROM bonus "
        "WHERE employee_id = ? AND eval_half = ?",
        (employee_id, _HALF),
    ).fetchone()
    return row


def _cost_bonus(connection: sqlite3.Connection) -> object:
    return connection.execute(
        "SELECT cost_bonus FROM monthly_pl WHERE employee_id = 1 AND year_month = '2026-10'"
    ).fetchone()[0]


def _logs(connection: sqlite3.Connection, table: str) -> list[tuple[object, ...]]:
    return connection.execute(
        "SELECT operation, target_key, before_value, after_value FROM operation_log "
        "WHERE target_table = ? ORDER BY id",
        (table,),
    ).fetchall()


# ---- 評価 ----


def test_save_evaluations_creates_and_updates_with_logs(connection: sqlite3.Connection) -> None:
    assert save_evaluations(connection, _HALF, [_evaluation()]) == 1
    assert save_evaluations(connection, _HALF, [_evaluation()]) == 0
    assert save_evaluations(connection, _HALF, [_evaluation(rank="B")]) == 1

    logs = _logs(connection, "evaluation")
    assert [(log[0], log[1]) for log in logs] == [("登録", "1:2026-H1"), ("更新", "1:2026-H1")]
    assert json.loads(str(logs[1][2])) == {"eval_rank": "A"}
    assert json.loads(str(logs[1][3])) == {"eval_rank": "B"}


def test_blank_rank_and_non_targets_are_not_saved(connection: sqlite3.Connection) -> None:
    saved = save_evaluations(
        connection, _HALF, [_evaluation(rank=None), _evaluation(employee_id=2)]
    )

    assert saved == 0
    assert connection.execute("SELECT COUNT(*) FROM evaluation").fetchone()[0] == 0


def test_invalid_rank_is_rejected(connection: sqlite3.Connection) -> None:
    with pytest.raises(InvalidEvaluationError):
        save_evaluations(connection, _HALF, [EvaluationInput(1, "E", "上長", "よい")])


def test_blank_evaluator_and_comment_are_saved_as_null(connection: sqlite3.Connection) -> None:
    save_evaluations(connection, _HALF, [EvaluationInput(1, "A", " ", "")])

    assert connection.execute(
        "SELECT eval_rank, evaluator, comment FROM evaluation"
    ).fetchone() == ("A", None, None)


def test_evaluation_before_2026_h1_is_not_allowed(connection: sqlite3.Connection) -> None:
    with pytest.raises(EvaluationNotAllowedError):
        save_evaluations(connection, "2025-H2", [_evaluation()])


# ---- 計算結果の保存 ----


def test_save_bonus_calculation_copies_the_rate(connection: sqlite3.Connection) -> None:
    save_evaluations(connection, _HALF, [_evaluation()])

    assert save_bonus_calculation(connection, _HALF) == 1

    assert _bonus(connection) == ("A", 12500, 375000, "アプリ計算", 0, "2026-10")
    assert [log[0] for log in _logs(connection, "bonus")] == ["登録"]


def test_recalculation_of_bonus_follows_rate_changes(connection: sqlite3.Connection) -> None:
    save_evaluations(connection, _HALF, [_evaluation()])
    save_bonus_calculation(connection, _HALF)
    connection.execute("UPDATE bonus_rate SET rate_bp = 15000 WHERE eval_rank = 'A'")
    connection.commit()

    assert save_bonus_calculation(connection, _HALF) == 1
    assert save_bonus_calculation(connection, _HALF) == 0

    assert _bonus(connection) == ("A", 15000, 450000, "アプリ計算", 0, "2026-10")


def test_employees_without_rank_are_not_saved(connection: sqlite3.Connection) -> None:
    assert save_bonus_calculation(connection, _HALF) == 0
    assert _bonus(connection) is None


def test_imported_bonus_is_not_overwritten(connection: sqlite3.Connection) -> None:
    connection.execute(
        "INSERT INTO bonus (employee_id, eval_half, pay_month, base_type, base_amount, "
        "bonus_amount, source, is_closed, created_at, updated_at) "
        "VALUES (1, ?, '2026-10', '月給合計', 300000, 123, '取込', 1, ?, ?)",
        (_HALF, _TIMESTAMP, _TIMESTAMP),
    )
    connection.commit()
    save_evaluations(connection, _HALF, [_evaluation()])

    assert save_bonus_calculation(connection, _HALF) == 0
    assert _bonus(connection) == (None, None, 123, "取込", 1, "2026-10")


# ---- 確定 ----


def test_tc_cal_007_confirmation_recalculates_the_pay_month(
    connection: sqlite3.Connection,
) -> None:
    save_evaluations(connection, _HALF, [_evaluation()])
    save_bonus_calculation(connection, _HALF)
    assert _cost_bonus(connection) == 0
    results: list[RecalculationResult] = []

    assert confirm_bonuses(connection, _HALF, on_recalculated=results.append) == 1

    assert _bonus(connection) == ("A", 12500, 375000, "アプリ計算", 1, "2026-10")
    assert _cost_bonus(connection) == 375000
    assert results[0].year_months == ("2026-10",)
    assert [log[0] for log in _logs(connection, "bonus")][-1] == "更新"


def test_confirmed_bonus_and_its_evaluation_are_not_changed(
    connection: sqlite3.Connection,
) -> None:
    save_evaluations(connection, _HALF, [_evaluation()])
    save_bonus_calculation(connection, _HALF)
    confirm_bonuses(connection, _HALF)

    assert save_evaluations(connection, _HALF, [_evaluation(rank="B")]) == 0
    assert save_bonus_calculation(connection, _HALF) == 0
    assert _bonus(connection) == ("A", 12500, 375000, "アプリ計算", 1, "2026-10")
    with pytest.raises(NothingToConfirmError):
        confirm_bonuses(connection, _HALF)


def test_confirmation_with_closed_pay_month_warns_through_result(
    connection: sqlite3.Connection,
) -> None:
    save_evaluations(connection, _HALF, [_evaluation()])
    save_bonus_calculation(connection, _HALF)
    close_month(connection, "2026-10")
    results: list[RecalculationResult] = []

    confirm_bonuses(connection, _HALF, on_recalculated=results.append)

    assert results[0].year_months == ()
    assert results[0].closed_months == ("2026-10",)
    assert _cost_bonus(connection) == 0


def test_nothing_to_confirm(connection: sqlite3.Connection) -> None:
    with pytest.raises(NothingToConfirmError):
        confirm_bonuses(connection, _HALF)


# ---- 手入力 ----


def _manual(**overrides: object) -> ManualBonusInput:
    values: dict[str, object] = {
        "employee_id": 1,
        "eval_half": _HALF,
        "base_type": "月給合計",
        "base_amount": 300000,
        "eval_rank": "A",
        "rate": "1.2345",
        "bonus_amount": 370350,
    }
    values.update(overrides)
    return ManualBonusInput(**values)  # type: ignore[arg-type]


def test_manual_bonus_is_registered_as_confirmed_and_recalculates(
    connection: sqlite3.Connection,
) -> None:
    results: list[RecalculationResult] = []

    register_manual_bonus(connection, _manual(), on_recalculated=results.append)

    assert _bonus(connection) == ("A", 12345, 370350, "手入力", 1, "2026-10")
    assert _cost_bonus(connection) == 370350
    assert results[0].year_months == ("2026-10",)
    assert [log[0] for log in _logs(connection, "bonus")] == ["登録"]


def test_tc_dbd_015_manual_bonus_for_annual_salary_employee_is_rejected(
    connection: sqlite3.Connection,
) -> None:
    with pytest.raises(NotBonusTargetError):
        register_manual_bonus(connection, _manual(employee_id=2))

    assert _bonus(connection, 2) is None


def test_duplicate_manual_bonus_is_rejected(connection: sqlite3.Connection) -> None:
    register_manual_bonus(connection, _manual())

    with pytest.raises(DuplicateBonusError):
        register_manual_bonus(connection, _manual())


@pytest.mark.parametrize(
    "overrides",
    [
        {"rate": "1.23456"},
        {"rate": "abc"},
        {"rate": "-1"},
        {"rate": "nan"},
        {"rate": "Infinity"},
        {"base_amount": -1},
        {"base_type": "未定義"},
        {"eval_rank": "E"},
        {"bonus_amount": -1},
    ],
)
def test_invalid_manual_bonus_is_rejected(
    connection: sqlite3.Connection, overrides: dict[str, object]
) -> None:
    with pytest.raises(InvalidManualBonusError):
        register_manual_bonus(connection, _manual(**overrides))


@pytest.mark.parametrize(
    "change",
    [
        "UPDATE evaluation SET eval_rank = 'B'",
        "DELETE FROM bonus_rate WHERE eval_rank = 'A'",
        "UPDATE employee SET retire_date = '2026-09-30' WHERE id = 1",
    ],
)
def test_stale_calculation_cannot_be_confirmed(connection: sqlite3.Connection, change: str) -> None:
    save_evaluations(connection, _HALF, [_evaluation()])
    save_bonus_calculation(connection, _HALF)
    connection.execute(change)
    connection.commit()

    with pytest.raises(StaleBonusCalculationError):
        confirm_bonuses(connection, _HALF)

    assert _bonus(connection) == ("A", 12500, 375000, "アプリ計算", 0, "2026-10")


def test_confirmation_after_recalculating_succeeds(connection: sqlite3.Connection) -> None:
    save_evaluations(connection, _HALF, [_evaluation()])
    save_bonus_calculation(connection, _HALF)
    save_evaluations(connection, _HALF, [_evaluation(rank="B")])
    save_bonus_calculation(connection, _HALF)

    assert confirm_bonuses(connection, _HALF) == 1
    assert _bonus(connection) == ("B", 10000, 300000, "アプリ計算", 1, "2026-10")


def test_manual_bonus_with_closed_pay_month_warns_through_result(
    connection: sqlite3.Connection,
) -> None:
    close_month(connection, "2026-10")
    results: list[RecalculationResult] = []

    register_manual_bonus(connection, _manual(), on_recalculated=results.append)

    assert results[0].closed_months == ("2026-10",)


def test_evaluation_of_employee_with_manual_bonus_is_skipped(
    connection: sqlite3.Connection,
) -> None:
    register_manual_bonus(connection, _manual())

    assert save_evaluations(connection, _HALF, [_evaluation()]) == 0


def test_failed_manual_bonus_does_not_notify(connection: sqlite3.Connection) -> None:
    results: list[RecalculationResult] = []

    with pytest.raises(NotBonusTargetError):
        register_manual_bonus(connection, _manual(employee_id=2), on_recalculated=results.append)

    assert results == []


# ---- 画面に出す内容 ----


def _screen_row(connection: sqlite3.Connection, employee_id: int = 1) -> tuple[object, ...]:
    row = next(
        row for row in load_bonus_screen(connection, _HALF).rows if row.employee_id == employee_id
    )
    return (row.eval_rank, row.rate_bp, row.bonus_amount, row.is_closed, row.source)


def test_screen_rows_follow_the_progress(connection: sqlite3.Connection) -> None:
    screen = load_bonus_screen(connection, _HALF)
    assert (screen.pay_month, screen.base_month, screen.evaluation_allowed) == (
        "2026-10",
        "2026-09",
        True,
    )
    assert [row.employee_name for row in screen.rows] == ["P"]
    assert screen.missing == ("P",)

    save_evaluations(connection, _HALF, [_evaluation()])
    assert _screen_row(connection) == ("A", 12500, None, False, None)

    save_bonus_calculation(connection, _HALF)
    assert _screen_row(connection) == ("A", 12500, 375000, False, "アプリ計算")

    confirm_bonuses(connection, _HALF)
    assert _screen_row(connection) == ("A", 12500, 375000, True, "アプリ計算")
    assert load_bonus_screen(connection, _HALF).missing == ()


def test_screen_shows_saved_bonus_of_non_target(connection: sqlite3.Connection) -> None:
    connection.execute(
        "INSERT INTO bonus (employee_id, eval_half, pay_month, base_type, base_amount, "
        "bonus_amount, source, is_closed, created_at, updated_at) "
        "VALUES (2, ?, '2026-10', '月給合計', 300000, 100000, '取込', 1, ?, ?)",
        (_HALF, _TIMESTAMP, _TIMESTAMP),
    )
    connection.commit()

    assert _screen_row(connection, 2) == (None, None, 100000, True, "取込")


def test_screen_of_19th_period_shows_saved_bonuses_only(connection: sqlite3.Connection) -> None:
    connection.execute(
        "INSERT INTO bonus (employee_id, eval_half, pay_month, base_type, base_amount, "
        "bonus_amount, source, is_closed, created_at, updated_at) "
        "VALUES (1, '2025-H2', '2026-04', '月給合計', 300000, 250000, '取込', 1, ?, ?)",
        (_TIMESTAMP, _TIMESTAMP),
    )
    connection.commit()

    screen = load_bonus_screen(connection, "2025-H2")

    assert not screen.evaluation_allowed
    assert [(row.employee_name, row.bonus_amount) for row in screen.rows] == [("P", 250000)]


def test_screen_marks_stale_calculation(connection: sqlite3.Connection) -> None:
    save_evaluations(connection, _HALF, [_evaluation()])
    save_bonus_calculation(connection, _HALF)
    save_evaluations(connection, _HALF, [_evaluation(rank="B")])

    row = load_bonus_screen(connection, _HALF).rows[0]

    assert (row.eval_rank, row.bonus_amount, row.needs_recalculation) == ("B", None, True)


def test_recalculation_removes_calculation_that_can_no_longer_be_made(
    connection: sqlite3.Connection,
) -> None:
    save_evaluations(connection, _HALF, [_evaluation()])
    save_bonus_calculation(connection, _HALF)
    connection.execute("UPDATE employee SET retire_date = '2026-09-30' WHERE id = 1")
    connection.commit()

    leftover = load_bonus_screen(connection, _HALF).rows[0]
    assert (leftover.bonus_amount, leftover.needs_recalculation) == (None, True)

    save_bonus_calculation(connection, _HALF)

    assert _bonus(connection) is None
    assert load_bonus_screen(connection, _HALF).rows == ()
    assert [log[0] for log in _logs(connection, "bonus")][-1] == "削除"

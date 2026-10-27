"""賞与の対象者と計算の試験（`SCN-040`、`CLC-019`〜`CLC-021`）。

`TC-CAL-005`、`TC-CLC-019`、`TC-DBD-014`、`TC-DBD-015` を含む。賞与額は計算仕様書
9章の EX-007 による。
"""

import sqlite3
from collections.abc import Iterator
from datetime import date
from pathlib import Path

import pytest

from crm_g.application.bonus import (
    InvalidPayMonthError,
    NoBaseMonthCalendarError,
    NoBonusRuleError,
    NotBonusTargetError,
    calculate_bonuses,
    check_bonus_target,
    check_pay_month,
    list_bonus_targets,
    pay_month_of,
)
from crm_g.application.business_calendar import create_year_calendar
from crm_g.application.salary_history import (
    SalaryHistoryInput,
    SalaryItemInput,
    register_salary_history,
)
from crm_g.infrastructure.db import open_connection
from crm_g.infrastructure.migration import apply_migrations

_DDL_DIR = Path(__file__).resolve().parents[2] / "DDL"
_TIMESTAMP = "2026-10-25T00:00:00.000000Z"
_HALF = "2026-H1"  # 支給月 2026-10、基準月 2026-09


def _employee(
    connection: sqlite3.Connection,
    employee_id: int,
    name: str,
    *,
    join_date: str = "2020-04-01",
    retire_date: str | None = None,
) -> None:
    connection.execute(
        "INSERT INTO employee (id, email, name, enrollment_status, join_date, retire_date, "
        "created_at, updated_at) VALUES (?, ?, ?, '在籍', ?, ?, ?, ?)",
        (
            employee_id,
            f"e{employee_id}@example.com",
            name,
            join_date,
            retire_date,
            _TIMESTAMP,
            _TIMESTAMP,
        ),
    )


def _salary(
    connection: sqlite3.Connection,
    employee_id: int,
    *,
    pay_system: str = "通常評価制度",
    items: tuple[tuple[str, int], ...] = (("BASE_SALARY", 300000),),
) -> None:
    register_salary_history(
        connection,
        employee_id,
        SalaryHistoryInput(
            effective_from=date(2025, 4, 1),
            pay_system=pay_system,
            employment_type="正社員",
            grade=None,
            annual_salary=sum(amount for _, amount in items) * 12
            if pay_system == "年俸制"
            else None,
            revision_type="入社",
            note=None,
            items=tuple(SalaryItemInput(code, amount) for code, amount in items),
        ),
    )


@pytest.fixture
def connection(tmp_path: Path) -> Iterator[sqlite3.Connection]:
    """P：通常評価制度・在籍、Q：年俸制、R：2026-09-30 退職（TC-CAL-005）。"""
    path = tmp_path / "test.db"
    with open_connection(path) as connection:
        apply_migrations(connection, _DDL_DIR, path, tmp_path / "backups")
        _employee(connection, 1, "P")
        _employee(connection, 2, "Q")
        _employee(connection, 3, "R", retire_date="2026-09-30")
        connection.commit()
        create_year_calendar(connection, 2025)
        create_year_calendar(connection, 2026)
        _salary(connection, 1, items=(("BASE_SALARY", 300000), ("DUTY_ALLOWANCE", 33333)))
        _salary(connection, 2, pay_system="年俸制")
        _salary(connection, 3)
        yield connection


def _rate(connection: sqlite3.Connection, eval_half: str, rank: str, rate_bp: int) -> None:
    connection.execute(
        "INSERT INTO bonus_rate (eval_half, eval_rank, rate_bp, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?)",
        (eval_half, rank, rate_bp, _TIMESTAMP, _TIMESTAMP),
    )


def _evaluate(connection: sqlite3.Connection, employee_id: int, rank: str) -> None:
    connection.execute(
        "INSERT INTO evaluation (employee_id, eval_half, eval_rank, evaluator, comment, "
        "created_at, updated_at) VALUES (?, ?, ?, '上長', 'よい', ?, ?)",
        (employee_id, _HALF, rank, _TIMESTAMP, _TIMESTAMP),
    )


def test_tc_cal_005_only_regular_pay_system_employees_in_office(
    connection: sqlite3.Connection,
) -> None:
    targets = list_bonus_targets(connection, _HALF)

    assert [target.employee_name for target in targets] == ["P"]


def test_base_amount_follows_the_rule_of_the_pay_month(connection: sqlite3.Connection) -> None:
    # 2026-04-01 以降のルールは「月給合計」、2025-04-01〜2026-03-31 は「基本給」（初期データ）。
    current = list_bonus_targets(connection, _HALF)[0]
    previous = list_bonus_targets(connection, "2025-H1")[0]

    assert (current.base_type, current.base_amount) == ("月給合計", 333333)
    assert (previous.base_type, previous.base_amount) == ("基本給", 300000)


def test_employee_joined_after_the_base_month_is_not_a_target(
    connection: sqlite3.Connection,
) -> None:
    _employee(connection, 4, "S", join_date="2026-10-01")
    _salary(connection, 4)

    assert [target.employee_name for target in list_bonus_targets(connection, _HALF)] == ["P"]


def test_employee_without_salary_history_is_not_a_target(connection: sqlite3.Connection) -> None:
    _employee(connection, 4, "T")

    assert [target.employee_name for target in list_bonus_targets(connection, _HALF)] == ["P"]


def test_missing_base_month_calendar_is_an_error(connection: sqlite3.Connection) -> None:
    # 2027-H1 の基準月は 2027-09。2027年の営業日カレンダーはない。
    with pytest.raises(NoBaseMonthCalendarError):
        list_bonus_targets(connection, "2027-H1")


def test_missing_bonus_rule_is_an_error(connection: sqlite3.Connection) -> None:
    connection.execute("DELETE FROM bonus_rule")

    with pytest.raises(NoBonusRuleError):
        list_bonus_targets(connection, _HALF)


@pytest.mark.parametrize(
    ("eval_half", "pay_month"), [("2026-H1", "2026-10"), ("2025-H2", "2026-04")]
)
def test_tc_dbd_014_pay_month_matching_the_half_is_accepted(eval_half: str, pay_month: str) -> None:
    assert pay_month_of(eval_half) == pay_month
    check_pay_month(eval_half, pay_month)


@pytest.mark.parametrize(
    ("eval_half", "pay_month"), [("2026-H1", "2026-04"), ("2025-H2", "2025-10")]
)
def test_tc_dbd_014_pay_month_not_matching_the_half_is_rejected(
    eval_half: str, pay_month: str
) -> None:
    with pytest.raises(InvalidPayMonthError):
        check_pay_month(eval_half, pay_month)


def test_tc_dbd_015_annual_salary_employee_is_rejected(connection: sqlite3.Connection) -> None:
    check_bonus_target(connection, _HALF, 1)

    with pytest.raises(NotBonusTargetError):
        check_bonus_target(connection, _HALF, 2)


def test_ex007_bonus_amount_is_rounded_up(connection: sqlite3.Connection) -> None:
    _rate(connection, _HALF, "A", 12500)
    _evaluate(connection, 1, "A")

    calculation = calculate_bonuses(connection, _HALF)

    assert calculation.pay_month == "2026-10"
    row = calculation.rows[0]
    assert (row.eval_rank, row.rate_bp, row.bonus_amount) == ("A", 12500, 416667)
    assert calculation.missing == ()


def test_tc_clc_019_rank_without_rate_is_not_calculated(connection: sqlite3.Connection) -> None:
    _rate(connection, _HALF, "A", 12500)
    _evaluate(connection, 1, "B")

    calculation = calculate_bonuses(connection, _HALF)

    assert [(row.target.employee_name, row.bonus_amount) for row in calculation.missing] == [
        ("P", None)
    ]
    assert calculation.missing[0].rate_bp is None


def test_employee_without_evaluation_is_listed_as_missing(connection: sqlite3.Connection) -> None:
    calculation = calculate_bonuses(connection, _HALF)

    assert [(row.eval_rank, row.bonus_amount) for row in calculation.missing] == [(None, None)]


def test_employee_retiring_on_the_first_day_of_the_pay_month_is_a_target(
    connection: sqlite3.Connection,
) -> None:
    connection.execute("UPDATE employee SET retire_date = '2026-10-01' WHERE id = 3")

    names = [target.employee_name for target in list_bonus_targets(connection, _HALF)]

    assert names == ["P", "R"]


def test_employee_joined_during_the_base_month_is_a_target(
    connection: sqlite3.Connection,
) -> None:
    _employee(connection, 4, "S", join_date="2026-09-16")
    _salary(connection, 4)

    names = [target.employee_name for target in list_bonus_targets(connection, _HALF)]

    assert names == ["P", "S"]


def test_pay_system_is_judged_by_the_history_of_the_basis_date(
    connection: sqlite3.Connection,
) -> None:
    # Q は 9/16 から通常評価制度に移行する。基準日（9月の最後の在籍営業日）の履歴で判定する。
    register_salary_history(
        connection,
        2,
        SalaryHistoryInput(
            effective_from=date(2026, 9, 16),
            pay_system="通常評価制度",
            employment_type="正社員",
            grade=None,
            annual_salary=None,
            revision_type="制度移行",
            note=None,
            items=(SalaryItemInput("BASE_SALARY", 400000),),
        ),
    )

    targets = list_bonus_targets(connection, _HALF)

    assert [(target.employee_name, target.base_amount) for target in targets] == [
        ("P", 333333),
        ("Q", 400000),
    ]


def test_invalid_half_code_is_rejected(connection: sqlite3.Connection) -> None:
    with pytest.raises(ValueError, match="half_code"):
        list_bonus_targets(connection, "2026-H3")

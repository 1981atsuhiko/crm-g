"""参画・単価履歴の試験（`SCN-050`、`TBL-015`、`TBL-017`、`SCD-020`）。"""

import json
import sqlite3
from datetime import date
from pathlib import Path

import pytest

from crm_g.application.assignment import (
    AssignmentInput,
    AssignmentNotFoundError,
    EmployeeNotFoundError,
    InvalidCategoryError,
    InvalidCustomerNameError,
    InvalidPeriodError,
    RateOutsidePeriodError,
    create_assignment,
    list_assignments,
    update_assignment,
)
from crm_g.application.unit_price import AssignmentNotFoundError as RateAssignmentNotFoundError
from crm_g.application.unit_price import (
    DuplicateRateHistoryError,
    InvalidUnitPriceError,
    OutsideAssignmentPeriodError,
    RateHistoryInput,
    RateHistoryNotFoundError,
    delete_rate_history,
    list_rate_history,
    register_rate_history,
    update_rate_history,
)
from crm_g.infrastructure.db import open_connection
from crm_g.infrastructure.migration import apply_migrations

_DDL_DIR = Path(__file__).resolve().parents[2] / "DDL"
_TIMESTAMP = "2026-10-25T00:00:00.000000Z"


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    path = tmp_path / "test.db"
    with open_connection(path) as connection:
        apply_migrations(connection, _DDL_DIR, path, tmp_path / "backups")
        connection.execute(
            "INSERT INTO employee "
            "(id, email, name, enrollment_status, join_date, created_at, updated_at) VALUES "
            "(1, 'taro@example.com', '山田太郎', '在籍', '2020-04-01', ?, ?), "
            "(2, 'hanako@example.com', '鈴木花子', '在籍', '2021-04-01', ?, ?)",
            (_TIMESTAMP, _TIMESTAMP, _TIMESTAMP, _TIMESTAMP),
        )
        connection.commit()
    return path


def _assignment(**overrides: object) -> AssignmentInput:
    values: dict[str, object] = {
        "customer_name": "A社",
        "project_name": "基幹システム",
        "category": "SES",
        "start_date": date(2026, 4, 1),
        "end_date": None,
    }
    values.update(overrides)
    return AssignmentInput(**values)  # type: ignore[arg-type]


def _rate(**overrides: object) -> RateHistoryInput:
    values: dict[str, object] = {
        "effective_from": date(2026, 4, 1),
        "unit_price": 700000,
        "revision_basis": None,
        "note": None,
    }
    values.update(overrides)
    return RateHistoryInput(**values)  # type: ignore[arg-type]


def _logs(connection: sqlite3.Connection, table: str) -> list[tuple[str, object, object]]:
    rows = connection.execute(
        "SELECT operation, before_value, after_value FROM operation_log "
        "WHERE target_table = ? ORDER BY id",
        (table,),
    ).fetchall()
    return [
        (row[0], json.loads(row[1]) if row[1] else None, json.loads(row[2]) if row[2] else None)
        for row in rows
    ]


# ---- 参画 ----


def test_create_and_list_assignments_in_start_date_order(db_path: Path) -> None:
    with open_connection(db_path) as connection:
        create_assignment(
            connection, 1, _assignment(customer_name="B社", start_date=date(2026, 10, 1))
        )
        create_assignment(connection, 1, _assignment())
        create_assignment(connection, 2, _assignment(customer_name="C社"))

        assignments = list_assignments(connection, 1)

    assert [assignment.customer_name for assignment in assignments] == ["A社", "B社"]
    assert assignments[0].label == "A社／基幹システム（2026-04-01〜）"


def test_create_assignment_logs_registered_columns(db_path: Path) -> None:
    with open_connection(db_path) as connection:
        create_assignment(connection, 1, _assignment())
        logs = _logs(connection, "assignment")

    assert logs == [
        (
            "登録",
            None,
            {
                "employee_id": 1,
                "customer_name": "A社",
                "project_name": "基幹システム",
                "category": "SES",
                "start_date": "2026-04-01",
                "end_date": None,
            },
        )
    ]


def test_update_assignment_changes_values_and_logs_only_changed_columns(db_path: Path) -> None:
    with open_connection(db_path) as connection:
        new_id = create_assignment(connection, 1, _assignment())
        update_assignment(connection, new_id, _assignment(end_date=date(2026, 9, 30)))

        assignment = list_assignments(connection, 1)[0]
        logs = _logs(connection, "assignment")

    assert assignment.end_date == date(2026, 9, 30)
    assert logs[-1] == ("更新", {"end_date": None}, {"end_date": "2026-09-30"})


def test_update_assignment_without_changes_logs_nothing(db_path: Path) -> None:
    with open_connection(db_path) as connection:
        new_id = create_assignment(connection, 1, _assignment())
        update_assignment(connection, new_id, _assignment())

        assert [log[0] for log in _logs(connection, "assignment")] == ["登録"]


@pytest.mark.parametrize(
    ("overrides", "error"),
    [
        ({"customer_name": "  "}, InvalidCustomerNameError),
        ({"category": "未定義"}, InvalidCategoryError),
        ({"end_date": date(2026, 3, 31)}, InvalidPeriodError),
    ],
)
def test_create_assignment_rejects_invalid_input(
    db_path: Path, overrides: dict[str, object], error: type[Exception]
) -> None:
    with open_connection(db_path) as connection:
        with pytest.raises(error):
            create_assignment(connection, 1, _assignment(**overrides))
        count = connection.execute("SELECT COUNT(*) FROM assignment").fetchone()[0]

    assert count == 0


def test_end_date_equal_to_start_date_is_accepted(db_path: Path) -> None:
    with open_connection(db_path) as connection:
        create_assignment(connection, 1, _assignment(end_date=date(2026, 4, 1)))


def test_blank_project_name_is_saved_as_null(db_path: Path) -> None:
    with open_connection(db_path) as connection:
        create_assignment(connection, 1, _assignment(project_name="  "))

        assert list_assignments(connection, 1)[0].project_name is None


def test_create_assignment_rejects_unknown_employee(db_path: Path) -> None:
    with open_connection(db_path) as connection, pytest.raises(EmployeeNotFoundError):
        create_assignment(connection, 999, _assignment())


def test_update_assignment_rejects_unknown_assignment(db_path: Path) -> None:
    with open_connection(db_path) as connection, pytest.raises(AssignmentNotFoundError):
        update_assignment(connection, 999, _assignment())


@pytest.mark.parametrize(
    "overrides",
    [
        {"start_date": date(2026, 5, 1)},
        {"end_date": date(2026, 4, 30)},
    ],
)
def test_update_assignment_rejects_period_that_leaves_rate_outside(
    db_path: Path, overrides: dict[str, object]
) -> None:
    with open_connection(db_path) as connection:
        assignment_id = create_assignment(connection, 1, _assignment())
        register_rate_history(connection, assignment_id, _rate(effective_from=date(2026, 4, 1)))
        register_rate_history(connection, assignment_id, _rate(effective_from=date(2026, 5, 1)))

        with pytest.raises(RateOutsidePeriodError):
            update_assignment(connection, assignment_id, _assignment(**overrides))

        assignment = list_assignments(connection, 1)[0]
    assert (assignment.start_date, assignment.end_date) == (date(2026, 4, 1), None)


def test_update_assignment_accepts_period_that_keeps_rates_inside(db_path: Path) -> None:
    with open_connection(db_path) as connection:
        assignment_id = create_assignment(connection, 1, _assignment())
        register_rate_history(connection, assignment_id, _rate(effective_from=date(2026, 5, 1)))

        update_assignment(
            connection,
            assignment_id,
            _assignment(start_date=date(2026, 5, 1), end_date=date(2026, 5, 1)),
        )


def test_update_assignment_accepts_end_date_on_rate_date_and_back_to_open(
    db_path: Path,
) -> None:
    with open_connection(db_path) as connection:
        assignment_id = create_assignment(connection, 1, _assignment())
        register_rate_history(connection, assignment_id, _rate(effective_from=date(2026, 9, 30)))

        update_assignment(connection, assignment_id, _assignment(end_date=date(2026, 9, 30)))
        update_assignment(connection, assignment_id, _assignment(end_date=None))

        assert list_assignments(connection, 1)[0].end_date is None


# ---- 単価 ----


def test_register_and_list_rate_history_in_effective_from_order(db_path: Path) -> None:
    with open_connection(db_path) as connection:
        assignment_id = create_assignment(connection, 1, _assignment())
        register_rate_history(connection, assignment_id, _rate(effective_from=date(2026, 10, 1)))
        register_rate_history(connection, assignment_id, _rate(unit_price=650000))

        history = list_rate_history(connection, assignment_id)
        logs = _logs(connection, "rate_history")
        employee_ids = connection.execute(
            "SELECT DISTINCT employee_id FROM rate_history"
        ).fetchall()

    assert [(entry.effective_from, entry.unit_price) for entry in history] == [
        (date(2026, 4, 1), 650000),
        (date(2026, 10, 1), 700000),
    ]
    assert employee_ids == [(1,)]
    assert logs[0][2] == {
        "employee_id": 1,
        "assignment_id": assignment_id,
        "effective_from": "2026-10-01",
        "unit_price": 700000,
        "revision_basis": None,
        "note": None,
    }


@pytest.mark.parametrize("effective_from", [date(2026, 3, 31), date(2026, 10, 1)])
def test_register_rate_history_rejects_date_outside_assignment(
    db_path: Path, effective_from: date
) -> None:
    with open_connection(db_path) as connection:
        assignment_id = create_assignment(connection, 1, _assignment(end_date=date(2026, 9, 30)))

        with pytest.raises(OutsideAssignmentPeriodError):
            register_rate_history(connection, assignment_id, _rate(effective_from=effective_from))


@pytest.mark.parametrize("effective_from", [date(2026, 4, 1), date(2026, 9, 30)])
def test_register_rate_history_accepts_assignment_boundaries(
    db_path: Path, effective_from: date
) -> None:
    with open_connection(db_path) as connection:
        assignment_id = create_assignment(connection, 1, _assignment(end_date=date(2026, 9, 30)))

        register_rate_history(connection, assignment_id, _rate(effective_from=effective_from))


def test_register_rate_history_rejects_duplicate_date(db_path: Path) -> None:
    with open_connection(db_path) as connection:
        assignment_id = create_assignment(connection, 1, _assignment())
        register_rate_history(connection, assignment_id, _rate())

        with pytest.raises(DuplicateRateHistoryError):
            register_rate_history(connection, assignment_id, _rate(unit_price=1))


def test_same_date_is_allowed_for_different_assignments(db_path: Path) -> None:
    with open_connection(db_path) as connection:
        first = create_assignment(connection, 1, _assignment())
        second = create_assignment(connection, 1, _assignment(customer_name="B社"))
        register_rate_history(connection, first, _rate())
        register_rate_history(connection, second, _rate())


def test_register_rate_history_rejects_negative_price(db_path: Path) -> None:
    with open_connection(db_path) as connection:
        assignment_id = create_assignment(connection, 1, _assignment())

        with pytest.raises(InvalidUnitPriceError):
            register_rate_history(connection, assignment_id, _rate(unit_price=-1))


def test_register_rate_history_rejects_unknown_assignment(db_path: Path) -> None:
    with open_connection(db_path) as connection, pytest.raises(RateAssignmentNotFoundError):
        register_rate_history(connection, 999, _rate())


def test_update_rate_history_logs_only_changed_columns(db_path: Path) -> None:
    with open_connection(db_path) as connection:
        assignment_id = create_assignment(connection, 1, _assignment())
        rate_id = register_rate_history(connection, assignment_id, _rate())
        update_rate_history(
            connection, rate_id, _rate(unit_price=750000, revision_basis="契約更新")
        )

        entry = list_rate_history(connection, assignment_id)[0]
        logs = _logs(connection, "rate_history")

    assert (entry.unit_price, entry.revision_basis) == (750000, "契約更新")
    assert logs[-1] == (
        "更新",
        {"unit_price": 700000, "revision_basis": None},
        {"unit_price": 750000, "revision_basis": "契約更新"},
    )


def test_update_rate_history_keeps_own_date_but_rejects_other_rows_date(db_path: Path) -> None:
    with open_connection(db_path) as connection:
        assignment_id = create_assignment(connection, 1, _assignment())
        first = register_rate_history(connection, assignment_id, _rate())
        register_rate_history(connection, assignment_id, _rate(effective_from=date(2026, 10, 1)))

        update_rate_history(connection, first, _rate(unit_price=1))
        with pytest.raises(DuplicateRateHistoryError):
            update_rate_history(connection, first, _rate(effective_from=date(2026, 10, 1)))


def test_update_rate_history_rejects_date_outside_assignment(db_path: Path) -> None:
    with open_connection(db_path) as connection:
        assignment_id = create_assignment(connection, 1, _assignment())
        rate_id = register_rate_history(connection, assignment_id, _rate())

        with pytest.raises(OutsideAssignmentPeriodError):
            update_rate_history(connection, rate_id, _rate(effective_from=date(2026, 3, 1)))


def test_update_rate_history_rejects_unknown_rate(db_path: Path) -> None:
    with open_connection(db_path) as connection, pytest.raises(RateHistoryNotFoundError):
        update_rate_history(connection, 999, _rate())


def test_delete_rate_history_logs_deleted_row(db_path: Path) -> None:
    with open_connection(db_path) as connection:
        assignment_id = create_assignment(connection, 1, _assignment())
        rate_id = register_rate_history(connection, assignment_id, _rate(note="初回"))
        delete_rate_history(connection, rate_id)

        history = list_rate_history(connection, assignment_id)
        logs = _logs(connection, "rate_history")

    assert history == ()
    assert logs[-1] == (
        "削除",
        {
            "employee_id": 1,
            "assignment_id": assignment_id,
            "effective_from": "2026-04-01",
            "unit_price": 700000,
            "revision_basis": None,
            "note": "初回",
        },
        None,
    )


def test_delete_rate_history_rejects_unknown_rate(db_path: Path) -> None:
    with open_connection(db_path) as connection, pytest.raises(RateHistoryNotFoundError):
        delete_rate_history(connection, 999)

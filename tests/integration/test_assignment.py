"""参画・単価履歴の試験（`SCN-050`、`TBL-015`、`TBL-017`、`SCD-020`）。"""

import json
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
    create_assignment,
    list_assignments,
    update_assignment,
)
from crm_g.application.unit_price import (
    AssignmentNotOwnedError,
    DuplicateRateHistoryError,
    InvalidUnitPriceError,
    RateHistoryInput,
    list_rate_history,
    register_rate_history,
)
from crm_g.application.unit_price import EmployeeNotFoundError as RateEmployeeNotFoundError
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
        "assignment_id": None,
        "effective_from": date(2026, 4, 1),
        "unit_price": 700000,
        "revision_basis": None,
        "note": None,
    }
    values.update(overrides)
    return RateHistoryInput(**values)  # type: ignore[arg-type]


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
        new_id = create_assignment(connection, 1, _assignment())
        row = connection.execute(
            "SELECT operation, target_table, target_key, after_value FROM operation_log"
        ).fetchone()

    assert row[:3] == ("登録", "assignment", str(new_id))
    assert json.loads(row[3]) == {
        "employee_id": 1,
        "customer_name": "A社",
        "project_name": "基幹システム",
        "category": "SES",
        "start_date": "2026-04-01",
        "end_date": None,
    }


def test_update_assignment_changes_values_and_logs_only_changed_columns(db_path: Path) -> None:
    with open_connection(db_path) as connection:
        new_id = create_assignment(connection, 1, _assignment())
        update_assignment(connection, new_id, _assignment(end_date=date(2026, 9, 30)))

        assignment = list_assignments(connection, 1)[0]
        row = connection.execute(
            "SELECT before_value, after_value FROM operation_log WHERE operation = '更新'"
        ).fetchone()

    assert assignment.end_date == date(2026, 9, 30)
    assert json.loads(row[0]) == {"end_date": None}
    assert json.loads(row[1]) == {"end_date": "2026-09-30"}


def test_update_assignment_without_changes_logs_nothing(db_path: Path) -> None:
    with open_connection(db_path) as connection:
        new_id = create_assignment(connection, 1, _assignment())
        update_assignment(connection, new_id, _assignment())
        count = connection.execute("SELECT COUNT(*) FROM operation_log").fetchone()[0]

    assert count == 1


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


def test_create_assignment_rejects_unknown_employee(db_path: Path) -> None:
    with open_connection(db_path) as connection, pytest.raises(EmployeeNotFoundError):
        create_assignment(connection, 999, _assignment())


def test_update_assignment_rejects_unknown_assignment(db_path: Path) -> None:
    with open_connection(db_path) as connection, pytest.raises(AssignmentNotFoundError):
        update_assignment(connection, 999, _assignment())


def test_register_rate_history_for_employee_and_assignment(db_path: Path) -> None:
    with open_connection(db_path) as connection:
        assignment_id = create_assignment(connection, 1, _assignment())
        register_rate_history(connection, 1, _rate(effective_from=date(2026, 10, 1)))
        register_rate_history(connection, 1, _rate(assignment_id=assignment_id, unit_price=800000))

        history = list_rate_history(connection, 1)
        log = connection.execute(
            "SELECT operation, target_table FROM operation_log WHERE target_table = 'rate_history'"
        ).fetchall()

    assert [(entry.effective_from, entry.unit_price) for entry in history] == [
        (date(2026, 4, 1), 800000),
        (date(2026, 10, 1), 700000),
    ]
    assert history[0].assignment_label == "A社／基幹システム"
    assert history[1].assignment_label is None
    assert log == [("登録", "rate_history"), ("登録", "rate_history")]


def test_register_rate_history_rejects_duplicate_key(db_path: Path) -> None:
    with open_connection(db_path) as connection:
        register_rate_history(connection, 1, _rate())

        with pytest.raises(DuplicateRateHistoryError):
            register_rate_history(connection, 1, _rate(unit_price=1))


def test_same_date_is_allowed_for_employee_level_and_assignment_level(db_path: Path) -> None:
    with open_connection(db_path) as connection:
        assignment_id = create_assignment(connection, 1, _assignment())
        register_rate_history(connection, 1, _rate())
        register_rate_history(connection, 1, _rate(assignment_id=assignment_id))

        assert len(list_rate_history(connection, 1)) == 2


def test_register_rate_history_rejects_other_employees_assignment(db_path: Path) -> None:
    with open_connection(db_path) as connection:
        other_assignment_id = create_assignment(connection, 2, _assignment())

        with pytest.raises(AssignmentNotOwnedError):
            register_rate_history(connection, 1, _rate(assignment_id=other_assignment_id))


def test_register_rate_history_rejects_negative_price(db_path: Path) -> None:
    with open_connection(db_path) as connection, pytest.raises(InvalidUnitPriceError):
        register_rate_history(connection, 1, _rate(unit_price=-1))


def test_register_rate_history_rejects_unknown_employee(db_path: Path) -> None:
    with open_connection(db_path) as connection, pytest.raises(RateEmployeeNotFoundError):
        register_rate_history(connection, 999, _rate())


def test_blank_project_name_is_saved_as_null(db_path: Path) -> None:
    with open_connection(db_path) as connection:
        create_assignment(connection, 1, _assignment(project_name="  "))

        assert list_assignments(connection, 1)[0].project_name is None

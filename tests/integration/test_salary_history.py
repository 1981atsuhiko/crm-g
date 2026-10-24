"""給与履歴の登録の試験（`SCN-030`、`TC-SCN-008`、`TC-SCN-009`、`TC-DBD-009`）。"""

from datetime import date
from pathlib import Path

import pytest

from crm_g.application.salary_history import (
    EmployeeNotFoundError,
    InvalidEmploymentTypeError,
    InvalidItemAmountError,
    InvalidPaySystemError,
    InvalidRevisionTypeError,
    InvalidValidFromError,
    MissingAnnualSalaryError,
    SalaryHistoryInput,
    SalaryItemInput,
    UnexpectedAnnualSalaryError,
    check_needs_warning,
    get_current_salary_history,
    list_available_pay_items,
    register_salary_history,
)
from crm_g.infrastructure.db import open_connection
from crm_g.infrastructure.migration import apply_migrations

_DDL_DIR = Path(__file__).resolve().parents[2] / "DDL"
_TIMESTAMP = "2026-10-24T00:00:00.000000Z"


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    path = tmp_path / "test.db"
    with open_connection(path) as connection:
        apply_migrations(connection, _DDL_DIR, path, tmp_path / "backups")
        connection.execute(
            "INSERT INTO employee "
            "(id, email, name, enrollment_status, join_date, created_at, updated_at) "
            "VALUES (1, 'taro@example.com', '山田太郎', '在籍', '2020-04-01', ?, ?)",
            (_TIMESTAMP, _TIMESTAMP),
        )
        connection.commit()
    return path


def _input(**overrides: object) -> SalaryHistoryInput:
    defaults: dict[str, object] = {
        "effective_from": date(2026, 4, 1),
        "pay_system": "通常評価制度",
        "employment_type": "正社員",
        "grade": None,
        "annual_salary": None,
        "revision_type": "定期（4月）",
        "note": None,
        "items": (
            SalaryItemInput(pay_item_code="BASE_SALARY", amount=300000),
            SalaryItemInput(pay_item_code="DUTY_ALLOWANCE", amount=20000),
        ),
    }
    defaults.update(overrides)
    return SalaryHistoryInput(**defaults)  # type: ignore[arg-type]


def test_get_current_salary_history_is_none_initially(db_path: Path) -> None:
    with open_connection(db_path) as connection:
        assert get_current_salary_history(connection, 1) is None


def test_list_available_pay_items_filters_by_date(db_path: Path) -> None:
    with open_connection(db_path) as connection:
        items = list_available_pay_items(connection, date(2026, 4, 1))

        assert any(item.code == "BASE_SALARY" for item in items)
        # 給与項目は、適用開始日順（sort_order）に並ぶ。
        assert [item.code for item in items][0] == "BASE_SALARY"


def test_check_needs_warning_false_in_revision_month(db_path: Path) -> None:
    with open_connection(db_path) as connection:
        data = _input(effective_from=date(2026, 4, 1), revision_type="定期（4月）")
        assert check_needs_warning(connection, 1, data) is False


def test_check_needs_warning_true_outside_revision_month(db_path: Path) -> None:
    with open_connection(db_path) as connection:
        data = _input(effective_from=date(2026, 5, 1), revision_type="定期（4月）")
        assert check_needs_warning(connection, 1, data) is True


def test_tc_scn_009_register_first_history_has_no_previous(db_path: Path) -> None:
    with open_connection(db_path) as connection:
        result = register_salary_history(connection, 1, _input())

        assert result.monthly_salary == 320000
        assert result.previous_effective_to is None

        current = get_current_salary_history(connection, 1)
        assert current is not None
        assert current.id == result.salary_history_id


def test_tc_scn_009_register_closes_previous_history(db_path: Path) -> None:
    with open_connection(db_path) as connection:
        first = register_salary_history(connection, 1, _input(effective_from=date(2025, 4, 1)))

        second = register_salary_history(
            connection,
            1,
            _input(
                effective_from=date(2026, 4, 1),
                items=(SalaryItemInput(pay_item_code="BASE_SALARY", amount=350000),),
            ),
        )

        assert second.previous_effective_to == date(2026, 3, 31)

        row = connection.execute(
            "SELECT effective_to FROM salary_history WHERE id = ?", (first.salary_history_id,)
        ).fetchone()
        assert row[0] == "2026-03-31"


def test_tc_dbd_009_register_rejects_valid_from_not_after_current(db_path: Path) -> None:
    with open_connection(db_path) as connection:
        register_salary_history(connection, 1, _input(effective_from=date(2026, 4, 1)))

        with pytest.raises(InvalidValidFromError):
            register_salary_history(connection, 1, _input(effective_from=date(2026, 1, 1)))


def test_register_rejects_undefined_pay_system(db_path: Path) -> None:
    with open_connection(db_path) as connection, pytest.raises(InvalidPaySystemError):
        register_salary_history(connection, 1, _input(pay_system="未定義の給与体系"))


def test_register_rejects_undefined_employment_type(db_path: Path) -> None:
    with open_connection(db_path) as connection, pytest.raises(InvalidEmploymentTypeError):
        register_salary_history(connection, 1, _input(employment_type="未定義の雇用形態"))


def test_register_rejects_undefined_revision_type(db_path: Path) -> None:
    with open_connection(db_path) as connection, pytest.raises(InvalidRevisionTypeError):
        register_salary_history(connection, 1, _input(revision_type="未定義の改定区分"))


def test_register_rejects_missing_annual_salary_for_nenpou(db_path: Path) -> None:
    with open_connection(db_path) as connection, pytest.raises(MissingAnnualSalaryError):
        register_salary_history(connection, 1, _input(pay_system="年俸制", annual_salary=None))


def test_register_rejects_unexpected_annual_salary(db_path: Path) -> None:
    with open_connection(db_path) as connection, pytest.raises(UnexpectedAnnualSalaryError):
        register_salary_history(
            connection, 1, _input(pay_system="通常評価制度", annual_salary=6000000)
        )


def test_register_accepts_annual_salary_for_nenpou(db_path: Path) -> None:
    with open_connection(db_path) as connection:
        register_salary_history(connection, 1, _input(pay_system="年俸制", annual_salary=6000000))


def test_register_rejects_negative_item_amount(db_path: Path) -> None:
    with open_connection(db_path) as connection, pytest.raises(InvalidItemAmountError):
        register_salary_history(
            connection,
            1,
            _input(items=(SalaryItemInput(pay_item_code="BASE_SALARY", amount=-1),)),
        )


def test_register_rejects_unknown_employee(db_path: Path) -> None:
    with open_connection(db_path) as connection, pytest.raises(EmployeeNotFoundError):
        register_salary_history(connection, 999, _input())


def test_register_logs_create_operation(db_path: Path) -> None:
    with open_connection(db_path) as connection:
        result = register_salary_history(connection, 1, _input())

        row = connection.execute(
            "SELECT operation, target_table, target_key FROM operation_log WHERE operation = '登録'"
        ).fetchone()
        assert row == ("登録", "salary_history", str(result.salary_history_id))


def test_register_logs_update_operation_for_previous_history(db_path: Path) -> None:
    with open_connection(db_path) as connection:
        first = register_salary_history(connection, 1, _input(effective_from=date(2025, 4, 1)))
        register_salary_history(connection, 1, _input(effective_from=date(2026, 4, 1)))

        row = connection.execute(
            "SELECT operation, target_table, target_key FROM operation_log WHERE operation = '更新'"
        ).fetchone()
        assert row == ("更新", "salary_history", str(first.salary_history_id))


def test_register_failure_rolls_back_log_and_data(db_path: Path) -> None:
    with open_connection(db_path) as connection:
        register_salary_history(connection, 1, _input(effective_from=date(2025, 4, 1)))

        with pytest.raises(InvalidValidFromError):
            register_salary_history(connection, 1, _input(effective_from=date(2025, 1, 1)))

        count = connection.execute("SELECT COUNT(*) FROM salary_history").fetchone()[0]
        assert count == 1

        log_count = connection.execute("SELECT COUNT(*) FROM operation_log").fetchone()[0]
        assert log_count == 1  # 最初の登録の'登録'ログだけが残る

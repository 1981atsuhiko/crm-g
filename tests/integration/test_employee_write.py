"""社員の登録・編集の試験（`SCN-022`、`TC-FUN-008`）。"""

from datetime import date, timedelta
from pathlib import Path

import pytest

from crm_g.application.employee import (
    DuplicateEmailError,
    DuplicateEmployeeNoError,
    EmployeeInput,
    EmployeeNotFoundError,
    InvalidBirthDateError,
    InvalidEmailFormatError,
    InvalidEmploymentTypeError,
    InvalidEnrollmentStatusError,
    InvalidGenderError,
    InvalidNameError,
    InvalidRetireDateError,
    create_employee,
    get_employee,
    update_employee,
)
from crm_g.infrastructure.db import open_connection
from crm_g.infrastructure.migration import apply_migrations

_DDL_DIR = Path(__file__).resolve().parents[2] / "DDL"


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    path = tmp_path / "test.db"
    with open_connection(path) as connection:
        apply_migrations(connection, _DDL_DIR, path, tmp_path / "backups")
    return path


def _input(**overrides: object) -> EmployeeInput:
    defaults: dict[str, object] = {
        "email": "Taro.Yamada@Example.com",
        "name": "山田太郎",
        "name_kana": "ヤマダタロウ",
        "employee_no": None,
        "employment_type": "正社員",
        "birth_date": date(1990, 1, 1),
        "gender": "男性",
        "join_date": date(2020, 4, 1),
        "nearest_station": "新宿駅",
        "residence_area": "東京都",
        "job_type": "SE",
        "enrollment_status": "在籍",
        "retire_date": None,
    }
    defaults.update(overrides)
    return EmployeeInput(**defaults)  # type: ignore[arg-type]


def test_tc_fun_008_create_employee_normalizes_email_to_lowercase(db_path: Path) -> None:
    with open_connection(db_path) as connection:
        employee_id = create_employee(connection, _input(email="Taro.Yamada@Example.com"))

        employee = get_employee(connection, employee_id)
        assert employee is not None
        assert employee.email == "taro.yamada@example.com"


def test_tc_fun_008_create_employee_rejects_duplicate_email_case_insensitively(
    db_path: Path,
) -> None:
    with open_connection(db_path) as connection:
        create_employee(connection, _input(email="taro@example.com"))

        with pytest.raises(DuplicateEmailError):
            create_employee(connection, _input(email="TARO@EXAMPLE.COM"))


def test_create_employee_rejects_invalid_email_format(db_path: Path) -> None:
    with open_connection(db_path) as connection, pytest.raises(InvalidEmailFormatError):
        create_employee(connection, _input(email="not-an-email"))


def test_create_employee_rejects_empty_name(db_path: Path) -> None:
    with open_connection(db_path) as connection, pytest.raises(InvalidNameError):
        create_employee(connection, _input(name=""))


def test_create_employee_rejects_undefined_employment_type(db_path: Path) -> None:
    with open_connection(db_path) as connection, pytest.raises(InvalidEmploymentTypeError):
        create_employee(connection, _input(employment_type="未定義の雇用形態"))


def test_create_employee_rejects_undefined_gender(db_path: Path) -> None:
    with open_connection(db_path) as connection, pytest.raises(InvalidGenderError):
        create_employee(connection, _input(gender="未定義の性別"))


def test_create_employee_rejects_undefined_enrollment_status(db_path: Path) -> None:
    with open_connection(db_path) as connection, pytest.raises(InvalidEnrollmentStatusError):
        create_employee(connection, _input(enrollment_status="未定義のステータス"))


def test_create_employee_rejects_birth_date_not_before_today(db_path: Path) -> None:
    with open_connection(db_path) as connection, pytest.raises(InvalidBirthDateError):
        create_employee(connection, _input(birth_date=date.today()))


def test_create_employee_accepts_birth_date_yesterday(db_path: Path) -> None:
    with open_connection(db_path) as connection:
        create_employee(connection, _input(birth_date=date.today() - timedelta(days=1)))


def test_create_employee_rejects_retire_date_before_join_date(db_path: Path) -> None:
    with open_connection(db_path) as connection, pytest.raises(InvalidRetireDateError):
        create_employee(
            connection,
            _input(join_date=date(2020, 4, 1), retire_date=date(2020, 3, 31)),
        )


def test_create_employee_accepts_retire_date_equal_to_join_date(db_path: Path) -> None:
    with open_connection(db_path) as connection:
        create_employee(
            connection,
            _input(join_date=date(2020, 4, 1), retire_date=date(2020, 4, 1)),
        )


def test_create_employee_rejects_duplicate_employee_no(db_path: Path) -> None:
    with open_connection(db_path) as connection:
        create_employee(connection, _input(email="a@example.com", employee_no="E001"))

        with pytest.raises(DuplicateEmployeeNoError):
            create_employee(connection, _input(email="b@example.com", employee_no="E001"))


def test_create_employee_allows_multiple_blank_employee_no(db_path: Path) -> None:
    with open_connection(db_path) as connection:
        create_employee(connection, _input(email="a@example.com", employee_no=None))
        create_employee(connection, _input(email="b@example.com", employee_no=None))


def test_create_employee_failure_rolls_back(db_path: Path) -> None:
    with open_connection(db_path) as connection:
        with pytest.raises(InvalidNameError):
            create_employee(connection, _input(email="a@example.com", name=""))

        assert get_employee(connection, 1) is None


def test_tc_fun_008_update_employee_cannot_change_email(db_path: Path) -> None:
    with open_connection(db_path) as connection:
        employee_id = create_employee(connection, _input(email="taro@example.com"))

        update_employee(
            connection, employee_id, _input(email="changed@example.com", name="山田太郎（改）")
        )

        employee = get_employee(connection, employee_id)
        assert employee is not None
        assert employee.email == "taro@example.com"
        assert employee.name == "山田太郎（改）"


def test_update_employee_rejects_unknown_id(db_path: Path) -> None:
    with open_connection(db_path) as connection, pytest.raises(EmployeeNotFoundError):
        update_employee(connection, 999, _input())


def test_update_employee_can_reuse_its_own_employee_no(db_path: Path) -> None:
    with open_connection(db_path) as connection:
        employee_id = create_employee(connection, _input(email="a@example.com", employee_no="E001"))

        update_employee(connection, employee_id, _input(employee_no="E001", name="山田太郎（改）"))

        employee = get_employee(connection, employee_id)
        assert employee is not None
        assert employee.employee_no == "E001"


def test_update_employee_failure_rolls_back(db_path: Path) -> None:
    with open_connection(db_path) as connection:
        employee_id = create_employee(connection, _input(email="a@example.com"))

        with pytest.raises(InvalidNameError):
            update_employee(connection, employee_id, _input(name=""))

        employee = get_employee(connection, employee_id)
        assert employee is not None
        assert employee.name == "山田太郎"

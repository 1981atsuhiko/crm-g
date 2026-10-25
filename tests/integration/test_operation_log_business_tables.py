"""業務データの `登録`・`更新`・`削除` の操作ログの試験（`SCD-020`）。"""

import json
import sqlite3
from datetime import date
from pathlib import Path
from typing import Any

import pytest

from crm_g.application.bonus_rate import save_rates
from crm_g.application.bonus_rule import add_bonus_rule
from crm_g.application.business_calendar import (
    create_year_calendar,
    refetch_holidays,
    register_manual_day,
)
from crm_g.application.employee import (
    DuplicateEmployeeNoError,
    EmployeeInput,
    create_employee,
    update_employee,
)
from crm_g.application.operation_log import changed_values
from crm_g.application.pay_item import (
    DuplicateCodeError,
    PayItemInput,
    create_pay_item,
    update_pay_item,
)
from crm_g.application.system_setting import change_coefficient
from crm_g.application.work_status_rate import change_rate
from crm_g.infrastructure.db import open_connection
from crm_g.infrastructure.migration import apply_migrations

_DDL_DIR = Path(__file__).resolve().parents[2] / "DDL"
_LATER_DATE = date(2027, 4, 1)


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    path = tmp_path / "test.db"
    with open_connection(path) as connection:
        apply_migrations(connection, _DDL_DIR, path, tmp_path / "backups")
    return path


def _logs(connection: sqlite3.Connection, table: str) -> list[dict[str, Any]]:
    rows = connection.execute(
        "SELECT operation, target_key, detail, before_value, after_value FROM operation_log "
        "WHERE target_table = ? ORDER BY id",
        (table,),
    ).fetchall()
    return [
        {
            "operation": row[0],
            "target_key": row[1],
            "detail": row[2],
            "before": json.loads(row[3]) if row[3] is not None else None,
            "after": json.loads(row[4]) if row[4] is not None else None,
        }
        for row in rows
    ]


def _employee_input(**overrides: object) -> EmployeeInput:
    values: dict[str, object] = {
        "email": "Taro@Example.com",
        "name": "山田太郎",
        "name_kana": None,
        "employee_no": None,
        "employment_type": None,
        "birth_date": None,
        "gender": None,
        "join_date": date(2020, 4, 1),
        "nearest_station": None,
        "residence_area": None,
        "job_type": None,
        "enrollment_status": "在籍",
        "retire_date": None,
    }
    values.update(overrides)
    return EmployeeInput(**values)  # type: ignore[arg-type]


def _pay_item_input(name: str = "新手当") -> PayItemInput:
    return PayItemInput(
        name=name,
        category="固定支給",
        in_monthly_salary=True,
        cost_direct=False,
        deduction_order=None,
        sort_order=99,
        valid_from=date(2026, 4, 1),
        valid_to=None,
    )


def test_changed_values_returns_only_changed_columns() -> None:
    before, after = changed_values({"a": 1, "b": 2}, {"a": 1, "b": 3, "c": None})

    assert before == {"b": 2}
    assert after == {"b": 3}


def test_create_employee_logs_registered_columns(db_path: Path) -> None:
    with open_connection(db_path) as connection:
        employee_id = create_employee(connection, _employee_input())

        logs = _logs(connection, "employee")

    assert len(logs) == 1
    assert logs[0]["operation"] == "登録"
    assert logs[0]["target_key"] == str(employee_id)
    assert logs[0]["after"]["email"] == "taro@example.com"
    assert logs[0]["after"]["name"] == "山田太郎"


def test_update_employee_logs_only_changed_columns(db_path: Path) -> None:
    with open_connection(db_path) as connection:
        employee_id = create_employee(connection, _employee_input())
        update_employee(connection, employee_id, _employee_input(name="山田次郎"))

        logs = _logs(connection, "employee")

    assert logs[-1]["operation"] == "更新"
    assert logs[-1]["before"] == {"name": "山田太郎"}
    assert logs[-1]["after"] == {"name": "山田次郎"}


def test_update_employee_without_changes_logs_nothing(db_path: Path) -> None:
    with open_connection(db_path) as connection:
        employee_id = create_employee(connection, _employee_input())
        update_employee(connection, employee_id, _employee_input())

        logs = _logs(connection, "employee")

    assert [log["operation"] for log in logs] == ["登録"]


def test_create_year_calendar_logs_one_entry_with_count(db_path: Path) -> None:
    with open_connection(db_path) as connection:
        create_year_calendar(connection, 2026)

        logs = _logs(connection, "business_calendar")

    assert logs == [
        {
            "operation": "登録",
            "target_key": "2026",
            "detail": "365件",
            "before": None,
            "after": None,
        }
    ]


def test_refetch_holidays_logs_only_changed_days(db_path: Path) -> None:
    with open_connection(db_path) as connection:
        create_year_calendar(connection, 2026)
        # 元日（祝日）を、自動取込のまま営業日に書き換えておく。
        connection.execute(
            "UPDATE business_calendar SET is_business_day = 1 WHERE calendar_date = '2026-01-01'"
        )
        connection.commit()

        refetch_holidays(connection, 2026)

        logs = _logs(connection, "business_calendar")

    assert logs[1:] == [
        {
            "operation": "更新",
            "target_key": "2026-01-01",
            "detail": None,
            "before": {"is_business_day": 1},
            "after": {"is_business_day": 0},
        }
    ]


def test_register_manual_day_logs_create_then_update(db_path: Path) -> None:
    with open_connection(db_path) as connection:
        register_manual_day(connection, date(2027, 12, 29), False, "年末年始")
        register_manual_day(connection, date(2027, 12, 29), False, "年末年始の休業")
        register_manual_day(connection, date(2027, 12, 29), False, "年末年始の休業")

        logs = _logs(connection, "business_calendar")

    assert [log["operation"] for log in logs] == ["登録", "更新"]
    assert logs[0]["after"] == {"is_business_day": 0, "source": "手動登録", "note": "年末年始"}
    assert logs[1]["before"] == {"note": "年末年始"}
    assert logs[1]["after"] == {"note": "年末年始の休業"}


def test_change_rate_logs_closing_and_new_period(db_path: Path) -> None:
    with open_connection(db_path) as connection:
        change_rate(connection, "DEV", "85.50", _LATER_DATE)

        logs = _logs(connection, "work_status_rate")

    assert [log["operation"] for log in logs] == ["更新", "登録"]
    assert logs[0]["after"] == {"valid_to": "2027-03-31"}
    assert logs[1]["target_key"] == "DEV:2027-04-01"
    assert logs[1]["after"]["payroll_rate_bp"] == 8550


def test_change_coefficient_logs_closing_and_new_period(db_path: Path) -> None:
    with open_connection(db_path) as connection:
        change_coefficient(connection, "1.5", _LATER_DATE)

        logs = _logs(connection, "system_setting")

    assert [log["operation"] for log in logs] == ["更新", "登録"]
    assert logs[0]["after"] == {"valid_to": "2027-03-31"}
    assert logs[1]["after"]["valid_from"] == "2027-04-01"


def test_add_bonus_rule_logs_closing_and_new_rule(db_path: Path) -> None:
    with open_connection(db_path) as connection:
        add_bonus_rule(connection, _LATER_DATE, "月給合計")

        logs = _logs(connection, "bonus_rule")

    assert [log["operation"] for log in logs] == ["更新", "登録"]
    assert logs[1]["target_key"] == "通常評価制度:2027-04-01"
    assert logs[1]["after"]["base_type"] == "月給合計"


def test_pay_item_create_and_update_are_logged(db_path: Path) -> None:
    with open_connection(db_path) as connection:
        create_pay_item(connection, "NEW_ALLOWANCE", _pay_item_input())
        update_pay_item(connection, "NEW_ALLOWANCE", _pay_item_input(name="新しい手当"))

        logs = _logs(connection, "pay_item")

    assert [log["operation"] for log in logs] == ["登録", "更新"]
    assert logs[0]["after"]["code"] == "NEW_ALLOWANCE"
    assert logs[1]["before"] == {"name": "新手当"}
    assert logs[1]["after"] == {"name": "新しい手当"}


def test_pay_item_failure_leaves_no_log(db_path: Path) -> None:
    with open_connection(db_path) as connection:
        with pytest.raises(DuplicateCodeError):
            create_pay_item(connection, "BASE_SALARY", _pay_item_input())

        logs = _logs(connection, "pay_item")

    assert logs == []


def test_save_rates_logs_create_update_and_delete(db_path: Path) -> None:
    with open_connection(db_path) as connection:
        save_rates(connection, "2026-H1", {"A": "1.5", "B": "1.0"})
        save_rates(connection, "2026-H1", {"A": "2.0", "B": "1.0"})
        save_rates(connection, "2026-H1", {"A": "2.0", "B": None})

        logs = _logs(connection, "bonus_rate")

    assert [(log["operation"], log["target_key"]) for log in logs] == [
        ("登録", "2026-H1:A"),
        ("登録", "2026-H1:B"),
        ("更新", "2026-H1:A"),
        ("削除", "2026-H1:B"),
    ]
    assert logs[2]["before"] == {"rate_bp": 15000}
    assert logs[2]["after"] == {"rate_bp": 20000}
    assert logs[3]["before"] == {"eval_half": "2026-H1", "eval_rank": "B", "rate_bp": 10000}
    assert logs[3]["after"] is None


def test_create_employee_logs_every_registered_column(db_path: Path) -> None:
    data = _employee_input(
        name_kana="ヤマダタロウ",
        employee_no="E001",
        employment_type="正社員",
        birth_date=date(1990, 5, 6),
        gender="男性",
        nearest_station="東京",
        residence_area="東京都",
        job_type="SE",
        enrollment_status="退職予定",
        retire_date=date(2027, 3, 31),
    )
    with open_connection(db_path) as connection:
        create_employee(connection, data)

        logs = _logs(connection, "employee")

    assert logs[0]["after"] == {
        "email": "taro@example.com",
        "name": "山田太郎",
        "name_kana": "ヤマダタロウ",
        "employee_no": "E001",
        "employment_type": "正社員",
        "birth_date": "1990-05-06",
        "gender": "男性",
        "join_date": "2020-04-01",
        "nearest_station": "東京",
        "residence_area": "東京都",
        "job_type": "SE",
        "enrollment_status": "退職予定",
        "retire_date": "2027-03-31",
    }


def test_update_employee_failure_leaves_no_log(db_path: Path) -> None:
    with open_connection(db_path) as connection:
        create_employee(connection, _employee_input(employee_no="E001"))
        second_id = create_employee(
            connection, _employee_input(email="hanako@example.com", name="鈴木花子")
        )

        with pytest.raises(DuplicateEmployeeNoError):
            update_employee(
                connection,
                second_id,
                _employee_input(email="hanako@example.com", name="鈴木花子", employee_no="E001"),
            )

        logs = _logs(connection, "employee")

    assert [log["operation"] for log in logs] == ["登録", "登録"]


def test_update_pay_item_with_same_values_logs_nothing(db_path: Path) -> None:
    with open_connection(db_path) as connection:
        create_pay_item(connection, "NEW_ALLOWANCE", _pay_item_input())
        update_pay_item(connection, "NEW_ALLOWANCE", _pay_item_input())

        logs = _logs(connection, "pay_item")

    assert [log["operation"] for log in logs] == ["登録"]


def test_update_pay_item_logs_deduction_order_change_from_none(db_path: Path) -> None:
    with open_connection(db_path) as connection:
        create_pay_item(connection, "NEW_DEDUCTION", _pay_item_input())
        changed = PayItemInput(
            name="新手当",
            category="一時手当",
            in_monthly_salary=False,
            cost_direct=False,
            deduction_order=99,
            sort_order=99,
            valid_from=date(2026, 4, 1),
            valid_to=None,
        )
        update_pay_item(connection, "NEW_DEDUCTION", changed)

        logs = _logs(connection, "pay_item")

    assert logs[-1]["before"] == {
        "category": "固定支給",
        "in_monthly_salary": 1,
        "deduction_order": None,
    }
    assert logs[-1]["after"] == {
        "category": "一時手当",
        "in_monthly_salary": 0,
        "deduction_order": 99,
    }


def test_refetch_holidays_does_not_touch_manual_days(db_path: Path) -> None:
    with open_connection(db_path) as connection:
        create_year_calendar(connection, 2026)
        # 元日（祝日）を、手動登録で営業日にする。再取込でも変えてはいけない。
        register_manual_day(connection, date(2026, 1, 1), True, "元日の出勤")

        refetch_holidays(connection, 2026)

        row = connection.execute(
            "SELECT is_business_day, source FROM business_calendar "
            "WHERE calendar_date = '2026-01-01'"
        ).fetchone()
        logs = _logs(connection, "business_calendar")

    assert row == (1, "手動登録")
    assert [log["operation"] for log in logs] == ["登録", "更新"]

"""保存後の再計算の通知（`UID-055`）と、締め済みの月への警告（`UID-054`）の画面の試験。

給与履歴の登録画面（`SCN-030`）と、社員の編集画面（`SCN-022`、`TC-FUN-009`）で確かめる。
"""

from datetime import date
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from crm_g.application.business_calendar import create_year_calendar
from crm_g.application.monthly_close import close_month
from crm_g.application.recalculation import recalculate_month
from crm_g.application.salary_history import (
    SalaryHistoryInput,
    SalaryItemInput,
    register_salary_history,
)
from crm_g.application.work_period import WorkPeriodInput, create_work_period
from crm_g.infrastructure.db import open_connection
from crm_g.infrastructure.migration import apply_migrations

_VIEWS_DIR = Path(__file__).resolve().parents[2] / "src" / "crm_g" / "ui" / "views"
_VIEW_PATH = _VIEWS_DIR / "salary_history.py"
_DDL_DIR = Path(__file__).resolve().parents[2] / "DDL"
_TIMESTAMP = "2026-10-25T00:00:00.000000Z"


@pytest.fixture
def db_path(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    data_dir = tmp_path / "crm-g"
    data_dir.mkdir(parents=True, exist_ok=True)
    path = data_dir / "crm-g.db"
    with open_connection(path) as connection:
        apply_migrations(connection, _DDL_DIR, path, data_dir / "backups")
        connection.execute(
            "INSERT INTO employee "
            "(id, email, name, enrollment_status, join_date, created_at, updated_at) "
            "VALUES (1, 'taro@example.com', '山田太郎', '在籍', '2020-04-01', ?, ?)",
            (_TIMESTAMP, _TIMESTAMP),
        )
        connection.commit()
        create_year_calendar(connection, 2026)
        register_salary_history(
            connection,
            1,
            SalaryHistoryInput(
                effective_from=date(2026, 4, 1),
                pay_system="通常評価制度",
                employment_type="正社員",
                grade=None,
                annual_salary=None,
                revision_type="定期（4月）",
                note=None,
                items=(SalaryItemInput("BASE_SALARY", 300000),),
            ),
        )
        create_work_period(
            connection,
            1,
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
        recalculate_month(connection, "2026-11")
        connection.commit()
        close_month(connection, "2026-10")
    return path


def test_registration_shows_recalculated_months_and_closed_month_warning(db_path: Path) -> None:
    app = AppTest.from_file(str(_VIEW_PATH), default_timeout=30).run()
    suffix = "1"
    app.date_input(key=f"salary_history_effective_from_{suffix}").set_value(date(2026, 10, 1))
    app.selectbox(key=f"salary_history_revision_type_{suffix}").set_value("定期（10月）")
    app.text_input(key=f"salary_history_item_BASE_SALARY_{suffix}").set_value("320000")
    app.button(key=f"salary_history_submit_{suffix}").click()
    app = app.run()
    app.button(key="salary_history_confirm").click()
    app = app.run()

    assert not app.exception
    assert any("1か月分（2026-11）を再計算しました" in info.value for info in app.info)
    assert any("締め済みの月（2026-10）" in warning.value for warning in app.warning)
    with open_connection(db_path) as connection:
        salaries = connection.execute(
            "SELECT year_month, monthly_salary FROM monthly_payroll ORDER BY year_month"
        ).fetchall()
    assert salaries == [("2026-10", 300000), ("2026-11", 320000)]

    # 通知は1回だけ出す（次の描画では消える）。
    app = app.run()
    assert not any("再計算しました" in info.value for info in app.info)


def test_tc_fun_009_join_date_change_on_employee_screen(db_path: Path) -> None:
    app = AppTest.from_file(str(_VIEWS_DIR / "employee.py"), default_timeout=30).run()
    app.selectbox(key="employee_list_selected").set_value("1：山田太郎")
    app.button(key="employee_open_detail").click()
    app = app.run()
    app.button(key="employee_open_edit").click()
    app = app.run()

    app.date_input(key="employee_join_date_1").set_value(date(2026, 10, 16))
    app.button(key="employee_submit_1").click()
    app = app.run()

    assert not app.exception
    assert any("1か月分（2026-11）を再計算しました" in info.value for info in app.info)
    assert any("締め済みの月（2026-10）" in warning.value for warning in app.warning)

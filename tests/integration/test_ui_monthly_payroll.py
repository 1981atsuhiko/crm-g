"""月次給与計算画面の試験（`SCN-032`、`TC-SCN-010`〜`TC-SCN-012`）。"""

from datetime import date
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from crm_g.application.business_calendar import create_year_calendar
from crm_g.application.salary_history import (
    SalaryHistoryInput,
    SalaryItemInput,
    register_salary_history,
)
from crm_g.application.work_period import WorkPeriodInput, create_work_period
from crm_g.infrastructure.db import open_connection
from crm_g.infrastructure.migration import apply_migrations

_VIEW_PATH = (
    Path(__file__).resolve().parents[2] / "src" / "crm_g" / "ui" / "views" / "monthly_payroll.py"
)
_DDL_DIR = Path(__file__).resolve().parents[2] / "DDL"
_APPTEST_TIMEOUT_SECONDS = 30
_TIMESTAMP = "2026-10-25T00:00:00.000000Z"
_MONTH = "2026-11"


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
            "(id, email, name, enrollment_status, join_date, created_at, updated_at) VALUES "
            "(1, 'taro@example.com', '山田太郎', '在籍', '2020-04-01', ?, ?), "
            "(2, 'hanako@example.com', '鈴木花子', '在籍', '2020-04-01', ?, ?)",
            (_TIMESTAMP, _TIMESTAMP, _TIMESTAMP, _TIMESTAMP),
        )
        connection.commit()
        create_year_calendar(connection, 2026)
        for employee_id in (1, 2):
            register_salary_history(
                connection,
                employee_id,
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
    return path


def _open(month: str = _MONTH) -> AppTest:
    app = AppTest.from_file(str(_VIEW_PATH), default_timeout=_APPTEST_TIMEOUT_SECONDS).run()
    year, month_number = (int(part) for part in month.split("-"))
    app.selectbox(key="monthly_payroll_month_year").set_value(year)
    app.selectbox(key="monthly_payroll_month_month").set_value(month_number)
    return app.run()


def _recalculate(app: AppTest) -> AppTest:
    app.button(key="monthly_payroll_recalculate").click()
    return app.run()


def _metric(app: AppTest, label: str) -> str:
    return next(str(metric.value) for metric in app.metric if metric.label == label)


def _add_error_for_employee_2(path: Path) -> None:
    with open_connection(path) as connection:
        connection.execute(
            "INSERT INTO work_period (employee_id, start_date, work_status_code, "
            "billing_ratio_bp, created_at, updated_at) "
            "VALUES (2, '2026-11-15', 'TRN_FULL', 10000, ?, ?)",
            (_TIMESTAMP, _TIMESTAMP),
        )
        connection.commit()


def _closed(path: Path) -> bool:
    with open_connection(path) as connection:
        row = connection.execute(
            "SELECT COUNT(*) FROM monthly_payroll WHERE year_month = ? AND is_closed = 1",
            (_MONTH,),
        ).fetchone()
    return int(row[0]) > 0


def test_month_without_results_guides_to_recalculate(db_path: Path) -> None:
    app = _open()

    assert not app.exception
    assert any("再計算" in info.value for info in app.info)
    assert app.button(key="monthly_payroll_close").disabled


def test_recalculate_shows_results_and_records_view_log(db_path: Path) -> None:
    app = _recalculate(_open())

    assert not app.exception
    assert any("再計算しました（2人" in toast.value for toast in app.toast)
    assert _metric(app, "計算結果の人数") == "2"
    rows = app.dataframe[0].value.to_dict("records")
    assert [row["支払額（円）"] for row in rows] == ["300,000", "300,000"]
    assert rows[0]["単価（円）"] == "－"
    with open_connection(db_path) as connection:
        tables = connection.execute(
            "SELECT target_table FROM operation_log WHERE operation = '閲覧' AND target_key = ?",
            (_MONTH,),
        ).fetchall()
    assert sorted(tables) == [("monthly_payroll",), ("monthly_payroll_item",)]


def test_close_and_reopen_through_confirm_dialog(db_path: Path) -> None:
    app = _recalculate(_open())

    app.button(key="monthly_payroll_close").click()
    app = app.run()
    assert not _closed(db_path)
    app.button(key="monthly_payroll_confirm_confirm").click()
    app = app.run()

    assert not app.exception
    assert _closed(db_path)
    assert any("締めました" in toast.value for toast in app.toast)

    app.button(key="monthly_payroll_reopen").click()
    app = app.run()
    app.button(key="monthly_payroll_confirm_confirm").click()
    app = app.run()

    assert not app.exception
    assert not _closed(db_path)


def test_cancel_close_keeps_month_open(db_path: Path) -> None:
    app = _recalculate(_open())

    app.button(key="monthly_payroll_close").click()
    app = app.run()
    app.button(key="monthly_payroll_confirm_cancel").click()
    app = app.run()

    assert not app.exception
    assert not _closed(db_path)


def test_tc_scn_010_closed_month_has_no_edit_parts(db_path: Path) -> None:
    app = _recalculate(_open())
    app.button(key="monthly_payroll_close").click()
    app = app.run()
    app.button(key="monthly_payroll_confirm_confirm").click()
    app = app.run()

    assert any("締め済み（" in markdown.value for markdown in app.markdown)
    assert app.button(key="monthly_payroll_recalculate").disabled
    assert app.button(key=f"monthly_payroll_hr_submit_{_MONTH}_1").disabled
    assert not [button for button in app.button if button.key == "monthly_payroll_close"]


def test_tc_scn_011_month_with_errors_needs_acknowledgement(db_path: Path) -> None:
    app = _recalculate(_open())
    _add_error_for_employee_2(db_path)
    app = app.run()

    assert _metric(app, "計算エラー") == "1"
    assert app.dataframe[-1].value.to_dict("records") == [
        {"氏名": "鈴木花子", "原因": "稼働期間が重なっている"}
    ]
    assert app.button(key="monthly_payroll_close").disabled

    app.checkbox(key=f"monthly_payroll_ack_{_MONTH}").check()
    app = app.run()
    app.button(key="monthly_payroll_close").click()
    app = app.run()
    app.button(key="monthly_payroll_confirm_confirm").click()
    app = app.run()

    assert not app.exception
    assert _closed(db_path)


def test_tc_scn_012_hr_settings_are_kept_after_recalculation(db_path: Path) -> None:
    app = _recalculate(_open())
    suffix = f"{_MONTH}_1"

    app.selectbox(key=f"monthly_payroll_hr_target_{suffix}").set_value("対象外")
    app.text_input(key=f"monthly_payroll_hr_note_{suffix}").set_value("別途精算")
    app.button(key=f"monthly_payroll_hr_submit_{suffix}").click()
    app = app.run()
    assert any("人事連携を保存しました" in toast.value for toast in app.toast)

    app = _recalculate(app)

    rows = app.dataframe[0].value.to_dict("records")
    assert rows[0]["人事連携"] == "対象外"
    with open_connection(db_path) as connection:
        assert connection.execute(
            "SELECT hr_target, hr_note FROM monthly_payroll WHERE employee_id = 1"
        ).fetchone() == ("対象外", "別途精算")


def test_only_review_filter_and_items(db_path: Path) -> None:
    app = _recalculate(_open())

    app.checkbox(key="monthly_payroll_only_review").check()
    app = app.run()

    assert app.dataframe[0].value.empty
    assert app.dataframe[1].value.to_dict("records") == [
        {"給与項目": "基本給", "支給額（円）": "300,000", "減額（円）": "0"}
    ]


def test_saved_note_is_shown_as_stored(db_path: Path) -> None:
    app = _recalculate(_open())
    suffix = f"{_MONTH}_1"

    app.text_input(key=f"monthly_payroll_hr_note_{suffix}").set_value("  別途精算  ")
    app.button(key=f"monthly_payroll_hr_submit_{suffix}").click()
    app = app.run()

    assert app.text_input(key=f"monthly_payroll_hr_note_{suffix}").value == "別途精算"

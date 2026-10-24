"""給与履歴の登録画面の試験（`SCN-030`、`TC-SCN-008`、`TC-SCN-009`）。"""

from datetime import date
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from crm_g.application.salary_history import (
    SalaryHistoryInput,
    SalaryItemInput,
    register_salary_history,
)
from crm_g.infrastructure.db import open_connection
from crm_g.infrastructure.migration import apply_migrations

_VIEW_PATH = (
    Path(__file__).resolve().parents[2] / "src" / "crm_g" / "ui" / "views" / "salary_history.py"
)
_DDL_DIR = Path(__file__).resolve().parents[2] / "DDL"
_APPTEST_TIMEOUT_SECONDS = 30
_TIMESTAMP = "2026-10-24T00:00:00.000000Z"


@pytest.fixture
def localappdata(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    data_dir = tmp_path / "crm-g"
    data_dir.mkdir(parents=True, exist_ok=True)
    db_path = data_dir / "crm-g.db"
    with open_connection(db_path) as connection:
        apply_migrations(connection, _DDL_DIR, db_path, data_dir / "backups")
        connection.execute(
            "INSERT INTO employee "
            "(id, email, name, enrollment_status, join_date, created_at, updated_at) "
            "VALUES (1, 'taro@example.com', '山田太郎', '在籍', '2020-04-01', ?, ?)",
            (_TIMESTAMP, _TIMESTAMP),
        )
        connection.commit()
    return tmp_path


def _new_app() -> AppTest:
    return AppTest.from_file(str(_VIEW_PATH), default_timeout=_APPTEST_TIMEOUT_SECONDS).run()


def test_shows_employee_selector_and_history_list(localappdata: Path) -> None:
    app = _new_app()

    assert not app.exception
    assert len(app.dataframe) == 1


def test_tc_scn_008_registration_in_revision_month_saves_without_warning(
    localappdata: Path,
) -> None:
    app = _new_app()

    suffix = "1"
    app.date_input(key=f"salary_history_effective_from_{suffix}").set_value(date(2026, 4, 1))
    app.selectbox(key=f"salary_history_pay_system_{suffix}").set_value("通常評価制度")
    app.selectbox(key=f"salary_history_revision_type_{suffix}").set_value("定期（4月）")
    app.text_input(key=f"salary_history_item_BASE_SALARY_{suffix}").set_value("300000")
    app.button(key=f"salary_history_submit_{suffix}").click()
    app = app.run()

    assert not app.exception
    assert len(app.warning) == 0

    app.button(key="salary_history_confirm").click()
    app = app.run()

    assert not app.exception
    assert any("登録しました" in toast.value for toast in app.toast)

    db_path = localappdata / "crm-g" / "crm-g.db"
    with open_connection(db_path) as connection:
        row = connection.execute(
            "SELECT effective_from FROM salary_history WHERE employee_id = 1"
        ).fetchone()
    assert row == ("2026-04-01",)


def test_tc_scn_008_registration_outside_revision_month_shows_warning_then_saves(
    localappdata: Path,
) -> None:
    app = _new_app()

    suffix = "1"
    app.date_input(key=f"salary_history_effective_from_{suffix}").set_value(date(2026, 5, 1))
    app.selectbox(key=f"salary_history_pay_system_{suffix}").set_value("通常評価制度")
    app.selectbox(key=f"salary_history_revision_type_{suffix}").set_value("定期（4月）")
    app.text_input(key=f"salary_history_item_BASE_SALARY_{suffix}").set_value("300000")
    app.button(key=f"salary_history_submit_{suffix}").click()
    app = app.run()

    assert not app.exception
    assert len(app.warning) == 1

    db_path = localappdata / "crm-g" / "crm-g.db"
    with open_connection(db_path) as connection:
        count_before = connection.execute(
            "SELECT COUNT(*) FROM salary_history WHERE employee_id = 1"
        ).fetchone()[0]
    assert count_before == 0

    app.button(key="salary_history_confirm").click()
    app = app.run()

    assert not app.exception
    with open_connection(db_path) as connection:
        count_after = connection.execute(
            "SELECT COUNT(*) FROM salary_history WHERE employee_id = 1"
        ).fetchone()[0]
    assert count_after == 1


def test_tc_scn_009_second_registration_closes_previous_history(localappdata: Path) -> None:
    db_path = localappdata / "crm-g" / "crm-g.db"
    with open_connection(db_path) as connection:
        register_salary_history(
            connection,
            1,
            SalaryHistoryInput(
                effective_from=date(2025, 4, 1),
                pay_system="通常評価制度",
                employment_type="正社員",
                grade=None,
                annual_salary=None,
                revision_type="入社",
                note=None,
                items=(SalaryItemInput(pay_item_code="BASE_SALARY", amount=280000),),
            ),
        )

    app = _new_app()
    suffix = "1"
    app.date_input(key=f"salary_history_effective_from_{suffix}").set_value(date(2026, 4, 1))
    app.selectbox(key=f"salary_history_pay_system_{suffix}").set_value("通常評価制度")
    app.selectbox(key=f"salary_history_revision_type_{suffix}").set_value("定期（4月）")
    app.text_input(key=f"salary_history_item_BASE_SALARY_{suffix}").set_value("300000")
    app.button(key=f"salary_history_submit_{suffix}").click()
    app = app.run()

    app.button(key="salary_history_confirm").click()
    app = app.run()

    assert not app.exception
    with open_connection(db_path) as connection:
        row = connection.execute(
            "SELECT effective_to FROM salary_history WHERE effective_from = '2025-04-01'"
        ).fetchone()
    assert row == ("2026-03-31",)


def test_cancel_discards_pending_registration(localappdata: Path) -> None:
    app = _new_app()

    suffix = "1"
    app.date_input(key=f"salary_history_effective_from_{suffix}").set_value(date(2026, 4, 1))
    app.selectbox(key=f"salary_history_pay_system_{suffix}").set_value("通常評価制度")
    app.selectbox(key=f"salary_history_revision_type_{suffix}").set_value("定期（4月）")
    app.button(key=f"salary_history_submit_{suffix}").click()
    app = app.run()

    app.button(key="salary_history_cancel").click()
    app = app.run()

    assert not app.exception

    db_path = localappdata / "crm-g" / "crm-g.db"
    with open_connection(db_path) as connection:
        count = connection.execute(
            "SELECT COUNT(*) FROM salary_history WHERE employee_id = 1"
        ).fetchone()[0]
    assert count == 0


def test_employee_selector_is_disabled_while_awaiting_confirmation(localappdata: Path) -> None:
    db_path = localappdata / "crm-g" / "crm-g.db"
    with open_connection(db_path) as connection:
        connection.execute(
            "INSERT INTO employee "
            "(id, email, name, enrollment_status, join_date, created_at, updated_at) "
            "VALUES (2, 'hanako@example.com', '鈴木花子', '在籍', '2021-04-01', ?, ?)",
            (_TIMESTAMP, _TIMESTAMP),
        )
        connection.commit()

    app = _new_app()
    suffix = "1"
    app.date_input(key=f"salary_history_effective_from_{suffix}").set_value(date(2026, 4, 1))
    app.selectbox(key=f"salary_history_pay_system_{suffix}").set_value("通常評価制度")
    app.selectbox(key=f"salary_history_revision_type_{suffix}").set_value("定期（4月）")
    app.text_input(key=f"salary_history_item_BASE_SALARY_{suffix}").set_value("300000")
    app.button(key=f"salary_history_submit_{suffix}").click()
    app = app.run()

    # 確認待ち中は、社員セレクターを操作できないため、見た目と登録対象がずれない。
    assert app.selectbox(key="salary_history_employee").disabled

    app.button(key="salary_history_confirm").click()
    app = app.run()

    assert not app.exception
    with open_connection(db_path) as connection:
        count_for_employee_1 = connection.execute(
            "SELECT COUNT(*) FROM salary_history WHERE employee_id = 1"
        ).fetchone()[0]
        count_for_employee_2 = connection.execute(
            "SELECT COUNT(*) FROM salary_history WHERE employee_id = 2"
        ).fetchone()[0]
    assert count_for_employee_1 == 1
    assert count_for_employee_2 == 0

"""昇給一覧画面の試験（`SCN-031`、`TC-FUN-010`）。"""

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
    Path(__file__).resolve().parents[2] / "src" / "crm_g" / "ui" / "views" / "raise_list.py"
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
        register_salary_history(
            connection,
            1,
            SalaryHistoryInput(
                # 「今日を含む半期」が既定になるため、テスト実行時点の日付が
                # 含まれる半期に合わせた適用開始日にする。
                effective_from=date.today(),
                pay_system="通常評価制度",
                employment_type="正社員",
                grade=None,
                annual_salary=None,
                revision_type="その他",
                note=None,
                items=(SalaryItemInput(pay_item_code="BASE_SALARY", amount=300000),),
            ),
        )
    return tmp_path


def _new_app() -> AppTest:
    return AppTest.from_file(str(_VIEW_PATH), default_timeout=_APPTEST_TIMEOUT_SECONDS).run()


def test_shows_raise_in_default_half_period(localappdata: Path) -> None:
    app = _new_app()

    assert not app.exception
    assert len(app.dataframe) == 1
    rows = app.dataframe[0].value.to_dict("records")
    assert rows[0]["氏名"] == "山田太郎"
    assert rows[0]["昇給額"] == "－"


def test_switching_to_full_year_still_shows_the_raise(localappdata: Path) -> None:
    app = _new_app()

    app.radio(key="raise_list_period_unit").set_value("通期")
    app = app.run()

    assert not app.exception
    assert len(app.dataframe) == 1


def test_search_filters_by_name(localappdata: Path) -> None:
    app = _new_app()

    app.text_input(key="raise_list_search").set_value("存在しない名前")
    app = app.run()

    assert not app.exception
    assert len(app.info) == 1

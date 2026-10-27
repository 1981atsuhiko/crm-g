"""売上画面の実績の集計の試験（`SCN-051`、`TC-FUN-012`）。"""

from datetime import date
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from crm_g.application.recalculation import year_month_text
from crm_g.infrastructure.db import open_connection
from crm_g.infrastructure.migration import apply_migrations

_VIEW_PATH = Path(__file__).resolve().parents[2] / "src" / "crm_g" / "ui" / "views" / "sales.py"
_DDL_DIR = Path(__file__).resolve().parents[2] / "DDL"
_TIMESTAMP = "2026-10-27T00:00:00.000000Z"


@pytest.fixture
def db_path(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """2026-04：A（SES）売上 600,000／原価 300,000、B（カテゴリなし）売上 400,000／原価 500,000。"""
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    data_dir = tmp_path / "crm-g"
    data_dir.mkdir(parents=True, exist_ok=True)
    path = data_dir / "crm-g.db"
    with open_connection(path) as connection:
        apply_migrations(connection, _DDL_DIR, path, data_dir / "backups")
        for employee_id, name, category, sales, cost in (
            (1, "A", "SES", 600000, 300000),
            (2, "B", None, 400000, 500000),
        ):
            connection.execute(
                "INSERT INTO employee (id, email, name, enrollment_status, created_at, "
                "updated_at) VALUES (?, ?, ?, '在籍', ?, ?)",
                (employee_id, f"e{employee_id}@example.com", name, _TIMESTAMP, _TIMESTAMP),
            )
            connection.execute(
                "INSERT INTO monthly_pl (employee_id, year_month, category, sales, cost_salary, "
                "cost_allowance, cost_bonus, cost_total, gross_profit, source, created_at, "
                "updated_at) VALUES (?, '2026-04', ?, ?, ?, 0, 0, ?, ?, 'アプリ計算', ?, ?)",
                (employee_id, category, sales, cost, cost, sales - cost, _TIMESTAMP, _TIMESTAMP),
            )
        connection.commit()
    return path


def _open() -> AppTest:
    app = AppTest.from_file(str(_VIEW_PATH), default_timeout=30).run()
    app.selectbox(key="sales_fiscal_year").set_value(2026)
    return app.run()


def _rows(app: AppTest) -> list[dict[str, object]]:
    records: list[dict[str, object]] = app.dataframe[0].value.to_dict("records")
    return records


def test_monthly_total(db_path: Path) -> None:
    app = _open()

    assert not app.exception
    assert _rows(app) == [
        {
            "期間": "2026/04",
            "売上（円）": "1,000,000",
            "原価（円）": "800,000",
            "粗利（円）": "200,000",
            "粗利率": "20.0%",
        }
    ]


def test_tc_fun_012_by_category(db_path: Path) -> None:
    app = _open()
    app.segmented_control(key="sales_axis").set_value("カテゴリ別")
    app = app.run()

    assert [(row["期間"], row["カテゴリ"]) for row in _rows(app)] == [
        ("2026/04", "SES"),
        ("2026/04", "未分類"),
    ]
    assert any("締め前の月を含む" in caption.value for caption in app.caption)
    # 2026-05 から、今日の月（20期の終わりの 2027-03 まで）の月は、計算結果がないため含めない。
    today = date.today()
    last = min(year_month_text(today.year, today.month), "2027-03")
    count = sum(
        1
        for year, month in [(2026, m) for m in range(5, 13)] + [(2027, m) for m in range(1, 4)]
        if year_month_text(year, month) <= last
    )
    assert any(f"計算結果がない {count}か月" in info.value for info in app.info)


def test_by_employee_per_fiscal_year(db_path: Path) -> None:
    app = _open()
    app.segmented_control(key="sales_unit").set_value("期別")
    app.segmented_control(key="sales_axis").set_value("社員別")
    app = app.run()

    assert app.selectbox(key="sales_fiscal_year").disabled
    assert [(row["期間"], row["社員"], row["粗利率"]) for row in _rows(app)] == [
        ("20期", "A", "50.0%"),
        ("20期", "B", "-25.0%"),
    ]


def test_fiscal_year_without_results(db_path: Path) -> None:
    app = _open()
    app.selectbox(key="sales_fiscal_year").set_value(2025)
    app = app.run()

    assert not app.tabs[0].dataframe
    assert any("集計する計算結果がありません" in info.value for info in app.info)


def test_deselected_unit_falls_back_to_monthly(db_path: Path) -> None:
    app = _open()
    app.segmented_control(key="sales_unit").set_value(None)
    app = app.run()

    assert _rows(app)[0]["期間"] == "2026/04"


def test_selected_fiscal_year_is_kept_after_switching_to_per_fiscal_year(db_path: Path) -> None:
    app = _open()
    app.selectbox(key="sales_fiscal_year").set_value(2025)
    app = app.run()
    app.segmented_control(key="sales_unit").set_value("期別")
    app = app.run()
    app.segmented_control(key="sales_unit").set_value("月別")
    app = app.run()

    assert app.selectbox(key="sales_fiscal_year").value == 2025


def test_employees_with_the_same_name_show_their_ids(db_path: Path) -> None:
    with open_connection(db_path) as connection:
        connection.execute("UPDATE employee SET name = 'A' WHERE id = 2")
        connection.commit()
    app = _open()
    app.segmented_control(key="sales_axis").set_value("社員別")
    app = app.run()

    assert [row["社員"] for row in _rows(app)] == ["A（1）", "A（2）"]

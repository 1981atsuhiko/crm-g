"""ダッシュボード画面の試験（`SCN-010`、`TC-FUN-001`〜`TC-FUN-004`）。"""

from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from crm_g.application.business_calendar import create_year_calendar
from crm_g.infrastructure.db import open_connection
from crm_g.infrastructure.migration import apply_migrations

_VIEW_PATH = Path(__file__).resolve().parents[2] / "src" / "crm_g" / "ui" / "views" / "home.py"
_DDL_DIR = Path(__file__).resolve().parents[2] / "DDL"
_TIMESTAMP = "2026-10-27T00:00:00.000000Z"


@pytest.fixture
def db_path(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """A：開発稼働・売上 600,000／原価 300,000。B：待機（減額無し）・売上 400,000／原価 500,000。

    C：2026-11-01 入社・待機（減額あり）。2026-10 は在籍していない。
    """
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    data_dir = tmp_path / "crm-g"
    data_dir.mkdir(parents=True, exist_ok=True)
    path = data_dir / "crm-g.db"
    with open_connection(path) as connection:
        apply_migrations(connection, _DDL_DIR, path, data_dir / "backups")
        for employee_id, name, joined in (
            (1, "A", "2020-04-01"),
            (2, "B", "2020-04-01"),
            (3, "C", "2026-11-01"),
        ):
            connection.execute(
                "INSERT INTO employee (id, email, name, enrollment_status, join_date, "
                "created_at, updated_at) VALUES (?, ?, ?, '在籍', ?, ?, ?)",
                (employee_id, f"e{employee_id}@example.com", name, joined, _TIMESTAMP, _TIMESTAMP),
            )
        for employee_id, sales, cost in ((1, 600000, 300000), (2, 400000, 500000)):
            connection.execute(
                "INSERT INTO monthly_pl (employee_id, year_month, sales, cost_salary, "
                "cost_allowance, cost_bonus, cost_total, gross_profit, source, created_at, "
                "updated_at) VALUES (?, '2026-10', ?, ?, 0, 0, ?, ?, 'アプリ計算', ?, ?)",
                (employee_id, sales, cost, cost, sales - cost, _TIMESTAMP, _TIMESTAMP),
            )
        for employee_id, start, code in (
            (1, "2026-04-01", "DEV"),
            (2, "2026-04-01", "WAIT_FULL"),
            (3, "2026-11-01", "WAIT_CUT"),
        ):
            connection.execute(
                "INSERT INTO work_period (employee_id, start_date, work_status_code, "
                "created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
                (employee_id, start, code, _TIMESTAMP, _TIMESTAMP),
            )
        connection.commit()
        create_year_calendar(connection, 2026)
    return path


def _open(year: int, month: int) -> AppTest:
    app = AppTest.from_file(str(_VIEW_PATH), default_timeout=30).run()
    app.selectbox(key="dashboard_month_year").set_value(year)
    app.selectbox(key="dashboard_month_month").set_value(month)
    return app.run()


def _metrics(app: AppTest) -> list[tuple[str, str]]:
    return [(metric.label, metric.value) for metric in app.metric]


def test_tc_fun_001_totals_of_the_month(db_path: Path) -> None:
    app = _open(2026, 10)

    assert not app.exception
    month = [("売上（円）", "1,000,000"), ("原価（円）", "800,000"), ("粗利（円）", "200,000")]
    month.append(("粗利率", "20.0%"))
    # 当月・当半期・当期（2026-10 は下期の開始月のため、3つとも同じ値）
    assert _metrics(app)[:12] == month * 3


def test_tc_fun_002_month_without_results(db_path: Path) -> None:
    app = _open(2026, 11)

    assert [value for _, value in _metrics(app)[:4]] == ["未計算"] * 4


def test_tc_fun_003_man_day_ratio(db_path: Path) -> None:
    app = _open(2026, 10)

    # A は全営業日が開発稼働、B は全営業日が待機（21 ÷ 42）。
    assert _metrics(app)[-1] == ("稼働人日率", "50.0%")


def test_tc_fun_003_man_day_ratio_without_present_days(db_path: Path) -> None:
    app = _open(2027, 1)

    assert not app.exception
    assert _metrics(app)[-1] == ("稼働人日率", "－")
    assert any("2027/01 の営業日カレンダーがありません" in warning.value for warning in app.warning)
    assert not app.dataframe
    assert not any("待機の社員はいません" in info.value for info in app.info)


def test_tc_fun_004_waiting_list(db_path: Path) -> None:
    app = _open(2026, 11)

    # A（開発稼働）は出ない。2026-11 の営業日は 19 日（11/3・11/23 が祝日）。
    assert app.dataframe[0].value.to_dict("records") == [
        {
            "氏名": "B",
            "待機の稼働状態": "待機（減額無し）",
            "待機の営業日数": 19,
            "期間": "2026/11/01〜2026/11/30",
        },
        {
            "氏名": "C",
            "待機の稼働状態": "待機（減額あり）",
            "待機の営業日数": 19,
            "期間": "2026/11/01〜2026/11/30",
        },
    ]


def test_month_without_waiting(db_path: Path) -> None:
    app = _open(2026, 3)

    assert not app.dataframe
    assert any("待機の社員はいません" in info.value for info in app.info)

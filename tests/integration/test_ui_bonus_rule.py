"""賞与ルール・掛け率画面の試験（`SCN-064`）。"""

from datetime import date
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from crm_g.infrastructure.db import open_connection
from crm_g.infrastructure.migration import apply_migrations

_VIEW_PATH = (
    Path(__file__).resolve().parents[2] / "src" / "crm_g" / "ui" / "views" / "bonus_rule.py"
)
_DDL_DIR = Path(__file__).resolve().parents[2] / "DDL"
_APPTEST_TIMEOUT_SECONDS = 30


@pytest.fixture
def localappdata(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    data_dir = tmp_path / "crm-g"
    data_dir.mkdir(parents=True, exist_ok=True)
    db_path = data_dir / "crm-g.db"
    with open_connection(db_path) as connection:
        apply_migrations(connection, _DDL_DIR, db_path, data_dir / "backups")
    return tmp_path


def _new_app() -> AppTest:
    return AppTest.from_file(str(_VIEW_PATH), default_timeout=_APPTEST_TIMEOUT_SECONDS).run()


def test_shows_seed_bonus_rules_and_rate_editor(localappdata: Path) -> None:
    app = _new_app()

    # 賞与ルールの一覧表 + 掛け率の data_editor（AppTestでは両方ともDataframe型）。
    assert len(app.dataframe) == 2
    assert not app.exception


def test_add_bonus_rule_succeeds(localappdata: Path) -> None:
    app = _new_app()

    app.date_input(key="bonus_rule_valid_from").set_value(date(2027, 4, 1))
    app.selectbox(key="bonus_rule_base_type").set_value("基本給")
    app.button(key="bonus_rule_submit").click()
    app = app.run()

    assert not app.exception

    db_path = localappdata / "crm-g" / "crm-g.db"
    with open_connection(db_path) as connection:
        row = connection.execute(
            "SELECT base_type FROM bonus_rule WHERE valid_from = '2027-04-01'"
        ).fetchone()
    assert row == ("基本給",)


def test_save_rates_with_no_edits_does_not_crash(localappdata: Path) -> None:
    """`AppTest`は`st.data_editor`のセル編集を模せないため、未編集での保存だけを確認する。"""
    app = _new_app()

    app.button(key="bonus_rate_save_2027-H1").click()
    app = app.run()

    assert not app.exception

    db_path = localappdata / "crm-g" / "crm-g.db"
    with open_connection(db_path) as connection:
        count = connection.execute(
            "SELECT COUNT(*) FROM bonus_rate WHERE eval_half = '2027-H1'"
        ).fetchone()[0]
    assert count == 0


def test_add_bonus_rule_shows_error_without_crashing(localappdata: Path) -> None:
    app = _new_app()

    # 現在のルール（2026-04-01〜）より前の適用開始日は拒否される。
    app.date_input(key="bonus_rule_valid_from").set_value(date(2025, 4, 1))
    app.selectbox(key="bonus_rule_base_type").set_value("基本給")
    app.button(key="bonus_rule_submit").click()
    app = app.run()

    assert not app.exception
    assert any("より後" in error.value for error in app.error)

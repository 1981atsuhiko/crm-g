"""閲覧ログの記録ヘルパーの試験（`UID-060`）。"""

from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from crm_g.infrastructure.db import open_connection
from crm_g.infrastructure.migration import apply_migrations

_FIXTURE_PATH = Path(__file__).resolve().parents[1] / "data" / "view_log_fixture.py"
_DDL_DIR = Path(__file__).resolve().parents[2] / "DDL"
_APPTEST_TIMEOUT_SECONDS = 30


@pytest.fixture
def db_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "test.db"
    with open_connection(path) as connection:
        apply_migrations(connection, _DDL_DIR, path, tmp_path / "backups")
    monkeypatch.setenv("CRM_G_TEST_DB_PATH", str(path))
    return path


def _log_count(db_path: Path) -> int:
    with open_connection(db_path) as connection:
        row = connection.execute("SELECT COUNT(*) FROM operation_log").fetchone()
        return int(row[0])


def test_records_once_on_first_display(db_path: Path) -> None:
    app = AppTest.from_file(str(_FIXTURE_PATH), default_timeout=_APPTEST_TIMEOUT_SECONDS).run()

    assert not app.exception
    assert _log_count(db_path) == 1


def test_does_not_record_again_on_rerun_with_same_target(db_path: Path) -> None:
    app = AppTest.from_file(str(_FIXTURE_PATH), default_timeout=_APPTEST_TIMEOUT_SECONDS).run()
    app.run()
    app.run()

    assert _log_count(db_path) == 1

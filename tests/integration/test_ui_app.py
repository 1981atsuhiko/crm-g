"""UIエントリポイントの試験（TC-SCN-001）。"""

from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from crm_g.application.auth import setup_passcode
from crm_g.infrastructure.db import open_connection
from crm_g.infrastructure.migration import apply_migrations
from crm_g.ui.session import AUTHENTICATED_KEY

_APP_PATH = Path(__file__).resolve().parents[2] / "src" / "crm_g" / "ui" / "app.py"
_DDL_DIR = Path(__file__).resolve().parents[2] / "DDL"
_APPTEST_TIMEOUT_SECONDS = 30
_PASSCODE = "taro@3sss.co.jp"


@pytest.fixture
def localappdata(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """`get_app_paths()` が、一時ディレクトリを指すようにする。"""
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    data_dir = tmp_path / "crm-g"
    data_dir.mkdir(parents=True, exist_ok=True)
    db_path = data_dir / "crm-g.db"
    with open_connection(db_path) as connection:
        apply_migrations(connection, _DDL_DIR, db_path, data_dir / "backups")
    return tmp_path


def _new_app() -> AppTest:
    return AppTest.from_file(str(_APP_PATH), default_timeout=_APPTEST_TIMEOUT_SECONDS).run()


def _titles(app: AppTest) -> list[str]:
    return [title.value for title in app.title]


def test_tc_scn_001_shows_only_setup_screen_when_passcode_not_set(
    localappdata: Path,
) -> None:
    app = _new_app()

    assert _titles(app) == ["パスコード設定"]
    assert not app.exception


def test_setup_then_navigates_to_dashboard(localappdata: Path) -> None:
    app = _new_app()

    app.text_input(key="setup_passcode").set_value(_PASSCODE)
    app.text_input(key="setup_confirm").set_value(_PASSCODE)
    app.button(key="setup_submit").click()
    app = app.run()

    assert _titles(app) == ["ダッシュボード"]
    assert app.session_state[AUTHENTICATED_KEY] is True


def test_setup_mismatch_shows_error_and_stays_on_setup(localappdata: Path) -> None:
    app = _new_app()

    app.text_input(key="setup_passcode").set_value(_PASSCODE)
    app.text_input(key="setup_confirm").set_value("different@3sss.co.jp")
    app.button(key="setup_submit").click()
    app = app.run()

    assert _titles(app) == ["パスコード設定"]
    assert [error.value for error in app.error] == ["確認用の入力が一致しません"]


def test_tc_scn_001_shows_only_login_screen_when_authenticated_passcode_is_set(
    localappdata: Path,
) -> None:
    db_path = localappdata / "crm-g" / "crm-g.db"
    with open_connection(db_path) as connection:
        setup_passcode(connection, _PASSCODE, _PASSCODE)

    app = _new_app()

    assert _titles(app) == ["ログイン"]
    assert not app.exception


def test_login_with_correct_passcode_navigates_to_dashboard(localappdata: Path) -> None:
    db_path = localappdata / "crm-g" / "crm-g.db"
    with open_connection(db_path) as connection:
        setup_passcode(connection, _PASSCODE, _PASSCODE)

    app = _new_app()
    app.text_input(key="login_passcode").set_value(_PASSCODE)
    app.button(key="login_submit").click()
    app = app.run()

    assert _titles(app) == ["ダッシュボード"]


def test_login_with_wrong_passcode_shows_generic_error(localappdata: Path) -> None:
    db_path = localappdata / "crm-g" / "crm-g.db"
    with open_connection(db_path) as connection:
        setup_passcode(connection, _PASSCODE, _PASSCODE)

    app = _new_app()
    app.text_input(key="login_passcode").set_value("wrong@3sss.co.jp")
    app.button(key="login_submit").click()
    app = app.run()

    assert _titles(app) == ["ログイン"]
    assert [error.value for error in app.error] == ["パスコードが正しくありません"]


def test_unauthenticated_session_cannot_switch_to_a_protected_page(
    localappdata: Path,
) -> None:
    db_path = localappdata / "crm-g" / "crm-g.db"
    with open_connection(db_path) as connection:
        setup_passcode(connection, _PASSCODE, _PASSCODE)

    app = AppTest.from_file(str(_APP_PATH), default_timeout=_APPTEST_TIMEOUT_SECONDS).run()
    with pytest.raises(ValueError, match="Could not find a navigation page"):
        app.switch_page("views/home.py")

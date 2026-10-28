"""バックアップ・復元画面の試験（`SCN-065`、`TC-SCN-021`）。"""

from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

import crm_g.application.backup as backup_app
from crm_g.application.auth import setup_passcode
from crm_g.infrastructure.db import open_connection
from crm_g.infrastructure.migration import apply_migrations
from crm_g.ui.components.messages import UNEXPECTED_ERROR_MESSAGE
from crm_g.ui.session import AUTHENTICATED_KEY

_VIEW_PATH = Path(__file__).resolve().parents[2] / "src" / "crm_g" / "ui" / "views" / "backup.py"
_DDL_DIR = Path(__file__).resolve().parents[2] / "DDL"
_APPTEST_TIMEOUT_SECONDS = 60
_PASSCODE = "taro@3sss.co.jp"
_COST_KEY = "原価係数"


@pytest.fixture
def data_dir(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """`get_app_paths()` が一時ディレクトリを指すようにし、パスコードを設定した DB を作る。"""
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    path = tmp_path / "crm-g"
    path.mkdir(parents=True, exist_ok=True)
    db_path = path / "crm-g.db"
    with open_connection(db_path) as connection:
        apply_migrations(connection, _DDL_DIR, db_path, path / "backups")
        setup_passcode(connection, _PASSCODE, _PASSCODE)
    # マイグレーションの前の自動のバックアップは、試験の対象外のため消す。
    for file in (path / "backups").glob("*.db"):
        file.unlink()
    return path


def _new_app() -> AppTest:
    """認証済みのタブとして、画面を初回実行した `AppTest` を返す。"""
    app = AppTest.from_file(str(_VIEW_PATH), default_timeout=_APPTEST_TIMEOUT_SECONDS)
    app.session_state[AUTHENTICATED_KEY] = True
    return app.run()


def _create(app: AppTest, destination: str = "") -> AppTest:
    """作成先を入れて「バックアップを作成」を押す。"""
    app.text_input(key="backup_destination").set_value(destination)
    app.button(key="backup_create_submit").click()
    return app.run()


def _cost_coefficient(data_dir: Path) -> int:
    with open_connection(data_dir / "crm-g.db") as connection:
        row = connection.execute(
            "SELECT value_integer FROM system_setting WHERE setting_key = ?", (_COST_KEY,)
        ).fetchone()
    return int(row[0])


def _set_cost_coefficient(data_dir: Path, value: int) -> None:
    with open_connection(data_dir / "crm-g.db") as connection:
        connection.execute(
            "UPDATE system_setting SET value_integer = ? WHERE setting_key = ?",
            (value, _COST_KEY),
        )


def _backup_names(data_dir: Path) -> list[str]:
    return sorted(path.name for path in (data_dir / "backups").glob("*.db"))


def _texts(app: AppTest) -> str:
    """画面の文言（マークダウン・警告・情報・コード）をつなげて返す。"""
    parts = [element.value for element in app.markdown]
    parts += [element.value for element in app.warning]
    parts += [element.value for element in app.info]
    parts += [element.value for element in app.code]
    return "\n".join(str(part) for part in parts)


def test_shows_notice_location_and_empty_list(data_dir: Path) -> None:
    app = _new_app()

    assert not app.exception
    assert [title.value for title in app.title] == ["バックアップ・復元"]
    # `UID-061` の注意書きを、画面の上部に出す。
    assert "給与・賞与・評価・個人情報・操作ログ" in app.warning[0].value
    assert str(data_dir / "backups") in _texts(app)
    assert "バックアップはありません。" in _texts(app)


def test_tc_scn_021_create_list_and_restore(data_dir: Path) -> None:
    """作成すると一覧に出る。復元の前に自動のバックアップが作られ、ログアウトする。"""
    original = _cost_coefficient(data_dir)
    app = _create(_new_app())

    assert not app.exception
    created = _backup_names(data_dir)
    assert len(created) == 1
    assert any("バックアップを作成しました" in toast.value for toast in app.toast)
    assert app.selectbox(key="backup_selected").value == created[0]
    assert len(app.dataframe) == 1

    _set_cost_coefficient(data_dir, original + 500)
    app.button(key="backup_restore").click()
    app = app.run()
    # 確認ダイアログに、ファイル名・置き換わること・自動のバックアップ・ログアウトを書く。
    message = _texts(app)
    assert created[0] in message
    assert "置き換わります" in message
    assert "自動で作ります" in message
    assert "ログアウトします" in message
    assert _cost_coefficient(data_dir) == original + 500

    # 閲覧ログの重複防止（UID-060）の状態も、復元の後は残さない。
    app.session_state["crm_g_last_logged_view_target"] = {"salary_history": ["SCN-021", "7"]}
    app.button(key="backup_restore_confirm_confirm").click()
    app = app.run()

    assert not app.exception
    assert _cost_coefficient(data_dir) == original
    assert len(_backup_names(data_dir)) == 2
    assert AUTHENTICATED_KEY not in app.session_state
    assert "crm_g_last_logged_view_target" not in app.session_state
    assert any("復元しました" in toast.value for toast in app.toast)


def test_cancel_restore_keeps_data(data_dir: Path) -> None:
    app = _create(_new_app())
    _set_cost_coefficient(data_dir, 1)

    app.button(key="backup_restore").click()
    app = app.run()
    app.button(key="backup_restore_confirm_cancel").click()
    app = app.run()

    assert not app.exception
    assert _cost_coefficient(data_dir) == 1
    assert len(_backup_names(data_dir)) == 1
    assert app.session_state[AUTHENTICATED_KEY] is True


def test_restore_of_invalid_file_shows_error_and_keeps_login(data_dir: Path) -> None:
    backup_dir = data_dir / "backups"
    backup_dir.mkdir(exist_ok=True)
    (backup_dir / "crm-g_20261001_000000.db").write_bytes(b"not a sqlite database" * 100)
    app = _new_app()

    app.button(key="backup_restore").click()
    app = app.run()
    app.button(key="backup_restore_confirm_confirm").click()
    app = app.run()

    assert not app.exception
    assert any("整合性の検査に通りませんでした" in error.value for error in app.error)
    assert app.session_state[AUTHENTICATED_KEY] is True
    assert _backup_names(data_dir) == ["crm-g_20261001_000000.db"]


def test_restore_unexpected_error_shows_general_message(
    data_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = _create(_new_app())

    def _fail(*args: object, **kwargs: object) -> None:
        raise RuntimeError("内部の情報")

    monkeypatch.setattr(backup_app, "verify_backup_integrity", _fail)
    app.button(key="backup_restore").click()
    app = app.run()
    app.button(key="backup_restore_confirm_confirm").click()
    app = app.run()

    assert [error.value for error in app.error] == [UNEXPECTED_ERROR_MESSAGE]
    assert app.session_state[AUTHENTICATED_KEY] is True


def test_create_into_specified_directory(data_dir: Path, tmp_path: Path) -> None:
    destination = tmp_path / "usb"
    destination.mkdir()

    app = _create(_new_app(), str(destination))

    assert not app.exception
    assert len(list(destination.glob("crm-g_*.db"))) == 1
    assert _backup_names(data_dir) == []
    assert any("指定したフォルダに" in toast.value for toast in app.toast)


@pytest.mark.parametrize(
    ("relative", "expected"),
    [("missing", "見つかりません"), (None, "ドライブ名")],
)
def test_create_with_invalid_destination_shows_error_without_path(
    data_dir: Path, tmp_path: Path, relative: str | None, expected: str
) -> None:
    destination = str(tmp_path / relative) if relative is not None else "backups"

    app = _create(_new_app(), destination)

    assert not app.exception
    errors = [error.value for error in app.error]
    assert len(errors) == 1
    assert expected in errors[0]
    assert str(tmp_path) not in errors[0]
    assert _backup_names(data_dir) == []


def test_create_unexpected_error_shows_general_message(
    data_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def _fail(*args: object, **kwargs: object) -> None:
        raise RuntimeError("内部の情報")

    monkeypatch.setattr(backup_app, "record_operation_log", _fail)

    app = _create(_new_app())

    assert [error.value for error in app.error] == [UNEXPECTED_ERROR_MESSAGE]
    assert _backup_names(data_dir) == []

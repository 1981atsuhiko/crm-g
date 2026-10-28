"""パスコード変更画面の試験（`SCN-066`、TC-FUN-018）。"""

from pathlib import Path

import pytest
from streamlit.proto.TextInput_pb2 import TextInput as TextInputProto
from streamlit.testing.v1 import AppTest

import crm_g.application.auth as auth_module
from crm_g.application.auth import InvalidPasscodeError, setup_passcode, verify_passcode
from crm_g.infrastructure.db import open_connection
from crm_g.infrastructure.migration import apply_migrations
from crm_g.ui.components.messages import UNEXPECTED_ERROR_MESSAGE
from crm_g.ui.session import AUTHENTICATED_KEY

_VIEW_PATH = (
    Path(__file__).resolve().parents[2] / "src" / "crm_g" / "ui" / "views" / "passcode_change.py"
)
_DDL_DIR = Path(__file__).resolve().parents[2] / "DDL"
_APPTEST_TIMEOUT_SECONDS = 30
_PASSCODE = "taro@3sss.co.jp"
_NEW_PASSCODE = "jiro@3sss.co.jp"
_INPUT_KEYS = ("passcode_change_current", "passcode_change_new", "passcode_change_confirm")


@pytest.fixture
def db_path(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """`get_app_paths()` が一時ディレクトリを指すようにし、パスコードを設定した DB を作る。"""
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    data_dir = tmp_path / "crm-g"
    data_dir.mkdir(parents=True, exist_ok=True)
    path = data_dir / "crm-g.db"
    with open_connection(path) as connection:
        apply_migrations(connection, _DDL_DIR, path, data_dir / "backups")
        setup_passcode(connection, _PASSCODE, _PASSCODE)
    return path


def _new_app() -> AppTest:
    """画面を初回実行した `AppTest` を返す。"""
    return AppTest.from_file(str(_VIEW_PATH), default_timeout=_APPTEST_TIMEOUT_SECONDS).run()


def _submit(app: AppTest, current_passcode: str, new_passcode: str, confirm: str) -> AppTest:
    """3つの入力欄に値を入れて「変更」を押し、再実行した結果を返す。"""
    app.text_input(key="passcode_change_current").set_value(current_passcode)
    app.text_input(key="passcode_change_new").set_value(new_passcode)
    app.text_input(key="passcode_change_confirm").set_value(confirm)
    app.button(key="passcode_change_submit").click()
    return app.run()


def _operation_counts(db_path: Path) -> dict[str, int]:
    """操作ログの操作ごとの件数を返す。"""
    with open_connection(db_path) as connection:
        rows = connection.execute(
            "SELECT operation, COUNT(*) FROM operation_log GROUP BY operation"
        ).fetchall()
    return {operation: count for operation, count in rows}


def _errors(app: AppTest) -> list[str]:
    """画面に出たエラーの文言を返す。"""
    return [error.value for error in app.error]


def _assert_inputs_cleared(app: AppTest) -> None:
    """送信後に、`st.session_state` に入力値が残っていないことを確認する。"""
    for key in _INPUT_KEYS:
        assert key not in app.session_state


def test_shows_form_with_password_inputs(db_path: Path) -> None:
    app = _new_app()

    assert [title.value for title in app.title] == ["パスコード変更"]
    for key in _INPUT_KEYS:
        assert app.text_input(key=key).proto.type == TextInputProto.PASSWORD
    assert not app.exception


def test_tc_fun_018_wrong_current_passcode_is_rejected_and_logged(db_path: Path) -> None:
    """①現在のパスコードを誤ると、変更されず、`認証失敗` が記録される。"""
    app = _submit(_new_app(), "wrong@3sss.co.jp", _NEW_PASSCODE, _NEW_PASSCODE)

    assert not app.exception
    assert _errors(app) == ["現在のパスコードが正しくありません"]
    assert _operation_counts(db_path) == {"パスコード設定": 1, "認証失敗": 1}
    _assert_inputs_cleared(app)
    with open_connection(db_path) as connection:
        verify_passcode(connection, _PASSCODE)


def test_tc_fun_018_change_passcode_logs_and_switches_passcode(db_path: Path) -> None:
    """②変更すると `パスコード設定` が記録され（値は残らない）、③新しいパスコードだけで通る。"""
    app = _submit(_new_app(), _PASSCODE, _NEW_PASSCODE, _NEW_PASSCODE)

    assert not app.exception
    assert _errors(app) == []
    assert [toast.value for toast in app.toast] == ["パスコードを変更しました"]
    assert _operation_counts(db_path) == {"パスコード設定": 2, "認証成功": 1}
    _assert_inputs_cleared(app)

    with open_connection(db_path) as connection:
        rows = connection.execute(
            "SELECT target_table, target_key, detail, before_value, after_value FROM operation_log"
        ).fetchall()
        credential = connection.execute(
            "SELECT salt, hash FROM app_credential WHERE id = 1"
        ).fetchone()
        for value in [*(column for row in rows for column in row), *credential]:
            assert _PASSCODE not in str(value)
            assert _NEW_PASSCODE not in str(value)

        with pytest.raises(InvalidPasscodeError):
            verify_passcode(connection, _PASSCODE)
        verify_passcode(connection, _NEW_PASSCODE)


def test_mismatched_new_passcode_is_rejected_without_verifying_current(db_path: Path) -> None:
    app = _submit(_new_app(), _PASSCODE, _NEW_PASSCODE, "saburo@3sss.co.jp")

    assert not app.exception
    assert _errors(app) == ["新しいパスコードと、確認用の入力が一致しません"]
    assert _operation_counts(db_path) == {"パスコード設定": 1}
    _assert_inputs_cleared(app)


def test_non_email_new_passcode_is_rejected(db_path: Path) -> None:
    app = _submit(_new_app(), _PASSCODE, "not-an-email", "not-an-email")

    assert not app.exception
    assert _errors(app) == ["新しいパスコードは、メールアドレス形式の文字列にしてください"]
    assert _operation_counts(db_path) == {"パスコード設定": 1}
    _assert_inputs_cleared(app)


def test_locked_account_shows_generic_message(db_path: Path) -> None:
    with open_connection(db_path) as connection:
        connection.execute(
            "UPDATE app_credential SET failed_count = 5, "
            "locked_until = '9999-12-31T23:59:59.000000Z' WHERE id = 1"
        )
        connection.commit()

    app = _submit(_new_app(), _PASSCODE, _NEW_PASSCODE, _NEW_PASSCODE)

    assert not app.exception
    assert _errors(app) == ["現在、入力を受け付けていません。時間をおいてやり直してください"]
    assert _operation_counts(db_path) == {"パスコード設定": 1}
    _assert_inputs_cleared(app)


def test_unexpected_error_shows_generic_message(
    db_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def _failing_change_passcode(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("擬似的な失敗")

    monkeypatch.setattr(auth_module, "change_passcode", _failing_change_passcode)

    app = _submit(_new_app(), _PASSCODE, _NEW_PASSCODE, _NEW_PASSCODE)

    assert not app.exception
    assert _errors(app) == [UNEXPECTED_ERROR_MESSAGE]


def test_can_retry_after_an_error(db_path: Path) -> None:
    """入力の誤りの後に、同じ画面で入力し直して変更できる。"""
    app = _submit(_new_app(), _PASSCODE, _NEW_PASSCODE, "saburo@3sss.co.jp")
    assert _errors(app) == ["新しいパスコードと、確認用の入力が一致しません"]

    app = _submit(app, _PASSCODE, _NEW_PASSCODE, _NEW_PASSCODE)

    assert not app.exception
    assert _errors(app) == []
    assert _operation_counts(db_path) == {"パスコード設定": 2, "認証成功": 1}
    _assert_inputs_cleared(app)


def test_same_passcode_is_accepted(db_path: Path) -> None:
    app = _submit(_new_app(), _PASSCODE, _PASSCODE, _PASSCODE)

    assert not app.exception
    assert any("パスコードを変更しました" in toast.value for toast in app.toast)


def test_messages_do_not_contain_passcodes(db_path: Path) -> None:
    app = _submit(_new_app(), "wrong@3sss.co.jp", _NEW_PASSCODE, _NEW_PASSCODE)
    shown = [*_errors(app), *(toast.value for toast in app.toast)]

    assert shown
    assert all("3sss" not in message for message in shown)


def test_locking_on_this_screen_does_not_log_out(db_path: Path) -> None:
    app = _new_app()
    app.session_state[AUTHENTICATED_KEY] = True
    for _ in range(5):
        app = _submit(app, "wrong@3sss.co.jp", _NEW_PASSCODE, _NEW_PASSCODE)

    app = _submit(app, _PASSCODE, _NEW_PASSCODE, _NEW_PASSCODE)

    assert _errors(app) == ["現在、入力を受け付けていません。時間をおいてやり直してください"]
    assert app.session_state[AUTHENTICATED_KEY] is True

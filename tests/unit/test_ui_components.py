"""UI共通部品の試験（`UID-051`、`UID-052`、`UID-053`）。"""

from pathlib import Path

from streamlit.testing.v1 import AppTest

_FIXTURE_PATH = Path(__file__).resolve().parents[1] / "data" / "ui_component_fixture.py"
_APPTEST_TIMEOUT_SECONDS = 30


def _new_app() -> AppTest:
    return AppTest.from_file(str(_FIXTURE_PATH), default_timeout=_APPTEST_TIMEOUT_SECONDS).run()


def test_show_errors_displays_each_message() -> None:
    app = _new_app()
    app.button(key="show_errors_button").click()
    app = app.run()

    assert [error.value for error in app.error] == ["エラー1", "エラー2"]


def test_show_warning_displays_message() -> None:
    app = _new_app()
    app.button(key="show_warning_button").click()
    app = app.run()

    assert [warning.value for warning in app.warning] == ["警告メッセージ"]


def test_show_status_displays_message() -> None:
    app = _new_app()
    app.button(key="show_status_button").click()
    app = app.run()

    assert [info.value for info in app.info] == ["状態メッセージ"]


def test_two_step_form_tracks_awaiting_confirmation_state() -> None:
    app = _new_app()
    assert "awaiting=False" in [markdown.value for markdown in app.markdown]

    app.button(key="start_confirmation_button").click()
    app = app.run()
    assert "awaiting=True" in [markdown.value for markdown in app.markdown]

    app.button(key="discard_button").click()
    app = app.run()
    assert "awaiting=False" in [markdown.value for markdown in app.markdown]


def test_confirm_dialog_shows_message_when_opened() -> None:
    app = _new_app()
    app.button(key="open_dialog_button").click()
    app = app.run()

    assert "本当に実行しますか" in [markdown.value for markdown in app.markdown]

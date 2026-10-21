from __future__ import annotations

from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from crm_poc import settings

APP_PATH = Path(__file__).resolve().parents[1] / "src" / "crm_poc" / "app.py"
PAGE_PREFIX = "ui_pages/"
APPTEST_TIMEOUT_SECONDS = 30


def _new_app(*, authenticated: bool) -> AppTest:
    app = AppTest.from_file(str(APP_PATH), default_timeout=APPTEST_TIMEOUT_SECONDS)
    if authenticated:
        app.session_state[settings.SESSION_AUTHENTICATED_KEY] = True
    return app.run()


def _sign_in(app: AppTest, passcode: str) -> AppTest:
    app.text_input(key="passcode_input").set_value(passcode)
    app.button(key="sign_in").click()
    return app.run()


def _titles(app: AppTest) -> list[str]:
    return [title.value for title in app.title]


def _texts(app: AppTest) -> list[str]:
    return [str(markdown.value) for markdown in app.markdown]


def _rendered(app: AppTest) -> str:
    values = [str(element.value) for element in (*app.markdown, *app.title, *app.text, *app.error)]
    values.extend(str(element.value) for element in (*app.code, *app.exception))
    return "\n".join(values)


def test_before_authentication_only_login_is_shown() -> None:
    app = _new_app(authenticated=False)

    assert _titles(app) == ["login"]
    assert not app.exception
    assert settings.PROTECTED_MARKER not in _rendered(app)


def test_wrong_passcode_stays_on_login_with_error() -> None:
    app = _sign_in(_new_app(authenticated=False), "wrong-passcode")

    assert _titles(app) == ["login"]
    assert [error.value for error in app.error] == ["authentication failed"]


def test_correct_passcode_moves_to_home() -> None:
    app = _sign_in(_new_app(authenticated=False), settings.DEMO_PASSCODE)

    assert _titles(app) == ["home"]
    assert app.session_state[settings.SESSION_AUTHENTICATED_KEY] is True


def test_unauthenticated_session_cannot_switch_to_secret_page() -> None:
    app = _new_app(authenticated=False)

    with pytest.raises(ValueError, match="Could not find a navigation page"):
        app.switch_page(PAGE_PREFIX + "secret.py")


@pytest.mark.parametrize(
    ("page_file", "expected_title"),
    [
        ("secret.py", "secret"),
        ("download.py", "download"),
        ("widgets.py", "widgets"),
    ],
)
def test_authenticated_session_can_switch_pages(page_file: str, expected_title: str) -> None:
    app = _new_app(authenticated=True)
    app.switch_page(PAGE_PREFIX + page_file)
    app.run()

    assert _titles(app) == [expected_title]
    assert not app.exception


def test_secret_page_shows_marker_only_after_authentication() -> None:
    app = _new_app(authenticated=True)
    app.switch_page(PAGE_PREFIX + "secret.py")
    app.run()

    assert settings.PROTECTED_MARKER in _rendered(app)


def test_download_page_has_two_download_buttons() -> None:
    app = _new_app(authenticated=True)
    app.switch_page(PAGE_PREFIX + "download.py")
    app.run()

    assert len(app.get("download_button")) == 2


def test_widgets_page_elements_are_visible_to_apptest() -> None:
    app = _new_app(authenticated=True)
    app.switch_page(PAGE_PREFIX + "widgets.py")
    app.run()

    assert len(app.get("file_uploader")) == 1
    assert len(app.get("dataframe")) == 1
    assert [date_input.value for date_input in app.date_input] == [None]


def test_dialog_content_is_visible_after_opening() -> None:
    app = _new_app(authenticated=True)
    app.switch_page(PAGE_PREFIX + "widgets.py")
    app.run()
    app.button(key="open_dialog").click()
    app.run()

    assert "dialog body" in _texts(app)

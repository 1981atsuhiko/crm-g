"""UI共通部品の試験（`UID-051`、`UID-052`、`UID-053`）。"""

from pathlib import Path

from streamlit.testing.v1 import AppTest
from streamlit.testing.v1.element_tree import Block

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


def _dismiss_dialog(app: AppTest) -> AppTest:
    """開いているダイアログを、右上の「×」・Esc キーで閉じた操作を再現して、再実行する。

    `AppTest` には、ダイアログを閉じる操作がない。`on_dismiss` を渡した `st.dialog` は、
    ダイアログのブロックの ID のウィジェットとして登録され、閉じるとフロントエンドが、その
    ウィジェットの trigger の値を送る。ここでは、同じ値を組み立てて、再実行に渡す。
    """
    dialogs = [node for node in _blocks(app._tree) if node.type == "dialog"]
    assert len(dialogs) == 1, "開いているダイアログが1つではありません"
    widget_states = app._tree.get_widget_states()
    dismissed = widget_states.widgets.add()
    dismissed.id = dialogs[0].proto.dialog.id
    dismissed.trigger_value = True
    return app._run(widget_states)


def _blocks(block: Block) -> list[Block]:
    """ブロックと、その中のブロックを、深さ優先で返す。"""
    found = [block]
    for child in block.children.values():
        if isinstance(child, Block):
            found.extend(_blocks(child))
    return found


def test_confirm_dialog_dismiss_calls_on_cancel() -> None:
    """「×」・Esc キーで閉じたときは、キャンセルと同じく `on_cancel` を呼ぶ（`UID-053`）。

    閉じても確認待ちの状態が残ると、後の再実行で、頼んでいないのにダイアログが開き直す。
    """
    app = _new_app()
    app.button(key="open_pending_dialog_button").click()
    app = app.run()
    assert "確認待ちのダイアログです" in _markdowns(app)

    app = _dismiss_dialog(app)

    assert not app.exception
    assert "pending=False" in _markdowns(app)
    assert "cancel_count=1" in _markdowns(app)
    assert "確認待ちのダイアログです" not in _markdowns(app)
    # 次の再実行でも、ダイアログは開き直さない。
    app = app.run()
    assert "確認待ちのダイアログです" not in _markdowns(app)
    assert "cancel_count=1" in _markdowns(app)


def test_confirm_dialog_cancel_button_calls_on_cancel_once() -> None:
    app = _new_app()
    app.button(key="open_pending_dialog_button").click()
    app = app.run()

    app.button(key="fixture_pending_dialog_cancel").click()
    app = app.run()

    assert not app.exception
    assert "pending=False" in _markdowns(app)
    assert "cancel_count=1" in _markdowns(app)


def _markdowns(app: AppTest) -> list[str]:
    return [str(markdown.value) for markdown in app.markdown]

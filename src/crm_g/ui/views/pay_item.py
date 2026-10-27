"""給与項目画面（`SCN-062`）。

給与項目・手当を、追加・変更する。削除は設けない（使わなくなった項目は、
有効期間の終了日を設定する）。操作ログへの記録は、アプリケーション層で行う（`SCD-020`）。
保存後に、再計算の通知と、締め済みの月への警告を出す（`UID-055`、`UID-054`）。
"""

from datetime import date
from pathlib import Path
from typing import TypedDict

import streamlit as st

from crm_g.application.pay_item import (
    CATEGORIES,
    CodeNotFoundError,
    DuplicateCodeError,
    InvalidCategoryError,
    InvalidCodeError,
    InvalidDeductionOrderError,
    InvalidPeriodError,
    MutuallyExclusiveFlagsError,
    PayItem,
    PayItemInput,
    create_pay_item,
    list_pay_items,
    update_pay_item,
)
from crm_g.infrastructure.db import open_connection
from crm_g.infrastructure.paths import get_app_paths
from crm_g.ui.components.confirm_dialog import confirm_dialog
from crm_g.ui.components.messages import UNEXPECTED_ERROR_MESSAGE, show_errors, show_success
from crm_g.ui.components.recalculation_notice import (
    remember_recalculation,
    show_recalculation_notice,
)

_NEW_ITEM_LABEL = "（新規追加）"
_PENDING_KEY = "pay_item_pending"
_LAST_ERROR_KEY = "pay_item_last_error"


class _PendingPayItem(TypedDict):
    """確認待ちの「項目の入力」（複数回の再実行をまたいで保持する）。"""

    is_new: bool
    code: str
    name: str
    category: str
    in_monthly_salary: bool
    cost_direct: bool
    deduction_order: int | None
    sort_order: int
    valid_from: str
    valid_to: str | None


def _format_flag(value: bool) -> str:
    return "○" if value else "×"


def _render_list(items: tuple[PayItem, ...]) -> str:
    st.dataframe(
        [
            {
                "コード": item.code,
                "名称": item.name,
                "区分": item.category,
                "月給に含む": _format_flag(item.in_monthly_salary),
                "原価に直接加算": _format_flag(item.cost_direct),
                "減額順": str(item.deduction_order) if item.deduction_order is not None else "",
                "表示順": item.sort_order,
                "開始日": item.valid_from.isoformat(),
                "終了日": item.valid_to.isoformat() if item.valid_to else "",
            }
            for item in items
        ],
        hide_index=True,
    )

    options = [_NEW_ITEM_LABEL, *(item.code for item in items)]
    return str(st.selectbox("給与項目を選ぶ", options, key="pay_item_selected_code"))


def _confirm_pending(db_path: Path, pending: _PendingPayItem) -> None:
    def _save() -> None:
        data = PayItemInput(
            name=pending["name"],
            category=pending["category"],
            in_monthly_salary=pending["in_monthly_salary"],
            cost_direct=pending["cost_direct"],
            deduction_order=pending["deduction_order"],
            sort_order=pending["sort_order"],
            valid_from=date.fromisoformat(pending["valid_from"]),
            valid_to=date.fromisoformat(pending["valid_to"]) if pending["valid_to"] else None,
        )
        with open_connection(db_path) as connection:
            try:
                if pending["is_new"]:
                    create_pay_item(connection, pending["code"], data)
                else:
                    update_pay_item(
                        connection, pending["code"], data, on_recalculated=remember_recalculation
                    )
            except (
                InvalidCodeError,
                InvalidCategoryError,
                DuplicateCodeError,
                CodeNotFoundError,
                MutuallyExclusiveFlagsError,
                InvalidDeductionOrderError,
                InvalidPeriodError,
            ) as error:
                st.session_state[_LAST_ERROR_KEY] = str(error)
            except Exception:
                st.session_state[_LAST_ERROR_KEY] = UNEXPECTED_ERROR_MESSAGE
            else:
                show_success("登録しました")
        st.session_state.pop(_PENDING_KEY, None)

    def _cancel() -> None:
        st.session_state.pop(_PENDING_KEY, None)

    action = "追加" if pending["is_new"] else "変更"
    confirm_dialog(
        "確認",
        f"給与項目 {pending['code']} を{action}します。よろしいですか？",
        key="pay_item_confirm",
        on_confirm=_save,
        on_cancel=_cancel,
    )


def _render_form(db_path: Path, selected: str, existing: PayItem | None) -> None:
    is_new = selected == _NEW_ITEM_LABEL
    st.subheader("項目の入力")

    # ウィジェットのkeyに選択中のレコードを含める。固定keyのままだと、選択を
    # 変えてもst.session_stateに残った前回の値が使われ続け、valueの指定が
    # 無視される（フォームが選択に追従しない不具合の原因になる）。
    key_suffix = selected

    with st.form(f"pay_item_form_{key_suffix}"):
        code = st.text_input(
            "コード",
            value="" if is_new else selected,
            disabled=not is_new,
            key=f"pay_item_code_{key_suffix}",
        )
        name = st.text_input(
            "名称", value=existing.name if existing else "", key=f"pay_item_name_{key_suffix}"
        )
        category = st.selectbox(
            "区分",
            CATEGORIES,
            index=CATEGORIES.index(existing.category) if existing else 0,
            key=f"pay_item_category_{key_suffix}",
        )
        in_monthly_salary = st.selectbox(
            "月給に含む",
            ["○", "×"],
            index=0 if (existing is None or existing.in_monthly_salary) else 1,
            key=f"pay_item_in_monthly_salary_{key_suffix}",
        )
        cost_direct = st.selectbox(
            "原価に直接加算",
            ["○", "×"],
            index=1 if (existing is None or not existing.cost_direct) else 0,
            key=f"pay_item_cost_direct_{key_suffix}",
        )
        deduction_order_text = st.text_input(
            "減額順（空欄＝減額の対象外）",
            value=str(existing.deduction_order) if existing and existing.deduction_order else "",
            key=f"pay_item_deduction_order_{key_suffix}",
        )
        sort_order = st.number_input(
            "表示順",
            value=existing.sort_order if existing else 0,
            step=1,
            key=f"pay_item_sort_order_{key_suffix}",
        )
        valid_from = st.date_input(
            "有効期間の開始日",
            value=existing.valid_from if existing else None,
            key=f"pay_item_valid_from_{key_suffix}",
        )
        valid_to = st.date_input(
            "有効期間の終了日（空欄＝未定）",
            value=existing.valid_to if existing else None,
            key=f"pay_item_valid_to_{key_suffix}",
        )
        submitted = st.form_submit_button("保存", key=f"pay_item_submit_{key_suffix}")

    if not submitted:
        return

    # 編集時は、無効化されたコード欄の値ではなく、選択中のコードをそのまま使う
    # （無効化は表示上の配慮であり、書き込み先を決める情報として信頼しない）。
    code = code if is_new else selected

    if not code:
        show_errors(["コードを入力してください"])
        return
    if not name:
        show_errors(["名称を入力してください"])
        return
    if not isinstance(valid_from, date):
        show_errors(["有効期間の開始日を入力してください"])
        return

    deduction_order: int | None = None
    if deduction_order_text:
        try:
            deduction_order = int(deduction_order_text)
        except ValueError:
            show_errors(["減額順は、整数で入力してください"])
            return

    st.session_state[_PENDING_KEY] = _PendingPayItem(
        is_new=is_new,
        code=code,
        name=name,
        category=category,
        in_monthly_salary=in_monthly_salary == "○",
        cost_direct=cost_direct == "○",
        deduction_order=deduction_order,
        sort_order=int(sort_order),
        valid_from=valid_from.isoformat(),
        valid_to=valid_to.isoformat() if isinstance(valid_to, date) else None,
    )


def render() -> None:
    st.title("給与項目")
    show_recalculation_notice()

    last_error = st.session_state.pop(_LAST_ERROR_KEY, None)
    if last_error is not None:
        show_errors([last_error])

    db_path = get_app_paths().db_path
    with open_connection(db_path) as connection:
        items = list_pay_items(connection)

    selected = _render_list(items)
    existing = (
        None
        if selected == _NEW_ITEM_LABEL
        else next((item for item in items if item.code == selected), None)
    )

    _render_form(db_path, selected, existing)

    pending = st.session_state.get(_PENDING_KEY)
    if pending is not None:
        _confirm_pending(db_path, pending)


render()

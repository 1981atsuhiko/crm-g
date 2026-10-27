"""賞与画面（`SCN-040`）。

半期を選び、対象者の一覧を見て、評価を入力し、賞与を計算・確定する。支給済みの賞与は
手入力する。確定は `UID-053` の確認の後に行い、保存後の再計算の通知・締め済みの月への
警告を出す（`UID-055`、`UID-054`）。

一覧は表示だけとし、評価は社員を選んで入力する（`st.data_editor` は、`AppTest` で操作
できないため。画面設計書 3.11 と同じく、画面の使いやすさの改善のときに置き換える）。
19期の半期は、評価の入力・計算の部品を出さず、手入力だけを行う。
"""

from datetime import date
from pathlib import Path
from typing import TypedDict

import streamlit as st

from crm_g.application.bonus import (
    NoBaseMonthCalendarError,
    NoBonusRuleError,
    NotBonusTargetError,
)
from crm_g.application.bonus_entry import (
    BASE_TYPES,
    EVAL_RANKS,
    BonusScreen,
    BonusScreenRow,
    DuplicateBonusError,
    EvaluationInput,
    InvalidEvaluationError,
    InvalidManualBonusError,
    ManualBonusInput,
    NothingToConfirmError,
    StaleBonusCalculationError,
    confirm_bonuses,
    load_bonus_screen,
    register_manual_bonus,
    save_bonus_calculation,
    save_evaluations,
)
from crm_g.application.employee import ENROLLMENT_STATUSES, list_employees
from crm_g.domain.period import current_fiscal_half, half_code, half_label, previous_half
from crm_g.infrastructure.db import open_connection
from crm_g.infrastructure.paths import get_app_paths
from crm_g.ui.components.confirm_dialog import confirm_dialog
from crm_g.ui.components.half_selector import select_half
from crm_g.ui.components.messages import (
    UNEXPECTED_ERROR_MESSAGE,
    show_errors,
    show_status,
    show_success,
    show_unexpected_error,
)
from crm_g.ui.components.recalculation_notice import (
    remember_recalculation,
    show_recalculation_notice,
)
from crm_g.ui.components.view_log import record_view_if_newly_displayed

_SCREEN_NAME = "SCN-040"
_PENDING_KEY = "bonus_pending_confirm"
_LAST_RESULT_KEY = "bonus_last_result"
_LAST_ERROR_KEY = "bonus_last_error"
_UNSET_RANK = "（未入力）"
_DASH = "－"
_SOURCE_CALCULATED = "アプリ計算"


class _Pending(TypedDict):
    """確定の確認待ち（再実行をまたいで保持するため、組み込み型で持つ）。"""

    eval_half: str
    message: str


def _yen(amount: int | None) -> str:
    return _DASH if amount is None else f"{amount:,}"


def _rate(rate_bp: int | None) -> str:
    return _DASH if rate_bp is None else f"{rate_bp / 10000:g}"


def _finish(message: str) -> None:
    st.session_state[_LAST_RESULT_KEY] = message
    st.rerun()


def _editable(row: BonusScreenRow) -> bool:
    return not row.is_closed and row.source in (None, _SOURCE_CALCULATED)


def _render_table(screen: BonusScreen) -> None:
    if not screen.rows:
        show_status("この半期の賞与の対象者・登録済みの賞与はありません。")
        return
    st.dataframe(
        [
            {
                "氏名": row.employee_name,
                "基準額の種類": row.base_type or "",
                "基準額（円）": _yen(row.base_amount),
                "評価ランク": row.eval_rank or "",
                "掛け率（倍）": _rate(row.rate_bp),
                "賞与額（円）": "要再計算" if row.needs_recalculation else _yen(row.bonus_amount),
                "評価者": row.evaluator or "",
                "コメント": row.comment or "",
                "確定": "○" if row.is_closed else "×",
            }
            for row in screen.rows
        ],
        hide_index=True,
    )


def _render_evaluation_form(db_path: Path, screen: BonusScreen) -> None:
    editable = {row.employee_id: row for row in screen.rows if _editable(row)}
    if not editable:
        return
    st.subheader("評価の入力")
    employee_id = st.selectbox(
        "評価を入力する社員",
        list(editable),
        format_func=lambda option: f"{option}：{editable[option].employee_name}",
        key=f"bonus_eval_employee_{screen.eval_half}",
    )
    selected = editable[employee_id]
    suffix = f"{screen.eval_half}_{employee_id}"
    ranks = [_UNSET_RANK, *EVAL_RANKS]
    with st.form(f"bonus_eval_form_{suffix}"):
        rank = st.selectbox(
            "評価ランク",
            ranks,
            index=ranks.index(selected.eval_rank) if selected.eval_rank in ranks else 0,
            key=f"bonus_eval_rank_{suffix}",
        )
        evaluator = st.text_input(
            "評価者", value=selected.evaluator or "", key=f"bonus_eval_evaluator_{suffix}"
        )
        comment = st.text_input(
            "コメント", value=selected.comment or "", key=f"bonus_eval_comment_{suffix}"
        )
        submitted = st.form_submit_button("評価を保存", key=f"bonus_eval_submit_{suffix}")
    if not submitted:
        return
    entry = EvaluationInput(
        employee_id, None if rank == _UNSET_RANK else rank, evaluator or None, comment or None
    )
    try:
        with open_connection(db_path) as connection:
            saved = save_evaluations(connection, screen.eval_half, [entry])
    except InvalidEvaluationError as error:
        show_errors([str(error)])
        return
    except Exception:
        show_unexpected_error()
        return
    for field in ("rank", "evaluator", "comment"):
        st.session_state.pop(f"bonus_eval_{field}_{suffix}", None)
    _finish("評価を保存しました" if saved else "評価に変更はありません")


def _render_actions(db_path: Path, screen: BonusScreen) -> None:
    calculate_column, confirm_column = st.columns(2)
    if calculate_column.button("賞与を計算", key="bonus_calculate"):
        try:
            with open_connection(db_path) as connection:
                saved = save_bonus_calculation(connection, screen.eval_half)
        except Exception:
            show_unexpected_error()
            return
        _finish(f"{saved}人の賞与を計算しました")

    unconfirmed = [
        row
        for row in screen.rows
        if _editable(row) and row.source == _SOURCE_CALCULATED and row.bonus_amount is not None
    ]
    stale = any(row.needs_recalculation for row in screen.rows)
    if stale:
        st.warning(
            "計算のあとに評価・掛け率・対象者が変わった社員がいます（賞与額が「要再計算」）。"
            "もう一度「賞与を計算」してから確定してください。"
        )
    if confirm_column.button("賞与を確定", key="bonus_confirm", disabled=not unconfirmed or stale):
        total = sum(row.bonus_amount or 0 for row in unconfirmed)
        st.session_state[_PENDING_KEY] = _Pending(
            eval_half=screen.eval_half,
            message=(
                f"{screen.eval_half} の賞与を確定します。対象は {len(unconfirmed)}人、"
                f"賞与額の合計は {total:,}円です。確定すると、支給月（{screen.pay_month}）の"
                "原価に反映され、取り消せません。よろしいですか？"
            ),
        )
        st.rerun()


def _render_pending(db_path: Path) -> None:
    pending: _Pending | None = st.session_state.get(_PENDING_KEY)
    if pending is None:
        return

    def _run() -> None:
        st.session_state.pop(_PENDING_KEY, None)
        try:
            with open_connection(db_path) as connection:
                count = confirm_bonuses(
                    connection, pending["eval_half"], on_recalculated=remember_recalculation
                )
        except (NothingToConfirmError, StaleBonusCalculationError) as error:
            st.session_state[_LAST_ERROR_KEY] = str(error)
        except Exception:
            st.session_state[_LAST_ERROR_KEY] = UNEXPECTED_ERROR_MESSAGE
        else:
            st.session_state[_LAST_RESULT_KEY] = f"{count}人の賞与を確定しました"

    def _cancel() -> None:
        st.session_state.pop(_PENDING_KEY, None)

    confirm_dialog(
        "賞与の確定",
        pending["message"],
        key="bonus_confirm_dialog",
        on_confirm=_run,
        on_cancel=_cancel,
    )


def _parse_amount(text: str, label: str) -> int | None:
    try:
        return int(text)
    except ValueError:
        show_errors([f"{label}は、0 以上の整数で入力してください"])
        return None


def _render_manual_form(db_path: Path, eval_half: str, pay_month: str) -> None:
    st.subheader("支給済みの賞与の手入力")
    with open_connection(db_path) as connection:
        employees = list_employees(connection, ENROLLMENT_STATUSES)
    if not employees:
        show_status("社員が登録されていません。")
        return
    names = {employee.id: employee.name for employee in employees}
    suffix = eval_half
    with st.form(f"bonus_manual_form_{suffix}"):
        employee_id = st.selectbox(
            "社員",
            list(names),
            format_func=lambda option: f"{option}：{names[option]}",
            key=f"bonus_manual_employee_{suffix}",
        )
        st.write(f"半期：{eval_half}　支給月：{pay_month}")
        base_type = st.selectbox("基準額の種類", BASE_TYPES, key=f"bonus_manual_base_type_{suffix}")
        base_amount = st.text_input("基準額（円）", key=f"bonus_manual_base_amount_{suffix}")
        rank = st.selectbox("評価ランク", EVAL_RANKS, key=f"bonus_manual_rank_{suffix}")
        rate = st.text_input("掛け率（倍）", key=f"bonus_manual_rate_{suffix}")
        bonus_amount = st.text_input("賞与額（円）", key=f"bonus_manual_amount_{suffix}")
        submitted = st.form_submit_button("登録", key=f"bonus_manual_submit_{suffix}")
    if not submitted:
        return
    base = _parse_amount(base_amount, "基準額")
    amount = _parse_amount(bonus_amount, "賞与額")
    if base is None or amount is None:
        return
    data = ManualBonusInput(employee_id, eval_half, base_type, base, rank, rate, amount)
    try:
        with open_connection(db_path) as connection:
            register_manual_bonus(connection, data, on_recalculated=remember_recalculation)
    except (NotBonusTargetError, DuplicateBonusError, InvalidManualBonusError) as error:
        show_errors([str(error)])
        return
    except (NoBaseMonthCalendarError, NoBonusRuleError) as error:
        show_errors([str(error)])
        return
    except Exception:
        show_unexpected_error()
        return
    for field in ("base_amount", "rate", "amount"):
        st.session_state.pop(f"bonus_manual_{field}_{suffix}", None)
    _finish("支給済みの賞与を登録しました")


def render() -> None:
    st.title("賞与")
    show_recalculation_notice()

    last_error = st.session_state.pop(_LAST_ERROR_KEY, None)
    if last_error is not None:
        show_errors([last_error])
    last_result = st.session_state.pop(_LAST_RESULT_KEY, None)
    if last_result is not None:
        show_success(last_result)

    db_path = get_app_paths().db_path
    year, half = select_half(
        "半期", key="bonus_half", default=previous_half(*current_fiscal_half(date.today()))
    )
    eval_half = half_code(year, half)
    _render_pending(db_path)

    screen: BonusScreen | None = None
    with open_connection(db_path) as connection:
        try:
            record_view_if_newly_displayed(connection, _SCREEN_NAME, "bonus", eval_half)
        except Exception:
            show_unexpected_error()
            return
        try:
            screen = load_bonus_screen(connection, eval_half)
        except (NoBaseMonthCalendarError, NoBonusRuleError) as error:
            show_errors([str(error)])

    if screen is not None:
        st.write(
            f"{half_label(year, half)}　支給月：{screen.pay_month}　基準月：{screen.base_month}"
        )
        _render_table(screen)
        if screen.evaluation_allowed:
            _render_evaluation_form(db_path, screen)
            _render_actions(db_path, screen)
            st.subheader("未入力一覧")
            if screen.missing:
                st.dataframe([{"氏名": name} for name in screen.missing], hide_index=True)
            else:
                show_status("評価・掛け率の未入力はありません。")
        else:
            show_status(
                "19期の半期は、評価の入力と計算はできません。支給済みの賞与の手入力だけを行います。"
            )
        _render_manual_form(db_path, eval_half, screen.pay_month)


render()

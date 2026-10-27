"""賞与ルール・掛け率画面（`SCN-064`）。

賞与ルールと、評価ランクの掛け率を登録する。画面設計書は、この2つの操作に
確認ダイアログ（`UID-053`）を求めていない（`SCN-060`〜`SCN-063`と異なる）ため、
ここでは設けない。操作ログへの記録は、アプリケーション層で行う（`SCD-020`）。
賞与ルール・掛け率の変更は、再計算のきっかけではない（`CLC-022`）。
"""

from datetime import date
from pathlib import Path

import streamlit as st

from crm_g.application.bonus_rate import (
    InvalidEvalHalfError,
    InvalidRateError,
    list_rates,
    save_rates,
)
from crm_g.application.bonus_rule import (
    BASE_TYPES,
    InvalidBaseTypeError,
    InvalidValidFromError,
    PeriodOverlapError,
    add_bonus_rule,
    list_bonus_rules,
)
from crm_g.domain.period import half_label, selectable_halves
from crm_g.infrastructure.db import open_connection
from crm_g.infrastructure.paths import get_app_paths
from crm_g.ui.components.messages import show_errors, show_success, show_unexpected_error

_TARGET_PAY_SYSTEM = "通常評価制度"


def _half_options() -> list[str]:
    """半期の候補を、半期コード（例：`2026-H1`）で返す（`UID-063`）。"""
    return [f"{year}-H{half}" for year, half in selectable_halves(date.today())]


def _half_code_label(half_code: str) -> str:
    """半期コードを、「20期 上期」の形で表す（`UID-063`）。"""
    year, half = half_code.split("-H")
    return half_label(int(year), int(half))


def _render_rule_section(db_path: Path) -> None:
    st.subheader("賞与ルール")

    with open_connection(db_path) as connection:
        rules = list_bonus_rules(connection, target_pay_system=_TARGET_PAY_SYSTEM)

    st.dataframe(
        [
            {
                "適用開始日": rule.valid_from.isoformat(),
                "適用終了日": rule.valid_to.isoformat() if rule.valid_to else "",
                "基準額の種類": rule.base_type,
                "対象の給与体系": rule.target_pay_system,
            }
            for rule in rules
        ],
        hide_index=True,
    )

    st.write(f"対象の給与体系：{_TARGET_PAY_SYSTEM}")
    with st.form("bonus_rule_form"):
        valid_from = st.date_input("適用開始日", key="bonus_rule_valid_from")
        base_type = st.selectbox("基準額の種類", BASE_TYPES, key="bonus_rule_base_type")
        submitted = st.form_submit_button("追加", key="bonus_rule_submit")

    if not submitted:
        return
    if not isinstance(valid_from, date):
        show_errors(["適用開始日を入力してください"])
        return

    with open_connection(db_path) as connection:
        try:
            add_bonus_rule(connection, valid_from, base_type, target_pay_system=_TARGET_PAY_SYSTEM)
        except (InvalidBaseTypeError, InvalidValidFromError, PeriodOverlapError) as error:
            show_errors([str(error)])
        except Exception:
            show_unexpected_error()
        else:
            show_success("追加しました")
            st.rerun()


def _render_rate_section(db_path: Path) -> None:
    st.subheader("掛け率")

    options = _half_options()
    selected_half = st.selectbox(
        "半期",
        options,
        index=len(options) - 1,
        format_func=_half_code_label,
        key="bonus_rate_half",
    )

    with open_connection(db_path) as connection:
        rates = list_rates(connection, selected_half)

    # エディタのkeyに選択中の半期を含める。固定keyのままだと、半期を変えても
    # st.session_stateに残った前回の内容が表示され続ける（PH-06-3で見つかった
    # フォームの不具合と同じ原因）。
    editor_key = f"bonus_rate_editor_{selected_half}"

    def _format_rate(rate_bp: int | None) -> str:
        return "" if rate_bp is None else f"{rate_bp / 10000:.4f}"

    edited = st.data_editor(
        [{"評価ランク": rate.eval_rank, "掛け率": _format_rate(rate.rate_bp)} for rate in rates],
        column_config={"評価ランク": st.column_config.TextColumn(disabled=True)},
        hide_index=True,
        key=editor_key,
    )

    st.caption("掛け率は0以上です。上限はありません。")
    st.caption(
        "掛け率を変えても、計算済みの賞与は変わりません（計算時の値を複写して保存するため）。"
    )

    if not st.button("保存", key=f"bonus_rate_save_{selected_half}"):
        return

    rates_input = {str(row["評価ランク"]): str(row["掛け率"]) for row in edited}

    with open_connection(db_path) as connection:
        try:
            save_rates(connection, selected_half, rates_input)
        except (InvalidEvalHalfError, InvalidRateError) as error:
            show_errors([str(error)])
        except Exception:
            show_unexpected_error()
        else:
            show_success("保存しました")
            st.rerun()


def render() -> None:
    st.title("賞与ルール・掛け率")

    db_path = get_app_paths().db_path

    _render_rule_section(db_path)
    _render_rate_section(db_path)


render()

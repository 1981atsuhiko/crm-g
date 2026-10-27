"""保存後の再計算の通知（`UID-055`）と、締め済みの月への警告（`UID-054`）。

更新ユースケースの `on_recalculated` に `remember_recalculation` を渡し、保存して
再実行したあとの描画で `show_recalculation_notice` を呼ぶ。保存のあとは `st.rerun()`
されるため、結果は `st.session_state` に組み込み型で残しておく。
"""

import streamlit as st

from crm_g.application.recalculation import RecalculationResult

_KEY = "recalculation_notice"


def remember_recalculation(result: RecalculationResult) -> None:
    """再計算の結果を、次の描画で通知するために残す。"""
    st.session_state[_KEY] = {
        "months": list(result.year_months),
        "errors": len(result.errors),
        "closed": list(result.closed_months),
    }


def show_recalculation_notice() -> None:
    """残しておいた再計算の結果があれば、通知・警告を出す。"""
    notice = st.session_state.pop(_KEY, None)
    if notice is None:
        return
    if notice["months"]:
        months = "、".join(notice["months"])
        st.info(f"{len(notice['months'])}か月分（{months}）を再計算しました。")
    if notice["errors"]:
        st.warning(
            f"計算エラーが {notice['errors']} 件あります。"
            "「月次給与計算」の画面で確認してください。"
        )
    if notice["closed"]:
        closed = "、".join(notice["closed"])
        st.warning(
            f"締め済みの月（{closed}）の計算結果は変わりません。反映するには、"
            "「月次給与計算」の画面で締めを解除して再計算してください。"
        )

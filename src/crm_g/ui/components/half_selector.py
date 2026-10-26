"""半期の選択（`UID-063`）。

候補は2025年上期から、今日が属する半期の次の半期までとし、「20期 上期」のように
表示する。既定は、今日が属する半期。選んだ値は、(年度, 半期) の組で返す。
"""

from datetime import date

import streamlit as st

from crm_g.domain.period import current_fiscal_half, half_label, selectable_halves


def select_half(label: str, *, key: str) -> tuple[int, int]:
    """半期を選ぶ `st.selectbox` を出し、選んだ (年度, 半期) を返す。"""
    today = date.today()
    options = list(selectable_halves(today))
    selected: tuple[int, int] = st.selectbox(
        label,
        options,
        index=options.index(current_fiscal_half(today)),
        format_func=lambda option: half_label(*option),
        key=key,
    )
    return selected

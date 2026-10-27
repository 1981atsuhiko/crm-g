"""年月の選択（`UID-063`）。

「年」と「月」の2つの `st.selectbox` で選ぶ。年の候補は、2025 年（19期の開始年）から、
今日が属する年の翌年まで。既定は今日の月。選んだ年月は `YYYY-MM` の形で返す。
"""

from datetime import date

import streamlit as st

_FIRST_YEAR = 2025


def select_year_month(*, key: str) -> str:
    """年と月を選ぶ欄を出し、選んだ年月を `YYYY-MM` で返す。"""
    today = date.today()
    years = list(range(_FIRST_YEAR, today.year + 2))
    year_column, month_column = st.columns(2)
    year = year_column.selectbox(
        "年",
        years,
        index=years.index(today.year),
        format_func=lambda y: f"{y}年",
        key=f"{key}_year",
    )
    month = month_column.selectbox(
        "月",
        list(range(1, 13)),
        index=today.month - 1,
        format_func=lambda m: f"{m}月",
        key=f"{key}_month",
    )
    return f"{year:04d}-{month:02d}"

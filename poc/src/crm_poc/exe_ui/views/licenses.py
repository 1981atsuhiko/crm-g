"""ライセンス一覧の確認ページ。"""

from __future__ import annotations

from dataclasses import asdict

import streamlit as st

from crm_poc import exe_probe

st.title("licenses")
st.dataframe([asdict(entry) for entry in exe_probe.list_licenses()])

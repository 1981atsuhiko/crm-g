"""同梱 SQL・jpholiday の確認ページ。"""

from __future__ import annotations

from dataclasses import asdict

import streamlit as st

from crm_poc import exe_probe

st.title("probe")
st.write(asdict(exe_probe.run_all_checks()))

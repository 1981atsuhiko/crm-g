"""認証前に内容が出てはならないページ（POC-03）。"""

from __future__ import annotations

import streamlit as st

from crm_poc import settings

st.title("secret")
st.write(settings.PROTECTED_MARKER)

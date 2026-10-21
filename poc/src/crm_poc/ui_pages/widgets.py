"""AppTest で扱えるかを確認するウィジェット（POC-05）。"""

from __future__ import annotations

import streamlit as st


@st.dialog("confirm")
def confirm_dialog() -> None:
    st.write("dialog body")
    if st.button("close", key="dialog_close"):
        st.rerun()


st.title("widgets")
if st.button("open dialog", key="open_dialog"):
    confirm_dialog()
st.file_uploader("upload", key="uploader")
st.data_editor({"name": ["a", "b"]}, key="editor")
st.date_input("date", value=None, key="date_without_value")

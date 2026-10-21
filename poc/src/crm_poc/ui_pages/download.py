"""download_button に渡した関数が失敗した場合の挙動（POC-04）。"""

from __future__ import annotations

import streamlit as st

from crm_poc import settings


def failing_download() -> bytes:
    raise RuntimeError(settings.DOWNLOAD_FAILURE_MESSAGE)


st.title("download")
st.download_button("download ok", data=lambda: settings.DOWNLOAD_OK_BYTES, file_name="ok.bin")
st.download_button("download failure", data=failing_download, file_name="failure.bin")

"""exe 内で起動する Streamlit の入口。st.navigation で 2 ページを登録する。"""

from __future__ import annotations

from pathlib import Path

import streamlit as st

from crm_poc import settings

VIEWS_DIRECTORY = Path(__file__).parent / settings.EXE_VIEWS_DIRECTORY_NAME

st.navigation(
    [
        st.Page(
            VIEWS_DIRECTORY / settings.EXE_PROBE_VIEW_FILE,
            title=settings.EXE_PROBE_PATH,
            url_path=settings.EXE_PROBE_PATH,
            default=True,
        ),
        st.Page(
            VIEWS_DIRECTORY / settings.EXE_LICENSES_VIEW_FILE,
            title=settings.EXE_LICENSES_PATH,
            url_path=settings.EXE_LICENSES_PATH,
        ),
    ]
).run()

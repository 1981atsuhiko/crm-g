"""閲覧ログの記録ヘルパー（`view_log.py`）の試験用ページ。

試験対象のDBパスは、環境変数 `CRM_G_TEST_DB_PATH` で渡す。
"""

import os
from pathlib import Path

import streamlit as st

from crm_g.infrastructure.db import open_connection
from crm_g.ui.components.view_log import record_view_if_newly_displayed

_db_path = Path(os.environ["CRM_G_TEST_DB_PATH"])

with open_connection(_db_path) as connection:
    record_view_if_newly_displayed(connection, "SCN_TEST", "salary_history", "1")

st.write("recorded")

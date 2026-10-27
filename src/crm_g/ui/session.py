"""`st.session_state` のキー（UID-031）。

`app.py` とビューの両方から参照するため、`app.py` から分離する。`app.py` は
モジュールの末尾で画面を実行するため、`app.py` を import すると、その副作用が
二重に走ってしまう。
"""

AUTHENTICATED_KEY = "crm_g_authenticated"

# 出力画面（`SCN-080`）の作成済みの CSV。画面を離れたときに `app.py` が破棄する。
EXPORT_STATE_KEY = "crm_g_export_prepared"

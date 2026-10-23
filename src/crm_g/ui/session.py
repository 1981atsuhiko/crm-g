"""`st.session_state` のキー（UID-031）。

`app.py` とビューの両方から参照するため、`app.py` から分離する。`app.py` は
モジュールの末尾で画面を実行するため、`app.py` を import すると、その副作用が
二重に走ってしまう。
"""

AUTHENTICATED_KEY = "crm_g_authenticated"

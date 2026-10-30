"""`st.session_state` のキー（UID-031）。

`app.py` とビューの両方から参照するため、`app.py` から分離する。`app.py` は
モジュールの末尾で画面を実行するため、`app.py` を import すると、その副作用が
二重に走ってしまう。
"""

AUTHENTICATED_KEY = "crm_g_authenticated"

# 出力画面（`SCN-080`）の作成済みの CSV。画面を離れたときに `app.py` が破棄する。
EXPORT_STATE_KEY = "crm_g_export_prepared"

# データ取込画面（`SCN-070`）の、確認した取込用 CSV（バイト列）と確認の結果、取込の結果。
# 取込を実行したとき・取込の種類やファイルを変えたとき・画面を離れたときに破棄する
# （画面を離れたときは `app.py` が破棄する。画面設計書 3.2 `UID-031`、移行設計書 6.1）。
IMPORT_STATE_KEY = "crm_g_import_checked"
IMPORT_RESULT_KEY = "crm_g_import_result"

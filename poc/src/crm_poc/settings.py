"""PoC で使う定数。値は、設計書（権限・セキュリティ設計書 SCD-006）に合わせる。"""

from __future__ import annotations

SCRYPT_N = 2**17
SCRYPT_R = 8
SCRYPT_P = 1
SCRYPT_DKLEN = 32
SCRYPT_SALT_BYTES = 16
SCRYPT_MAXMEM_BYTES = 256 * 1024 * 1024

LOCK_FILE_NAME = "crm-g.lock"
LOCK_READY_MESSAGE = "locked"
LOCK_FAILED_EXIT_CODE = 3

BYTES_PER_MIB = 1024 * 1024

# 動作確認専用の架空の値。実際のパスコードではない。
DEMO_PASSCODE = "poc-demo-passcode"
DEMO_SALT = bytes(range(SCRYPT_SALT_BYTES))
PROTECTED_MARKER = "SECRET-MARKER-12345"

SESSION_AUTHENTICATED_KEY = "authenticated"

LOGIN_PATH = "login"
HOME_PATH = "home"
PROTECTED_PATH = "secret"
DOWNLOAD_PATH = "download"
WIDGETS_PATH = "widgets"

DOWNLOAD_OK_BYTES = b"poc-download-ok"
DOWNLOAD_FAILURE_MESSAGE = "poc download failure"

EXE_VIEWS_DIRECTORY_NAME = "views"
EXE_PROBE_VIEW_FILE = "probe.py"
EXE_LICENSES_VIEW_FILE = "licenses.py"
EXE_PROBE_PATH = "probe"
EXE_LICENSES_PATH = "licenses"

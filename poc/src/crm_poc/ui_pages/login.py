"""認証前に表示する唯一のページ。"""

from __future__ import annotations

import streamlit as st

from crm_poc import settings
from crm_poc.scrypt_check import hash_passcode, verify_passcode


@st.cache_resource
def expected_digest() -> bytes:
    return hash_passcode(settings.DEMO_PASSCODE, settings.DEMO_SALT)


def render() -> None:
    st.title("login")
    passcode = st.text_input("passcode", type="password", key="passcode_input")
    if not st.button("sign in", key="sign_in"):
        return
    if verify_passcode(passcode, settings.DEMO_SALT, expected_digest()):
        st.session_state[settings.SESSION_AUTHENTICATED_KEY] = True
        st.rerun()
        return
    st.error("authentication failed")


render()

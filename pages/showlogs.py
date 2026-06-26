# Show the contents of app log files on server in real-time

import streamlit as st


def read_last_lines(filepath: str, n: int = 200) -> str:
    """Return the last *n* lines of *filepath* as a single string."""
    try:
        with open(filepath, "r") as fh:
            lines = fh.readlines()
        return "".join(lines[-n:]) if lines else "(log is empty)"
    except FileNotFoundError:
        return f"(file not found: {filepath})"
    except Exception as exc:
        return f"(error reading file: {exc})"


st.set_page_config(
    page_title="RAPAS Pipeline Logs",
    page_icon=":sparkles:",
    layout="wide",
)

if not st.session_state.get("logged_in", False):
    st.warning("You must log in to access this page.")
    try:
        st.switch_page("pages/login.py")
    except Exception:
        st.warning(
            "Automatic redirect is not available in this launch mode. "
            "Open the login page directly or start the app with frontend.py."
        )
    st.stop()

st.title("📋 Pipeline Logs")

col_back, col_info = st.columns([1, 5])
with col_back:
    if st.button("← Back to App"):
        st.switch_page("pages/app.py")
with col_info:
    st.caption("Showing last 200 lines · auto-refreshes every 5 s · read-only")

tab_backend, tab_frontend = st.tabs(["🖥️ Backend Log", "🌐 Frontend Log"])


@st.fragment(run_every=5)
def _show_log(filepath: str) -> None:
    content = read_last_lines(filepath)
    st.text_area(
        label=filepath,
        value=content,
        height=600,
        disabled=True,
        label_visibility="collapsed",
    )


with tab_backend:
    _show_log("backend.log")

with tab_frontend:
    _show_log("frontend.log")

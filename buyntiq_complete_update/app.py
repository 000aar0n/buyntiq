"""Run from this folder with: python -m streamlit run app.py"""

from pathlib import Path

import streamlit as st

from buyntiq.state import initialize, change_mode
from buyntiq.ui import styles


# Use the existing Buyntiq favicon directly from this app folder.
# Reading the SVG into a string avoids working-directory/path issues on Streamlit Cloud.
FAVICON_PATH = Path(__file__).resolve().parent / "assets" / "favicon.svg"

try:
    PAGE_ICON = FAVICON_PATH.read_text(encoding="utf-8")
except OSError:
    # Black fallback so the tab never goes back to the old square icon.
    PAGE_ICON = """
    <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64">
      <rect width="64" height="64" rx="10" fill="#000000"/>
      <text x="32" y="44"
            text-anchor="middle"
            font-family="Arial, Helvetica, sans-serif"
            font-size="42"
            font-weight="800"
            fill="#ffffff">B</text>
    </svg>
    """


st.set_page_config(
    page_title="Buyntiq",
    page_icon=PAGE_ICON,
    layout="wide",
    initial_sidebar_state="collapsed",
)

initialize()
styles()


# =========================================================
# PAGE ROUTES
# =========================================================

pages = [
    st.Page(
        "views/home.py",
        title="Home",
        default=True,
    ),
    st.Page(
        "views/research.py",
        title="Stock research",
        url_path="research",
    ),
    st.Page(
        "views/builder.py",
        title="Portfolio builder",
        url_path="builder",
    ),
    st.Page(
        "views/review.py",
        title="Portfolio review",
        url_path="review",
    ),
]

current = st.navigation(
    pages,
    position="hidden",
)


# =========================================================
# BUYNTIQ HEADER
# =========================================================
# Same black / monochrome UI as before.
# buyntiq.ui.styles() remains authoritative for the design.
# =========================================================

brand, settings = st.columns(
    [4, 1],
    vertical_alignment="center",
)

with brand:
    st.markdown(
        """
        <div class="brand">
            <span class="brand-mark"></span>
            buyntiq
            <span>RESEARCH WORKSPACE</span>
        </div>
        """,
        unsafe_allow_html=True,
    )

with settings:
    # This widget lives in the entrypoint so its state survives page switches.
    st.toggle(
        "Demo data",
        key="demo_mode",
        on_change=change_mode,
        help="Use generated prices and company metrics. No real market data.",
    )


# =========================================================
# CUSTOM NAVIGATION
# =========================================================

with st.container(key="navigation"):
    nav = st.columns(4)

    for col, page in zip(nav, pages):
        with col:
            st.page_link(
                page,
                label=page.title,
                width="stretch",
            )


# =========================================================
# DEMO MODE NOTICE
# =========================================================

if st.session_state.demo_mode:
    st.markdown(
        """
        <div class="demo-notice">
            <strong>DEMO MODE</strong>
            <span>
                Prices, financials, forecasts, and results use synthetic data.
            </span>
        </div>
        """,
        unsafe_allow_html=True,
    )


# =========================================================
# CURRENT PAGE
# =========================================================

current.run()


# =========================================================
# FOOTER
# =========================================================

st.markdown(
    """
    <div class="app-footer">
        <span>buyntiq / independent research</span>
        <span>Historical data. Measured uncertainty. No promised returns.</span>
    </div>
    """,
    unsafe_allow_html=True,
)

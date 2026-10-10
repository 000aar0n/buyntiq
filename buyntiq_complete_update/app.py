"""Live stock research and cash-aware portfolio projections.

Run from this folder with: python -m streamlit run app.py
"""

import streamlit as st

from buyntiq.state import initialize, change_mode
from buyntiq.ui import ASSETS, styles
from buyntiq import accounts


# =========================================================
# PAGE CONFIG
# =========================================================
# Uses the existing Buyntiq B favicon in:
# buyntiq_complete_update/assets/favicon.svg
# =========================================================

st.set_page_config(
    page_title="Buyntiq",
    page_icon=str(ASSETS / "favicon.svg"),
    layout="wide",
    initial_sidebar_state="collapsed",
)


# =========================================================
# APP INITIALIZATION
# =========================================================

initialize()

# Restore/sync the signed-in user's account state before any page widgets render.
accounts.sync_session()

# Existing Buyntiq black / monochrome UI.
styles()


# =========================================================
# ACCOUNT BUTTON SAFETY STYLE
# =========================================================
# The account module already styles this in the account build,
# but this guarantees the top-level Account trigger stays black
# with readable white text even if other UI CSS changes.
# =========================================================

st.markdown(
    """
    <style>
    .st-key-account_control [data-testid="stPopover"] button {
        background:#000000 !important;
        color:#ffffff !important;
        border:1px solid #505050 !important;
        border-radius:0 !important;
        box-shadow:none !important;
        font-weight:800 !important;
    }

    .st-key-account_control [data-testid="stPopover"] button p,
    .st-key-account_control [data-testid="stPopover"] button svg {
        color:#ffffff !important;
        fill:#ffffff !important;
    }

    .st-key-account_control [data-testid="stPopover"] button:hover {
        background:#161616 !important;
        color:#ffffff !important;
        border-color:#9a9a9a !important;
    }
    </style>
    """,
    unsafe_allow_html=True,
)


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
# TOP HEADER
# =========================================================
# Same layout as the account-enabled Buyntiq build:
# Buyntiq brand | Account
# =========================================================

brand, account = st.columns(
    [5, 1.25],
    vertical_alignment="center",
)

with brand:
    st.markdown(
        '<a class="brand-link" href="/" target="_self" aria-label="Buyntiq home">'
        '<div class="brand">'
        '<span class="brand-mark"></span>'
        'buyntiq'
        '<span>RESEARCH WORKSPACE</span>'
        '</div>'
        '</a>',
        unsafe_allow_html=True,
    )

with account:
    # Restored Account button/menu.
    # The actual sign-in/account behavior remains centralized in
    # buyntiq/accounts.py so app.py does not duplicate auth logic.
    accounts.render_account_menu()

# =========================================================
# CUSTOM NAVIGATION
# =========================================================

with st.container(key="navigation"):
    nav = st.columns(len(pages))

    for col, page in zip(nav, pages):
        with col:
            st.page_link(
                page,
                label=page.title,
                width="stretch",
            )


# =========================================================
# CURRENT PAGE
# =========================================================

current.run()


# =========================================================
# FOOTER
# =========================================================

st.markdown(
    '<div class="app-footer">'
    '<span>buyntiq / independent research</span>'
    '<span>Historical data. Measured uncertainty. No promised returns.</span>'
    '</div>',
    unsafe_allow_html=True,
)

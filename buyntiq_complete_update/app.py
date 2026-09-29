"""Run from this folder with: python -m streamlit run app.py"""
import streamlit as st
from buyntiq.state import initialize, change_mode
from buyntiq.ui import styles
from buyntiq import accounts

st.set_page_config(page_title="Buyntiq", page_icon="◼", layout="wide", initial_sidebar_state="collapsed")
initialize()
accounts.sync_session()
styles()

pages = [st.Page("views/home.py", title="Home", default=True),
         st.Page("views/research.py", title="Stock research", url_path="research"),
         st.Page("views/builder.py", title="Portfolio builder", url_path="builder"),
         st.Page("views/review.py", title="Portfolio review", url_path="review")]
current = st.navigation(pages, position="hidden")
brand, account, settings = st.columns([4, 1.25, 1], vertical_alignment="center")
with brand:
    st.markdown(
        '<a class="brand-link" href="/" target="_self" aria-label="Buyntiq home">'
        '<div class="brand"><span class="brand-mark"></span>buyntiq<span>RESEARCH WORKSPACE</span></div>'
        '</a>',
        unsafe_allow_html=True,
    )
with account:
    accounts.render_account_menu()
with settings:
    # This widget lives in the entrypoint so its state survives page switches.
    st.toggle("Demo data", key="demo_mode", on_change=change_mode, help="Use generated prices and company metrics. No real market data.")
with st.container(key="navigation"):
    nav = st.columns(4)
    for col, page in zip(nav, pages):
        with col:
            st.page_link(page, label=page.title, width="stretch")
if st.session_state.demo_mode:
    st.markdown('<div class="demo-notice"><strong>DEMO MODE</strong><span>Prices, financials, forecasts, and results use synthetic data.</span></div>', unsafe_allow_html=True)
current.run()
st.markdown('<div class="app-footer"><span>buyntiq / independent research</span><span>Historical data. Measured uncertainty. No promised returns.</span></div>', unsafe_allow_html=True)

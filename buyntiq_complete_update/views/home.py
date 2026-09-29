import streamlit as st
from buyntiq.ui import section
from buyntiq import accounts

st.markdown('''<div class="home-hero"><div class="eyebrow">A little more signal. A lot more clarity.</div>
<h1>Understand the market.<br><span class="muted-title">Build your perspective.</span></h1>
<p>One workspace for stock research, measured forecasts, and a closer look at your portfolio.</p>
<div class="hero-meta"><span>01 / RESEARCH</span><span>02 / BUILD</span><span>03 / REVIEW</span></div></div>''', unsafe_allow_html=True)

cards = [
    ("01", "↗", "Stock research", "Look beneath the ticker. Explore price trends, company fundamentals, and forecasts with visible uncertainty.", "views/research.py", "Explore a stock"),
    ("02", "◫", "Portfolio builder", "Move from a list to an allocation. Rank fully analyzed candidates and choose how much diversification matters.", "views/builder.py", "Build a portfolio"),
    ("03", "≋", "Portfolio review", "Bring what you own. See position weights, research quality, concentration, and the risk behind the numbers.", "views/review.py", "Review your holdings"),
]
for col, (number, icon, title, description, page, cta) in zip(st.columns(3, gap="medium"), cards):
    with col, st.container(border=True):
        st.markdown(f'<div class="feature-number">WORKSPACE / {number}</div><div class="feature-icon">{icon}</div><div class="feature-title">{title}</div><div class="feature-description">{description}</div>', unsafe_allow_html=True)
        st.page_link(page, label=cta + "  →", width="stretch")

section("01", "Your watchlist", "Open a research page")
if st.session_state.watchlist:
    columns = st.columns(min(6, len(st.session_state.watchlist)))
    for i, ticker in enumerate(st.session_state.watchlist):
        if columns[i % len(columns)].button(ticker, key="home_"+ticker, width="stretch"):
            st.session_state.research_symbol = ticker
            st.session_state._research_symbol = ticker
            st.switch_page("views/research.py")
else:
    st.caption("Save symbols below to keep your research close by.")
with st.expander("Edit watchlist"):
    text = st.text_input("Tickers, separated by commas", value=", ".join(st.session_state.watchlist), key="home_watchlist_input")
    st.button("Save watchlist", on_click=lambda: accounts.save_watchlist(st.session_state.home_watchlist_input))
    st.caption(accounts.storage_caption())

if st.session_state.recent_searches:
    section("RECENT", "Recent searches", "Newest first")
    columns = st.columns(min(6, len(st.session_state.recent_searches)))
    for i, ticker in enumerate(st.session_state.recent_searches):
        if columns[i % len(columns)].button(ticker, key="home_recent_"+ticker, width="stretch"):
            st.session_state.research_symbol = ticker
            st.session_state._research_symbol = ticker
            st.switch_page("views/research.py")
    st.button("Clear recent searches", key="home_clear_recent", on_click=accounts.clear_recent)

section("02", "Continue your work", "Current session")
activity = [
    ("research_result", "views/research.py", "research"),
    ("builder_result", "views/builder.py", "built portfolio"),
    ("review_result", "views/review.py", "portfolio review"),
]
found = False
for key, path, label in activity:
    result = st.session_state.get(key)
    if result:
        found = True
        with st.container(border=True):
            title = result.get("symbol", "Latest " + label)
            st.page_link(path, label=f"{title}  →")
            st.caption("Saved " + result["created_at"][:16].replace("T", " ") + " UTC" + (" · synthetic demo" if result.get("demo") else ""))
if not found:
    st.caption("Your latest analyses will appear here. Start with a ticker, a new portfolio, or your existing holdings.")

with st.expander("How Buyntiq reads the evidence"):
    st.write("Research scores combine technical and company signals. The ML ensemble earns a share of that score only after outperforming a simple return baseline in development and a separate historical holdout.")
    st.write("The 80% forecast interval is an empirical estimate. Its measured holdout coverage is shown beside it. Overlapping horizons reduce the number of independent observations.")
    st.caption("Pages open without fetching market data. Company requests and price histories are cached, and longer forecasts load only when requested.")

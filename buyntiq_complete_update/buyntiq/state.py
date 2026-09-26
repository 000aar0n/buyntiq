"""Session-only user inputs. Pages cannot delete each other's durable values."""
import os
import streamlit as st


def initialize():
    defaults = {"demo_mode": os.getenv("BUYNTIQ_DEMO", "0") == "1",
                "watchlist": ["AAPL", "MSFT", "NVDA", "AMD", "META"],
                "research_symbol": "AAPL", "research_result": None,
                "builder_result": None, "review_result": None,
                "review_rows": [{"Ticker": "", "Shares": 0.0}, {"Ticker": "", "Shares": 0.0}],
                "builder_settings": {"sector": "All sectors", "universe": "Starter list", "risk": "Balanced", "method": "Highest scores", "count": 5, "budget": 10000.0, "finalists": 12, "custom": "AAPL, MSFT, NVDA, AMD, META, GOOGL", "limit": "Entire US universe", "horizon_name": "3 months"}}
    for key, value in defaults.items():
        if key not in st.session_state:
            st.session_state[key] = value


def change_mode():
    for key in ("research_result", "builder_result", "review_result"):
        st.session_state[key] = None
    st.session_state.pop("extra_forecasts", None)
    st.session_state.pop("news_result", None)


def persistent_widget(widget, name, **kwargs):
    """Copy ephemeral page widgets to a session value on every change."""
    key = "_" + name
    if key not in st.session_state:
        st.session_state[key] = st.session_state[name]
    def save():
        st.session_state[name] = st.session_state[key]
    return widget(key=key, on_change=save, **kwargs)

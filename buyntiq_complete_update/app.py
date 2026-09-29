import streamlit as st
import pandas as pd
import numpy as np

from pathlib import Path
import json
import os
import re
import tempfile
import time
import shutil
import hashlib
import uuid
import html
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed


# Keep the app palette authoritative even when an older Streamlit theme is deployed.
# Streamlit serializes these server theme options for native widgets and tables.
from streamlit import config as _theme_config

_cool_theme = {
    "primaryColor": "#365B76", "backgroundColor": "#F5F7F9",
    "secondaryBackgroundColor": "#E6ECF1", "textColor": "#18232F",
    "borderColor": "#93A4B3", "dataframeBorderColor": "#93A4B3",
    "dataframeHeaderBackgroundColor": "#DBE5ED", "codeBackgroundColor": "#E6ECF1",
    "linkColor": "#365B76", "codeTextColor": "#354757",
    "chartCategoricalColors": ["#365B76", "#66849A", "#92AABB", "#233C50"],
    "chartSequentialColors": ["#EDF2F5", "#CBD9E4", "#92AABB", "#66849A", "#365B76", "#233C50"],
    "chartDivergingColors": ["#233C50", "#66849A", "#EDF2F5", "#92AABB", "#354757"],
}
for _hue in ("red", "orange", "yellow", "blue", "green", "violet", "gray"):
    _cool_theme.update({_hue + "Color": "#536675", _hue + "BackgroundColor": "#E6ECF1",
                        _hue + "TextColor": "#354757"})
_theme_options = _theme_config.get_config_options()
_theme_changed = False
for _scope in ("theme", "theme.sidebar", "theme.light", "theme.light.sidebar", "theme.dark", "theme.dark.sidebar"):
    for _name, _value in _cool_theme.items():
        if _scope + "." + _name in _theme_options:
            _theme_changed |= _theme_config.get_option(_scope + "." + _name) != _value
            _theme_config.set_option(_scope + "." + _name, _value)
_theme_changed |= _theme_config.get_option("theme.base") != "light"
_theme_config.set_option("theme.base", "light")
if _theme_changed:
    # Theme metadata is sent before the script starts; resend it after an override.
    st.rerun()

st.set_page_config(
    page_title="Buyntiq",
    page_icon="buyntiq_complete_update/assets/favicon.svg",
    layout="wide",
)



# =========================================================
# LOCAL / DISK CACHE
# =========================================================
#
# On a normal local computer this survives app restarts.
# On Streamlit Community Cloud the filesystem is temporary, so it normally
# survives reruns while the app instance is alive but can disappear after a
# reboot/redeploy. st.cache_data still gives an additional in-memory layer.
# =========================================================

PRICE_CACHE_TTL = 6 * 60 * 60
FUNDAMENTAL_CACHE_TTL = 12 * 60 * 60
EARNINGS_CACHE_TTL = 6 * 60 * 60
NEWS_CACHE_TTL = 60 * 60
FAST_SCREEN_CACHE_TTL = 30 * 60


def _make_cache_root():
    candidates = [
        Path.cwd() / ".stock_signal_cache_v10",
        Path(tempfile.gettempdir()) / "stock_signal_cache_v10",
    ]

    for candidate in candidates:
        try:
            candidate.mkdir(parents=True, exist_ok=True)
            test_file = candidate / ".write_test"
            test_file.write_text("ok", encoding="utf-8")
            test_file.unlink(missing_ok=True)
            return candidate
        except Exception:
            continue

    return Path(tempfile.gettempdir())


LOCAL_CACHE_ROOT = _make_cache_root()

for _folder in ["prices", "json", "screens"]:
    try:
        (LOCAL_CACHE_ROOT / _folder).mkdir(parents=True, exist_ok=True)
    except Exception:
        pass


def _safe_cache_name(value):
    return re.sub(
        r"[^A-Za-z0-9_.-]+",
        "_",
        str(value),
    )[:180]


def _is_fresh(path, ttl_seconds):
    try:
        if not path.exists():
            return False

        age = time.time() - path.stat().st_mtime
        return age <= ttl_seconds
    except Exception:
        return False


def _price_cache_path(ticker):
    return (
        LOCAL_CACHE_ROOT
        / "prices"
        / ("{}.pkl".format(_safe_cache_name(ticker)))
    )


def _read_price_cache(ticker, ttl_seconds=PRICE_CACHE_TTL):
    path = _price_cache_path(ticker)

    if not _is_fresh(path, ttl_seconds):
        return None

    try:
        data = pd.read_pickle(path)

        if (
            isinstance(data, pd.DataFrame)
            and not data.empty
            and "Close" in data.columns
        ):
            return data

    except Exception:
        pass

    return None


def _write_price_cache(ticker, data):
    if (
        data is None
        or not isinstance(data, pd.DataFrame)
        or data.empty
        or "Close" not in data.columns
    ):
        return

    path = _price_cache_path(ticker)
    temp_path = path.with_suffix(".tmp")

    try:
        data.to_pickle(temp_path)
        os.replace(temp_path, path)
    except Exception:
        try:
            temp_path.unlink(missing_ok=True)
        except Exception:
            pass


def _json_cache_path(kind, ticker):
    return (
        LOCAL_CACHE_ROOT
        / "json"
        / (
            "{}__{}.json".format(
                _safe_cache_name(kind),
                _safe_cache_name(ticker),
            )
        )
    )


def _read_json_cache(kind, ticker, ttl_seconds):
    path = _json_cache_path(kind, ticker)

    if not _is_fresh(path, ttl_seconds):
        return None

    try:
        return json.loads(
            path.read_text(encoding="utf-8")
        )
    except Exception:
        return None


def _write_json_cache(kind, ticker, value):
    path = _json_cache_path(kind, ticker)
    # Concurrent forecasts can share an identical data fingerprint.
    # Give each writer its own temporary file before atomic replacement.
    temp_path = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")

    try:
        temp_path.write_text(
            json.dumps(value),
            encoding="utf-8",
        )
        os.replace(temp_path, path)
    except Exception:
        try:
            temp_path.unlink(missing_ok=True)
        except Exception:
            pass


def _screen_cache_path(sector_focus):
    return (
        LOCAL_CACHE_ROOT
        / "screens"
        / (
            "{}.pkl".format(
                _safe_cache_name(sector_focus)
            )
        )
    )


def _read_screen_cache(sector_focus):
    path = _screen_cache_path(sector_focus)

    if not _is_fresh(
        path,
        FAST_SCREEN_CACHE_TTL,
    ):
        return None

    try:
        data = pd.read_pickle(path)

        if isinstance(data, pd.DataFrame):
            return data

    except Exception:
        pass

    return None


def _write_screen_cache(sector_focus, data):
    if (
        data is None
        or not isinstance(data, pd.DataFrame)
        or data.empty
    ):
        return

    path = _screen_cache_path(sector_focus)
    temp_path = path.with_suffix(".tmp")

    try:
        data.to_pickle(temp_path)
        os.replace(temp_path, path)
    except Exception:
        try:
            temp_path.unlink(missing_ok=True)
        except Exception:
            pass


def clear_local_disk_cache():
    """
    Clear the on-disk cache as well as Streamlit's in-memory cache.
    Useful after code/schema changes or if Yahoo returned malformed data.
    """
    try:
        shutil.rmtree(
            LOCAL_CACHE_ROOT,
            ignore_errors=True,
        )
    except Exception:
        pass

    try:
        for folder in [
            "prices",
            "json",
            "screens",
        ]:
            (
                LOCAL_CACHE_ROOT
                / folder
            ).mkdir(
                parents=True,
                exist_ok=True,
            )
    except Exception:
        pass


@st.cache_resource(show_spinner=False)
def get_yfinance():
    """
    Import yfinance only after the user actually requests market data.
    This keeps the initial Streamlit page render lighter.
    """
    import yfinance as yf
    return yf


# =========================================================
# VISUAL DESIGN ONLY — backend/model logic below is unchanged
# =========================================================

st.markdown("""
<style>
:root { color-scheme: light; --ink:#18232f; --secondary:#354757; --muted:#536675; --border:#18232f; --subtle:#e6ecf1; --surface:#f5f7f9; }
.stApp { background:linear-gradient(145deg,#f8fafb 0%,#e8eef3 55%,#dce5ec 100%) fixed; color:var(--ink); }
/* Remove Streamlit's entire native top chrome. */
[data-testid="stHeader"],
[data-testid="stToolbar"],
[data-testid="stDecoration"],
[data-testid="stStatusWidget"],
#MainMenu,
header[data-testid="stHeader"] { display:none!important; visibility:hidden!important; height:0!important; min-height:0!important; }
[data-testid="stSidebar"] { background:linear-gradient(180deg,#edf2f6 0%,#dbe5ed 100%); border-right:2px solid var(--ink); }
[data-testid="stSidebar"] .block-container { padding-top:2.2rem; }
.block-container { max-width:1080px; padding-top:1.15rem; padding-bottom:5rem; }
html,body,button,input,textarea { font-family:Inter,-apple-system,BlinkMacSystemFont,"Segoe UI",Arial,sans-serif; }
h1,h2,h3,h4 { color:var(--ink)!important; letter-spacing:-.035em; font-weight:650!important; }
h1 { font-size:2.35rem!important; } h2 { font-size:1.55rem!important; } h3 { font-size:1.18rem!important; }
p, label, [data-testid="stMarkdownContainer"] { color:var(--ink); }
[data-testid="stCaptionContainer"] p { color:var(--muted); font-size:.82rem; line-height:1.55; }
.app-hero { background:linear-gradient(125deg,#dce7f0 0%,#edf2f5 52%,#c6d5e2 100%); padding:2rem 2.1rem 1.8rem; margin:0 9px 2.8rem 0; border:3px solid var(--ink); box-shadow:8px 8px 0 var(--ink); }
.app-eyebrow { color:var(--muted); font-size:.68rem; font-weight:750; letter-spacing:.14em; text-transform:uppercase; }
.app-hero h1 { margin:.6rem 0 .55rem; font-size:clamp(2.6rem,5.5vw,4.15rem)!important; line-height:1.04; text-transform:uppercase; font-weight:850!important; letter-spacing:-.06em; }
.app-hero p { max-width:620px; color:var(--secondary); line-height:1.65; margin:0; }
.app-nav { display:flex; flex-wrap:wrap; gap:1.25rem; margin-top:1.35rem; }
.app-nav a { font-size:.78rem; font-weight:700; color:var(--secondary); text-decoration:none; padding-bottom:4px; border-bottom:2px solid transparent; transition:color .17s ease,border-color .17s ease; }
.app-nav a:hover,.app-nav a:focus { color:var(--ink); border-color:var(--ink); }
.sidebar-brand { color:var(--ink); font-size:1.26rem; font-weight:900; text-transform:uppercase; letter-spacing:-.06em; line-height:1.08; margin-bottom:.55rem; }
.sidebar-description { color:var(--muted); font-size:.79rem; line-height:1.5; margin-bottom:1.4rem; }
[data-testid="stMetric"] { background:linear-gradient(135deg,#f9fbfc,#e3eaf0); border:2px solid var(--ink); border-radius:0; box-shadow:4px 4px 0 var(--ink); padding:1rem 1.1rem; min-height:108px; margin:0 4px 5px 0; }
[data-testid="stMetricValue"] { color:var(--ink); font-size:1.7rem; letter-spacing:-.04em; }
[data-testid="stMetricLabel"] { color:var(--muted); font-size:.8rem; }
.stButton button,.stDownloadButton button,[data-testid="stFormSubmitButton"] button { background:linear-gradient(135deg,#edf2f5,#cbd9e4); color:var(--ink)!important; border:2px solid var(--ink); border-radius:0; font-size:.84rem; font-weight:800; letter-spacing:.005em; min-height:43px; box-shadow:3px 3px 0 var(--ink); transition:background .14s ease,transform .14s ease,box-shadow .14s ease; }
.stButton button:hover,.stDownloadButton button:hover,[data-testid="stFormSubmitButton"] button:hover { background:linear-gradient(135deg,#d9e5ee,#b7cad9); border-color:var(--ink); color:var(--ink)!important; transform:translate(-2px,-2px); box-shadow:5px 5px 0 var(--ink);  }
.stButton button:focus-visible,[data-testid="stFormSubmitButton"] button:focus-visible { outline:3px solid var(--ink); outline-offset:4px; }
.stButton button[kind="primary"], [data-testid="stFormSubmitButton"] button[kind="primary"] { background:var(--ink); border-color:var(--ink); color:#fff!important; }
.stButton button[kind="primary"]:hover,[data-testid="stFormSubmitButton"] button[kind="primary"]:hover { background:#344c60; border-color:var(--ink); color:#fff!important; }
.stButton button[kind="primary"] p,[data-testid="stFormSubmitButton"] button[kind="primary"] p { color:#fff!important; font-size:1rem!important; font-weight:800!important; letter-spacing:.03em; }
[data-testid="stSidebar"] .stButton button { justify-content:flex-start; background:transparent; border:2px solid transparent; border-radius:0; font-weight:800; box-shadow:none; }
[data-testid="stSidebar"] .stButton button:hover { background:#cedde8; border-color:var(--ink); transform:none; box-shadow:none; }
[data-baseweb="input"], [data-baseweb="select"]>div,[data-baseweb="textarea"] { background:#fafcfd!important; border:2px solid var(--ink)!important; border-radius:0!important; color:var(--ink)!important; }
[data-testid="stTextInput"] [data-baseweb="base-input"],
[data-testid="stTextInput"] [data-baseweb="input"]>div,
[data-testid="stTextInput"] input,
[data-testid="stNumberInput"] input,
[data-baseweb="select"] input { background:#fafcfd!important; background-image:none!important; color:#18232f!important; -webkit-text-fill-color:#18232f!important; caret-color:#365b76!important; border-radius:0!important; }
[data-testid="stTextInput"] input::placeholder { color:#536675!important; -webkit-text-fill-color:#536675!important; opacity:1; }
[data-testid="stTextInput"] input:autofill { box-shadow:0 0 0 1000px #fafcfd inset!important; }
[data-testid="stTextInput"] input:-webkit-autofill { -webkit-box-shadow:0 0 0 1000px #fafcfd inset!important; -webkit-text-fill-color:#18232f!important; }
[data-baseweb="input"]:focus-within,[data-baseweb="textarea"]:focus-within { border-color:var(--ink)!important; box-shadow:3px 3px 0 var(--ink)!important; }
[data-testid="stExpander"] { background:#fafcfd; border:2px solid var(--ink); border-radius:0; overflow:hidden; }
[data-baseweb="tab-list"] { gap:1.3rem; border-bottom:2px solid var(--ink); padding:0; margin:1.4rem 0 1.1rem; }
button[data-baseweb="tab"] { color:#526675; border-radius:0; background:none; font-weight:650; padding:.8rem .15rem; transition:color .17s ease; }
button[data-baseweb="tab"]:hover,button[data-baseweb="tab"][aria-selected="true"] { color:var(--ink); }
[data-baseweb="tab-highlight"] { background:var(--ink); height:4px; }
[data-testid="stDataFrame"] { border:2px solid var(--ink); border-radius:0; overflow:hidden; }
.stock-hero { display:flex; align-items:center; justify-content:space-between; gap:1rem; margin:1.3rem 0 1rem; }
.stock-kicker { font-size:.69rem; color:var(--muted); font-weight:750; letter-spacing:.09em; text-transform:uppercase; }
.stock-name { font-size:1.9rem; font-weight:700; letter-spacing:-.04em; color:var(--ink); }
.stock-company { font-size:.88rem; color:var(--secondary); }
.signal-pill { color:var(--ink); background:#cddde8; border:2px solid var(--ink); border-radius:0; font-size:.75rem; font-weight:750; padding:8px 12px; white-space:nowrap; }
.empty-state { background:linear-gradient(135deg,#f8fafb,#e1eaf0); border:2px solid var(--ink); box-shadow:4px 4px 0 var(--ink); border-radius:0; padding:1.3rem 1.5rem; margin:1rem 0 2rem; }
.empty-state strong { color:var(--ink); display:block; margin-bottom:.25rem; font-size:.94rem; }
.empty-state span { color:var(--secondary); font-size:.82rem; }
[data-testid="stAlert"] { background:#e5edf3!important; border:2px solid var(--ink)!important; border-radius:0; color:var(--ink)!important; }
[data-testid="stAlert"] svg { color:#354c60!important; }
[data-testid="stVegaLiteChart"] { box-sizing:border-box; width:100%; min-width:0; padding:0!important; margin:14px 0 24px; background:#f8fafc; border:0; box-shadow:inset 0 0 0 2px var(--ink); }
[data-baseweb="tag"] { background:#dbe5ed!important; color:#18232f!important; border-radius:0!important; }
[data-baseweb="tag"] span,[data-baseweb="tag"] svg { color:#18232f!important; }
[data-baseweb="slider"] [role="slider"] { background:#365b76!important; border-color:#365b76!important; }
[data-baseweb="slider"] div:has(>[role="slider"]) { background:#365b76!important; }
[data-baseweb="checkbox"] input:checked+div { background:#365b76!important; border-color:#365b76!important; }
[data-testid="stProgress"] > div > div > div > div { background-color:#365b76!important; }
[data-testid="stProgress"] [role="progressbar"] > div { background-color:#365b76!important; }
[data-baseweb="progress-bar"] > div { background-color:#365b76!important; }
[data-baseweb="switch"] input:checked + div, [data-baseweb="switch"] [aria-checked="true"] { background-color:#365b76!important; border-color:#365b76!important; }
[data-testid="stFileUploaderDropzone"] button { color:var(--ink)!important; border-color:var(--ink)!important; }
[data-testid="stFileUploaderDropzone"] button:hover { background:#d9e5ee!important; color:var(--ink)!important; }
a:focus-visible, button:focus-visible, input:focus-visible, textarea:focus-visible, select:focus-visible { outline-color:#365b76!important; }
[data-baseweb="radio"] input:checked+div { background:#365b76!important; border-color:#365b76!important; }
[data-testid="stWidgetLabel"] p,[data-testid="stSliderTickBarMin"],[data-testid="stSliderTickBarMax"] { color:#354757!important; }
[data-baseweb="menu"],[data-baseweb="popover"] { background:#f5f7f9!important; color:#18232f!important; }
[data-baseweb="base-input"], [data-baseweb="textarea"] textarea { background:#fafcfd!important; color:#18232f!important; caret-color:#365b76!important; }
[data-baseweb="select"] [role="combobox"], [role="listbox"], [role="option"] { background:#fafcfd!important; color:#18232f!important; }
[role="option"]:hover,[role="option"][aria-selected="true"] { background:#dbe5ed!important; color:#18232f!important; }
[role="tooltip"], #vg-tooltip-element { background:#f5f7f9!important; color:#18232f!important; border:1px solid #93a4b3!important; }
[data-testid="stMetricDelta"] { color:#354757!important; background:#e6ecf1!important; }
[data-testid="stMetricDelta"] svg { fill:currentColor!important; }
[data-testid="stAlert"] p,[data-testid="stAlert"] strong { color:#18232f!important; }
[data-testid="stMarkdownContainer"] a { color:#365b76!important; }
[data-testid="stMarkdownContainer"] code { color:#354757!important; background:#e6ecf1!important; }
::selection { background:#c6d5e2; color:#18232f; }
hr { border-color:var(--border); }
@media(max-width:700px){ .block-container { padding-left:1rem; padding-right:1rem; padding-top:1.5rem; } .app-hero { padding:1.35rem; margin-right:6px; box-shadow:6px 6px 0 var(--ink); } .app-hero { margin-bottom:1.4rem; } .app-hero h1 { font-size:2.1rem!important; } .app-nav { gap:1rem; } [data-baseweb="tab-list"] { gap:.7rem; overflow-x:auto; } button[data-baseweb="tab"] { padding:.6rem .05rem; font-size:.8rem; } .stock-hero { align-items:flex-start; } }
@media(prefers-reduced-motion:reduce){* { transition:none!important; transform:none!important; scroll-behavior:auto!important; }}
</style>
<div class="app-hero"><div class="app-eyebrow">Buyntiq / Research workspace</div><h1>Buyntiq.</h1><p>A clearer way to explore stocks, compare signals, and build a portfolio.</p><nav class="app-nav" aria-label="On this page"><a href="#stock-research">Stock research</a><a href="#portfolio-builder">Portfolio builder</a><a href="#rate-my-portfolio">Rate my portfolio</a></nav></div>
""", unsafe_allow_html=True)


def render_price_chart(frame):
    """Explicit cool chart styling, independent of Streamlit's active theme."""
    import altair as alt

    data = frame.rename_axis("Date").reset_index().melt(
        id_vars="Date", var_name="Series", value_name="Value"
    )
    series = list(frame.columns)
    palette = ["#365b76", "#66849a", "#92aabb", "#233c50"]
    chart = (
        alt.Chart(data)
        .mark_line(strokeWidth=2, clip=True)
        .encode(
            x=alt.X("Date:T", title=None, axis=alt.Axis(labelPadding=10)),
            y=alt.Y("Value:Q", title="Price (USD)", scale=alt.Scale(zero=False),
                    axis=alt.Axis(labelPadding=8, titlePadding=16)),
            color=alt.Color("Series:N", scale=alt.Scale(domain=series, range=palette[:len(series)]),
                            legend=alt.Legend(title=None, orient="bottom", padding=12) if len(series) > 1 else None),
            tooltip=[alt.Tooltip("Date:T", title="Date"), "Series:N", alt.Tooltip("Value:Q", format=".2f")],
        )
        .properties(height=380, padding={"left":16, "right":16, "top":24, "bottom":16},
                    autosize=alt.AutoSizeParams(type="fit", contains="padding", resize=True))
        .configure(background="#f8fafc", font="Arial")
        .configure_view(strokeOpacity=0)
        .configure_axis(labelColor="#354757", titleColor="#18232f", gridColor="#dbe3ea",
                        domainColor="#93a4b3", tickColor="#93a4b3")
        .configure_legend(labelColor="#354757", titleColor="#18232f")
        .interactive()
    )
    st.altair_chart(chart, use_container_width=True, theme=None)


STRONG_BUY_THRESHOLD = 80
BUY_THRESHOLD = 65
HOLD_THRESHOLD = 45
SELL_THRESHOLD = 30

HORIZONS = {"1 Month": 21, "3 Months": 63, "6 Months": 126}
FEATURE_COLUMNS = [
    "return_1m", "return_3m", "return_6m", "rsi",
    "distance_ma20", "distance_ma50", "distance_ma200",
    "volatility_21d", "volume_change_21d",
]

FEATURED_STOCKS = [
    ("MU", "Micron"),
    ("AMD", "AMD"),
    ("NVDA", "NVIDIA"),
    ("META", "Meta"),
    ("AAPL", "Apple"),
    ("TSM", "TSMC"),
]

POSITIVE_WORDS = {
    "beat", "beats", "upgrade", "upgraded", "raises", "raised", "record",
    "strong", "growth", "profit", "bullish", "outperform", "approval",
    "partnership", "contract", "surge", "surges",
}
NEGATIVE_WORDS = {
    "miss", "misses", "cut", "cuts", "downgrade", "downgraded", "lawsuit",
    "probe", "investigation", "weak", "decline", "loss", "warning", "recall",
    "layoff", "layoffs", "bearish", "underperform", "restriction", "restrictions",
}


if "ticker_input" not in st.session_state:
    st.session_state["ticker_input"] = ""
if "auto_analyze" not in st.session_state:
    st.session_state["auto_analyze"] = False


def choose_ticker(symbol):
    st.session_state["ticker_input"] = symbol
    st.session_state["auto_analyze"] = True


def safe_float(value):
    try:
        if value is None:
            return None
        value = float(value)
        return value if np.isfinite(value) else None
    except Exception:
        return None


def normalize_fraction(value):
    value = safe_float(value)
    if value is None:
        return None
    if abs(value) > 2:
        return value / 100.0
    return value


def clamp(value, low=0.0, high=100.0):
    return float(np.clip(value, low, high))


def _clean_price_frame(data, ticker=None):
    """
    Normalize the several DataFrame shapes yfinance can return.

    yfinance may return:
      - normal columns: Close, Open, High, Low, Volume
      - MultiIndex: (Price, Ticker)
      - MultiIndex: (Ticker, Price)

    v10 assumed normal columns for single-ticker downloads, which caused
    valid symbols to appear "missing" when yfinance returned a MultiIndex.
    """
    if (
        data is None
        or not isinstance(
            data,
            pd.DataFrame,
        )
        or data.empty
    ):
        return None

    frame = data

    if isinstance(
        frame.columns,
        pd.MultiIndex,
    ):
        ticker_upper = (
            str(ticker).upper()
            if ticker
            else None
        )

        level0 = [
            str(x).upper()
            for x in frame.columns.get_level_values(0)
        ]
        level1 = [
            str(x).upper()
            for x in frame.columns.get_level_values(1)
        ]

        # Shape: (Ticker, Price)
        if (
            ticker_upper
            and ticker_upper in level0
        ):
            try:
                frame = frame.xs(
                    ticker,
                    axis=1,
                    level=0,
                    drop_level=True,
                )
            except Exception:
                # Case-insensitive fallback.
                matching = [
                    x
                    for x in frame.columns.get_level_values(0).unique()
                    if str(x).upper()
                    == ticker_upper
                ]
                if matching:
                    frame = frame.xs(
                        matching[0],
                        axis=1,
                        level=0,
                        drop_level=True,
                    )

        # Shape: (Price, Ticker)
        elif (
            ticker_upper
            and ticker_upper in level1
        ):
            try:
                frame = frame.xs(
                    ticker,
                    axis=1,
                    level=1,
                    drop_level=True,
                )
            except Exception:
                matching = [
                    x
                    for x in frame.columns.get_level_values(1).unique()
                    if str(x).upper()
                    == ticker_upper
                ]
                if matching:
                    frame = frame.xs(
                        matching[0],
                        axis=1,
                        level=1,
                        drop_level=True,
                    )

        # Single-symbol MultiIndex. Detect which level contains OHLCV names.
        else:
            price_names = {
                "OPEN",
                "HIGH",
                "LOW",
                "CLOSE",
                "ADJ CLOSE",
                "VOLUME",
            }

            unique0 = {
                str(x).upper()
                for x in frame.columns.get_level_values(0).unique()
            }
            unique1 = {
                str(x).upper()
                for x in frame.columns.get_level_values(1).unique()
            }

            if unique0.intersection(
                price_names
            ):
                try:
                    frame = frame.copy(deep=False)
                    frame.columns = (
                        frame.columns
                        .get_level_values(0)
                    )
                except Exception:
                    return None

            elif unique1.intersection(
                price_names
            ):
                try:
                    frame = frame.copy(deep=False)
                    frame.columns = (
                        frame.columns
                        .get_level_values(1)
                    )
                except Exception:
                    return None

            else:
                return None

    frame = frame.copy()

    # Clean duplicate columns after flattening.
    if frame.columns.duplicated().any():
        frame = frame.loc[
            :,
            ~frame.columns.duplicated(),
        ]

    # Normalize column names while preserving expected capitalization.
    rename_map = {}

    for col in frame.columns:
        text = str(col).strip()
        upper = text.upper()

        canonical = {
            "OPEN": "Open",
            "HIGH": "High",
            "LOW": "Low",
            "CLOSE": "Close",
            "ADJ CLOSE": "Adj Close",
            "VOLUME": "Volume",
        }.get(upper)

        if canonical:
            rename_map[col] = canonical

    if rename_map:
        frame = frame.rename(
            columns=rename_map
        )

    if "Close" not in frame.columns:
        return None

    try:
        if (
            getattr(
                frame.index,
                "tz",
                None,
            )
            is not None
        ):
            frame.index = (
                frame.index
                .tz_localize(None)
            )
    except Exception:
        pass

    frame = (
        frame
        .sort_index()
    )

    frame = frame[
        ~frame.index.duplicated(
            keep="last"
        )
    ]

    frame = frame[
        frame["Close"].notna()
    ]

    if frame.empty:
        return None

    if "Volume" not in frame.columns:
        frame["Volume"] = np.nan

    return frame


def _fetch_single_history(ticker):
    yf = get_yfinance()

    symbol = normalize_yahoo_symbol(
        ticker
    )

    if symbol is None:
        return None

    # First use Ticker.history because it normally returns simple OHLCV
    # columns for a single symbol.
    for attempt in range(2):
        try:
            data = (
                yf.Ticker(symbol)
                .history(
                    period="10y",
                    interval="1d",
                    auto_adjust=True,
                    timeout=12,
                )
            )

            data = _clean_price_frame(
                data,
                ticker=symbol,
            )

            if data is not None:
                _write_price_cache(
                    symbol,
                    data,
                )
                return data

        except Exception:
            pass

        if attempt == 0:
            time.sleep(0.25)

    # Fallback to yf.download. Some yfinance versions return a MultiIndex
    # even for one ticker; _clean_price_frame handles that shape now.
    for attempt in range(2):
        try:
            data = yf.download(
                tickers=symbol,
                period="10y",
                interval="1d",
                auto_adjust=True,
                progress=False,
                threads=False,
                group_by="column",
            )

            data = _clean_price_frame(
                data,
                ticker=symbol,
            )

            if data is not None:
                _write_price_cache(
                    symbol,
                    data,
                )
                return data

        except Exception:
            pass

        if attempt == 0:
            time.sleep(0.25)

    return None


def download_data(ticker, force_refresh=False):
    """
    Read normalized price history from disk first, then Yahoo if needed.
    """
    symbol = normalize_yahoo_symbol(
        ticker
    )

    if symbol is None:
        return None

    if not force_refresh:
        cached = _read_price_cache(
            symbol
        )

        if cached is not None:
            return _clean_price_frame(
                cached,
                ticker=symbol,
            )

    return _fetch_single_history(
        symbol
    )


FUNDAMENTAL_FIELDS = ("revenue_growth", "earnings_growth", "profit_margin", "forward_pe", "debt_to_equity", "free_cash_flow")


def _fundamental_count(value):
    return sum(safe_float((value or {}).get(key)) is not None for key in FUNDAMENTAL_FIELDS)


def _statement_value(frame, aliases, column):
    if frame is None or frame.empty or column not in frame.columns:
        return None
    for alias in aliases:
        if alias in frame.index:
            return safe_float(frame.loc[alias, column])
    return None


def _statement_fundamentals(income, balance, cashflow):
    """Use dated, annual statement values only when the snapshot is missing."""
    result = {}
    if isinstance(income, pd.DataFrame) and not income.empty:
        income = income.reindex(sorted(income.columns, reverse=True), axis=1)
        current = income.columns[0]
        revenue = _statement_value(income, ["TotalRevenue", "Total Revenue", "OperatingRevenue"], current)
        net_income = _statement_value(income, ["NetIncome", "Net Income", "NetIncomeCommonStockholders"], current)
        if revenue is not None and revenue > 0 and net_income is not None:
            result["profit_margin"] = net_income / revenue
        if len(income.columns) > 1:
            previous = income.columns[1]
            # Never label a skipped reporting year as one-year growth.
            gap = (pd.Timestamp(current) - pd.Timestamp(previous)).days
            if 300 <= gap <= 430:
                old_revenue = _statement_value(income, ["TotalRevenue", "Total Revenue", "OperatingRevenue"], previous)
                old_income = _statement_value(income, ["NetIncome", "Net Income", "NetIncomeCommonStockholders"], previous)
                if revenue is not None and old_revenue is not None and old_revenue > 0:
                    result["revenue_growth"] = revenue / old_revenue - 1
                if net_income is not None and old_income is not None and old_income > 0:
                    result["earnings_growth"] = net_income / old_income - 1
        result["statement_date"] = str(pd.Timestamp(current).date())
    if isinstance(balance, pd.DataFrame) and not balance.empty:
        column = max(balance.columns)
        debt = _statement_value(balance, ["TotalDebt", "Total Debt"], column)
        equity = _statement_value(balance, ["StockholdersEquity", "Stockholders Equity", "TotalEquityGrossMinorityInterest"], column)
        if debt is not None and equity is not None and equity > 0:
            result["debt_to_equity"] = debt / equity
    if isinstance(cashflow, pd.DataFrame) and not cashflow.empty:
        column = max(cashflow.columns)
        fcf = _statement_value(cashflow, ["FreeCashFlow", "Free Cash Flow"], column)
        if fcf is None:
            operating = _statement_value(cashflow, ["OperatingCashFlow", "Operating Cash Flow"], column)
            capex = _statement_value(cashflow, ["CapitalExpenditure", "Capital Expenditure"], column)
            if operating is not None and capex is not None:
                fcf = operating - abs(capex)
        result["free_cash_flow"] = fcf
    return result


@st.cache_resource(show_spinner=False)
def _sec_request_state():
    return {"lock": threading.Lock(), "last": 0.0}


def _sec_json(url):
    import requests
    state = _sec_request_state()
    with state["lock"]:
        delay = 0.25 - (time.monotonic() - state["last"])
        if delay > 0:
            time.sleep(delay)
        state["last"] = time.monotonic()
    response = requests.get(url, timeout=(4, 12), headers={
        "User-Agent": os.environ.get("SEC_USER_AGENT", "Buyntiq research application contact https://github.com/underurmom01/thingy"),
        "Accept": "application/json",
    })
    response.raise_for_status()
    return response.json()


@st.cache_data(ttl=86400, show_spinner=False)
def _sec_ticker_index():
    cached = _read_json_cache("sec_index_v1", "tickers", 86400)
    if cached:
        return cached
    records = _sec_json("https://www.sec.gov/files/company_tickers.json")
    result = {normalize_yahoo_symbol(item["ticker"]): int(item["cik_str"]) for item in records.values()}
    _write_json_cache("sec_index_v1", "tickers", result)
    return result


def _sec_annual_series(facts, aliases):
    """Annual USD facts, newest restatement per period, explicit tag priority."""
    result = {}
    for alias in aliases:
        candidates = {}
        for entry in facts.get(alias, {}).get("units", {}).get("USD", []):
            if entry.get("form") not in ("10-K", "10-K/A") or not entry.get("start"):
                continue
            try:
                duration = (pd.Timestamp(entry["end"]) - pd.Timestamp(entry["start"])).days
            except (ValueError, KeyError):
                continue
            if not 300 <= duration <= 430 or safe_float(entry.get("val")) is None:
                continue
            end = entry["end"]
            if end not in candidates or entry.get("filed", "") > candidates[end].get("filed", ""):
                candidates[end] = entry
        for end, entry in candidates.items():
            result.setdefault(end, entry)
    return result


def _sec_fundamentals_from_facts(payload):
    facts = payload.get("facts", {}).get("us-gaap", {})
    revenue = _sec_annual_series(facts, ["RevenueFromContractWithCustomerExcludingAssessedTax", "RevenueFromContractWithCustomerIncludingAssessedTax", "Revenues", "SalesRevenueNet"])
    income = _sec_annual_series(facts, ["NetIncomeLoss", "ProfitLoss"])
    operating = _sec_annual_series(facts, ["NetCashProvidedByUsedInOperatingActivities"])
    capex = _sec_annual_series(facts, ["PaymentsToAcquirePropertyPlantAndEquipment"])
    result = {"company_name": payload.get("entityName"), "currency": "USD", "source": "SEC EDGAR annual filings"}
    if revenue:
        dates = sorted(revenue, reverse=True)
        end = dates[0]
        current = revenue[end]["val"]
        result["statement_date"] = end
        result["filed_at"] = revenue[end].get("filed")
        result["filing_accession"] = revenue[end].get("accn")
        if current > 0 and end in income and income[end]["start"] == revenue[end]["start"]:
            result["profit_margin"] = income[end]["val"] / current
        if len(dates) > 1 and 300 <= (pd.Timestamp(end)-pd.Timestamp(dates[1])).days <= 430:
            previous = dates[1]
            prior_revenue = revenue[previous]["val"]
            if prior_revenue > 0:
                result["revenue_growth"] = current / prior_revenue - 1
            if end in income and previous in income and income[previous]["val"] > 0:
                if income[end]["start"] == revenue[end]["start"] and income[previous]["start"] == revenue[previous]["start"]:
                    result["earnings_growth"] = income[end]["val"] / income[previous]["val"] - 1
    if operating and capex:
        latest = max(operating)
        if latest in capex and operating[latest]["start"] == capex[latest]["start"]:
            result["free_cash_flow"] = operating[latest]["val"] - abs(capex[latest]["val"])
            result["cashflow_date"] = latest
    return result


def download_sec_fundamentals(symbol):
    cached = _read_json_cache("sec_financials_v1", symbol, 86400)
    if _fundamental_count(cached):
        return cached
    failure = _read_json_cache("sec_failures_v1", symbol, 300)
    if failure:
        return {}
    try:
        cik = _sec_ticker_index().get(symbol)
        if cik is None:
            return {}
        payload = _sec_json("https://data.sec.gov/api/xbrl/companyfacts/CIK{:010d}.json".format(cik))
        result = _sec_fundamentals_from_facts(payload)
        if _fundamental_count(result):
            _write_json_cache("sec_financials_v1", symbol, result)
            return result
    except Exception:
        pass
    _write_json_cache("sec_failures_v1", symbol, {"unavailable": True})
    return {}


def download_fundamentals(ticker, force_refresh=False):
    symbol = normalize_yahoo_symbol(ticker)
    if not symbol:
        return {"status": "unavailable", "message": "Enter a valid ticker."}
    kind = "fundamentals_v18"
    cached = _read_json_cache(kind, symbol, FUNDAMENTAL_CACHE_TTL)
    if not force_refresh and _fundamental_count(cached):
        return cached
    if not force_refresh:
        recent_failure = _read_json_cache("fundamental_failures_v18", symbol, 60)
        if recent_failure is not None:
            return recent_failure
    result = dict.fromkeys(FUNDAMENTAL_FIELDS)
    errors = []
    rate_limited = False
    stock = get_yfinance().Ticker(symbol)
    try:
        info = stock.get_info() or {}
        if not isinstance(info, dict):
            info = {}
        result.update(
            company_name=info.get("longName") or info.get("shortName"),
            sector=info.get("sector"), currency=info.get("financialCurrency") or info.get("currency"),
            revenue_growth=safe_float(info.get("revenueGrowth")),
            earnings_growth=safe_float(info.get("earningsGrowth")),
            profit_margin=safe_float(info.get("profitMargins")),
            forward_pe=safe_float(info.get("forwardPE")),
            free_cash_flow=safe_float(info.get("freeCashflow")),
        )
        debt_percent = safe_float(info.get("debtToEquity"))
        result["debt_to_equity"] = debt_percent / 100 if debt_percent is not None else None
    except Exception as exc:
        rate_limited = "RateLimit" in type(exc).__name__ or "429" in str(exc)
        errors.append("Yahoo is temporarily rate-limiting requests." if rate_limited else "Snapshot: {}".format(type(exc).__name__))
    # Fetch only the missing statement families. Forward P/E cannot be
    # reconstructed from historical statements and is never fabricated.
    statement_fields = {
        "income": ("revenue_growth", "earnings_growth", "profit_margin"),
        "balance": ("debt_to_equity",), "cashflow": ("free_cash_flow",),
    }
    methods = {"income": stock.get_income_stmt, "balance": stock.get_balance_sheet, "cashflow": stock.get_cashflow}
    tables = {key: None for key in methods}
    missing = [key for key, fields in statement_fields.items() if any(result.get(field) is None for field in fields)]
    if missing and not rate_limited:
        with ThreadPoolExecutor(max_workers=min(3, len(missing))) as executor:
            jobs = {executor.submit(methods[key], freq="yearly"): key for key in missing}
            for job in as_completed(jobs):
                key = jobs[job]
                try:
                    tables[key] = job.result()
                except Exception as exc:
                    errors.append("{}: {}".format(key, type(exc).__name__))
    fallback = _statement_fundamentals(tables["income"], tables["balance"], tables["cashflow"])
    fallback_fields = []
    for key in FUNDAMENTAL_FIELDS:
        if result.get(key) is None and safe_float(fallback.get(key)) is not None:
            result[key] = fallback[key]
            fallback_fields.append(key)
    result.update(statement_date=fallback.get("statement_date"), statement_fields=fallback_fields,
                  fetched_at=pd.Timestamp.now(tz="UTC").isoformat(), errors=errors, source="Yahoo Finance")
    if _fundamental_count(result) < 3:
        sec = download_sec_fundamentals(symbol)
        sec_fields = []
        for key in FUNDAMENTAL_FIELDS:
            if result.get(key) is None and safe_float(sec.get(key)) is not None:
                result[key] = sec[key]
                sec_fields.append(key)
        if sec_fields:
            result["source"] = "SEC EDGAR annual filings" if len(sec_fields) == _fundamental_count(result) else "Yahoo Finance + SEC EDGAR annual filings"
            result["sec_fields"] = sec_fields
            for key in ("statement_date", "cashflow_date", "filed_at", "filing_accession"):
                result[key] = sec.get(key) or result.get(key)
            result["company_name"] = result.get("company_name") or sec.get("company_name")
            result["currency"] = result.get("currency") or sec.get("currency")
    count = _fundamental_count(result)
    result["coverage"] = count
    if count:
        result["status"] = "ready" if count == len(FUNDAMENTAL_FIELDS) else "partial"
        result["message"] = "{} of {} company metrics available.".format(count, len(FUNDAMENTAL_FIELDS))
        _write_json_cache(kind, symbol, result)
        try:
            _json_cache_path("fundamental_failures_v18", symbol).unlink(missing_ok=True)
        except OSError:
            pass
    else:
        stale = _read_json_cache(kind, symbol, 7 * 24 * 3600)
        if _fundamental_count(stale):
            result = dict(stale, status="stale", message="Showing the last saved company data; Yahoo could not refresh it.")
        else:
            result["status"] = "unavailable"
            result["message"] = "Company financials are temporarily unavailable from Yahoo and the public-filings fallback. Retry shortly; some funds and new listings do not publish these metrics."
        # A failed provider response is never kept as a successful 12h result.
        _write_json_cache("fundamental_failures_v18", symbol, result)
    return result


def download_latest_earnings(ticker):
    cached = _read_json_cache(
        "earnings",
        ticker,
        EARNINGS_CACHE_TTL,
    )

    if cached is not None:
        return cached

    yf = get_yfinance()
    result = None

    for attempt in range(2):
        try:
            earnings = (
                yf.Ticker(ticker)
                .get_earnings_dates(
                    limit=8
                )
            )

            if (
                earnings is None
                or earnings.empty
            ):
                break

            reported_col = None
            estimate_col = None
            surprise_col = None

            for col in earnings.columns:
                name = (
                    str(col)
                    .lower()
                    .replace(" ", "")
                )

                if "reportedeps" in name:
                    reported_col = col
                elif "epsestimate" in name:
                    estimate_col = col
                elif "surprise" in name:
                    surprise_col = col

            if reported_col is None:
                break

            past = earnings[
                earnings[
                    reported_col
                ].notna()
            ]

            if past.empty:
                break

            row = past.iloc[0]

            reported = safe_float(
                row.get(reported_col)
            )

            estimate = (
                safe_float(
                    row.get(
                        estimate_col
                    )
                )
                if estimate_col is not None
                else None
            )

            surprise = (
                normalize_fraction(
                    row.get(
                        surprise_col
                    )
                )
                if surprise_col is not None
                else None
            )

            if (
                surprise is None
                and reported is not None
                and estimate not in (
                    None,
                    0,
                )
            ):
                surprise = (
                    reported - estimate
                ) / abs(estimate)

            try:
                date_text = str(
                    past.index[0].date()
                )
            except Exception:
                date_text = str(
                    past.index[0]
                )

            result = {
                "date": date_text,
                "reported_eps": reported,
                "estimate_eps": estimate,
                "eps_surprise": surprise,
            }

            break

        except Exception:
            if attempt == 0:
                time.sleep(0.25)

    _write_json_cache(
        "earnings",
        ticker,
        result,
    )

    return result




def parse_news_item(item):
    if not isinstance(item, dict):
        return None

    if item.get("title"):
        return {
            "title": str(item.get("title", "")),
            "publisher": str(item.get("publisher", "")),
            "url": str(item.get("link", "")),
        }

    content = item.get("content")
    if isinstance(content, dict):
        provider = content.get("provider")
        publisher = provider.get("displayName", "") if isinstance(provider, dict) else ""
        canonical = content.get("canonicalUrl")
        url = canonical.get("url", "") if isinstance(canonical, dict) else ""
        if not url:
            click = content.get("clickThroughUrl")
            url = click.get("url", "") if isinstance(click, dict) else ""
        title = str(content.get("title", ""))
        if title:
            return {"title": title, "publisher": str(publisher), "url": str(url)}

    return None


def download_news(ticker):
    cached = _read_json_cache(
        "news",
        ticker,
        NEWS_CACHE_TTL,
    )

    if cached is not None:
        return cached

    yf = get_yfinance()
    out = []

    for attempt in range(2):
        try:
            raw = (
                yf.Ticker(ticker)
                .news
            )

            if not raw:
                break

            for item in raw[:12]:
                parsed = parse_news_item(
                    item
                )

                if parsed:
                    out.append(parsed)

            break

        except Exception:
            if attempt == 0:
                time.sleep(0.25)

    _write_json_cache(
        "news",
        ticker,
        out,
    )

    return out


def headline_sentiment(news_items):
    if not news_items:
        return None
    total = 0.0
    counted = 0
    for item in news_items:
        words = set(item["title"].lower().replace("/", " ").replace("-", " ").split())
        pos = len(words & POSITIVE_WORDS)
        neg = len(words & NEGATIVE_WORDS)
        diff = pos - neg
        if diff != 0:
            total += np.clip(diff, -2, 2)
            counted += 1
    if counted == 0:
        return 0.0
    return float(np.clip(total / (counted * 2.0), -1, 1))


def calculate_rsi(close, period=14):
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    avg_loss = loss.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    rsi = 100 - (100 / (1 + rs))
    rsi = rsi.mask((avg_loss == 0) & (avg_gain > 0), 100)
    rsi = rsi.mask((avg_gain == 0) & (avg_loss > 0), 0)
    rsi = rsi.mask((avg_gain == 0) & (avg_loss == 0), 50)
    return rsi


def pct_return(close, days):
    if len(close) <= days:
        return np.nan
    return float(close.iloc[-1] / close.iloc[-days - 1] - 1)


def max_drawdown(close):
    peak = close.cummax()
    return float((close / peak - 1).min())


def scaled_points(value, bad_value, good_value, points):
    if pd.isna(value):
        return 0.0
    if value <= bad_value:
        return 0.0
    if value >= good_value:
        return float(points)
    return float(((value - bad_value) / (good_value - bad_value)) * points)


def rsi_points(rsi):
    if pd.isna(rsi):
        return 0
    if 50 <= rsi <= 65:
        return 12
    if 45 <= rsi < 50:
        return 9
    if 65 < rsi <= 72:
        return 8
    if 40 <= rsi < 45:
        return 6
    if 30 <= rsi < 40:
        return 3
    if 72 < rsi <= 80:
        return 4
    return 0


def volatility_points(vol):
    if vol <= 0.18:
        return 8
    if vol <= 0.28:
        return 6
    if vol <= 0.40:
        return 3
    if vol <= 0.55:
        return 1
    return 0


def drawdown_points(dd):
    if dd >= -0.10:
        return 8
    if dd >= -0.20:
        return 6
    if dd >= -0.30:
        return 3
    if dd >= -0.45:
        return 1
    return 0


def technical_analysis_from_data(data):
    data = _clean_price_frame(
        data
    )

    if data is None:
        return None

    close = data["Close"].dropna()

    # v9 required 200 trading days and therefore rejected every newer
    # listing. 63 sessions is enough for a useful partial technical view.
    if len(close) < 63:
        return None

    price = safe_float(
        close.iloc[-1]
    )

    if price is None:
        return None

    ma20_series = (
        close.rolling(20).mean()
    )
    ma50_series = (
        close.rolling(50).mean()
    )
    ma200_series = (
        close.rolling(200).mean()
    )

    ma20 = (
        safe_float(
            ma20_series.iloc[-1]
        )
        if len(close) >= 20
        else None
    )

    ma50 = (
        safe_float(
            ma50_series.iloc[-1]
        )
        if len(close) >= 50
        else None
    )

    ma200 = (
        safe_float(
            ma200_series.iloc[-1]
        )
        if len(close) >= 200
        else None
    )

    r1 = (
        pct_return(close, 21)
        if len(close) > 21
        else None
    )

    r3 = (
        pct_return(close, 63)
        if len(close) > 63
        else None
    )

    r6 = (
        pct_return(close, 126)
        if len(close) > 126
        else None
    )

    rsi = safe_float(
        calculate_rsi(
            close
        ).iloc[-1]
    )

    daily = (
        close
        .pct_change()
        .dropna()
    )

    annualized_vol = (
        safe_float(
            daily.std()
            * np.sqrt(252)
        )
        if len(daily) >= 20
        else None
    )

    dd = (
        safe_float(
            max_drawdown(close)
        )
        if len(close) >= 20
        else None
    )

    score = 0.0
    possible = 0.0

    def add_points(
        value,
        bad,
        good,
        points,
    ):
        nonlocal score, possible

        if value is None:
            return

        score += scaled_points(
            value,
            bad,
            good,
            points,
        )
        possible += points

    add_points(
        r1,
        -0.10,
        0.10,
        8,
    )
    add_points(
        r3,
        -0.20,
        0.20,
        12,
    )
    add_points(
        r6,
        -0.30,
        0.30,
        16,
    )

    if ma20 is not None:
        possible += 6

        if price > ma20:
            score += 6

    if ma50 is not None:
        possible += 10

        if price > ma50:
            score += 10

    if ma200 is not None:
        possible += 10

        if price > ma200:
            score += 10

    if (
        ma50 is not None
        and ma200 is not None
    ):
        possible += 10

        if ma50 > ma200:
            score += 10

    if rsi is not None:
        score += rsi_points(rsi)
        possible += 12

    if annualized_vol is not None:
        score += volatility_points(
            annualized_vol
        )
        possible += 8

    if dd is not None:
        score += drawdown_points(dd)
        possible += 8

    if possible <= 0:
        return None

    normalized_score = clamp(
        score / possible * 100.0
    )

    chart = pd.DataFrame(
        {
            "Price": close,
            "MA 20": ma20_series,
            "MA 50": ma50_series,
            "MA 200": ma200_series,
        }
    )

    return {
        "data": data,
        "price": price,
        "technical_score":
            normalized_score,
        "return_1m": r1,
        "return_3m": r3,
        "return_6m": r6,
        "ma20": ma20,
        "ma50": ma50,
        "ma200": ma200,
        "rsi": (
            rsi
            if rsi is not None
            else 50.0
        ),
        "annualized_volatility": (
            annualized_vol
            if annualized_vol is not None
            else 0.50
        ),
        "max_drawdown": dd,
        "chart": chart,
    }


def technical_analysis(ticker):
    return technical_analysis_from_data(
        download_data(ticker)
    )


def weighted_available(parts):
    if not parts:
        return None
    total_weight = sum(weight for score, weight in parts)
    return clamp(sum(score * weight for score, weight in parts) / total_weight)


def fundamental_score(fund):
    if not fund:
        return None, []

    parts = []
    notes = []

    rev = fund.get("revenue_growth")
    if rev is not None:
        parts.append((clamp((rev + 0.10) / 0.35 * 100), 25))
        notes.append("Revenue growth: {:+.1%}".format(rev))

    earn = fund.get("earnings_growth")
    if earn is not None:
        parts.append((clamp((earn + 0.20) / 0.55 * 100), 25))
        notes.append("Earnings growth: {:+.1%}".format(earn))

    margin = fund.get("profit_margin")
    if margin is not None:
        parts.append((clamp((margin - 0.01) / 0.24 * 100), 20))
        notes.append("Profit margin: {:.1%}".format(margin))

    pe = fund.get("forward_pe")
    if pe is not None:
        if pe <= 0:
            pe_score = 0
        elif 8 <= pe <= 25:
            pe_score = 100
        elif pe <= 35:
            pe_score = 75
        elif pe <= 50:
            pe_score = 45
        elif pe < 8:
            pe_score = 65
        else:
            pe_score = 20
        parts.append((pe_score, 15))
        notes.append("Forward P/E: {:.1f}".format(pe))

    debt = fund.get("debt_to_equity")
    if debt is not None:
        ratio = debt
        if ratio <= 0.30:
            debt_score = 100
        elif ratio <= 0.70:
            debt_score = 75
        elif ratio <= 1.20:
            debt_score = 45
        else:
            debt_score = 20
        parts.append((debt_score, 10))
        notes.append("Debt/equity: {:.2f}".format(ratio))

    fcf = fund.get("free_cash_flow")
    if fcf is not None:
        parts.append((100 if fcf > 0 else 20, 5))
        notes.append("Free cash flow: {}".format("positive" if fcf > 0 else "negative"))

    return weighted_available(parts), notes


def event_score(earnings, news_items):
    parts = []
    notes = []

    if earnings is not None and earnings.get("eps_surprise") is not None:
        surprise = earnings["eps_surprise"]
        surprise_score = clamp(50 + (surprise / 0.10) * 50)
        parts.append((surprise_score, 60))
        notes.append("Latest EPS surprise: {:+.1%}".format(surprise))

    sentiment = headline_sentiment(news_items)
    if sentiment is not None:
        sentiment_score = clamp(50 + sentiment * 50)
        parts.append((sentiment_score, 40))
        label = "positive" if sentiment > 0.15 else "negative" if sentiment < -0.15 else "mixed / neutral"
        notes.append("Recent headline tone: {}".format(label))

    return weighted_available(parts), notes


def market_reaction(technical, fund_score, evt_score):
    data = technical["data"]
    close = data["Close"].dropna()
    volume = (
        data["Volume"].dropna()
        if "Volume" in data.columns
        else pd.Series(dtype=float)
    )
    if len(close) < 25:
        return None

    ret1 = float(close.iloc[-1] / close.iloc[-2] - 1)
    ret3 = float(close.iloc[-1] / close.iloc[-4] - 1)
    vol20 = float(close.pct_change().rolling(20).std().iloc[-1])
    z = ret1 / vol20 if np.isfinite(vol20) and vol20 > 0 else 0.0

    if len(volume) >= 20:
        avg_volume = volume.rolling(20).mean().iloc[-1]
        volume_ratio = (
            float(
                volume.iloc[-1]
                / avg_volume
            )
            if (
                np.isfinite(avg_volume)
                and avg_volume > 0
            )
            else 1.0
        )
    else:
        volume_ratio = 1.0

    evt = evt_score if evt_score is not None else 50
    fund = fund_score if fund_score is not None else 50
    rsi = technical["rsi"]

    direction = "NONE"
    strength = "LOW"
    opportunity_score = 50.0
    notes = ["No clear event/price divergence detected"]

    if z <= -2.0 and (evt >= 55 or fund >= 60):
        direction = "NEGATIVE"
        raw = abs(z) * 15 + max(evt - 50, 0) * 0.5 + max(fund - 50, 0) * 0.25
        if rsi < 35:
            raw += 15
        if volume_ratio >= 1.5:
            raw += 10
        strength = "HIGH" if raw >= 70 else "MEDIUM" if raw >= 45 else "LOW"
        opportunity_score = 90 if strength == "HIGH" else 72 if strength == "MEDIUM" else 60
        notes = ["Large downside move relative to normal volatility"]
        if rsi < 35:
            notes.append("RSI is oversold")
        if evt >= 55:
            notes.append("Recent earnings/news context is not strongly negative")
        if fund >= 60:
            notes.append("Fundamental score remains relatively strong")

    elif z >= 2.0 and (evt <= 45 or fund <= 50 or rsi > 75):
        direction = "POSITIVE"
        raw = abs(z) * 15
        if rsi > 75:
            raw += 20
        if volume_ratio >= 1.5:
            raw += 10
        strength = "HIGH" if raw >= 70 else "MEDIUM" if raw >= 45 else "LOW"
        opportunity_score = 15 if strength == "HIGH" else 30 if strength == "MEDIUM" else 40
        notes = ["Large upside move relative to normal volatility"]
        if rsi > 75:
            notes.append("RSI is very high / potentially overbought")
        if evt <= 45:
            notes.append("Recent event/news context does not strongly support the move")

    return {
        "one_day_return": ret1,
        "three_day_return": ret3,
        "reaction_z": z,
        "volume_ratio": volume_ratio,
        "direction": direction,
        "strength": strength,
        "opportunity_score": opportunity_score,
        "notes": notes,
    }


def hybrid_score(technical_score_value, fundamental_score_value, event_score_value, reaction_score_value):
    """
    Legacy/non-ML hybrid score. Kept for compatibility with existing code.
    The displayed signal now uses ml_adjusted_hybrid_score() whenever a
    3-month forecast is available.
    """
    parts = [(technical_score_value, 40)]
    if fundamental_score_value is not None:
        parts.append((fundamental_score_value, 25))
    if event_score_value is not None:
        parts.append((event_score_value, 20))
    if reaction_score_value is not None:
        parts.append((reaction_score_value, 15))
    return weighted_available(parts)


def ml_forecast_component(forecast):
    """
    Converts the 3-month Random Forest forecast into a 0-100 score and
    discounts it when historical validation is weak.

    A 0% forecast is neutral (50).
    Roughly -20% maps toward 0 and +20% maps toward 100 before the
    reliability discount.
    """
    if forecast is None:
        return None, 0.0

    predicted_return = safe_float(
        forecast.get("predicted_return")
    )
    direction_accuracy = safe_float(
        forecast.get("directional_accuracy")
    )
    baseline_accuracy = safe_float(
        forecast.get("baseline_accuracy")
    )
    mae = safe_float(
        forecast.get("mae")
    )

    if predicted_return is None:
        return None, 0.0

    raw_ml_score = clamp(
        50.0
        + (
            predicted_return / 0.20
        ) * 50.0
    )

    # Start with moderate trust. Beating the always-up baseline raises
    # trust; failing to beat it lowers trust, but does not zero it out.
    reliability = 0.50

    if (
        direction_accuracy is not None
        and baseline_accuracy is not None
    ):
        edge = (
            direction_accuracy
            - baseline_accuracy
        )

        reliability = float(
            np.clip(
                0.50 + edge * 2.5,
                0.15,
                0.90,
            )
        )

    # Large historical error further reduces trust.
    if mae is not None:
        mae_penalty = float(
            np.clip(
                mae / 0.30,
                0.0,
                1.0,
            )
        )

        reliability *= (
            1.0
            - 0.35 * mae_penalty
        )

    reliability = float(
        np.clip(
            reliability,
            0.10,
            0.90,
        )
    )

    # Pull unreliable forecasts toward neutral rather than allowing them
    # to dominate the score.
    adjusted_ml_score = (
        50.0
        + (
            raw_ml_score - 50.0
        ) * reliability
    )

    return clamp(adjusted_ml_score), reliability


def ml_adjusted_hybrid_score(
    technical_score_value,
    fundamental_score_value,
    event_score_value,
    reaction_score_value,
    forecast_3m,
):
    """
    Final score used for BUY/HOLD/SELL.

    Target weights when every component is available:
      Technical       30%
      Fundamentals    20%
      Earnings/News   15%
      Market Reaction 10%
      3M ML Forecast  25%

    Missing components are reweighted automatically.
    """
    ml_score, ml_reliability = ml_forecast_component(
        forecast_3m
    )

    parts = [
        (
            technical_score_value,
            30,
        )
    ]

    if fundamental_score_value is not None:
        parts.append(
            (
                fundamental_score_value,
                20,
            )
        )

    if event_score_value is not None:
        parts.append(
            (
                event_score_value,
                15,
            )
        )

    if reaction_score_value is not None:
        parts.append(
            (
                reaction_score_value,
                10,
            )
        )

    if ml_score is not None:
        parts.append(
            (
                ml_score,
                25,
            )
        )

    score = weighted_available(parts)
    guardrail_note = None

    # Guardrails stop the UI from showing a strong bullish label while a
    # reasonably trustworthy 3M model is forecasting a meaningful decline.
    if forecast_3m is not None:
        predicted_return = safe_float(
            forecast_3m.get(
                "predicted_return"
            )
        )

        if predicted_return is not None:
            if (
                predicted_return <= -0.08
                and ml_reliability >= 0.45
            ):
                score = min(
                    score,
                    HOLD_THRESHOLD
                    + (
                        BUY_THRESHOLD
                        - HOLD_THRESHOLD
                    )
                    - 0.1,
                )

                guardrail_note = (
                    "3M ML forecast is {:.1%} with enough historical reliability "
                    "to cap the final signal at HOLD."
                ).format(
                    predicted_return
                )

            elif (
                predicted_return <= -0.05
                and ml_reliability >= 0.35
            ):
                score = min(
                    score,
                    STRONG_BUY_THRESHOLD
                    - 0.1,
                )

                guardrail_note = (
                    "3M ML forecast is {:.1%}, so the final signal cannot be "
                    "STRONG BUY unless the forecast becomes less negative."
                ).format(
                    predicted_return
                )

    return (
        clamp(score),
        ml_score,
        ml_reliability,
        guardrail_note,
    )


def label_from_score(score):
    if score >= STRONG_BUY_THRESHOLD:
        return "STRONG BUY"
    if score >= BUY_THRESHOLD:
        return "BUY"
    if score >= HOLD_THRESHOLD:
        return "HOLD"
    if score >= SELL_THRESHOLD:
        return "SELL"
    return "STRONG SELL"


def build_feature_frame(data):
    close = data["Close"].astype(float)
    volume = pd.to_numeric(data.get("Volume", pd.Series(np.nan, index=data.index)), errors="coerce")
    ma20 = close.rolling(20).mean()
    ma50 = close.rolling(50).mean()
    ma200 = close.rolling(200).mean()

    features = pd.DataFrame(index=data.index)
    features["return_1m"] = close.pct_change(21)
    features["return_3m"] = close.pct_change(63)
    features["return_6m"] = close.pct_change(126)
    features["rsi"] = calculate_rsi(close)
    features["distance_ma20"] = close / ma20 - 1
    features["distance_ma50"] = close / ma50 - 1
    features["distance_ma200"] = close / ma200 - 1
    features["volatility_21d"] = close.pct_change().rolling(21).std() * np.sqrt(252)
    features["volume_change_21d"] = (volume / volume.rolling(21).mean().replace(0, np.nan) - 1).replace([np.inf, -np.inf], np.nan).fillna(0.0)
    return features


def build_training_dataset(data, horizon_days):
    features = build_feature_frame(data)
    close = data["Close"].astype(float)
    features["target"] = close.shift(-horizon_days) / close - 1
    return features.replace([np.inf, -np.inf], np.nan).dropna()


def train_forecast_model(dataset, fast_mode=False, horizon_days=63):
    if len(dataset) < 300:
        return None

    # scikit-learn is one of the heavier imports in the app.
    # Delay it until a forecast is actually requested.
    from sklearn.ensemble import RandomForestRegressor
    from sklearn.metrics import mean_absolute_error

    X = dataset[FEATURE_COLUMNS]
    y = dataset["target"]
    split = int(len(dataset) * 0.80)
    X_train, X_test = X.iloc[:max(0, split - horizon_days)], X.iloc[split:]
    y_train, y_test = y.iloc[:max(0, split - horizon_days)], y.iloc[split:]

    if len(X_train) < 200 or len(X_test) < 30:
        return None

    model = RandomForestRegressor(
        n_estimators=(
            32
            if fast_mode
            else 150
        ),
        max_depth=(
            7
            if fast_mode
            else 8
        ),
        min_samples_leaf=5,
        random_state=42,
        n_jobs=1,
    )
    model.fit(X_train, y_train)
    pred = model.predict(X_test)

    mae = float(mean_absolute_error(y_test, pred))
    direction_acc = float(np.mean(np.sign(y_test.to_numpy()) == np.sign(pred)))
    baseline_acc = float(np.mean(y_test.to_numpy() > 0))

    # Validate with a horizon-sized gap, then refit on all available labels.
    # Fast mode uses fewer trees; cached forecasts avoid repeated training.
    model.fit(X, y)
    return model, mae, direction_acc, baseline_acc


@st.cache_data(ttl=21600, show_spinner=False, max_entries=1024)
def predict_horizon(data, horizon_days, fast_mode=False):
    data_hash = hashlib.sha256(pd.util.hash_pandas_object(data, index=True).values.tobytes()).hexdigest()
    cache_key = "{}-{}-{}".format(data_hash, horizon_days, int(fast_mode))
    persisted = _read_json_cache("forecast_v15", cache_key, PRICE_CACHE_TTL)
    if persisted is not None:
        return persisted
    dataset = build_training_dataset(data, horizon_days)
    trained = train_forecast_model(dataset, fast_mode=fast_mode, horizon_days=horizon_days)
    if trained is None:
        return None

    model, mae, direction_acc, baseline_acc = trained
    current = build_feature_frame(data)[FEATURE_COLUMNS].dropna()
    if current.empty:
        return None

    row = current.iloc[[-1]]
    predicted_return = float(model.predict(row)[0])
    current_price = float(data["Close"].dropna().iloc[-1])

    result = {
        "predicted_return": predicted_return,
        "estimated_price": current_price * (1 + predicted_return),
        "mae": mae,
        "directional_accuracy": direction_acc,
        "baseline_accuracy": baseline_acc,
    }
    _write_json_cache("forecast_v15", cache_key, result)
    return result


@st.cache_data(ttl=1800, show_spinner=False)
def run_all_forecasts(ticker):
    data = download_data(ticker)
    if data is None:
        return {}
    with ThreadPoolExecutor(max_workers=min(3, os.cpu_count() or 1)) as executor:
        jobs = {executor.submit(predict_horizon, data, days): label for label, days in HORIZONS.items()}
        return {jobs[job]: job.result() for job in as_completed(jobs)}



# =========================================================
# AI PORTFOLIO BUILDER
# Automatically discovers stocks from the broad U.S.-listed market.
# Stage 1: fast price-based screening.
# Stage 2: deep hybrid + ML analysis only on finalists.
# =========================================================

SECTOR_OPTIONS = [
    "All US-listed stocks (all sectors)",
    "Information Technology",
    "Health Care",
    "Financials",
    "Consumer Discretionary",
    "Communication Services",
    "Industrials",
    "Consumer Staples",
    "Energy",
    "Utilities",
    "Real Estate",
    "Materials",
    "Other / Miscellaneous",
]

SECTOR_TO_NASDAQ = {
    "Information Technology": "Technology",
    "Health Care": "Health Care",
    "Financials": "Finance",
    "Consumer Discretionary": "Consumer Discretionary",
    "Communication Services": "Telecommunications",
    "Industrials": "Industrials",
    "Consumer Staples": "Consumer Staples",
    "Energy": "Energy",
    "Utilities": "Utilities",
    "Real Estate": "Real Estate",
    "Materials": "Basic Materials",
    "Other / Miscellaneous": "Miscellaneous",
}


def normalize_yahoo_symbol(symbol):
    if symbol is None:
        return None

    symbol = str(symbol).strip().upper()

    if not symbol:
        return None

    symbol = (
        symbol
        .replace(".", "-")
        .replace("/", "-")
    )

    if any(
        bad in symbol
        for bad in [
            "$",
            "^",
            "=",
            "+",
            "*",
        ]
    ):
        return None

    if len(symbol) > 12:
        return None

    return symbol


def looks_like_common_stock_name(name):
    """
    Used only for the Nasdaq Trader fallback directories, which contain
    stocks plus some other listed securities.
    """
    text = str(name or "").upper()

    excluded_terms = [
        " WARRANT",
        " WARRANTS",
        " RIGHT",
        " RIGHTS",
        " UNIT",
        " UNITS",
        " PREFERRED",
        " PREFERENCE",
        " ETF",
        " ETN",
        " EXCHANGE TRADED FUND",
        " CLOSED END FUND",
        " CLOSED-END FUND",
        " BOND",
        " NOTES DUE",
        " SENIOR NOTE",
        " DEBENTURE",
    ]

    return not any(
        term in text
        for term in excluded_terms
    )


@st.cache_data(ttl=21600, show_spinner=False)
def get_us_stock_universe():
    cached = _read_json_cache("universe_v15", "US", 21600)
    if cached:
        return pd.DataFrame.from_records(cached)
    universe = _fetch_us_stock_universe()
    if not universe.empty:
        _write_json_cache("universe_v15", "US", universe.to_dict("records"))
    return universe


def _fetch_us_stock_universe():
    """
    Broad U.S.-listed stock universe.

    Primary source:
      Nasdaq's public stock screener, including sector metadata.

    Fallback:
      Nasdaq Trader's Nasdaq-listed and other-exchange-listed
      symbol directories.

    This is called only after Build Portfolio is pressed.
    """
    import requests

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/146.0.0.0 Safari/537.36"
        ),
        "Accept": "application/json,text/plain,*/*",
        "Accept-Language": "en-US,en;q=0.9",
        "Origin": "https://www.nasdaq.com",
        "Referer": "https://www.nasdaq.com/market-activity/stocks/screener",
    }

    # 1) Preferred source: Nasdaq stock screener
    try:
        endpoint = "https://api.nasdaq.com/api/screener/stocks"

        rows_out = []
        seen = set()
        page_size = 5000

        for offset in range(0, 20000, page_size):
            params = {
                "tableonly": "true",
                "limit": str(page_size),
                "offset": str(offset),
                "download": "true",
            }

            response = requests.get(
                endpoint,
                headers=headers,
                params=params,
                timeout=25,
            )
            response.raise_for_status()

            payload = response.json()
            data = payload.get("data") if isinstance(payload, dict) else None

            if not data:
                break

            rows = data.get("rows", []) or []

            if not rows:
                break

            new_count = 0

            for row in rows:
                symbol = normalize_yahoo_symbol(
                    row.get("symbol")
                )

                if symbol is None or symbol in seen:
                    continue

                seen.add(symbol)
                new_count += 1

                rows_out.append(
                    {
                        "Symbol": symbol,
                        "Company": row.get("name") or symbol,
                        "Sector": row.get("sector") or "Unknown",
                        "Industry": row.get("industry") or "",
                        "Country": row.get("country") or "",
                        "Exchange": "",
                        "Universe Source": "Nasdaq Stock Screener",
                    }
                )

            # Prevent looping forever if the endpoint ignores offset.
            if new_count == 0:
                break

            if len(rows) < page_size:
                break

        df = pd.DataFrame(rows_out)

        # A true full-market result should be much larger than an index.
        if len(df) >= 1000:
            return (
                df
                .drop_duplicates(subset=["Symbol"])
                .sort_values("Symbol")
                .reset_index(drop=True)
            )

    except Exception:
        pass

    # 2) Fallback: official Nasdaq Trader symbol directories
    try:
        from io import StringIO

        nasdaq_url = (
            "https://www.nasdaqtrader.com/"
            "dynamic/SymDir/nasdaqlisted.txt"
        )
        other_url = (
            "https://www.nasdaqtrader.com/"
            "dynamic/SymDir/otherlisted.txt"
        )

        basic_headers = {
            "User-Agent": headers["User-Agent"]
        }

        nasdaq_response = requests.get(
            nasdaq_url,
            headers=basic_headers,
            timeout=20,
        )
        other_response = requests.get(
            other_url,
            headers=basic_headers,
            timeout=20,
        )

        nasdaq_response.raise_for_status()
        other_response.raise_for_status()

        nasdaq_df = pd.read_csv(
            StringIO(nasdaq_response.text),
            sep="|",
            dtype=str,
        )
        other_df = pd.read_csv(
            StringIO(other_response.text),
            sep="|",
            dtype=str,
        )

        rows_out = []

        for _, row in nasdaq_df.iterrows():
            raw_symbol = row.get("Symbol")

            if (
                raw_symbol is None
                or str(raw_symbol).startswith("File Creation Time")
            ):
                continue

            if str(row.get("Test Issue", "N")).upper() == "Y":
                continue

            if str(row.get("ETF", "N")).upper() == "Y":
                continue

            name = row.get("Security Name", raw_symbol)

            if not looks_like_common_stock_name(name):
                continue

            symbol = normalize_yahoo_symbol(raw_symbol)

            if symbol is None:
                continue

            rows_out.append(
                {
                    "Symbol": symbol,
                    "Company": name,
                    "Sector": "Unknown",
                    "Industry": "",
                    "Country": "",
                    "Exchange": "NASDAQ",
                    "Universe Source": "Nasdaq Trader Directory",
                }
            )

        exchange_names = {
            "A": "NYSE American",
            "N": "NYSE",
            "P": "NYSE Arca",
            "Z": "Cboe/BATS",
            "V": "IEX",
        }

        for _, row in other_df.iterrows():
            raw_symbol = (
                row.get("NASDAQ Symbol")
                or row.get("ACT Symbol")
            )

            if (
                raw_symbol is None
                or str(raw_symbol).startswith("File Creation Time")
            ):
                continue

            if str(row.get("Test Issue", "N")).upper() == "Y":
                continue

            if str(row.get("ETF", "N")).upper() == "Y":
                continue

            name = row.get("Security Name", raw_symbol)

            if not looks_like_common_stock_name(name):
                continue

            symbol = normalize_yahoo_symbol(raw_symbol)

            if symbol is None:
                continue

            exchange_code = str(
                row.get("Exchange", "")
            ).strip()

            rows_out.append(
                {
                    "Symbol": symbol,
                    "Company": name,
                    "Sector": "Unknown",
                    "Industry": "",
                    "Country": "",
                    "Exchange": exchange_names.get(
                        exchange_code,
                        exchange_code,
                    ),
                    "Universe Source": "Nasdaq Trader Directory",
                }
            )

        df = pd.DataFrame(rows_out)

        if not df.empty:
            return (
                df
                .drop_duplicates(subset=["Symbol"])
                .sort_values("Symbol")
                .reset_index(drop=True)
            )

    except Exception:
        pass

    return pd.DataFrame(
        columns=[
            "Symbol",
            "Company",
            "Sector",
            "Industry",
            "Country",
            "Exchange",
            "Universe Source",
        ]
    )


def universe_for_sector(sector_focus, universe=None):
    if universe is None:
        universe = get_us_stock_universe()

    if universe.empty:
        return universe

    if sector_focus == "All US-listed stocks (all sectors)":
        return universe.copy()

    wanted_sector = SECTOR_TO_NASDAQ.get(
        sector_focus,
        sector_focus,
    )

    known_sectors = (
        universe["Sector"]
        .astype(str)
        .str.strip()
        .str.lower()
    )

    # The official directory fallback has no sector field.
    if int((known_sectors != "unknown").sum()) == 0:
        return pd.DataFrame(columns=universe.columns)

    return (
        universe[
            known_sectors
            == str(wanted_sector).strip().lower()
        ]
        .copy()
        .reset_index(drop=True)
    )


def quick_screen_score(close):
    """
    First-pass score for the whole market.

    Stocks with less than 200 trading days are still scanned. The score
    automatically reweights around the history that is available.
    """
    close = close.dropna()

    if len(close) < 21:
        return None

    price = safe_float(close.iloc[-1])

    if price is None or price <= 0:
        return None

    score = 0.0
    possible = 0.0

    def add_scaled(value, low, high, points):
        nonlocal score, possible

        if value is None:
            return

        score += scaled_points(
            value,
            low,
            high,
            points,
        )
        possible += points

    r1 = pct_return(close, 21) if len(close) > 21 else None
    r3 = pct_return(close, 63) if len(close) > 63 else None
    r6 = pct_return(close, 126) if len(close) > 126 else None

    add_scaled(r1, -0.10, 0.10, 14)
    add_scaled(r3, -0.20, 0.20, 18)
    add_scaled(r6, -0.30, 0.30, 22)

    for window, points in [
        (20, 8),
        (50, 10),
        (200, 12),
    ]:
        if len(close) >= window:
            ma_value = safe_float(
                close.rolling(window).mean().iloc[-1]
            )

            if ma_value is not None:
                possible += points

                if price > ma_value:
                    score += points

    if len(close) >= 200:
        ma50 = safe_float(
            close.rolling(50).mean().iloc[-1]
        )
        ma200 = safe_float(
            close.rolling(200).mean().iloc[-1]
        )

        if ma50 is not None and ma200 is not None:
            possible += 8

            if ma50 > ma200:
                score += 8

    if len(close) >= 15:
        rsi_value = safe_float(
            calculate_rsi(close).iloc[-1]
        )

        if rsi_value is not None:
            possible += 5

            if 45 <= rsi_value <= 68:
                score += 5
            elif (
                35 <= rsi_value < 45
                or 68 < rsi_value <= 75
            ):
                score += 3

    daily = close.pct_change().dropna()

    if len(daily) >= 20:
        vol = safe_float(
            daily.tail(63).std() * np.sqrt(252)
        )

        if vol is not None:
            possible += 3

            if vol <= 0.25:
                score += 3
            elif vol <= 0.45:
                score += 1

    if possible <= 0:
        return None

    return clamp(
        score / possible * 100.0
    )


def _select_fast_finalists(
    screened,
    sector_focus,
    finalist_limit,
    diversify_finalists=True,
):
    if screened is None or screened.empty:
        return pd.DataFrame()

    # "Best Stocks" mode must preserve the actual score ranking even in
    # all-market mode. Sector balancing is only used by the diversified mode.
    if (
        sector_focus != "All US-listed stocks (all sectors)"
        or not diversify_finalists
    ):
        return (
            screened
            .sort_values("Quick Score", ascending=False)
            .head(finalist_limit)
            .reset_index(drop=True)
        )

    real_sectors = [
        sector
        for sector in (
            screened["Sector"]
            .dropna()
            .astype(str)
            .unique()
            .tolist()
        )
        if (
            sector.strip()
            and sector.strip().lower()
            != "unknown"
        )
    ]

    if not real_sectors:
        return (
            screened
            .head(finalist_limit)
            .reset_index(drop=True)
        )

    selected_indices = []

    per_sector = max(
        6,
        int(
            np.ceil(
                finalist_limit
                / max(
                    len(real_sectors) * 2,
                    1,
                )
            )
        ),
    )

    for sector in real_sectors:
        sector_rows = screened[
            screened["Sector"]
            .astype(str)
            == sector
        ].head(per_sector)

        selected_indices.extend(
            sector_rows.index.tolist()
        )

    selected_indices = list(
        dict.fromkeys(
            selected_indices
        )
    )

    diversified = screened.loc[
        selected_indices
    ].copy()

    if len(diversified) < finalist_limit:
        remaining = screened.drop(
            index=selected_indices,
            errors="ignore",
        )

        need = (
            finalist_limit
            - len(diversified)
        )

        diversified = pd.concat(
            [
                diversified,
                remaining.head(need),
            ],
            ignore_index=True,
        )

    return (
        diversified
        .sort_values(
            "Quick Score",
            ascending=False,
        )
        .head(finalist_limit)
        .reset_index(drop=True)
    )


def _extract_close_series(batch, ticker):
    if batch is None or batch.empty:
        return None
    if isinstance(batch.columns, pd.MultiIndex):
        for key in ((ticker, "Close"), ("Close", ticker)):
            if key in batch.columns:
                close = batch[key]
                if isinstance(close, pd.DataFrame):
                    close = close.iloc[:, 0]
                return close.dropna()
    frame = _clean_price_frame(batch, ticker=ticker)
    return frame["Close"].dropna() if frame is not None else None


def fast_screen_sector(
    sector_focus,
    finalist_limit=150,
    scan_limit=600,
    _progress=None,
    diversify_finalists=True,
):
    """
    Stage 1 still scans the broad selected U.S. universe, but the complete
    quick-score table is now also saved locally. Rebuilding a portfolio
    shortly afterward can therefore skip the full-market price scan.

    Do not wrap this function in st.cache_data: its progress callback writes
    to a placeholder created by the caller, which cannot be safely replayed
    from Streamlit's cached UI messages. The disk cache below retains the
    30-minute screening cache without caching page-specific UI callbacks.
    """
    cache_key = "{}__{}".format(sector_focus, scan_limit)
    disk_cached = _read_screen_cache(cache_key)

    if (
        disk_cached is not None
        and not disk_cached.empty
    ):
        return _select_fast_finalists(
            disk_cached,
            sector_focus,
            finalist_limit,
            diversify_finalists=diversify_finalists,
        )

    yf = get_yfinance()

    full_universe = (
        get_us_stock_universe()
    )

    universe = universe_for_sector(
        sector_focus,
        universe=full_universe,
    )

    if universe.empty:
        return pd.DataFrame()

    if scan_limit and len(universe) > scan_limit:
        # Sample evenly within each sector. The default quick scan is bounded;
        # users can explicitly request the complete market scan.
        grouped = []
        for _, group in universe.groupby("Sector", dropna=False):
            ordered = group.sort_values("Symbol").reset_index(drop=True)
            positions = np.linspace(
                0, len(ordered) - 1, min(len(ordered), scan_limit), dtype=int
            )
            grouped.append(ordered.iloc[positions].reset_index(drop=True))
        sampled = []
        cursor = 0
        while len(sampled) < scan_limit and grouped:
            for group in grouped:
                if len(sampled) >= scan_limit:
                    break
                if cursor < len(group):
                    sampled.append(group.iloc[cursor])
            cursor += 1
            grouped = [g for g in grouped if cursor < len(g)]
        universe = pd.DataFrame(sampled).reset_index(drop=True)

    tickers = (
        universe["Symbol"]
        .dropna()
        .astype(str)
        .unique()
        .tolist()
    )

    sector_map = dict(
        zip(
            universe["Symbol"],
            universe["Sector"],
        )
    )
    name_map = dict(
        zip(
            universe["Symbol"],
            universe["Company"],
        )
    )
    exchange_map = dict(
        zip(
            universe["Symbol"],
            universe["Exchange"],
        )
    )

    rows = []
    uncached_tickers = []
    for symbol in tickers:
        cached_score = _read_json_cache("quick_score_v15", symbol, FAST_SCREEN_CACHE_TTL)
        if cached_score is not None:
            rows.append({"Ticker": symbol, "Company": name_map.get(symbol, symbol),
                         "Sector": sector_map.get(symbol, "Unknown"), "Exchange": exchange_map.get(symbol, ""),
                         "Quick Score": cached_score})
        else:
            uncached_tickers.append(symbol)
    tickers = uncached_tickers

    # A moderately large batch reduces Python/network overhead without
    # trying to make one enormous Yahoo request.
    chunk_size = 100

    for start_index in range(
        0,
        len(tickers),
        chunk_size,
    ):
        chunk = tickers[
            start_index:
            start_index + chunk_size
        ]

        if _progress is not None:
            _progress("Screening market data: {} / {} symbols requested; {} scores ready".format(
                start_index, len(tickers), len(rows)
            ))

        try:
            batch = yf.download(
                tickers=chunk,
                period="1y",
                interval="1d",
                auto_adjust=True,
                progress=False,
                group_by="ticker",
                threads=8,
                timeout=12,
            )
        except Exception:
            continue

        for ticker in chunk:
            try:
                close = _extract_close_series(
                    batch,
                    ticker,
                )

                if close is None:
                    continue

                quick = quick_screen_score(
                    close
                )

                if quick is None:
                    continue
                _write_json_cache("quick_score_v15", ticker, quick)

                rows.append(
                    {
                        "Ticker": ticker,
                        "Company":
                            name_map.get(
                                ticker,
                                ticker,
                            ),
                        "Sector":
                            sector_map.get(
                                ticker,
                                "Unknown",
                            ),
                        "Exchange":
                            exchange_map.get(
                                ticker,
                                "",
                            ),
                        "Quick Score":
                            quick,
                    }
                )

            except Exception:
                continue

    if not rows:
        return pd.DataFrame()

    screened = (
        pd.DataFrame(rows)
        .sort_values(
            "Quick Score",
            ascending=False,
        )
        .reset_index(drop=True)
    )

    _write_screen_cache(
        cache_key,
        screened,
    )

    return _select_fast_finalists(
        screened,
        sector_focus,
        finalist_limit,
        diversify_finalists=diversify_finalists,
    )


def _extract_batch_history(
    batch,
    ticker,
    chunk_length,
):
    if (
        batch is None
        or not isinstance(
            batch,
            pd.DataFrame,
        )
        or batch.empty
    ):
        return None

    # Delegate all MultiIndex handling to the same normalizer used for
    # single-ticker downloads.
    return _clean_price_frame(
        batch,
        ticker=ticker,
    )


def prefetch_price_histories(tickers, min_rows=0):
    """
    Batch-download 10-year history for the finalist pool and place each
    ticker into the local cache.

    v9 made one 10-year Yahoo request per finalist. This is the main fix for
    the large lists of valid symbols that randomly failed deep analysis.
    """
    tickers = list(
        dict.fromkeys(
            [
                str(t).strip().upper()
                for t in tickers
                if str(t).strip()
            ]
        )
    )

    missing = []

    for ticker in tickers:
        cached = _read_price_cache(ticker)
        if cached is None or len(cached) < min_rows:
            missing.append(ticker)

    if not missing:
        return {
            "requested": len(tickers),
            "cached": len(tickers),
            "downloaded": 0,
            "failed": [],
        }

    yf = get_yfinance()
    downloaded = 0
    unresolved = []

    chunk_size = 55

    for start_index in range(
        0,
        len(missing),
        chunk_size,
    ):
        chunk = missing[
            start_index:
            start_index + chunk_size
        ]

        try:
            batch = yf.download(
                tickers=chunk,
                period="10y",
                interval="1d",
                auto_adjust=True,
                progress=False,
                group_by="ticker",
                threads=8,
                timeout=12,
            )

        except Exception:
            batch = None

        for ticker in chunk:
            frame = (
                _extract_batch_history(
                    batch,
                    ticker,
                    len(chunk),
                )
                if batch is not None
                else None
            )

            if frame is not None:
                _write_price_cache(
                    ticker,
                    frame,
                )
                downloaded += 1
            else:
                unresolved.append(
                    ticker
                )

    # Bound retries: Yahoo can reject an entire batch and individual retries
    # for every symbol would otherwise make the page appear frozen.
    final_failed = []

    for ticker in unresolved[:4]:
        frame = download_data(
            ticker,
            force_refresh=True,
        )

        if frame is None:
            final_failed.append(
                ticker
            )

    final_failed.extend(unresolved[4:])

    return {
        "requested": len(tickers),
        "cached":
            len(tickers)
            - len(missing),
        "downloaded": downloaded,
        "failed": final_failed,
    }




def portfolio_snapshot(
    ticker,
    company_fallback=None,
    sector_fallback=None,
    fast_ml=True,
):
    data = download_data(
        ticker
    )

    technical = (
        technical_analysis_from_data(
            data
        )
    )

    if technical is None:
        return None

    # Metadata is optional. Price history is the only hard requirement.
    fundamentals = (
        download_fundamentals(
            ticker
        )
    )
    earnings = (
        download_latest_earnings(
            ticker
        )
    )
    news_items = (
        download_news(
            ticker
        )
    )

    fund_score, _ = (
        fundamental_score(
            fundamentals
        )
    )

    evt_score, _ = (
        event_score(
            earnings,
            news_items,
        )
    )

    reaction = market_reaction(
        technical,
        fund_score,
        evt_score,
    )

    reaction_score = (
        reaction["opportunity_score"]
        if reaction is not None
        else None
    )

    # Portfolio mode uses fewer trees than the one-stock detailed page.
    # It keeps the same features and historical train/test logic.
    ml = predict_horizon(
        technical["data"],
        HORIZONS["3 Months"],
        fast_mode=fast_ml,
    )

    (
        overall,
        ml_score,
        ml_reliability,
        ml_guardrail_note,
    ) = ml_adjusted_hybrid_score(
        technical["technical_score"],
        fund_score,
        evt_score,
        reaction_score,
        ml,
    )

    label = label_from_score(
        overall
    )

    predicted_return = None
    directional_accuracy = None
    baseline_accuracy = None
    mae = None

    if ml is not None:
        predicted_return = (
            ml["predicted_return"]
        )
        directional_accuracy = (
            ml["directional_accuracy"]
        )
        baseline_accuracy = (
            ml["baseline_accuracy"]
        )
        mae = ml["mae"]

    close = (
        technical["data"]
        ["Close"]
        .dropna()
    )

    daily_returns = (
        close
        .pct_change()
        .dropna()
        .tail(252)
    )

    company_name = (
        (
            fundamentals.get(
                "company_name"
            )
            if fundamentals
            else None
        )
        or company_fallback
        or ticker
    )

    sector = (
        (
            fundamentals.get(
                "sector"
            )
            if fundamentals
            else None
        )
        or sector_fallback
        or "Unknown"
    )

    return {
        "ticker": ticker,
        "company_name":
            company_name,
        "sector": sector,
        "price":
            technical["price"],
        "overall_score":
            overall,
        "signal": label,
        "technical_score":
            technical[
                "technical_score"
            ],
        "fundamental_score":
            fund_score,
        "event_score":
            evt_score,
        "reaction_score":
            reaction_score,
        "ml_score":
            ml_score,
        "ml_reliability":
            ml_reliability,
        "ml_guardrail_note":
            ml_guardrail_note,
        "volatility":
            technical[
                "annualized_volatility"
            ],
        "predicted_return_3m":
            predicted_return,
        "directional_accuracy":
            directional_accuracy,
        "baseline_accuracy":
            baseline_accuracy,
        "mae": mae,
        "daily_returns":
            daily_returns,
        "analysis_depth":
            "Full",
    }


def enrich_portfolio_holding(snapshot, technical):
    try:
        fundamentals = download_fundamentals(snapshot["ticker"])
        score, _ = fundamental_score(fundamentals)
        snapshot["fundamental_score"] = score
        snapshot["fundamentals_status"] = fundamentals.get("status", "unavailable")
        if fundamentals.get("company_name"):
            snapshot["company_name"] = fundamentals["company_name"]
        snapshot["overall_score"] = hybrid_score(snapshot["technical_score"], score, snapshot.get("event_score"), snapshot.get("reaction_score"))
        snapshot["signal"] = label_from_score(snapshot["overall_score"])
    except Exception:
        snapshot["fundamentals_status"] = "unavailable"
    updated = add_portfolio_ml(snapshot, technical)
    if updated.get("fundamental_score") is not None:
        updated["analysis_depth"] = "Technical + company" + (" + ML" if updated.get("predicted_return_3m") is not None else "")
    return updated


def add_portfolio_ml(snapshot, technical):
    """Train a fast forecast only for a selected holding."""
    if technical is None:
        return snapshot
    try:
        ml = predict_horizon(
            technical["data"], HORIZONS["3 Months"], fast_mode=True
        )
    except ImportError:
        snapshot["ml_status"] = "Install scikit-learn: python -m pip install scikit-learn"
        return snapshot
    except Exception as exc:
        snapshot["ml_status"] = "{}: {}".format(type(exc).__name__, str(exc)[:160])
        return snapshot
    if ml is None:
        rows = len(build_training_dataset(technical["data"], HORIZONS["3 Months"]))
        snapshot["ml_status"] = "Insufficient usable history: {} training rows (need 330+)".format(rows)
        return snapshot
    score, ml_score, reliability, note = ml_adjusted_hybrid_score(
        snapshot["technical_score"], snapshot.get("fundamental_score"),
        snapshot.get("event_score"), snapshot.get("reaction_score"), ml,
    )
    snapshot.update(
        overall_score=score, signal=label_from_score(score), ml_score=ml_score,
        ml_reliability=reliability, ml_guardrail_note=note,
        predicted_return_3m=ml["predicted_return"],
        directional_accuracy=ml["directional_accuracy"],
        baseline_accuracy=ml["baseline_accuracy"], mae=ml["mae"],
        ml_status="Random Forest (3M)", analysis_depth="Technical + ML",
    )
    return snapshot


def technical_fallback_snapshot(
    ticker,
    company_name,
    sector,
    technical,
    quick_score=None,
):
    """
    Build a portfolio-compatible snapshot from price data only.

    This is a fallback, not a replacement for full analysis. It is used when
    metadata/news/earnings/ML are unavailable or when a very large requested
    portfolio would otherwise take too long to fully analyze.
    """
    if technical is None:
        return None

    technical_score_value = safe_float(
        technical.get(
            "technical_score"
        )
    )

    quick_score_value = safe_float(
        quick_score
    )

    if technical_score_value is None:
        return None

    if quick_score_value is None:
        quick_score_value = (
            technical_score_value
        )

    # Conservative score: price-based evidence only.
    overall = clamp(
        0.70
        * technical_score_value
        + 0.30
        * quick_score_value
    )

    close = (
        technical["data"]
        ["Close"]
        .dropna()
    )

    daily_returns = (
        close
        .pct_change()
        .dropna()
        .tail(252)
    )

    return {
        "ticker": ticker,
        "company_name":
            company_name
            or ticker,
        "sector":
            sector
            or "Unknown",
        "price":
            technical["price"],
        "overall_score":
            overall,
        "signal":
            label_from_score(
                overall
            ),
        "technical_score":
            technical_score_value,
        "fundamental_score":
            None,
        "event_score":
            None,
        "reaction_score":
            None,
        "ml_score":
            None,
        "ml_reliability":
            0.0,
        "ml_guardrail_note":
            None,
        "volatility":
            technical[
                "annualized_volatility"
            ],
        "predicted_return_3m":
            None,
        "directional_accuracy":
            None,
        "baseline_accuracy":
            None,
        "mae":
            None,
        "daily_returns":
            daily_returns,
        "analysis_depth":
            "Technical fallback",
    }



PORTFOLIO_PROFILES = {
    "Conservative": {
        "score_weight": 0.50,
        "forecast_weight": 0.15,
        "risk_weight": 0.35,
        "correlation_penalty": 0.25,
        "volatility_power": 1.25,
        "strength_power": 1.0,
        "max_weight": 0.30,
    },
    "Balanced": {
        "score_weight": 0.50,
        "forecast_weight": 0.25,
        "risk_weight": 0.25,
        "correlation_penalty": 0.16,
        "volatility_power": 0.80,
        "strength_power": 1.15,
        "max_weight": 0.35,
    },
    "Aggressive": {
        "score_weight": 0.43,
        "forecast_weight": 0.42,
        "risk_weight": 0.15,
        "correlation_penalty": 0.08,
        "volatility_power": 0.35,
        "strength_power": 1.35,
        "max_weight": 0.45,
    },
}


def portfolio_base_strength(snapshot, profile):
    score_factor = clamp(snapshot["overall_score"]) / 100.0

    predicted = snapshot["predicted_return_3m"]
    if predicted is None:
        forecast_factor = 0.50
    else:
        # A -25% to +25% 3M forecast maps to 0..1.
        forecast_factor = float(
            np.clip(
                (predicted + 0.25) / 0.50,
                0.0,
                1.0,
            )
        )

    # Reduce how much confidence we place in a forecast that has weak
    # historical validation. This does not fully discard it.
    if (
        snapshot["directional_accuracy"] is not None
        and snapshot["baseline_accuracy"] is not None
    ):
        edge = (
            snapshot["directional_accuracy"]
            - snapshot["baseline_accuracy"]
        )
        reliability = float(
            np.clip(
                0.55 + edge * 2.0,
                0.25,
                0.90,
            )
        )
    else:
        reliability = 0.35

    if snapshot["mae"] is not None:
        error_penalty = float(
            np.clip(
                snapshot["mae"] / 0.35,
                0.0,
                1.0,
            )
        )
        reliability *= 1.0 - 0.30 * error_penalty

    # Pull uncertain forecasts toward neutral instead of treating them
    # as equally reliable.
    forecast_factor = (
        reliability * forecast_factor
        + (1.0 - reliability) * 0.50
    )

    volatility = snapshot["volatility"]
    risk_factor = 1.0 - float(
        np.clip(
            (volatility - 0.15) / 0.55,
            0.0,
            1.0,
        )
    )

    strength = (
        profile["score_weight"] * score_factor
        + profile["forecast_weight"] * forecast_factor
        + profile["risk_weight"] * risk_factor
    )

    return float(strength)


def max_positive_correlation(snapshot, selected):
    if not selected:
        return 0.0

    candidate = snapshot["daily_returns"]
    correlations = []

    for chosen in selected:
        other = chosen["daily_returns"]
        aligned = pd.concat(
            [candidate, other],
            axis=1,
            join="inner",
        ).dropna()

        if len(aligned) < 40:
            continue

        corr = safe_float(
            aligned.iloc[:, 0].corr(
                aligned.iloc[:, 1]
            )
        )

        if corr is not None:
            correlations.append(
                max(corr, 0.0)
            )

    if not correlations:
        return 0.0

    return float(max(correlations))


def cap_and_normalize_weights(raw_weights, max_weight):
    raw = np.asarray(raw_weights, dtype=float)
    raw = np.maximum(raw, 0.000001)

    if raw.sum() <= 0:
        raw = np.ones_like(raw)

    weights = raw / raw.sum()

    n = len(weights)
    if n == 0:
        return weights

    # A cap below 1/n is mathematically impossible.
    max_weight = max(
        float(max_weight),
        1.0 / n,
    )

    for _ in range(25):
        over = weights > max_weight + 1e-10
        if not np.any(over):
            break

        excess = float(
            np.sum(
                weights[over] - max_weight
            )
        )

        weights[over] = max_weight
        under = ~over

        if not np.any(under):
            break

        under_total = float(
            weights[under].sum()
        )

        if under_total <= 0:
            weights[under] += (
                excess
                / int(np.sum(under))
            )
        else:
            weights[under] += (
                excess
                * weights[under]
                / under_total
            )

    return weights / weights.sum()


def build_ai_portfolio(
    snapshots,
    holdings_count,
    risk_profile,
    diversify_sectors=False,
    selection_mode="Best Stocks",
):
    profile = PORTFOLIO_PROFILES[
        risk_profile
    ]

    usable = [
        item
        for item in snapshots
        if item is not None
    ]

    if not usable:
        return None

    holdings_count = min(
        holdings_count,
        len(usable),
    )

    for item in usable:
        item["base_strength"] = (
            portfolio_base_strength(
                item,
                profile,
            )
        )

    correlations = pd.DataFrame(
        {item["ticker"]: item["daily_returns"] for item in usable}
    ).corr(min_periods=40).fillna(0.0).clip(lower=0.0)

    # IMPORTANT: candidates arrive here AFTER company + ML enrichment.
    # Best Stocks therefore means exactly what it says: highest FINAL
    # research score wins. Risk profile still affects position sizing.
    if selection_mode == "Best Stocks":
        ranked = sorted(
            usable,
            key=lambda item: (
                safe_float(item.get("overall_score"))
                if safe_float(item.get("overall_score")) is not None
                else -1.0,
                item.get("base_strength", 0.0),
            ),
            reverse=True,
        )
        selected = []
        for item in ranked[:holdings_count]:
            chosen = dict(item)
            chosen["selection_strength"] = (
                clamp(chosen.get("overall_score", 0.0)) / 100.0
            )
            selected.append(chosen)

    else:
        selected = []
        remaining = list(usable)
        maximum_correlations = {item["ticker"]: 0.0 for item in usable}
        selected_sector_counts = {}

        # Diversified mode starts from the fully analyzed final scores, then
        # allows correlation/sector penalties to make modest substitutions.
        known_sectors = {
            (item.get("sector") or "Unknown")
            for item in usable
            if (item.get("sector") or "Unknown") != "Unknown"
        }
        sector_count = max(len(known_sectors), 1)
        max_per_sector = max(
            2,
            int(np.ceil(holdings_count / float(sector_count))) + 1,
        )

        while len(selected) < holdings_count and remaining:
            best = None
            best_adjusted = -999

            for candidate in remaining:
                candidate_sector = candidate.get("sector") or "Unknown"
                same_sector_count = selected_sector_counts.get(candidate_sector, 0)

                if (
                    diversify_sectors
                    and candidate_sector != "Unknown"
                    and same_sector_count >= max_per_sector
                ):
                    continue

                corr = maximum_correlations[candidate["ticker"]]
                sector_penalty = 0.0
                if diversify_sectors and candidate_sector != "Unknown":
                    sector_penalty = 0.035 * same_sector_count

                # Keep diversification secondary to the final research score.
                # The old code could let correlation dominate the ranking.
                adjusted = (
                    0.82 * (clamp(candidate["overall_score"]) / 100.0)
                    + 0.18 * candidate["base_strength"]
                    - 0.50 * profile["correlation_penalty"] * corr
                    - sector_penalty
                )

                if adjusted > best_adjusted:
                    best = candidate
                    best_adjusted = adjusted

            if best is None and remaining:
                best = max(
                    remaining,
                    key=lambda item: clamp(item["overall_score"]),
                )
                best_adjusted = clamp(best["overall_score"]) / 100.0

            if best is None:
                break

            chosen = dict(best)
            chosen["selection_strength"] = best_adjusted
            selected.append(chosen)

            chosen_sector = chosen.get("sector") or "Unknown"
            selected_sector_counts[chosen_sector] = (
                selected_sector_counts.get(chosen_sector, 0) + 1
            )

            latest_correlations = correlations[chosen["ticker"]]
            for item in remaining:
                symbol = item["ticker"]
                maximum_correlations[symbol] = max(
                    maximum_correlations[symbol],
                    float(latest_correlations[symbol]),
                )

            remaining = [
                item for item in remaining
                if item["ticker"] != best["ticker"]
            ]

    if not selected:
        return None

    raw_weights = []

    for item in selected:
        volatility = max(
            item["volatility"],
            0.08,
        )

        raw = (
            max(
                item["base_strength"],
                0.05,
            )
            ** profile["strength_power"]
        ) / (
            volatility
            ** profile["volatility_power"]
        )

        raw_weights.append(raw)

    weights = cap_and_normalize_weights(
        raw_weights,
        profile["max_weight"],
    )

    for index, item in enumerate(selected):
        item["weight"] = float(
            weights[index]
        )

    # Portfolio-level risk estimate from recent daily covariance.
    returns_frame = pd.DataFrame(
        {
            item["ticker"]:
                item["daily_returns"]
            for item in selected
        }
    ).dropna()

    portfolio_volatility = None

    if len(returns_frame) >= 40:
        covariance = (
            returns_frame.cov()
            * 252
        )

        weight_vector = np.array(
            [
                item["weight"]
                for item in selected
            ]
        )

        try:
            variance = float(
                weight_vector.T
                @ covariance.to_numpy()
                @ weight_vector
            )
            portfolio_volatility = (
                np.sqrt(
                    max(
                        variance,
                        0.0,
                    )
                )
            )
        except Exception:
            portfolio_volatility = None

    forecast_items = [
        item
        for item in selected
        if item[
            "predicted_return_3m"
        ] is not None
    ]

    weighted_forecast = None

    if forecast_items:
        forecast_weight_total = sum(
            item["weight"]
            for item in forecast_items
        )

        if forecast_weight_total > 0:
            weighted_forecast = sum(
                item["weight"]
                * item[
                    "predicted_return_3m"
                ]
                for item in forecast_items
            ) / forecast_weight_total

    weighted_score = sum(
        item["weight"]
        * item["overall_score"]
        for item in selected
    )

    return {
        "holdings": selected,
        "weighted_score": weighted_score,
        "weighted_forecast_3m":
            weighted_forecast,
        "portfolio_volatility":
            portfolio_volatility,
    }



WATCHLIST_PATH = LOCAL_CACHE_ROOT.parent / "stock_signal_watchlist.json"


def parse_watchlist(text):
    symbols = []
    for token in re.split(r"[,;\s]+", text.strip()):
        symbol = normalize_yahoo_symbol(token)
        if symbol and re.fullmatch(r"[A-Z0-9][A-Z0-9.-]{0,11}", symbol) and symbol not in symbols:
            symbols.append(symbol)
    return symbols[:40]


if "watchlist" not in st.session_state:
    try:
        saved = json.loads(WATCHLIST_PATH.read_text(encoding="utf-8"))
        st.session_state["watchlist"] = parse_watchlist(" ".join(saved))
    except (OSError, ValueError, TypeError):
        st.session_state["watchlist"] = [symbol for symbol, _ in FEATURED_STOCKS]

with st.sidebar:
    st.markdown('<div class="sidebar-brand">Buyntiq</div><div class="sidebar-description">Your personal research workspace.</div>', unsafe_allow_html=True)
    st.markdown("### Watchlist")
    st.caption("Select a ticker to open its research.")
    for symbol in st.session_state["watchlist"]:
        st.button(symbol, key="watch_" + symbol, on_click=choose_ticker,
                  args=(symbol,), use_container_width=True)
    if not st.session_state["watchlist"]:
        st.caption("Add your first symbol below.")
    with st.expander("Edit watchlist"):
        with st.form("watchlist_editor"):
            watchlist_text = st.text_area(
                "Symbols", value=", ".join(st.session_state["watchlist"]),
                help="Separate symbols with commas. Remove a symbol to delete it. Up to 40 symbols.",
            )
            if st.form_submit_button("Save watchlist", use_container_width=True):
                st.session_state["watchlist"] = parse_watchlist(watchlist_text)
                try:
                    temporary = WATCHLIST_PATH.with_suffix(".tmp")
                    temporary.write_text(json.dumps(st.session_state["watchlist"]), encoding="utf-8")
                    os.replace(temporary, WATCHLIST_PATH)
                except OSError:
                    st.warning("Saved for this session; could not save to disk.")
                st.rerun()
    st.divider()
    with st.expander("Data settings"):
        if st.button("Refresh market data", use_container_width=True):
            st.cache_data.clear()
            clear_local_disk_cache()
            st.success("Market caches cleared.")
    st.caption("Price and company data may update at different times.")


st.markdown('<div id="stock-research"></div>', unsafe_allow_html=True)
ticker = st.text_input(
    "Stock ticker",
    placeholder="AAPL",
    max_chars=12,
    key="ticker_input",
).strip().upper()

ticker = (
    normalize_yahoo_symbol(
        ticker
    )
    if ticker
    else ""
)

analyze_clicked = st.button("Analyze", type="primary", use_container_width=True)
should_analyze = analyze_clicked or st.session_state.get("auto_analyze", False)


if should_analyze:
    st.session_state["auto_analyze"] = False
    if not ticker:
        st.warning("Enter a ticker first.")
    else:
        with st.spinner("Loading stock research…"):
            with ThreadPoolExecutor(max_workers=4) as executor:
                technical_job = executor.submit(technical_analysis, ticker)
                fund_job = executor.submit(download_fundamentals, ticker)
                earnings_job = executor.submit(download_latest_earnings, ticker)
                news_job = executor.submit(download_news, ticker)
                technical = technical_job.result()
                fundamentals = fund_job.result()
                earnings = earnings_job.result()
                news_items = news_job.result()
            if technical is None:
                st.error("Price history is unavailable for this ticker. Try again shortly.")
            else:
                forecasts = run_all_forecasts(ticker)
                st.session_state["analysis_bundle"] = dict(
                    ticker=ticker, technical=technical, fundamentals=fundamentals,
                    earnings=earnings, news_items=news_items, forecasts=forecasts,
                )

bundle = st.session_state.get("analysis_bundle")
if bundle and bundle["ticker"] == ticker:
    technical, fundamentals = bundle["technical"], bundle["fundamentals"]
    earnings, news_items, forecasts = bundle["earnings"], bundle["news_items"], bundle["forecasts"]
    fund_score, fund_notes = fundamental_score(fundamentals)
    evt_score, evt_notes = event_score(earnings, news_items)
    reaction = market_reaction(technical, fund_score, evt_score)
    reaction_score = reaction["opportunity_score"] if reaction else None
    forecast_3m = forecasts.get("3 Months")
    overall, ml_score, ml_reliability, ml_guardrail_note = ml_adjusted_hybrid_score(
        technical["technical_score"], fund_score, evt_score, reaction_score, forecast_3m,
    )
    label = label_from_score(overall)
    company_name = fundamentals.get("company_name") or ticker
    st.markdown('<div class="stock-hero"><div><div class="stock-kicker">your research, at a glance</div><div class="stock-name">'+html.escape(ticker)+'</div><div class="stock-company">'+html.escape(company_name)+'</div></div><div class="signal-pill">'+label+'</div></div>', unsafe_allow_html=True)
    c1,c2,c3 = st.columns(3)
    c1.metric("Current price", "${:,.2f}".format(technical["price"]))
    c2.metric("Research score", "{:.0f}/100".format(overall))
    c3.metric("3-month ML outlook", "{:+.1%}".format(forecast_3m["predicted_return"]) if forecast_3m else "Unavailable")
    st.caption("Historical model estimate · not a promised return")
    if ml_guardrail_note:
        st.caption(ml_guardrail_note)
    overview_tab, company_tab, forecast_tab, news_tab = st.tabs(["Overview", "Company", "Forecasts", "News"])
    with overview_tab:
        render_price_chart(technical["chart"][["Price"]].tail(504))
        cols=st.columns(3)
        for col,key,name in zip(cols,["return_1m","return_3m","return_6m"],["Past month","Past 3 months","Past 6 months"]):
            value=safe_float(technical[key])
            col.metric(name, "{:+.1%}".format(value) if value is not None else "Unavailable")
        with st.expander("Behind the research score"):
            st.dataframe(pd.DataFrame({"Component":["Technical","Company","Earnings & news","Market reaction","3M machine learning"],"Score":["{:.0f}/100".format(value) if value is not None else "Unavailable" for value in [technical["technical_score"],fund_score,evt_score,reaction_score,ml_score]]}),hide_index=True,use_container_width=True)
            st.caption("Available components are weighted together; missing company data is not treated as a zero score.")
            if forecast_3m:
                st.caption("ML reliability weight: {:.0%}".format(ml_reliability))
        with st.expander("Technical indicators & moving averages"):
            t1,t2,t3=st.columns(3)
            t1.metric("RSI", "{:.1f}".format(technical["rsi"]))
            t2.metric("Annual volatility", "{:.1%}".format(technical["annualized_volatility"]))
            t3.metric("Max drawdown", "{:.1%}".format(technical["max_drawdown"]))
            render_price_chart(technical["chart"].tail(504))
    with company_tab:
        status=fundamentals.get("status", "unavailable")
        if status in ("unavailable","stale"):
            st.warning(fundamentals.get("message", "Company data is unavailable."))
        else:
            st.caption(fundamentals.get("message", "Company financial snapshot"))
        company_metrics=[("Revenue growth","revenue_growth","{:+.1%}"),("Earnings growth","earnings_growth","{:+.1%}"),("Profit margin","profit_margin","{:.1%}"),("Forward P/E","forward_pe","{:.1f}×"),("Debt / equity","debt_to_equity","{:.2f}×"),("Free cash flow","free_cash_flow","{:,.0f}")]
        for offset in (0,3):
            for col,(name,key,fmt) in zip(st.columns(3),company_metrics[offset:offset+3]):
                value=safe_float(fundamentals.get(key))
                col.metric(name,fmt.format(value) if value is not None else "Unavailable")
        if fundamentals.get("currency"):
            st.caption("Cash flow reported in {}.".format(fundamentals["currency"]))
        if fundamentals.get("statement_fields"):
            st.caption("Annual-statement fallback: {}. Latest income statement: {}. Snapshot metrics may cover different periods.".format(
                ", ".join(key.replace("_"," ") for key in fundamentals["statement_fields"]),fundamentals.get("statement_date") or "not provided"))
        if fundamentals.get("sec_fields"):
            st.caption("Public-filings fallback: {}. Fiscal year ended {}. Cash-flow period ended {}. Forward P/E requires analyst estimates and is not supplied by SEC filings.".format(
                ", ".join(key.replace("_", " ") for key in fundamentals["sec_fields"]), fundamentals.get("statement_date") or "not provided", fundamentals.get("cashflow_date") or "not provided"))
        if fundamentals.get("fetched_at"):
            st.caption("Fetched {} UTC · {}".format(fundamentals["fetched_at"][:16].replace("T"," "), fundamentals.get("source", "Yahoo Finance")))
        if st.button("Refresh company data",key="refresh_company"):
            with st.spinner("Refreshing company financials…"):
                bundle["fundamentals"] = download_fundamentals(ticker,force_refresh=True)
            st.rerun()
        with st.expander("Financial notes & data availability"):
            for note in fund_notes:
                st.write("• "+note)
            st.caption("Some funds, young companies, and foreign listings lack these fields. Unavailable metrics are excluded from the score.")
            for error in fundamentals.get("errors",[]):
                st.caption(error)
    with forecast_tab:
        forecast_rows=[]
        for name in HORIZONS:
            forecast=forecasts.get(name)
            if forecast:
                forecast_rows.append({"Horizon":name,"Model return":"{:+.1%}".format(forecast["predicted_return"]),"Model price":"${:,.2f}".format(forecast["estimated_price"]),"Direction accuracy":"{:.1%}".format(forecast["directional_accuracy"]),"Always-up baseline":"{:.1%}".format(forecast["baseline_accuracy"]),"Mean error":"{:.1%}".format(forecast["mae"])})
            else:
                forecast_rows.append({"Horizon":name,"Model return":"Insufficient history"})
        st.dataframe(pd.DataFrame(forecast_rows),hide_index=True,use_container_width=True)
        st.caption("Random Forest · historical prices and volume · a forecast is not a guarantee")
        with st.expander("How to read these forecasts"):
            st.write("Direction accuracy measures historical up/down predictions. Compare it with the always-up baseline; a higher-looking accuracy alone does not mean the model adds value. Mean error measures historical return prediction error.")
            st.write("The 3-month model contributes to the research score. News and company fundamentals influence the score separately; they are not historical model inputs.")
            for name,forecast in forecasts.items():
                if forecast and forecast["directional_accuracy"] <= forecast["baseline_accuracy"]:
                    st.caption(name+": did not beat the always-up baseline.")
    with news_tab:
        if earnings:
            e1,e2,e3=st.columns(3)
            e1.metric("Latest earnings",earnings.get("date","Unavailable"))
            eps=earnings.get("reported_eps"); surprise=earnings.get("eps_surprise")
            e2.metric("Reported EPS","{:.2f}".format(eps) if eps is not None else "Unavailable")
            e3.metric("EPS surprise","{:+.1%}".format(surprise) if surprise is not None else "Unavailable")
        for note in evt_notes:
            st.caption(note)
        if news_items:
            for item in news_items[:6]:
                suffix=" — "+item["publisher"] if item["publisher"] else ""
                if item["url"]:
                    st.markdown("- [{}]({}){}".format(item["title"],item["url"],suffix))
                else:
                    st.write(item["title"]+suffix)
        else:
            st.caption("No recent headlines returned for this ticker.")
        with st.expander("Market reaction signals"):
            if reaction:
                r1,r2,r3,r4=st.columns(4)
                r1.metric("1-day move","{:+.1%}".format(reaction["one_day_return"]))
                r2.metric("3-day move","{:+.1%}".format(reaction["three_day_return"]))
                r3.metric("Move vs. normal","{:+.1f}×".format(reaction["reaction_z"]))
                r4.metric("Volume vs. 20D","{:.1f}×".format(reaction["volume_ratio"]))
                st.caption("Direction: {} · Strength: {}".format(reaction["direction"],reaction["strength"]))
                for note in reaction["notes"]:
                    st.write("• "+note)
            else:
                st.caption("Not enough data to evaluate the latest reaction.")
else:
    st.markdown('<div class="empty-state"><strong>Start with a stock.</strong><span>Enter a ticker above or select one from your watchlist.</span></div>', unsafe_allow_html=True)


# =========================================================
# PORTFOLIO BUILDER UI
# =========================================================

def normalize_portfolio_entries(entries):
    holdings = {}
    for i, row in entries.iterrows():
        raw = row.get("Ticker")
        ticker = "" if pd.isna(raw) else str(raw).strip().upper()
        shares = safe_float(row.get("Shares"))
        if not ticker and (shares is None or shares == 0):
            continue
        if not re.fullmatch(r"[A-Z0-9][A-Z0-9.\-^=]{0,19}", ticker):
            raise ValueError("Row {}: enter a valid stock ticker.".format(i + 1))
        if shares is None or not np.isfinite(shares) or shares <= 0:
            raise ValueError("Row {}: shares must be a positive number.".format(i + 1))
        holdings[ticker] = holdings.get(ticker, 0.0) + shares
    if not holdings:
        raise ValueError("Add at least one ticker and its share count.")
    if len(holdings) > 100:
        raise ValueError("Rate up to 100 different stocks at a time.")
    return holdings


def rate_portfolio_position(ticker, shares):
    data = download_data(ticker)
    if data is None or data.empty:
        raise ValueError("Price history unavailable")
    close = data["Close"].dropna()
    price = safe_float(close.iloc[-1]) if len(close) else None
    if price is None or not np.isfinite(price) or price <= 0:
        raise ValueError("Latest price unavailable")
    technical = technical_analysis_from_data(data)
    try:
        fundamentals = download_fundamentals(ticker) or {}
    except Exception:
        fundamentals = {}
    currency = fundamentals.get("currency")
    if currency and currency.upper() != "USD":
        raise ValueError("Quote is in {}; this view supports USD-priced holdings".format(currency))
    company_score, _ = fundamental_score(fundamentals)
    tech_score = technical["technical_score"] if technical else None
    parts = [(value, weight) for value, weight in [(tech_score, .6), (company_score, .4)]
             if value is not None and np.isfinite(value)]
    score = sum(v * w for v, w in parts) / sum(w for _, w in parts) if parts else None
    return {"Ticker": ticker, "Shares": shares, "Price": price, "Value": price * shares,
            "Research score": score, "Technical": tech_score, "Company": company_score,
            "Sector": fundamentals.get("sector") or "Unknown",
            "Price date": str(close.index[-1].date()),
            "Coverage": "Technical + company" if len(parts) == 2 else
                        "Technical only" if tech_score is not None else
                        "Company only" if company_score is not None else "Unrated"}


def summarize_portfolio_rating(rows):
    table = pd.DataFrame(rows)
    total = float(table["Value"].sum())
    table["Weight"] = table["Value"] / total
    rated = table["Research score"].notna()
    coverage = float(table.loc[rated, "Weight"].sum())
    score = float((table.loc[rated, "Research score"] * table.loc[rated, "Weight"]).sum() / coverage) if coverage else None
    table = table.sort_values("Weight", ascending=False).reset_index(drop=True)
    return {"table": table, "total": total, "score": score, "coverage": coverage}


st.divider()
st.markdown('<div id="rate-my-portfolio"></div>', unsafe_allow_html=True)
st.subheader("Rate my portfolio")
st.caption("Enter your USD-priced stocks and share counts. Fractional shares work; duplicate tickers are combined.")
with st.form("rate_portfolio_form"):
    rating_entries = st.data_editor(
        pd.DataFrame({"Ticker": ["", ""], "Shares": [0.0, 0.0]}),
        num_rows="dynamic", hide_index=True, use_container_width=True,
        column_config={"Ticker": st.column_config.TextColumn("Stock ticker", help="For example: AAPL or MSFT"),
                       "Shares": st.column_config.NumberColumn("Shares owned", min_value=0.0, format="%.6f")},
        key="rating_holdings_editor",
    )
    rate_clicked = st.form_submit_button("Rate my portfolio", type="primary", use_container_width=True)

if rate_clicked:
    try:
        entered_holdings = normalize_portfolio_entries(rating_entries)
    except ValueError as exc:
        st.error(str(exc))
    else:
        rows, failures = [], []
        with st.spinner("Checking your holdings…"):
            with ThreadPoolExecutor(max_workers=min(4, len(entered_holdings))) as executor:
                jobs = {executor.submit(rate_portfolio_position, ticker, shares): ticker
                        for ticker, shares in entered_holdings.items()}
                for job in as_completed(jobs):
                    try:
                        rows.append(job.result())
                    except Exception as exc:
                        failures.append(jobs[job] + ": " + (str(exc) if isinstance(exc, ValueError) else "Market data unavailable; try again."))
        result = summarize_portfolio_rating(rows) if rows else None
        st.session_state["portfolio_rating_result"] = {"result": result, "failures": sorted(failures),
                                                       "time": pd.Timestamp.now(tz="UTC").strftime("%Y-%m-%d %H:%M UTC")}

if "portfolio_rating_result" in st.session_state:
    saved_rating = st.session_state["portfolio_rating_result"]
    st.caption("Last submitted portfolio · {} · Submit again after editing holdings.".format(saved_rating["time"]))
    for failure in saved_rating["failures"]:
        st.warning(failure)
    result = saved_rating["result"]
    if result is None:
        st.error("No holdings could be priced. Check the symbols and try again.")
    else:
        table = result["table"]
        partial = bool(saved_rating["failures"])
        a, b, c = st.columns(3)
        a.metric("Priced holdings value" if partial else "Portfolio value", "${:,.2f}".format(result["total"]))
        b.metric("Research rating (priced holdings)" if partial else "Research rating",
                 "{:.0f}/100".format(result["score"]) if result["score"] is not None else "Unavailable")
        c.metric("Largest position", "{} · {:.1%}".format(table.iloc[0]["Ticker"], table.iloc[0]["Weight"]))
        if partial:
            st.info("Unpriced holdings are excluded from value, weights and rating. These results cover only the priced portion of your portfolio.")
        st.caption("Rating: value-weighted stock research scores, combining technical signals (60%) and company fundamentals (40%) where available. Rated coverage: {:.0%} of priced value. Missing components are omitted, not scored as zero. This is not a diversification or risk score.".format(result["coverage"]))
        display = table.copy()
        display["Weight"] = display["Weight"] * 100
        st.dataframe(display, hide_index=True, use_container_width=True, column_config={
            "Price": st.column_config.NumberColumn(format="$%.2f"),
            "Value": st.column_config.NumberColumn(format="$%.2f"),
            "Weight": st.column_config.NumberColumn("Weight (%)", format="%.1f%%"),
            "Research score": st.column_config.NumberColumn(format="%.1f"),
            "Technical": st.column_config.NumberColumn(format="%.1f"),
            "Company": st.column_config.NumberColumn(format="%.1f"),
        })
        if table.iloc[0]["Weight"] > .25:
            st.info("Concentration: {} accounts for {:.1%} of priced value.".format(table.iloc[0]["Ticker"], table.iloc[0]["Weight"]))
        sectors = table.groupby("Sector")["Weight"].sum().sort_values(ascending=False)
        with st.expander("Sector breakdown"):
            st.dataframe((sectors * 100).rename("Weight (%)").round(1), use_container_width=True)
        st.caption("Uses cached historical closing prices, not live quotes. Prices may have different dates. Cash, debt, taxes and trading costs are not included. Research scores do not predict guaranteed returns.")
        st.download_button("Download portfolio review", display.to_csv(index=False), "buyntiq_portfolio_review.csv", "text/csv", key="download_rated_portfolio")


st.divider()
st.markdown('<div id="portfolio-builder"></div>', unsafe_allow_html=True)
st.subheader("Portfolio builder")
st.caption(
    "Choose a sector, risk profile, number of holdings, and budget."
)


with st.expander(
    "Portfolio settings",
    expanded=False,
):
    p1, p2, p3, p4 = st.columns(4)

    sector_focus = p1.selectbox(
        "Sector focus",
        SECTOR_OPTIONS,
        index=0,
        key="portfolio_sector_focus",
        help=(
            "Choose one sector, or scan the entire U.S.-listed stock universe across all sectors."
        ),
    )

    risk_profile = p2.selectbox(
        "Risk profile",
        [
            "Conservative",
            "Balanced",
            "Aggressive",
        ],
        index=1,
        key="portfolio_risk_profile",
    )

    selection_mode = st.radio(
        "Selection method",
        ["Best Stocks", "Diversified Portfolio"],
        index=0,
        horizontal=True,
        key="portfolio_selection_mode",
        help=(
            "Best Stocks picks the highest final research scores after full company + ML analysis. "
            "Diversified Portfolio starts from those final scores but can make small substitutions to reduce correlation and sector concentration."
        ),
    )

    # IMPORTANT: do not fetch the market universe during page render.
    # The input can accept up to 500; the real limit is checked only after
    # the user presses the build button.
    holdings_count = p3.number_input(
        "Holdings",
        min_value=1,
        max_value=500,
        value=5,
        step=1,
        key="portfolio_holdings_count",
        help=(
            "Choose how many stocks you want. The app checks the actual "
            "available sector universe only after you press Build Portfolio."
        ),
    )

    holdings_count = int(
        holdings_count
    )

    budget = p4.number_input(
        "Budget",
        min_value=0.0,
        value=10000.0,
        step=1000.0,
        help=(
            "Use 0 if you only want percentages. "
            "This version searches U.S.-listed stocks, so allocations are in USD."
        ),
        key="portfolio_budget",
    )

    full_market_scan = st.checkbox(
        "Scan the entire market (much slower)", value=False,
        help="Quick mode samples across sectors; a full scan can take several minutes on the first run.",
    )

    if holdings_count > 25:
        st.caption(
            "Large portfolios take longer: the builder now fully analyzes a finalist pool before selecting holdings."
        )

    build_portfolio_clicked = st.button(
        "Build portfolio",
        type="primary",
        use_container_width=True,
        key="build_ai_portfolio_button",
    )

    if build_portfolio_clicked:
        # Nothing above this point requires network access.
        # Only now do we load the stock universe.
        with st.spinner(
            "Loading the full U.S.-listed stock universe..."
        ):
            full_us_universe = get_us_stock_universe()

            selected_universe = universe_for_sector(
                sector_focus,
                universe=full_us_universe,
            )

        selected_universe_size = len(
            selected_universe
        )

        if selected_universe_size == 0:
            st.error(
                "Could not load stocks for that sector. If the sector feed is temporarily unavailable, choose All US-listed stocks; the official symbol-directory fallback still supports the full-market scan."
            )
            st.stop()

        requested_holdings = holdings_count
        holdings_count = min(
            holdings_count,
            selected_universe_size,
        )

        if requested_holdings > selected_universe_size:
            st.warning(
                "You requested {} holdings, but this universe currently has {} "
                "available stocks. Building with {} instead.".format(
                    requested_holdings,
                    selected_universe_size,
                    holdings_count,
                )
            )

        source_name = (
            selected_universe["Universe Source"].iloc[0]
            if (
                not selected_universe.empty
                and "Universe Source" in selected_universe.columns
            )
            else "market source"
        )

        st.caption(
            "Loaded {:,} stock symbols in the selected universe · source: {}.".format(
                selected_universe_size,
                source_name,
            )
        )

        if sector_focus == "All US-listed stocks (all sectors)":
            st.info(
                "Stage 1 will attempt a price-based screen of every symbol in the U.S.-listed stock universe. "
                "Only the strongest candidates move to the much slower deep-analysis stage."
            )

        with st.spinner(
            "Screening stocks (first scan may take a few minutes; later scans use cache)..."
        ):
            # Stage 1 candidate pool can be very broad because this stage is
            # only price-based. It is deliberately much larger than the later
            # fundamentals/news/ML pool.
            finalist_limit = min(
                selected_universe_size,
                max(40, holdings_count + min(30, holdings_count)),
            )

            screen_status = st.empty()
            finalists = fast_screen_sector(
                sector_focus,
                finalist_limit=finalist_limit,
                scan_limit=(None if full_market_scan else max(600, holdings_count * 2)),
                _progress=screen_status.caption,
                diversify_finalists=(selection_mode == "Diversified Portfolio"),
            )
            screen_status.empty()

        if finalists.empty:
            st.error(
                "The stock universe could not be screened right now. Try refreshing the app."
            )

        else:
            st.caption(
                "Price screen selected {} candidates from {}.".format(
                    len(finalists),
                    sector_focus,
                )
            )

            with st.expander(
                "See first-pass finalists",
                expanded=False,
            ):
                st.dataframe(
                    finalists,
                    use_container_width=True,
                    hide_index=True,
                )

            # -------------------------------------------------
            # Stage 2A: one batched 10-year history prefetch
            # -------------------------------------------------
            candidate_tickers = (
                finalists[
                    "Ticker"
                ]
                .astype(str)
                .tolist()
            )

            with st.spinner(
                "Loading candidate history from the local cache or Yahoo..."
            ):
                prefetch_stats = (
                    prefetch_price_histories(
                        candidate_tickers
                    )
                )

            unavailable_prices = set(prefetch_stats["failed"])

            st.caption(
                "10Y history · {} already cached · {} newly batch-loaded · {} unavailable.".format(
                    prefetch_stats[
                        "cached"
                    ],
                    prefetch_stats[
                        "downloaded"
                    ],
                    len(
                        prefetch_stats[
                            "failed"
                        ]
                    ),
                )
            )

            # -------------------------------------------------
            # Build a large pool of valid price-based candidates.
            # These are enough to construct a COMPLETE portfolio even
            # if some slower metadata/news/ML calls fail.
            # -------------------------------------------------
            technical_rows = []
            technical_map = {}
            technical_failures = []

            for _, row in finalists.iterrows():
                symbol = row[
                    "Ticker"
                ]

                if symbol in unavailable_prices:
                    technical_failures.append(symbol)
                    continue

                data = download_data(
                    symbol
                )

                technical = (
                    technical_analysis_from_data(
                        data
                    )
                )

                if technical is None:
                    technical_failures.append(
                        symbol
                    )
                    continue

                technical_map[
                    symbol
                ] = technical

                shortlist_score = (
                    0.52
                    * float(
                        row[
                            "Quick Score"
                        ]
                    )
                    + 0.48
                    * float(
                        technical[
                            "technical_score"
                        ]
                    )
                )

                technical_rows.append(
                    {
                        "Ticker":
                            symbol,
                        "Company":
                            row.get(
                                "Company",
                                symbol,
                            ),
                        "Sector":
                            row.get(
                                "Sector",
                                "Unknown",
                            ),
                        "Exchange":
                            row.get(
                                "Exchange",
                                "",
                            ),
                        "Quick Score":
                            row[
                                "Quick Score"
                            ],
                        "Technical Score":
                            technical[
                                "technical_score"
                            ],
                        "Shortlist Score":
                            shortlist_score,
                    }
                )

            technical_table = (
                pd.DataFrame(
                    technical_rows
                )
                .sort_values(
                    "Shortlist Score",
                    ascending=False,
                )
                .reset_index(
                    drop=True
                )
                if technical_rows
                else pd.DataFrame()
            )

            if technical_table.empty:
                st.error(
                    "No candidates had enough usable price history for portfolio construction."
                )
                st.stop()

            if len(technical_table) < holdings_count:
                st.warning(
                    "You requested {} holdings, but only {} candidates currently have usable price history. "
                    "The portfolio will use all {} available candidates.".format(
                        holdings_count,
                        len(technical_table),
                        len(technical_table),
                    )
                )

            effective_holdings_count = min(
                holdings_count,
                len(
                    technical_table
                ),
            )

            # -------------------------------------------------
            # Stage 3: deep-analyze a REAL finalist pool first.
            # The old version selected holdings before fundamentals + ML,
            # which meant a mediocre early pick could never be replaced.
            # -------------------------------------------------
            selection_pool_size = min(
                len(technical_table),
                max(
                    effective_holdings_count,
                    max(30, effective_holdings_count + min(25, effective_holdings_count)),
                ),
            )

            selection_pool = (
                technical_table
                .head(selection_pool_size)
                .reset_index(drop=True)
            )

            snapshot_by_ticker = {}
            for _, row in selection_pool.iterrows():
                symbol = row["Ticker"]
                fallback = technical_fallback_snapshot(
                    symbol,
                    row.get("Company", symbol),
                    row.get("Sector", "Unknown"),
                    technical_map.get(symbol),
                    quick_score=row.get("Quick Score"),
                )
                if fallback is not None:
                    snapshot_by_ticker[symbol] = fallback

            candidate_snapshots = list(snapshot_by_ticker.values())
            holdings_count = min(
                effective_holdings_count,
                len(candidate_snapshots),
            )
            failures = []

            # ML needs enough history. Extend history for the entire finalist
            # pool BEFORE selection, not just for names already chosen.
            short_history = [
                h["ticker"]
                for h in candidate_snapshots
                if (
                    h["ticker"] in technical_map
                    and len(technical_map[h["ticker"]]["data"]) < 650
                )
            ]
            if short_history:
                prefetch_price_histories(short_history, min_rows=650)
                for symbol in short_history:
                    refreshed = _read_price_cache(symbol)
                    if (
                        refreshed is not None
                        and symbol in technical_map
                        and len(refreshed) > len(technical_map[symbol]["data"])
                    ):
                        technical_map[symbol] = technical_analysis_from_data(refreshed)

            # Run company fundamentals + Random Forest on ALL finalists.
            # Only after these final scores exist do we choose the holdings.
            if candidate_snapshots:
                progress = st.progress(0)
                status = st.empty()
                workers = min(4, os.cpu_count() or 1, len(candidate_snapshots))
                with ThreadPoolExecutor(max_workers=max(1, workers)) as executor:
                    jobs = {
                        executor.submit(
                            enrich_portfolio_holding,
                            candidate,
                            technical_map.get(candidate["ticker"]),
                        ): candidate
                        for candidate in candidate_snapshots
                    }
                    for done, job in enumerate(as_completed(jobs), 1):
                        candidate = jobs[job]
                        try:
                            job.result()
                        except Exception as exc:
                            candidate["ml_status"] = "Model error: {}".format(exc)
                            failures.append(candidate["ticker"])
                        status.caption(
                            "Deep analysis ready: {}/{} finalists".format(
                                done, len(candidate_snapshots)
                            )
                        )
                        progress.progress(done / len(candidate_snapshots))
                status.empty()
                progress.empty()

            diversify_sectors = (
                selection_mode == "Diversified Portfolio"
                and sector_focus == "All US-listed stocks (all sectors)"
            )

            result = build_ai_portfolio(
                candidate_snapshots,
                holdings_count,
                risk_profile,
                diversify_sectors=diversify_sectors,
                selection_mode=selection_mode,
            )

            # Keep the full analyzed ranking for transparency/debugging.
            analyzed_rankings = sorted(
                candidate_snapshots,
                key=lambda item: clamp(item.get("overall_score", 0.0)),
                reverse=True,
            )

            if result is None:
                st.error(
                    "The portfolio builder could not get enough usable market data."
                )

            else:
                st.markdown(
                    "### Suggested {} Portfolio".format(
                        risk_profile
                    )
                )

                st.caption(
                    "Sector focus: {}".format(
                        sector_focus
                    )
                )

                built_count = len(
                    result[
                        "holdings"
                    ]
                )

                if built_count == requested_holdings:
                    st.success(
                        "Portfolio complete: {} of {} requested holdings.".format(
                            built_count,
                            requested_holdings,
                        )
                    )
                else:
                    st.warning(
                        "Portfolio contains {} of {} requested holdings because only {} usable candidates were available.".format(
                            built_count,
                            requested_holdings,
                            len(
                                candidate_snapshots
                            ),
                        )
                    )

                m1, m2, m3 = st.columns(3)

                m1.metric(
                    "Weighted Hybrid Score",
                    "{:.1f}/100".format(
                        result["weighted_score"]
                    ),
                )

                if result["weighted_forecast_3m"] is not None:
                    m2.metric(
                        "Weighted 3M ML Forecast",
                        "{:+.1%}".format(
                            result[
                                "weighted_forecast_3m"
                            ]
                        ),
                    )
                else:
                    m2.metric(
                        "Weighted 3M ML Forecast",
                        "N/A",
                    )

                if result["portfolio_volatility"] is not None:
                    m3.metric(
                        "Est. Annualized Volatility",
                        "{:.1%}".format(
                            result[
                                "portfolio_volatility"
                            ]
                        ),
                    )
                else:
                    m3.metric(
                        "Est. Annualized Volatility",
                        "N/A",
                    )

                rows = []

                for holding in result["holdings"]:
                    predicted = holding[
                        "predicted_return_3m"
                    ]

                    direction_accuracy = holding[
                        "directional_accuracy"
                    ]

                    baseline = holding[
                        "baseline_accuracy"
                    ]

                    allocation = (
                        holding["weight"]
                        * budget
                    )

                    approximate_shares = None

                    if (
                        budget > 0
                        and holding["price"] > 0
                    ):
                        approximate_shares = int(
                            np.floor(
                                allocation
                                / holding["price"]
                            )
                        )

                    rows.append(
                        {
                            "Ticker":
                                holding["ticker"],

                            "Company":
                                holding[
                                    "company_name"
                                ] or "",

                            "Sector":
                                holding.get(
                                    "sector"
                                ) or "",

                            "Weight":
                                "{:.1%}".format(
                                    holding["weight"]
                                ),

                            "Hybrid Score":
                                "{:.1f}".format(
                                    holding[
                                        "overall_score"
                                    ]
                                ),

                            "Signal":
                                holding["signal"],

                            "Analysis": holding.get("analysis_depth", "Technical + ML"),
                            "ML Model": holding.get("ml_status", "Unavailable"),
                            "Company Score": ("{:.0f}/100".format(holding["fundamental_score"]) if holding.get("fundamental_score") is not None else "Unavailable"),
                            "Company Data": holding.get("fundamentals_status", "unavailable"),

                            "3M ML Score":
                                (
                                    "{:.0f}/100".format(
                                        holding["ml_score"]
                                    )
                                    if holding.get("ml_score") is not None
                                    else "N/A"
                                ),

                            "ML Reliability": ("{:.0%}".format(holding["ml_reliability"]) if holding.get("predicted_return_3m") is not None else "Unavailable"),

                            "3M ML Forecast":
                                (
                                    "{:+.1%}".format(
                                        predicted
                                    )
                                    if predicted is not None
                                    else "N/A"
                                ),

                            "Direction Accuracy":
                                (
                                    "{:.1%}".format(
                                        direction_accuracy
                                    )
                                    if direction_accuracy is not None
                                    else "N/A"
                                ),

                            "Always-Up Baseline":
                                (
                                    "{:.1%}".format(
                                        baseline
                                    )
                                    if baseline is not None
                                    else "N/A"
                                ),

                            "Volatility":
                                "{:.1%}".format(
                                    holding[
                                        "volatility"
                                    ]
                                ),

                            "Target Allocation":
                                (
                                    "${:,.0f}".format(
                                        allocation
                                    )
                                    if budget > 0
                                    else "—"
                                ),

                            "Approx. Shares":
                                (
                                    approximate_shares
                                    if approximate_shares is not None
                                    else "—"
                                ),
                        }
                    )

                portfolio_table = pd.DataFrame(rows)

                st.session_state["last_portfolio"] = {
                    "table": portfolio_table,
                    "risk": risk_profile,
                    "sector": sector_focus,
                    "budget": budget,
                }

                st.dataframe(portfolio_table[["Ticker", "Weight", "Signal", "3M ML Forecast", "Target Allocation"]], use_container_width=True, hide_index=True)
                with st.expander("All company & model details"):
                    st.dataframe(portfolio_table, use_container_width=True, hide_index=True)

                with st.expander("Final analyzed candidate ranking"):
                    selected_tickers = {h["ticker"] for h in result["holdings"]}
                    ranking_rows = []
                    for rank, candidate in enumerate(analyzed_rankings, 1):
                        ranking_rows.append({
                            "Rank": rank,
                            "Ticker": candidate["ticker"],
                            "Company": candidate.get("company_name", ""),
                            "Final Score": round(float(candidate.get("overall_score", 0.0)), 1),
                            "Technical": (round(float(candidate["technical_score"]), 1) if candidate.get("technical_score") is not None else None),
                            "Company Score": (round(float(candidate["fundamental_score"]), 1) if candidate.get("fundamental_score") is not None else None),
                            "3M ML Forecast": ("{:+.1%}".format(candidate["predicted_return_3m"]) if candidate.get("predicted_return_3m") is not None else "N/A"),
                            "Selected": "Yes" if candidate["ticker"] in selected_tickers else "",
                        })
                    st.dataframe(
                        pd.DataFrame(ranking_rows),
                        use_container_width=True,
                        hide_index=True,
                    )

                with st.expander("How the portfolio was chosen"):
                    st.markdown(
                        "#### How it chose the portfolio"
                    )

                    st.write(
                        "• The first pass ranks available U.S.-listed stocks using momentum, trend, RSI, and volatility."
                    )

                    st.write(
                        "• The strongest technical finalists are fully analyzed with company fundamentals and a fast Random Forest forecast BEFORE the final holdings are chosen."
                    )

                    st.write(
                        "• The 3-month ML forecast affects the final research score, while weak historical validation reduces its influence."
                    )

                    if selection_mode == "Best Stocks":
                        st.write(
                            "• Best Stocks mode then picks the holdings with the highest FINAL research scores. Correlation and sector concentration do not knock a higher-scoring stock out."
                        )
                    else:
                        st.write(
                            "• Diversified Portfolio mode starts from the final scores, then applies smaller correlation and sector-concentration penalties."
                        )

                    if failures:
                        st.write(
                            "• A finalist whose model cannot train keeps its technical/company score instead of being discarded."
                        )

                if technical_failures:
                    with st.expander(
                        "Stocks skipped for insufficient/unavailable price history ({})".format(
                            len(
                                technical_failures
                            )
                        ),
                        expanded=False,
                    ):
                        st.write(
                            ", ".join(
                                technical_failures
                            )
                        )

                if failures:
                    with st.expander(
                        "Model errors ({})".format(
                            len(failures)
                        ),
                        expanded=False,
                    ):
                        st.write(
                            ", ".join(
                                failures
                            )
                        )
                        st.caption(
                            "These names remain in the portfolio with a technical score when their model is unavailable."
                        )

                st.info(
                    "This is a model-generated research portfolio, not a guarantee of returns. "
                    "Quick mode samples listed stocks across sectors; enable the full market checkbox to screen every available symbol. Finalists are fully analyzed before holdings are chosen, using technical/company scores and a 3-month Random Forest forecast when enough data exists. This is a research experiment; results can change as market data changes."
                )

    elif "last_portfolio" in st.session_state:
        saved_portfolio = st.session_state["last_portfolio"]
        st.markdown("### Latest portfolio")
        st.caption("{} · {} · ${:,.0f} budget at build time. Rebuild to apply changed settings.".format(
            saved_portfolio["risk"], saved_portfolio["sector"], saved_portfolio["budget"]
        ))
        st.dataframe(saved_portfolio["table"][["Ticker", "Weight", "Signal", "3M ML Forecast", "Target Allocation"]], use_container_width=True, hide_index=True)
        with st.expander("All company & model details"):
            st.dataframe(saved_portfolio["table"], use_container_width=True, hide_index=True)

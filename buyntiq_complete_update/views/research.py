import streamlit as st
import pandas as pd
from buyntiq import ui, data
from buyntiq.analytics import analyze, model_brief
from buyntiq.state import persistent_widget

# News retrieval and relevance rules are local to this page.
import re
import time
from html import escape
from urllib.parse import urlsplit

_NEWS_ALIASES = {
    "NVDA": ["Nvidia"], "NFLX": ["Netflix"],
    "AAPL": ["Apple"], "MSFT": ["Microsoft"], "TSLA": ["Tesla"],
    "AMZN": ["Amazon", "Amazon Web Services"],
    "GOOG": ["Google", "Alphabet"], "GOOGL": ["Google", "Alphabet"],
    "META": ["Meta Platforms", "Facebook", "Instagram", "WhatsApp"],
    "BRK-B": ["Berkshire Hathaway"], "BRK-A": ["Berkshire Hathaway"],
}


def _news_terms(symbol, name):
    cleaned = re.sub(r"\s*·.*$", "", str(name or ""))
    cleaned = re.sub(r",?\s+(?:Inc\.?|Incorporated|Corp\.?|Corporation|Ltd\.?|Limited|plc|Company|Co\.?)$", "", cleaned, flags=re.I).strip()
    names = [cleaned] + _NEWS_ALIASES.get(symbol, [])
    # Never treat a bare ticker or generic company word as an unambiguous name.
    return list(dict.fromkeys(n for n in names if len(n) >= 3 and n.upper() != symbol and n.lower() not in {"holdings", "group", "company", "limited"}))


def _news_match(text, symbol, names):
    text = str(text or "")
    for name in names:
        if re.search(r"(?<!\w)" + re.escape(name) + r"(?!\w)", text, re.I):
            return name
    # Explicit ticker syntax avoids matching ordinary words such as ON, IT or CAT.
    ticker = re.escape(symbol)
    if re.search(r"(?:\$" + ticker + r"\b|\(" + ticker + r"\)|(?:NASDAQ|NYSE|AMEX)\s*:\s*" + ticker + r"\b)", text, re.I):
        return symbol
    return None


def _headline_focus(title, symbol, names):
    hit = _news_match(title, symbol, names)
    if not hit:
        return False
    # Require the company in the leading headline clause, not a passing later mention.
    lead = re.split(r"[;|]|\s[—–]\s", title, maxsplit=1)[0]
    if not _news_match(lead, symbol, names):
        return False
    # Conservative handling of roundup/comparison stories: do not show a story
    # whose first recognized company is someone else.
    first_target = re.search(r"(?<!\w)" + re.escape(hit) + r"(?!\w)", title, re.I)
    if first_target:
        for other_symbol, aliases in _NEWS_ALIASES.items():
            if other_symbol == symbol or any(a.lower() in {n.lower() for n in names} for a in aliases):
                continue
            for alias in aliases:
                found = re.search(r"(?<!\w)" + re.escape(alias) + r"(?!\w)", title, re.I)
                if found and found.start() < first_target.start():
                    return False
    return True


def _rank_news(articles, symbol, company_name):
    names = _news_terms(symbol, company_name)
    direct, mentions, other = [], [], []
    for article in articles:
        title_hit = _news_match(article["title"], symbol, names)
        summary_hit = _news_match(article.get("summary"), symbol, names)
        item = dict(article)
        if title_hit and _headline_focus(article["title"], symbol, names):
            item["relevance"] = "Company-focused headline: " + title_hit
            direct.append(item)
        elif summary_hit:
            item["relevance"] = "Summary mentions " + summary_hit + "; company may not be the main subject"
            mentions.append(item)
        else:
            item["relevance"] = "Company relevance not confirmed"
            other.append(item)
    return direct, mentions, other


@st.cache_data(ttl=60, show_spinner=False, max_entries=128)
def _fetch_company_news(symbol, refresh_token=0):
    # Errors propagate and are not converted into a cached empty feed.
    raw = data._stock(symbol).get_news(count=30)
    if not isinstance(raw, list):
        raise ValueError("Unexpected news response")
    articles, seen = [], set()
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        record = entry.get("content") or entry
        if not isinstance(record, dict):
            continue
        canonical = record.get("canonicalUrl") or {}
        link = canonical.get("url") if isinstance(canonical, dict) else None
        link = link or record.get("link")
        if not isinstance(link, str):
            continue
        parsed = urlsplit(link)
        if parsed.scheme != "https" or not parsed.netloc or link in seen:
            continue
        title = str(record.get("title") or "").strip()
        if not title:
            continue
        seen.add(link)
        provider = record.get("provider") or {}
        publisher = (provider.get("displayName") if isinstance(provider, dict) else None) or record.get("publisher") or "Publisher unavailable"
        raw_date = record.get("pubDate") or record.get("providerPublishTime")
        try:
            date = pd.to_datetime(raw_date, unit="s" if isinstance(raw_date, (int,float)) else None, utc=True)
            date_text = date.strftime("%b %d, %Y · %H:%M UTC") if pd.notna(date) else "Date unavailable"
            sort_date = date.value if pd.notna(date) else 0
        except (ValueError, TypeError, OverflowError):
            date_text, sort_date = "Date unavailable", 0
        articles.append({"title":title,"url":link,"publisher":str(publisher),"date":date_text,
                         "summary":str(record.get("summary") or record.get("description") or ""),"sort_date":sort_date})
    if raw and not articles:
        raise ValueError("News response contained no usable article links")
    return sorted(articles, key=lambda item:item["sort_date"], reverse=True)


def _news_cards(articles):
    for item in articles:
        title, link = escape(item["title"]), escape(item["url"], quote=True)
        summary = item.get("summary", "")
        summary = summary[:360] + ("…" if len(summary) > 360 else "")
        st.markdown(
            '<article class="buyntiq-news-card"><a target="_blank" rel="noopener noreferrer" href="' + link + '">' + title + '</a>'
            + '<div class="buyntiq-news-meta">' + escape(item["publisher"] + " · " + item["date"]) + '</div>'
            + ('<p>' + escape(summary) + '</p>' if summary else '')
            + '<div class="buyntiq-news-relevance">' + escape(item["relevance"]) + '</div></article>',
            unsafe_allow_html=True)


@st.cache_data(ttl=60, show_spinner=False, max_entries=128)
def _recent_quote(symbol):
    frame = data._stock(symbol).history(period="1d", interval="1m", auto_adjust=False, prepost=False, timeout=8, raise_errors=True)
    if frame is None or frame.empty or "Close" not in frame:
        raise ValueError("No intraday quote")
    valid = frame["Close"].dropna()
    if valid.empty or not float(valid.iloc[-1]) > 0:
        raise ValueError("Invalid intraday quote")
    stamp = pd.Timestamp(valid.index[-1])
    if stamp.tzinfo is not None:
        stamp = stamp.tz_convert("UTC")
    return float(valid.iloc[-1]), str(stamp)


@st.fragment(run_every="60s")
def _quote_panel(symbol, demo, currency):
    if demo:
        return
    enabled = st.toggle("Auto-refresh quote every 60 seconds", value=True, key="news_live_quote")
    if not enabled:
        return
    try:
        price, stamp = _recent_quote(symbol)
        st.metric("Latest intraday quote", f"{price:,.2f} {currency or ''}")
        st.caption(f"Latest available 1-minute bar: {stamp}. Provider data may be delayed; outside market hours the last available bar is shown. Research scores below use adjusted daily history.")
    except Exception:
        st.info("Intraday quote unavailable. The adjusted daily close below remains available with its own date.")


ui.header("01 / Stock research", "Look beneath the ticker.", "Price action, company quality, and an ML forecast you can inspect.")
left, right = st.columns([4, 1], vertical_alignment="bottom")
with left:
    entered = persistent_widget(st.text_input, "research_symbol", label="US-listed stock ticker", placeholder="AAPL", max_chars=12)
with right:
    clicked = st.button("Analyze stock", type="primary", width="stretch")

def choose_symbol(symbol):
    st.session_state.research_symbol = symbol
    st.session_state._research_symbol = symbol

if st.session_state.watchlist:
    chips = st.columns(min(6, len(st.session_state.watchlist)))
    for i, s in enumerate(st.session_state.watchlist[:6]):
        chips[i].button(s, key="research_chip_" + s, on_click=choose_symbol, args=(s,), width="stretch")

if clicked:
    with st.status("Researching your stock…", expanded=True) as status:
        st.write("Loading prices and company data, then validating the 3-month ensemble.")
        try:
            result = analyze(entered, demo=st.session_state.demo_mode)
            st.session_state.research_result = result
            st.session_state.pop("extra_forecasts", None)
            st.session_state.pop("news_result", None)
            status.update(label=f"Research ready · {result['elapsed']:.1f}s", state="complete", expanded=False)
        except Exception as exc:
            status.update(label="Research could not be loaded", state="error", expanded=False)
            st.error(str(exc))

r = st.session_state.research_result
if not r:
    st.markdown('<div class="empty-state"><h3>Start with a company you know.</h3>Enter a ticker above, then select Analyze stock.<br>The first request includes historical model validation.</div>', unsafe_allow_html=True)
    st.stop()

if not st.session_state.demo_mode:
    created = pd.to_datetime(r.get("created_at"), utc=True, errors="coerce")
    due = pd.isna(created) or (pd.Timestamp.now(tz="UTC")-created).total_seconds() >= 3600
    attempt_key = "research_refresh_attempt_" + r["symbol"]
    if due and time.time()-st.session_state.get(attempt_key, 0) >= 60:
        st.session_state[attempt_key] = time.time()
        try:
            with st.spinner("Refreshing research older than one hour…"):
                r = analyze(r["symbol"], demo=False)
                st.session_state.research_result = r
                st.session_state.pop("extra_forecasts", None)
        except Exception:
            st.warning("Hourly refresh failed. Showing previous research; check its price date before using it.")
    st.caption("Market data is rechecked after one hour on your next request. Prices are adjusted daily bars, not live intraday quotes. Quote and news polling run every 60 seconds while this page is open. Forecasts use daily history and are not retrained every minute.")

t, company, model = r["technical"], r["company"], r.get("forecast") or {}
if r["symbol"] != data.normalize_symbol(entered):
    st.caption(f"Showing saved research for {r['symbol']}. Select Analyze stock to load the ticker entered above.")
ui.section("RESEARCH", r["symbol"], company.get("company_name", ""))
st.caption(company.get("company_name", r["symbol"]) + " · " + company.get("sector", "Unknown sector"))
ui.badge(r["signal"])
_quote_panel(r["symbol"], st.session_state.demo_mode, company.get("currency"))
columns = st.columns(4)
with columns[0]: ui.metric("ADJUSTED CLOSE", ui.fmt(t["price"], "money") if company.get("currency") == "USD" else f"{t['price']:,.2f}", company.get("currency") or "Quote currency unverified")
with columns[1]: ui.metric("RESEARCH SCORE", f"{r['score']:.0f} / 100", "Technical + company + validated ML")
with columns[2]: ui.metric("3-MONTH MODEL", ui.fmt(model.get("predicted_return"), "percent"), "Historical estimate, not a target")
with columns[3]: ui.metric("ANNUALIZED VOLATILITY", ui.fmt(t["annualized_volatility"], "ratio"), "Historical daily price variation")
ui.data_notes([r])
if r.get("model_error"):
    st.info(r["model_error"])
overview, forecasts, fundamentals, news_tab = st.tabs(["Overview", "ML forecasts", "Company", "News"])
with overview:
    period_col, average_col = st.columns([3, 1], vertical_alignment="bottom")
    with period_col:
        period = st.radio("Chart range", ["3M", "6M", "1Y", "3Y"], index=2, horizontal=True, label_visibility="collapsed", key="research_chart_range")
    with average_col:
        averages = st.checkbox("Moving averages", value=True)
    ui.price_chart(r["prices"], {"3M":3,"6M":6,"1Y":12,"3Y":36}[period], averages)
    c1,c2,c3 = st.columns(3)
    for col, label, key in [(c1,"PAST MONTH","return_1m"),(c2,"PAST 3 MONTHS","return_3m"),(c3,"PAST 6 MONTHS","return_6m")]:
        with col: ui.metric(label, ui.fmt(t.get(key), "percent"), "Adjusted historical return")
    ui.section("01", "Model brief", "Generated from your analysis")
    for point in model_brief(r):
        st.write(point)
    with st.expander("See the score calculation"):
        st.dataframe(pd.DataFrame([{"Component": name, "Score": v["score"], "Weight (%)": v["weight"]*100, "Contribution": v["weight"]*v["score"]} for name,v in r["components"].items()]).round(2), hide_index=True, width="stretch")
        st.caption("Base weights: 55 technical / 45 company. Company weight scales with available metrics. ML receives up to 25% × its evidence weight; the remainder is rescaled. The same formula is used in both portfolio tools. These factor weights are heuristics, not a fitted trading strategy.")
    with st.expander("Technical detail"):
        st.write(f"RSI: {t['rsi']:.1f} · historical maximum drawdown: {ui.fmt(t.get('max_drawdown'), 'ratio')}")

with forecasts:
    ui.section("02", "Forecast & uncertainty", "3-month horizon")
    if not model.get("available"):
        st.info(model.get("reason", "The ML forecast is unavailable. Technical and company signals remain available."))
    else:
        c1,c2,c3 = st.columns(3)
        with c1: ui.metric("MODEL RETURN", ui.fmt(model["predicted_return"], "percent"), "63 observed trading sessions ahead")
        with c2: ui.metric("EMPIRICAL 80% RANGE", f"{model['lower_return']:+.0%} to {model['upper_return']:+.0%}", "Not a guaranteed probability band")
        with c3: ui.metric("MEASURED COVERAGE", ui.fmt(model["interval_coverage"], "ratio"), "Coverage on the separate holdout")
        st.caption(f"Calibrated on {model['calibration_start']}–{model['calibration_end']}. Tested on {model['holdout_start']}–{model['holdout_end']}. The range is estimated from past forecast errors and can fail under new market conditions.")
        if model["evidence_weight"] == 0:
            st.info("No demonstrated error advantage in both validation stages. This forecast has zero weight in the research score.")
        ui.section("VALIDATION", "What the model actually earned")
        evaluation = pd.DataFrame([
            {"Measure":"Mean absolute return error", "Ensemble":f"{model['mae']:.1%}", "Baseline":f"{model['baseline_mae']:.1%}"},
            {"Measure":"Direction accuracy (overlapping windows)", "Ensemble":f"{model['directional_accuracy']:.1%}", "Baseline":f"{model['always_up_accuracy']:.1%} · always up"},
            {"Measure":"Direction accuracy (non-overlapping windows)", "Ensemble":f"{model['nonoverlap_accuracy']:.1%}", "Baseline":"Small sample; interpret cautiously"},
        ])
        st.dataframe(evaluation, width="stretch", hide_index=True)
        st.caption(f"Return-error baseline: {model['baseline']}. {model['holdout_rows']} daily forecasts span only {model['independent_windows']} non-overlapping {model['horizon']}-session windows. The holdout sets an evidence gate after model selection; it is not an independent test of the entire gated strategy.")
        with st.expander("Models, weights & chronological folds"):
            st.dataframe(pd.DataFrame([{"Model":name, "Ensemble weight (%)":round(w*100,1), "Development MAE (%)":round(model['development_errors'][name]*100,2)} for name,w in model['weights'].items()]), hide_index=True, width="stretch")
            st.dataframe(pd.DataFrame(model["folds"]), hide_index=True, width="stretch")
            st.caption("Each training block stops a full forecast horizon before the next test block. Model weights use development folds only. Interval width uses the later calibration block. The final model is refitted on all labels currently available.")
            if model["feature_importance"]:
                st.write("Extra Trees feature importance (global model diagnostic)")
                st.dataframe(pd.DataFrame(list(model["feature_importance"].items()),columns=["Feature","Importance"]),hide_index=True,width="stretch")
                st.caption("Importance is an association learned by one model; it is not a causal explanation of this prediction.")
        with st.expander("Inspect held-out predictions"):
            predictions = pd.DataFrame(model["holdout_predictions"])
            st.dataframe(predictions, hide_index=True, width="stretch")
            ui.download_table(predictions, r["symbol"]+"_holdout.csv", "download_holdout")
        if st.button("Calculate 1-month and 6-month forecasts", width="stretch"):
            from buyntiq.model import forecast
            with st.spinner("Validating the additional horizons…"):
                try:
                    st.session_state.extra_forecasts = {label:forecast(r["prices"], days) for label, days in [("1 month",21),("6 months",126)]}
                except Exception as exc:
                    st.error(f"Additional forecasts unavailable: {exc}")
        extra = st.session_state.get("extra_forecasts")
        if extra:
            table = []
            for label,f in {"1 month":extra["1 month"],"3 months":model,"6 months":extra["6 months"]}.items():
                table.append({"Horizon":label,"Model return":ui.fmt(f.get("predicted_return"),"percent"),"Lower estimate":ui.fmt(f.get("lower_return"),"percent"),"Upper estimate":ui.fmt(f.get("upper_return"),"percent"),"Measured interval coverage":ui.fmt(f.get("interval_coverage"),"ratio")})
            st.dataframe(pd.DataFrame(table), hide_index=True, width="stretch")

with fundamentals:
    ui.section("03", "Company fundamentals", f"{company.get('coverage',0)}/6 metrics")
    if company.get("status") == "unavailable":
        st.info("Company information could not be loaded. Some listings and funds do not publish these metrics.")
    items = [("Revenue growth","revenue_growth","percent"),("Earnings growth","earnings_growth","percent"),("Profit margin","profit_margin","ratio"),("Forward P/E","forward_pe","number"),("Debt / equity","debt_to_equity","number"),("Free cash flow","free_cash_flow","number")]
    for offset in (0,3):
        for col, (label,key,kind) in zip(st.columns(3), items[offset:offset+3]):
            with col: ui.metric(label.upper(), ui.fmt(company.get(key),kind), company.get("financial_currency") or "" if key=="free_cash_flow" else "Company snapshot")
    st.caption("Cash flow is reported in the company's financial currency. Prices use quote currency. Financial ratios are broad heuristics and are not adjusted for sector accounting differences.")
    if company.get("description"):
        st.write(company["description"])
    st.caption("Source: " + company.get("source","Unavailable") + " · fetched " + company.get("fetched_at","unknown")[:16])
    if st.button("Refresh price & company data"):
        with st.spinner("Refreshing research…"):
            try:
                st.session_state.research_result = analyze(r["symbol"],st.session_state.demo_mode,refresh=True)
                st.session_state.pop("extra_forecasts",None)
                st.session_state.pop("news_result",None)
                st.rerun()
            except Exception as exc:
                st.error(str(exc))

@st.fragment(run_every="60s")
def _news_panel():
    st.markdown("""<style>
    .buyntiq-news-card {background:#171717;border:1px solid #404040;border-radius:10px;padding:18px 20px;margin:12px 0;}
    [data-testid="stMarkdownContainer"] .buyntiq-news-card a {color:#fafafa!important;font-size:1.03rem;font-weight:650;line-height:1.5;text-decoration:none;overflow-wrap:anywhere;}
    [data-testid="stMarkdownContainer"] .buyntiq-news-card a:hover {text-decoration:underline;color:#fff!important;}
    .buyntiq-news-card a:focus-visible {outline:2px solid #fff;outline-offset:4px;}
    .buyntiq-news-card p {color:#d6d6d6!important;font-size:.88rem;margin:10px 0 0;line-height:1.65;}
    .buyntiq-news-meta {color:#b8b8b8;font-size:.76rem;margin-top:9px;}
    .buyntiq-news-relevance {color:#bdbdbd;font-size:.74rem;margin-top:11px;border-top:1px solid #383838;padding-top:9px;}
    </style>""", unsafe_allow_html=True)
    ui.section("04", "Company news", r["symbol"])
    st.caption("Click Load company news once to start the feed. Automatic polling works while this page is open; it cannot guarantee the publisher has supplied newer articles.")
    st.caption("Only headlines passing a strict company-focus check appear here. Summary-only mentions and unconfirmed coverage are hidden. This is a conservative rules-based filter, not AI or a full-article review. News does not change your rating or forecast.")
    auto = st.toggle("Auto-refresh news every 60 seconds", value=True, key="news_live_feed")
    key = "company_news_v2_" + r["symbol"]
    if st.session_state.demo_mode:
        st.info("Turn off demo mode and analyze a stock to load live news.")
    else:
        col1, col2 = st.columns(2)
        load = col1.button("Load company news", key="load_company_news")
        refresh = col2.button("Refresh news", key="refresh_company_news")
        previous = st.session_state.get(key)
        expired = previous is not None and time.time() - previous.get("loaded_epoch", 0) >= 60
        retry_key = key + "_attempt"
        if load or refresh or (auto and expired and time.time()-st.session_state.get(retry_key, 0) >= 60):
            st.session_state[retry_key] = time.time()
            with st.spinner("Fetching and filtering company news…"):
                try:
                    if refresh:
                        _fetch_company_news.clear(r["symbol"])
                    articles = _fetch_company_news(r["symbol"])
                    st.session_state[key] = {"articles": articles, "loaded_epoch":time.time(), "loaded":pd.Timestamp.now(tz="UTC").strftime("%H:%M UTC")}
                except Exception:
                    st.error("The news provider could not supply usable articles. Try Refresh news shortly. This does not mean there is no company news.")
                    if key in st.session_state:
                        st.warning("Showing the previously loaded articles below.")
        saved = st.session_state.get(key)
        if saved is not None:
            direct, mentions, other = _rank_news(saved["articles"], r["symbol"], company.get("company_name", ""))
            st.caption(f"Fetched {saved['loaded']} · {len(saved['articles'])} articles checked. Cached for up to 60 seconds; Refresh news requests a new feed.")
            st.subheader("Company in the headline")
            if direct:
                _news_cards(direct)
            else:
                st.info("No headlines directly mentioning this company were found in the returned feed. This does not mean no relevant news exists.")
            st.caption(f"{len(mentions)+len(other)} broader or uncertain stories hidden. Strict filtering can miss relevant articles; fewer results are preferable to unrelated ones.")

with news_tab:
    _news_panel()

import streamlit as st
import pandas as pd
from buyntiq import ui, data
from buyntiq.analytics import analyze, model_brief
from buyntiq.state import persistent_widget

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

t, company, model = r["technical"], r["company"], r.get("forecast") or {}
if r["symbol"] != data.normalize_symbol(entered):
    st.caption(f"Showing saved research for {r['symbol']}. Select Analyze stock to load the ticker entered above.")
ui.section("RESEARCH", r["symbol"], company.get("company_name", ""))
st.caption(company.get("company_name", r["symbol"]) + " · " + company.get("sector", "Unknown sector"))
ui.badge(r["signal"])
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

with news_tab:
    ui.section("04", "Recent coverage", "Loaded on request")
    st.caption("Headlines provide context. They are not added to the ML forecast or research score.")
    if st.button("Load latest headlines"):
        with st.spinner("Loading news…"):
            st.session_state.news_result = data.news(r["symbol"], st.session_state.demo_mode)
    if "news_result" in st.session_state:
        articles = st.session_state.news_result
        if not articles:
            st.info("No headlines available." + (" Demo mode makes no news requests." if st.session_state.demo_mode else ""))
        for article in articles:
            st.link_button(article["title"],article["url"],width="stretch")
            st.caption(article["publisher"]+" · "+article["date"])

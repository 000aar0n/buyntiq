import hashlib
import streamlit as st
import pandas as pd
from buyntiq import ui
from buyntiq.portfolio import normalize_entries, review

ui.header("03 / Portfolio review", "See what you really hold.", "Add stocks and share counts to inspect research quality, concentration, and historical risk.")

def reset_editor(rows):
    st.session_state.review_rows = rows
    st.session_state.review_editor_base = rows
    st.session_state.review_editor_epoch = st.session_state.get("review_editor_epoch",0)+1

with st.expander("Import holdings from CSV"):
    uploaded = st.file_uploader("CSV with Ticker and Shares columns",type=["csv"])
    if uploaded is not None and st.button("Load CSV into table"):
        try:
            incoming = pd.read_csv(uploaded)
            incoming.columns = incoming.columns.str.strip().str.title()
            if not {"Ticker","Shares"}.issubset(incoming.columns):
                raise ValueError("The CSV needs columns named Ticker and Shares.")
            holdings = normalize_entries(incoming)
            reset_editor([{"Ticker":s,"Shares":q} for s,q in holdings.items()])
            st.rerun()
        except Exception as exc:
            st.error(str(exc))
    st.caption("Example: AAPL, 10.5 shares. Duplicate tickers are combined. USD quotes only.")

col1,col2,space = st.columns([1,1,3])
if col1.button("Load example",width="stretch"):
    reset_editor([{"Ticker":"AAPL","Shares":10.0},{"Ticker":"MSFT","Shares":5.0},{"Ticker":"NVDA","Shares":12.0}])
    st.rerun()
if col2.button("Clear table",width="stretch"):
    reset_editor([{"Ticker":"","Shares":0.0},{"Ticker":"","Shares":0.0}])
    st.rerun()
st.caption("Long positions only · fractional shares supported · up to 50 unique holdings")
epoch = st.session_state.get("review_editor_epoch",0)
editor_key = f"review_editor_{epoch}"
if editor_key not in st.session_state:
    st.session_state.review_editor_base = st.session_state.review_rows
entries = st.data_editor(pd.DataFrame(st.session_state.review_editor_base),num_rows="dynamic",hide_index=True,width="stretch",
                         column_config={"Ticker":st.column_config.TextColumn("Stock ticker",required=True),
                                        "Shares":st.column_config.NumberColumn("Shares owned",min_value=0.,format="%.6f",required=True)},key=editor_key)
st.session_state.review_rows = entries.to_dict("records")
if st.button("Review my portfolio",type="primary",width="stretch"):
    bar = st.progress(0.,text="Preparing your holdings")
    try:
        holdings = normalize_entries(entries)
        result = review(holdings,demo=st.session_state.demo_mode,progress=lambda fraction,message:bar.progress(fraction,text=message))
        result["input_holdings"] = holdings
        st.session_state.review_result = result
    except Exception as exc:
        st.error(str(exc))
    finally:
        bar.empty()

result = st.session_state.review_result
if not result:
    st.markdown('<div class="empty-state"><h3>Your holdings, in perspective.</h3>Enter tickers and share counts above to begin.<br>Nothing is connected to a brokerage or used to place trades.</div>',unsafe_allow_html=True)
    st.stop()
table = result["table"]
partial = bool(result["errors"])
ui.section("REVIEW","Your portfolio at a glance","Priced holdings only" if partial else "Current session")
st.caption("Review saved " + result["created_at"][:16].replace("T"," ") + " UTC. Run the review again to apply changes to the table.")
if partial:
    st.warning(f"Partial review: {len(result['errors'])} holding(s) excluded. Value, weights, score, and risk describe only successfully priced USD holdings; the value of excluded holdings is unknown.")
cols = st.columns(4)
with cols[0]:ui.metric("PRICED VALUE" if partial else "PORTFOLIO VALUE",ui.fmt(result["total"],"money"),"Historical close × shares")
with cols[1]:ui.metric("RESEARCH RATING",f"{result['score']:.0f} / 100","Weighted by position value")
with cols[2]:ui.metric("LARGEST POSITION",f"{table.iloc[0]['Weight']:.1%}",table.iloc[0]["Ticker"])
with cols[3]:ui.metric("EST. ANNUAL VOLATILITY",ui.fmt((result["risk"] or {}).get("volatility"),"ratio"),"Historical covariance estimate")
ui.forecast_summary(table, result["total"])
ui.holdings_table(table)
ui.data_notes(result["results"])
st.caption("Research rating uses the same company, technical, and gated ML scores as Stock Research. It is separate from diversification and risk. This view does not calculate profit/loss because purchase prices are not provided.")
left,right = st.columns(2)
with left:
    ui.section("01","Position weights")
    ui.allocation_chart(table)
with right:
    ui.section("02","Sector exposure")
    ui.allocation_chart(table,"Sector")
ui.section("03","Portfolio observations")
largest = table.iloc[0]
sector_weights = table.groupby("Sector").Weight.sum().sort_values(ascending=False)
st.write(f"**Concentration.** {largest['Ticker']} represents {largest['Weight']:.1%} of priced value. Your largest sector, {sector_weights.index[0]}, represents {sector_weights.iloc[0]:.1%}.")
st.write(f"**Effective diversification.** {len(table)} priced holdings behave like {result['effective_holdings']:.1f} equally weighted positions by concentration alone. This calculation does not account for correlation.")
ml_positions = int((table["ML score weight"]>0).sum())
st.write(f"**ML evidence.** {ml_positions} of {len(table)} priced holdings have a forecast that earned weight in their research score. Forecast availability alone does not establish predictive value.")
with st.expander("Position correlations"):
    if result["risk"]:
        st.dataframe(result["risk"]["correlations"].round(2),width="stretch")
        st.caption(f"Based on {result['risk']['observations']} aligned daily returns. Correlations change over time.")
    else:
        st.info("Not enough aligned history for a risk estimate.")
ui.errors_panel(result["errors"])
ui.download_table(table,"buyntiq_review.csv","download_review")
st.caption("Cash, debt, taxes, transaction costs, and unpriced positions are excluded. Data and financial metrics may have different as-of dates.")

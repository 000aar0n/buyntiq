import streamlit as st
import pandas as pd
from buyntiq import billing, ui, data
from buyntiq.portfolio import build

ui.header("02 / Portfolio builder", "Give your ideas a structure.", "Screen a universe, compare fully analyzed finalists, and turn their scores into an allocation.")
billing.require_pro("Portfolio builder")
settings = st.session_state.builder_settings
with st.form("builder_form"):
    c1,c2,c3 = st.columns(3)
    choices = ["Starter list", "My symbols", "US listing directory"]
    universe_choice = c1.selectbox("Universe",choices,index=choices.index(settings["universe"]))
    sectors = ["All sectors"] + list(data.STARTER)
    sector = c2.selectbox("Sector",sectors,index=sectors.index(settings["sector"]))
    methods = ["Highest scores", "Diversified"]
    method = c3.selectbox("Selection",methods,index=methods.index(settings["method"]),help="Highest scores selects by final research score among analyzed finalists with positive return estimates. Diversified applies correlation and sector penalties.")
    c1,c2,c3 = st.columns(3)
    budget = c1.number_input("Budget (USD)",min_value=0.,value=settings["budget"],step=1000.)
    count = c2.number_input("Holdings",min_value=1,max_value=30,value=settings["count"])
    profiles = ["Conservative","Balanced","Aggressive"]
    risk = c3.selectbox("Risk profile",profiles,index=profiles.index(settings["risk"]),help="Changes the allocation weights and position cap. Highest scores still selects by final score.")
    horizons = {"1 month":21,"3 months":63,"6 months":126,"1 year":252}
    horizon_name = st.selectbox("Investment horizon", list(horizons), index=list(horizons).index(settings.get("horizon_name", "3 months")))
    with st.expander("Screening controls & custom symbols"):
        custom = st.text_area("My symbols",value=settings["custom"],help="Used only when Universe is set to My symbols. Up to 100 valid symbols.")
        c1,c2 = st.columns(2)
        finalists = c1.slider("Full company + ML analyses",6,40,settings["finalists"],help="The technical first pass creates this shortlist. Increase it to compare more finalists. The holdings count is always respected.")
        limit = c2.select_slider("Directory screen limit",options=[50,100,250,500,"Entire US universe"],value=settings.get("limit", "Entire US universe"),help="Entire US universe screens all directory-returned listings matching your sector. Full company + ML analysis is applied to the technical shortlist, not every listing.")
        st.caption("Starter list: 62 named stocks across 11 sectors. Full analysis is slower on first use. Prices, company snapshots, and model results are reused when cached.")
    st.caption("Selection rule: known sectors, available company financials, valid prices, and positive return estimates for the selected investment horizon are required. Fewer holdings may be returned if the shortlist has too few positive estimates.")
    st.caption("Entire US universe: choose US listing directory above and Entire US universe under Directory screen limit. Large scans can take many minutes; provider failures are reported, not counted as a complete scan.")
    submitted = st.form_submit_button("Build portfolio",type="primary",width="stretch")
if submitted:
    st.session_state.builder_settings = dict(universe=universe_choice,sector=sector,method=method,budget=budget,count=int(count),risk=risk,custom=custom,finalists=finalists,limit=limit,horizon_name=horizon_name)
    bar = st.progress(0.,text="Preparing the research universe")
    try:
        with billing.action("build"):
            sector_hints = {}
            if universe_choice == "My symbols":
                symbols = data.parse_symbols(custom)
                if sector != "All sectors":
                    st.caption("Custom symbols are analyzed as entered; the sector filter applies to the starter list and directory.")
            else:
                if st.session_state.demo_mode and universe_choice == "US listing directory":
                    raise ValueError("Use Starter list or My symbols in demo mode. The directory requires a live request.")
                universe = data.universe(broad=universe_choice == "US listing directory")
                if sector != "All sectors":
                    aliases = {"Consumer Cyclical":["consumer discretionary"],"Consumer Defensive":["consumer staples"],"Financial Services":["finance","financials"],"Basic Materials":["basic materials","materials"],"Communication Services":["telecommunications"]}
                    accepted = [sector.lower()] + aliases.get(sector,[])
                    universe = universe[universe.Sector.str.lower().isin(accepted)]
                if universe_choice == "US listing directory" and limit != "Entire US universe" and len(universe)>limit:
                    # Randomize within sectors then round-robin sectors for balance.
                    groups = [g.sample(frac=1,random_state=42).reset_index(drop=True) for _,g in universe.groupby("Sector")]
                    rows = [g.iloc[i] for i in range(max(map(len,groups))) for g in groups if i<len(g)]
                    universe = pd.DataFrame(rows[:limit])
                symbols = universe.Symbol.tolist()
                sector_hints = dict(zip(universe.Symbol, universe.Sector))
            if not symbols:
                raise ValueError("No symbols matched. Choose another sector or enter valid custom tickers.")
            result = build(symbols,count=int(count),budget=budget,profile=risk,method=method,finalists=finalists,
                           horizon=horizons[horizon_name],demo=st.session_state.demo_mode,sector_hints=sector_hints,progress=lambda fraction,message:bar.progress(fraction,text=message))
            result["universe_name"] = universe_choice
            result["sector_filter"] = sector
            st.session_state.builder_result = result
    except Exception as exc:
        st.session_state.builder_result = None
        st.error(str(exc))
    finally:
        bar.empty()

result = st.session_state.builder_result
if not result:
    st.markdown('<div class="empty-state"><h3>From research to allocation.</h3>Choose a universe and your settings, then build.<br>The default analyzes a shortlist to keep the first run manageable.</div>',unsafe_allow_html=True)
    st.stop()
table = result["table"]
ui.section("ALLOCATION","Your research portfolio",result["method"])
st.caption(f"Forecast horizon: {result.get('horizon',63)} trading sessions. Universe: {result.get('universe_count',result['screened'])} listings; {result['screened']} successfully screened; {result['analyzed']} received full analysis.")
st.caption(f"Built {result['created_at'][:16].replace('T',' ')} UTC · {result['universe_name']} · {result['sector_filter']} · {result['profile']} · ${result['budget']:,.0f} budget. Submit Build portfolio to apply changed settings.")
if len(table)<result["requested"]:
    st.warning(f"Only {len(table)} of {result['requested']} requested holdings met the positive-return and data requirements. Review exclusions below.")

st.markdown('<div class="builder-vspace"></div>', unsafe_allow_html=True)

cols = st.columns(4)
with cols[0]:ui.metric("WEIGHTED RESEARCH SCORE",f"{result['score']:.0f} / 100","Weighted by target allocation")
with cols[1]:ui.metric("HOLDINGS",str(len(table)),f"{result['analyzed']} candidates fully analyzed")
with cols[2]:ui.metric("EST. ANNUAL VOLATILITY",ui.fmt((result["risk"] or {}).get("volatility"),"ratio"),"Shrunk historical covariance")
with cols[3]:ui.metric("UNALLOCATED CASH",ui.fmt(result["cash"],"money"),"After rounding to whole shares")

st.markdown('<div class="builder-vspace"></div>', unsafe_allow_html=True)

ui.forecast_summary(table, result["budget"], result.get("horizon",63))

st.markdown('<div class="builder-vspace"></div>', unsafe_allow_html=True)

ui.holdings_table(table, result.get("horizon",63))

st.markdown('<div class="builder-vspace-sm"></div>', unsafe_allow_html=True)

ui.data_notes(result["results"])
st.caption("Weights and risk estimates describe fractional target allocations. Whole-share execution can produce different weights; the remaining budget stays as cash.")

st.markdown('<div class="builder-vspace-lg"></div>', unsafe_allow_html=True)

left,right = st.columns([1,1])
with left:
    ui.section("01","Position weights")
    ui.allocation_chart(table)
with right:
    ui.section("02","Sector exposure")
    ui.allocation_chart(table,"Sector")

st.markdown('<div class="builder-vspace-lg"></div>', unsafe_allow_html=True)

ui.download_table(table,"buyntiq_portfolio.csv","download_builder")

st.markdown('<div class="builder-vspace"></div>', unsafe_allow_html=True)

with st.expander("Final candidate ranking",expanded=True):
    ranking = result["ranking"].copy()
    ranking["ML score weight"] *= 100
    st.dataframe(ranking.round(2),width="stretch",hide_index=True)
    st.caption(f"{result['screened']} symbols received a technical screen; {result['analyzed']} finalists completed company + ML analysis. Highest scores means best within those analyzed finalists, not the whole market.")

st.markdown('<div class="builder-vspace-sm"></div>', unsafe_allow_html=True)

with st.expander("How the allocation was made"):
    st.write("1. Screen valid price histories by the existing technical score.")
    st.write("2. Compute the same company + ML research score used on Stock Research for each finalist.")
    st.write("3. Exclude missing, zero, and negative return estimates. Highest scores selects remaining candidates by final score, with ticker ordering to break exact ties. Diversified subtracts up to 12 score points for positive correlation and 4 points for each selected stock in the same sector.")
    st.write("4. Allocate using score and inverse historical volatility, adjusted by the risk profile, then enforce the position cap.")
    st.caption(f"Effective position cap: {result['effective_cap']:.0%}." + (f" Raised from {result['nominal_cap']:.0%} because fewer holdings make the requested cap impossible." if result["effective_cap"]>result["nominal_cap"] else ""))
    st.caption("Risk uses a Ledoit–Wolf shrinkage covariance estimate from up to 252 aligned daily returns. The scoring and allocation rules have not been shown to outperform in an out-of-sample trading backtest.")
ui.errors_panel(result["errors"])

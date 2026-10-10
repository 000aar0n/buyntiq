"""Monochrome visual system shared by four independent Streamlit pages."""
from pathlib import Path
import html
import streamlit as st

ASSETS = Path(__file__).resolve().parent.parent / "assets"


def styles():
    st.markdown("<style>" + (ASSETS / "style.css").read_text() + "</style>", unsafe_allow_html=True)


def header(kicker, title, description):
    st.markdown(f'<div class="page-heading"><div class="eyebrow">{html.escape(kicker)}</div><h1>{html.escape(title)}</h1><p>{html.escape(description)}</p></div>', unsafe_allow_html=True)


def section(number, title, detail=""):
    st.markdown(f'<div class="section-heading"><span>{html.escape(number)}</span><h3>{html.escape(title)}</h3><small>{html.escape(detail)}</small></div>', unsafe_allow_html=True)


def fmt(value, kind="number"):
    if value is None:
        return "—"
    import math
    if not math.isfinite(value):
        return "—"
    if kind == "money":
        return f"${value:,.2f}"
    if kind == "percent":
        return f"{value:+.1%}"
    if kind == "ratio":
        return f"{value:.1%}"
    return f"{value:,.1f}"


def metric(label, value, detail=""):
    st.markdown(f'<div class="metric-card"><div class="metric-label">{html.escape(label)}</div><div class="metric-value">{html.escape(str(value))}</div><div class="metric-detail">{html.escape(detail)}</div></div>', unsafe_allow_html=True)


def badge(text):
    st.markdown(f'<span class="badge">{html.escape(text)}</span>', unsafe_allow_html=True)


def chart_style(chart):
    import altair as alt
    return (chart.configure(background="transparent", font="Arial")
            .configure_view(strokeOpacity=0)
            .configure_axis(labelColor="#9e9e9e", titleColor="#bcbcbc", gridColor="#252525", domainColor="#333333", tickColor="#333333", labelPadding=8)
            .configure_legend(labelColor="#bbbbbb", titleColor="#bbbbbb"))


def price_chart(frame, months=12, averages=True):
    import altair as alt
    import pandas as pd
    c = frame.Close
    chart_frame = pd.DataFrame({"Adjusted close": c})
    if averages:
        chart_frame["50-day average"] = c.rolling(50).mean()
        chart_frame["200-day average"] = c.rolling(200).mean()
    df = chart_frame.tail(months*21).rename_axis("Date").reset_index().melt("Date", var_name="Series", value_name="Price")
    labels = list(chart_frame.columns)
    chart = alt.Chart(df).mark_line(strokeWidth=2).encode(
        x=alt.X("Date:T", title=None), y=alt.Y("Price:Q", title="Adjusted price", scale=alt.Scale(zero=False)),
        color=alt.Color("Series:N", scale=alt.Scale(domain=labels, range=["#eeeeee", "#999999", "#5c5c5c"][:len(labels)]), legend=alt.Legend(orient="bottom", title=None)),
        strokeDash=alt.StrokeDash("Series:N", scale=alt.Scale(domain=labels, range=[[1,0],[6,3],[2,4]][:len(labels)]), legend=None),
        tooltip=[alt.Tooltip("Date:T"), alt.Tooltip("Series:N"), alt.Tooltip("Price:Q", format=",.2f")],
    ).properties(height=340).interactive()
    st.altair_chart(chart_style(chart), width="stretch", theme=None)


def allocation_chart(table, group="Ticker"):
    import altair as alt
    totals = table.groupby(group, as_index=False).Weight.sum().sort_values("Weight", ascending=False)
    chart = alt.Chart(totals).mark_bar(color="#d0d0d0", cornerRadiusEnd=3).encode(
        x=alt.X("Weight:Q", title=None, axis=alt.Axis(format="%")),
        y=alt.Y(f"{group}:N", title=None, sort="-x"),
        tooltip=[group, alt.Tooltip("Weight:Q", format=".1%")],
    ).properties(height=max(150, len(totals)*32))
    st.altair_chart(chart_style(chart), width="stretch", theme=None)


def holdings_table(table, horizon=63):
    display = table.copy()
    for column in ["Weight", "ML forecast", "ML score weight"]:
        if column in display:
            display[column] *= 100
    columns = {c: st.column_config.NumberColumn(c, format="$%.2f") for c in ["Price", "Value", "Target allocation"]}
    columns.update({c: st.column_config.NumberColumn(("Return estimate" if c == "ML forecast" else c) + " (%)", format="%.1f%%") for c in ["Weight", "ML forecast", "ML score weight"]})
    columns["Score"] = st.column_config.NumberColumn(format="%.1f")
    st.dataframe(display, hide_index=True, width="stretch", column_config=columns)


def data_notes(results):
    stale = [r["symbol"] for r in results if r["prices"].attrs.get("stale") or r["company"].get("stale")]
    if stale:
        st.warning("Showing saved data after a provider failure: " + ", ".join(stale))
    dates = sorted({r["as_of"] for r in results})
    if dates:
        mode = "Synthetic demo · " if any(r.get("demo") for r in results) else ""
        st.caption(mode + "Adjusted historical closes · " + (dates[0] if len(dates)==1 else f"{dates[0]} to {dates[-1]}") + " · prices are not live quotes")


def download_table(table, filename, key):
    # Label exported demo data and retain ratio columns as decimal fractions.
    prefix = "DEMO_" if st.session_state.demo_mode else ""
    export = table.copy()
    if st.session_state.demo_mode:
        export["Data source"] = "Synthetic demo"
    st.download_button("Download CSV", export.to_csv(index=False), prefix + filename, "text/csv", key=key)


def errors_panel(errors):
    if errors:
        with st.expander(f"Unavailable or excluded symbols · {len(errors)}"):
            for symbol, message in errors.items():
                st.write(f"**{symbol}** — {message}")


def forecast_summary(table, value, horizon=63, allocation_choice=False):
    from buyntiq.portfolio import projected_portfolio
    label = {21:"1-MONTH",63:"3-MONTH",126:"6-MONTH",252:"1-YEAR"}[horizon]
    whole = allocation_choice and value > 0 and "Shares" in table
    if whole:
        basis = st.radio("Projection allocation", ["Whole shares + cash", "Fractional target weights"], horizontal=True, key="projection_basis")
        whole = basis == "Whole shares + cash"
    result = projected_portfolio(table, value, whole)
    if not result["available"]:
        st.warning(f"Return estimates cover {result['coverage']:.1%} of invested allocation. A full portfolio projection is unavailable.")
        return
    cols = st.columns(3)
    with cols[0]: metric(f"PORTFOLIO · {label} RETURN", f"{result['return']:+.1%}", f"{horizon} trading sessions · weighted dollar gains")
    with cols[1]: metric("PROJECTED GAIN / LOSS", f"${result['gain']:+,.2f}", "Before fees and taxes")
    with cols[2]: metric("PROJECTED END VALUE", f"${result['end_value']:,.2f}", "Includes uninvested cash" if whole else "Displayed holdings / target allocation")
    st.caption("Estimates are uncertain, not guaranteed returns. Cash assumes 0% return. Forecast method identifies limited-history ML and statistical fallbacks; these receive no ML rating weight.")
    weak = table.get("Forecast method")
    if weak is not None:
        count = int(weak.ne("Validated ensemble").sum())
        if count:
            st.caption(f"Forecast evidence: {count} of {len(table)} holdings have weak or unvalidated estimates. Selecting positive forecasts does not establish that those stocks will rise.")
    unknown = table.loc[table.Sector.eq("Unknown"), "Ticker"].tolist()
    if unknown:
        st.warning("Sector data remains unavailable for: " + ", ".join(unknown) + ". These are not treated as a shared sector during diversification.")

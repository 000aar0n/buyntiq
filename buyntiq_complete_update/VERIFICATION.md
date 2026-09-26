# Verification for this build

Checked on September 26, 2026 with Python 3.12.14, Streamlit 1.55.0, pandas 2.2.3, NumPy 2.3.5, scikit-learn 1.8.0, Altair 6.3.0, and yfinance 0.2.66.

## Automated checks

`python -m pytest -q`: **24 passed** (13.61 seconds in the final engine verification run). All app/package/page Python files also compile successfully.

Coverage includes:

- Prefix-invariant features: modifying later prices cannot change earlier features.
- Full forecast-horizon gaps at training/calibration/holdout boundaries for all three horizons.
- Zero score weight for unqualified ML; no fabricated skill against a perfect baseline.
- Stable content-keyed model caching and exact price-cache round trips.
- Missing/failed provider responses, labeled stale fallback, and no silent switch to demo data.
- Quote-currency versus financial-reporting currency, including negative debt/equity handling.
- Duplicate symbols, fractional holdings, invalid share counts, partial reviews, and arithmetic totals.
- Allocation sums, feasible position caps, whole-share rounding, and cash residuals.
- Highest-score selection based on final rankings.
- All four independent pages, no market requests during navigation, persistent session results, and mode changes that clear results.
- Builder and review workflows with downloads.

## Browser checks

The real Streamlit app was opened in headless Chromium at **1440 × 1050** and **390 × 844**.

- Navigated Home → Stock Research → Portfolio Builder → Portfolio Review → Home.
- Ran stock research and viewed its price chart and ML validation tab.
- Built an allocation from custom symbols and reviewed example holdings.
- No JavaScript page errors or Streamlit exception elements were observed.
- No horizontal page overflow at mobile width.
- Desktop and mobile screenshots were visually inspected. `previews/home.png` shows the homepage using synthetic demo mode.

## Observed timing

- The initial Home page AppTest run took approximately **0.2 seconds**, excluding server/process startup. It issued no market-data request.
- The first synthetic stock analysis during development took approximately **3.3 seconds**, including scientific-library imports; model fitting itself took approximately **1.3 seconds**.
- A cached synthetic stock analysis completed in approximately **0.1 seconds** during browser inspection.

These are local observations, not performance guarantees. Hardware, provider latency, first-time imports, ticker history, and finalist count affect actual timings.

## Unverified external behavior

The live Yahoo price request was unavailable in this execution environment. Successful live responses are covered with provider mocks, but end-to-end live data and the Nasdaq directory still need to be checked from the deployment environment. The Windows launcher was inspected but could not be run on this Linux host.

The forecast checks use generated data. They validate software behavior and evaluation boundaries, **not real-market prediction accuracy or trading profitability**. No such performance claim is made.

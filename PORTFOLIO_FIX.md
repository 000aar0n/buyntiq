Buyntiq portfolio update
========================
Install: extract this ZIP and upload its CONTENTS into the existing GitHub folder
buyntiq_v4/buyntiq, replacing files. Keep the Streamlit entrypoint unchanged.
After the app updates, submit Build portfolio again to replace old session results.

Portfolio Builder:
- Choose investment horizon: 1 month (21 sessions), 3 months (63), 6 months
  (126), or 1 year (252). Forecasts are trained for the selected horizon;
  these are not linearly scaled 3-month predictions.
- Whole-portfolio projected return, gain/loss, and ending value appear above
  holdings. Default projection uses whole shares plus cash, with a 0% cash return.
  Switch to fractional target weights if that is how you plan to allocate.
- Recommendations require a known sector, available company financial scoring,
  a valid USD price, and a positive finite return estimate. Exclusions are visible.
  Missing individual financial metrics can still reduce the company-score weight.
- Portfolio Review preserves entered holdings, even when data is incomplete.
- Choose Universe = US listing directory, Sector = All sectors, and Directory
  screen limit = Entire US universe to price-screen every returned listing.
  Screening is chunked to bound memory. Full ML runs on the technical shortlist
  selected by Full company + ML analyses. This is NOT full ML on every US stock.
- Directory coverage depends on the provider; do not assume OTC coverage or
  complete data for every security. Provider failures are reported as exclusions.
  Full scans can take many minutes. Prices and forecasts are cached.

Forecast evidence:
- Full ensemble with chronological purging, calibration and holdout first.
- Short-history Ridge ML where at least 60 horizon-specific labels have matured.
- Statistical fallback if even that training data is unavailable, explicitly labeled.
- Limited-history forecasts receive no ML research-score influence.
- Positive forecasts are not a guarantee of gains. No claim of improved real-market
  accuracy or market outperformance is made. Live provider access was not verified.
